import { useState, useEffect, useCallback } from 'react';
import {
  Activity,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  Play,
  Square,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  Shield,
} from 'lucide-react';
import { API_BASE } from '@/lib/api-base';
import { useAuth } from '@/context/AuthContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';

const STATUS_BADGE: Record<string, string> = {
  RUNNING: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  COMPLETED: 'bg-green-500/20 text-green-400 border-green-500/30',
  FAILED: 'bg-red-500/20 text-red-400 border-red-500/30',
  TERMINATED: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-500/20 text-red-400 border-red-500/30',
  WARNING: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  INFO: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
};

const ALERT_STATUS_BADGE: Record<string, string> = {
  OPEN: 'bg-red-500/20 text-red-400 border-red-500/30',
  ACKNOWLEDGED: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  RESOLVED: 'bg-green-500/20 text-green-400 border-green-500/30',
};

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...opts });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Overview Tab ──

function OverviewTab() {
  const [health, setHealth] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setHealth(await api('/api/operations/health-summary')); }
    catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [refresh]);

  if (!health && loading) return <div className="p-6 text-muted-foreground">Loading...</div>;
  if (!health) return <div className="p-6 text-muted-foreground">Failed to load health data</div>;

  const conductorColor = health.conductor?.status === 'healthy' ? 'text-green-400'
    : health.conductor?.status === 'degraded' ? 'text-yellow-400' : 'text-red-400';

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4">
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Conductor</CardTitle></CardHeader>
        <CardContent>
          <div className={`text-lg font-semibold capitalize ${conductorColor}`}>
            {health.conductor?.status ?? 'unknown'}
          </div>
          {health.conductor?.message && <p className="text-xs text-muted-foreground mt-1 truncate">{health.conductor.message}</p>}
        </CardContent>
      </Card>
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Active Workers</CardTitle></CardHeader>
        <CardContent><div className="text-2xl font-bold">{health.active_workers}</div></CardContent>
      </Card>
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Running Workflows</CardTitle></CardHeader>
        <CardContent><div className="text-2xl font-bold text-blue-400">{health.running_workflows}</div></CardContent>
      </Card>
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Failed (24h)</CardTitle></CardHeader>
        <CardContent><div className={`text-2xl font-bold ${health.failed_workflows_24h > 0 ? 'text-red-400' : ''}`}>{health.failed_workflows_24h}</div></CardContent>
      </Card>
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Open Alerts</CardTitle></CardHeader>
        <CardContent><div className={`text-2xl font-bold ${health.open_alerts > 0 ? 'text-yellow-400' : ''}`}>{health.open_alerts}</div></CardContent>
      </Card>
      <Card className="bg-cv-card border-cv-border">
        <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Kafka</CardTitle></CardHeader>
        <CardContent><div className="text-lg font-semibold text-green-400 capitalize">{health.kafka?.status ?? 'unknown'}</div></CardContent>
      </Card>
    </div>
  );
}

// ── Workflows Tab ──

function WorkflowsTab() {
  const { can } = useAuth();
  const isAdmin = can('admin');
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [actionDialog, setActionDialog] = useState<{ type: 'retry' | 'terminate'; id: string } | null>(null);
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setWorkflows(await api('/api/operations/workflows?limit=100')); }
    catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const viewDetail = async (wf: any) => {
    setSelected(wf);
    try { setDetail(await api(`/api/operations/workflows/${wf.conductor_workflow_id}`)); }
    catch { setDetail(null); }
  };

  const doAction = async () => {
    if (!actionDialog || !reason.trim()) return;
    const endpoint = actionDialog.type === 'retry' ? 'retry' : 'terminate';
    await api(`/api/operations/workflows/${actionDialog.id}/${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    setActionDialog(null);
    setReason('');
    refresh();
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>
      <div className="rounded-md border border-cv-border overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Workflow ID</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Completed</TableHead>
              {isAdmin && <TableHead>Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflows.length === 0 && (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">No workflows found</TableCell></TableRow>
            )}
            {workflows.map((wf) => (
              <TableRow
                key={wf.id}
                className="cursor-pointer hover:bg-cv-card/50"
                onClick={() => viewDetail(wf)}
              >
                <TableCell className="font-mono text-xs">{wf.conductor_workflow_id?.slice(0, 12)}...</TableCell>
                <TableCell>{wf.workflow_name}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={STATUS_BADGE[wf.workflow_status] ?? ''}>
                    {wf.workflow_status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{fmtTime(wf.started_at)}</TableCell>
                <TableCell className="text-xs">{fmtTime(wf.completed_at)}</TableCell>
                {isAdmin && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    {wf.workflow_status === 'FAILED' && (
                      <Button size="sm" variant="ghost" onClick={() => setActionDialog({ type: 'retry', id: wf.conductor_workflow_id })}>
                        <RotateCcw className="w-3.5 h-3.5" />
                      </Button>
                    )}
                    {wf.workflow_status === 'RUNNING' && (
                      <Button size="sm" variant="ghost" className="text-red-400" onClick={() => setActionDialog({ type: 'terminate', id: wf.conductor_workflow_id })}>
                        <Square className="w-3.5 h-3.5" />
                      </Button>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {selected && detail && (
        <Card className="bg-cv-card border-cv-border">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Workflow Detail</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => { setSelected(null); setDetail(null); }}>
              <XCircle className="w-4 h-4" />
            </Button>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-black/30 rounded p-3 overflow-auto max-h-64">{JSON.stringify(detail, null, 2)}</pre>
          </CardContent>
        </Card>
      )}

      <Dialog open={!!actionDialog} onOpenChange={() => { setActionDialog(null); setReason(''); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{actionDialog?.type === 'retry' ? 'Retry Workflow' : 'Terminate Workflow'}</DialogTitle>
          </DialogHeader>
          <textarea
            className="w-full rounded border border-cv-border bg-black/30 p-2 text-sm min-h-[80px]"
            placeholder="Reason (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => { setActionDialog(null); setReason(''); }}>Cancel</Button>
            <Button onClick={doAction} disabled={!reason.trim()}>
              {actionDialog?.type === 'retry' ? 'Retry' : 'Terminate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Alerts Tab ──

function AlertsTab() {
  const { can } = useAuth();
  const isAdmin = can('admin');
  const [alerts, setAlerts] = useState<any[]>([]);
  const [filter, setFilter] = useState<string>('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [noteDialog, setNoteDialog] = useState<{ type: 'ack' | 'resolve'; id: string } | null>(null);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const qs = filter ? `?status=${filter}&limit=100` : '?limit=100';
    try { setAlerts(await api(`/api/operations/alerts${qs}`)); }
    catch { /* ignore */ }
    finally { setLoading(false); }
  }, [filter]);

  useEffect(() => { refresh(); }, [refresh]);

  const toggleExpand = async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    try { setDeliveries(await api(`/api/operations/alerts/${id}/deliveries`)); }
    catch { setDeliveries([]); }
  };

  const doAction = async () => {
    if (!noteDialog) return;
    const endpoint = noteDialog.type === 'ack' ? 'acknowledge' : 'resolve';
    const body = noteDialog.type === 'ack' ? { note } : { resolution_note: note || 'Resolved' };
    await api(`/api/operations/alerts/${noteDialog.id}/${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    setNoteDialog(null);
    setNote('');
    refresh();
  };

  const filters = ['', 'OPEN', 'ACKNOWLEDGED', 'RESOLVED'] as const;

  return (
    <div className="p-4 space-y-4">
      <div className="flex gap-2 items-center justify-between flex-wrap">
        <div className="flex gap-1">
          {filters.map((f) => (
            <Button
              key={f || 'all'}
              variant={filter === f ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter(f)}
            >
              {f || 'All'}
            </Button>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>
      <div className="rounded-md border border-cv-border overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8"></TableHead>
              <TableHead>Severity</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Triggered</TableHead>
              {isAdmin && <TableHead>Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {alerts.length === 0 && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-8">No alerts found</TableCell></TableRow>
            )}
            {alerts.map((a) => (
              <>
                <TableRow key={a.id} className="cursor-pointer hover:bg-cv-card/50" onClick={() => toggleExpand(a.id)}>
                  <TableCell>
                    {expanded === a.id ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={SEVERITY_BADGE[a.severity] ?? ''}>{a.severity}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[200px] truncate">{a.title}</TableCell>
                  <TableCell className="text-xs">{a.source_type}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={ALERT_STATUS_BADGE[a.status] ?? ''}>{a.status}</Badge>
                  </TableCell>
                  <TableCell className="text-xs">{fmtTime(a.first_triggered_at)}</TableCell>
                  {isAdmin && (
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {a.status === 'OPEN' && (
                        <Button size="sm" variant="ghost" onClick={() => setNoteDialog({ type: 'ack', id: a.id })}>
                          <CheckCircle2 className="w-3.5 h-3.5 mr-1" /> Ack
                        </Button>
                      )}
                      {(a.status === 'OPEN' || a.status === 'ACKNOWLEDGED') && (
                        <Button size="sm" variant="ghost" className="text-green-400" onClick={() => setNoteDialog({ type: 'resolve', id: a.id })}>
                          <Shield className="w-3.5 h-3.5 mr-1" /> Resolve
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
                {expanded === a.id && (
                  <TableRow key={`${a.id}-detail`}>
                    <TableCell colSpan={7} className="bg-black/20 p-4">
                      <div className="space-y-2">
                        {a.description && <p className="text-sm text-muted-foreground">{a.description}</p>}
                        <div className="text-xs font-medium text-muted-foreground">Delivery History</div>
                        {deliveries.length === 0 ? (
                          <p className="text-xs text-muted-foreground">No deliveries recorded</p>
                        ) : (
                          <div className="space-y-1">
                            {deliveries.map((d) => (
                              <div key={d.id} className="flex gap-4 text-xs">
                                <span className="font-mono">{d.channel}</span>
                                <span>{d.recipient}</span>
                                <Badge variant="outline" className="text-[10px]">{d.delivery_status}</Badge>
                                <span className="text-muted-foreground">{fmtTime(d.delivered_at)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={!!noteDialog} onOpenChange={() => { setNoteDialog(null); setNote(''); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{noteDialog?.type === 'ack' ? 'Acknowledge Alert' : 'Resolve Alert'}</DialogTitle>
          </DialogHeader>
          <textarea
            className="w-full rounded border border-cv-border bg-black/30 p-2 text-sm min-h-[80px]"
            placeholder={noteDialog?.type === 'ack' ? 'Note (optional)' : 'Resolution note (required)'}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => { setNoteDialog(null); setNote(''); }}>Cancel</Button>
            <Button onClick={doAction} disabled={noteDialog?.type === 'resolve' && !note.trim()}>
              {noteDialog?.type === 'ack' ? 'Acknowledge' : 'Resolve'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Audit Log Tab ──

function AuditLogTab() {
  const [entries, setEntries] = useState<any[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const limit = 50;

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setEntries(await api(`/api/operations/audit-log?limit=${limit}&offset=${offset}`)); }
    catch { /* ignore */ }
    finally { setLoading(false); }
  }, [offset]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="p-4 space-y-4">
      <div className="rounded-md border border-cv-border overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Timestamp</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>Target ID</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.length === 0 && (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">{loading ? 'Loading...' : 'No audit entries'}</TableCell></TableRow>
            )}
            {entries.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="text-xs whitespace-nowrap">{fmtTime(e.created_at)}</TableCell>
                <TableCell className="font-mono text-xs">{e.actor_id?.slice(0, 8)}...</TableCell>
                <TableCell>
                  <Badge variant="outline">{e.action}</Badge>
                </TableCell>
                <TableCell className="text-xs">{e.target_type}</TableCell>
                <TableCell className="font-mono text-xs">{e.target_id?.slice(0, 12)}...</TableCell>
                <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate">{e.reason ?? '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex justify-between">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <span className="text-xs text-muted-foreground self-center">
          Showing {offset + 1}–{offset + entries.length}
        </span>
        <Button variant="outline" size="sm" disabled={entries.length < limit} onClick={() => setOffset(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}

// ── Main View ──

export function OperationsView() {
  return (
    <div className="flex flex-col h-full overflow-auto custom-scrollbar bg-cv-bg">
      <div className="shrink-0 px-4 pt-5 pb-0 pl-14 md:pl-5">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-cv-cyan" />
          <h1 className="text-xl font-semibold">Workflow Operations</h1>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          Monitor workflows, manage alerts, and review audit history
        </p>
      </div>

      <Tabs defaultValue="overview" className="flex-1 flex flex-col px-4 pt-4">
        <TabsList className="bg-cv-card border border-cv-border w-fit">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="workflows">Workflows</TabsTrigger>
          <TabsTrigger value="alerts">Alerts</TabsTrigger>
          <TabsTrigger value="audit">Audit Log</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="flex-1"><OverviewTab /></TabsContent>
        <TabsContent value="workflows" className="flex-1"><WorkflowsTab /></TabsContent>
        <TabsContent value="alerts" className="flex-1"><AlertsTab /></TabsContent>
        <TabsContent value="audit" className="flex-1"><AuditLogTab /></TabsContent>
      </Tabs>
    </div>
  );
}
