import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Search,
  Download,
  AlertTriangle,
  Clock,
  CheckCircle2,
  Camera,
  MapPin,
  ChevronDown,
  Volume2,
  Eye,
  ChevronLeft,
  ChevronRight,
  X,
  Shield,
  XCircle,
  ArrowUpRight,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';
import { API_BASE } from '@/lib/api-base';
import { useCameras } from '@/hooks/useCameras';

type SeverityKey = 'critical' | 'warning' | 'info';
type StatusKey = 'active' | 'acknowledged' | 'resolved';

interface EventRow {
  id: string;
  cameraId: string;
  cameraName: string;
  zone: string;
  location: string;
  timestamp: Date;
  eventType: string;
  severity: SeverityKey;
  confidence: number;
  status: StatusKey;
  title: string;
  message: string;
  snapshotUrl?: string;
  ttsAudioUrl?: string;
  repeatCount?: number;
  dwellTime?: number;
  notes?: string;
}

const PAGE_SIZE = 25;

export function HistoryView() {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | StatusKey>('all');
  const [severityFilter, setSeverityFilter] = useState<'all' | SeverityKey>('all');
  const [zoneFilter, setZoneFilter] = useState('All zones');
  const [typeTab, setTypeTab] = useState<'all' | 'dumping_detected' | 'suspicious_activity' | 'repeat_offender'>('all');
  const [events, setEvents] = useState<EventRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { cameras } = useCameras();

  const zones = useMemo(() => {
    const set = new Set(cameras.map(c => c.zone));
    return ['All zones', ...Array.from(set).sort()];
  }, [cameras]);

  const fetchEvents = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (statusFilter !== 'all') params.set('status', statusFilter);
      const resp = await fetch(`${API_BASE}/api/alerts?${params}`);
      if (!resp.ok) return;
      const data = await resp.json();
      setEvents(data.map((a: any) => ({
        id: a.id,
        cameraId: a.camera_id || '',
        cameraName: a.title?.match(/at (cam-\d+)/i)?.[1] || a.camera_id?.slice(0, 8) || 'Unknown',
        zone: a.zone || '—',
        location: a.location || '',
        timestamp: new Date(a.timestamp),
        eventType: a.severity === 'critical' ? 'dumping_detected' : a.severity === 'warning' ? 'suspicious_activity' : 'suspicious_activity',
        severity: a.severity as SeverityKey,
        confidence: a.confidence ?? (a.severity === 'critical' ? 95 : 60),
        status: a.status as StatusKey,
        title: a.title || '',
        message: a.message || '',
        snapshotUrl: a.snapshot_url ? `${API_BASE}${a.snapshot_url}` : undefined,
        ttsAudioUrl: a.tts_audio_url,
        repeatCount: a.repeat_count,
        dwellTime: a.dwell_time_s,
        notes: a.notes,
      })));
    } catch { /* ignore */ }
    setLoading(false);
  }, [statusFilter]);

  useEffect(() => { fetchEvents(); const iv = setInterval(fetchEvents, 30_000); return () => clearInterval(iv); }, [fetchEvents]);

  const filtered = useMemo(() => events.filter(e => {
    if (severityFilter !== 'all' && e.severity !== severityFilter) return false;
    if (zoneFilter !== 'All zones' && e.zone !== zoneFilter) return false;
    if (typeTab !== 'all' && e.eventType !== typeTab) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return e.title.toLowerCase().includes(q) || e.cameraName.toLowerCase().includes(q) || e.zone.toLowerCase().includes(q) || e.message.toLowerCase().includes(q);
  }), [events, severityFilter, zoneFilter, typeTab, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selected = selectedId ? events.find(e => e.id === selectedId) ?? null : null;

  const typeCounts = useMemo(() => ({
    all: events.length,
    dumping_detected: events.filter(e => e.eventType === 'dumping_detected').length,
    suspicious_activity: events.filter(e => e.eventType === 'suspicious_activity').length,
    repeat_offender: events.filter(e => e.eventType === 'repeat_offender').length,
  }), [events]);

  const handleExportCsv = () => {
    const params = new URLSearchParams({ limit: '1000' });
    if (statusFilter !== 'all') params.set('status', statusFilter);
    window.open(`${API_BASE}/api/events/export/csv?${params}`, '_blank');
  };

  const handleReview = async (eventId: string, status: string, notes?: string) => {
    try {
      const body: Record<string, string> = { status };
      if (notes) body.notes = notes;
      const resp = await fetch(`${API_BASE}/api/alerts/${eventId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        setEvents(prev => prev.map(e => e.id === eventId ? { ...e, status: status as StatusKey, notes } : e));
      }
    } catch { /* ignore */ }
  };

  return (
    <div className="flex h-full overflow-hidden bg-cv-bg">
      <div className={cn('flex flex-col flex-1 min-w-0 transition-all', selected && 'sm:mr-[480px]')}>
        {/* Header */}
        <div className="px-4 sm:px-6 pt-4 sm:pt-5 pb-3">
          <div className="text-[11px] text-cv-muted mb-1">Event History</div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-lg font-semibold text-cv-fg">Event History</h1>
              <p className="text-[13px] text-cv-fg2 mt-0.5">Review detected dumping events, evidence, and follow-up actions</p>
            </div>
            <button onClick={handleExportCsv} className="flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium border border-cv-border text-cv-fg2 hover:bg-white/5 transition-all flex-shrink-0">
              <Download className="w-4 h-4" />
              Export CSV
            </button>
          </div>
        </div>

        {/* KPI Row */}
        <div className="px-4 sm:px-6 pb-4">
          <div className="grid grid-cols-4 gap-2 sm:gap-4">
            <KpiCard accent={CV_COLORS.cyan} label="Total events" value={String(events.length)} />
            <KpiCard accent={CV_COLORS.orange} label="Active" value={String(events.filter(e => e.status === 'active').length)} />
            <KpiCard accent={CV_COLORS.blue} label="Acknowledged" value={String(events.filter(e => e.status === 'acknowledged').length)} />
            <KpiCard accent={CV_COLORS.green} label="Resolved" value={String(events.filter(e => e.status === 'resolved').length)} />
          </div>
        </div>

        {/* Event Type Tabs */}
        <div className="px-4 sm:px-6 pb-3">
          <div className="flex items-center gap-1 overflow-x-auto">
            {([['all', 'All events'], ['dumping_detected', 'Dumping'], ['suspicious_activity', 'Suspicious'], ['repeat_offender', 'Repeat']] as const).map(([key, label]) => (
              <button key={key} onClick={() => { setTypeTab(key); setPage(0); }}
                className={cn('px-3 py-1.5 rounded-md text-sm whitespace-nowrap transition-colors',
                  typeTab === key ? 'bg-cv-cyan/10 text-cv-cyan border border-cv-cyan/30' : 'text-cv-fg2 hover:bg-white/5 border border-transparent'
                )}>
                {label} <span className="text-[11px] tabular-nums ml-1 opacity-70">{typeCounts[key]}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Filter Toolbar */}
        <div className="px-4 sm:px-6 pb-3">
          <div className="flex flex-row flex-wrap items-center gap-2 sm:gap-3">
            <FilterSelect value={statusFilter === 'all' ? 'All status' : statusFilter} options={['All status', 'active', 'acknowledged', 'resolved']} onChange={v => { setStatusFilter(v === 'All status' ? 'all' : v as StatusKey); setPage(0); }} />
            <FilterSelect value={severityFilter === 'all' ? 'All severity' : severityFilter} options={['All severity', 'critical', 'warning', 'info']} onChange={v => { setSeverityFilter(v === 'All severity' ? 'all' : v as SeverityKey); setPage(0); }} />
            <FilterSelect value={zoneFilter} options={zones} onChange={v => { setZoneFilter(v); setPage(0); }} />
            <div className="relative flex-1 min-w-[180px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-cv-muted" />
              <input type="text" placeholder="Search events, cameras, zones…" value={search} onChange={e => setSearch(e.target.value)}
                className="w-full h-[42px] pl-9 pr-3 rounded-md text-sm bg-cv-panel border border-cv-border text-cv-fg placeholder:text-cv-muted focus:outline-none focus:border-cv-cyan" />
            </div>
            <span className="text-xs text-cv-muted whitespace-nowrap self-center">{filtered.length} events</span>
          </div>
        </div>

        {/* Table / Cards */}
        <div className="flex-1 min-h-0 overflow-auto px-4 sm:px-6 pb-2">
          <div className="rounded-lg border border-cv-border bg-cv-panel">
            {loading ? (
              <div className="flex items-center justify-center h-40 text-sm text-cv-muted">Loading events…</div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-40 gap-2 text-cv-muted">
                <Clock className="w-10 h-10 opacity-30" />
                <p className="text-sm">{events.length === 0 ? 'No events recorded yet' : 'No events match these filters'}</p>
                {events.length > 0 && <button onClick={() => { setStatusFilter('all'); setSeverityFilter('all'); setZoneFilter('All zones'); setTypeTab('all'); setSearch(''); }} className="text-cv-cyan text-sm hover:underline">Clear filters</button>}
              </div>
            ) : (
              <>
                {/* Desktop Table */}
                <div className="hidden sm:block overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-cv-border text-[11px] uppercase text-cv-muted">
                        <th className="text-left py-3 px-4 font-medium">Time</th>
                        <th className="text-left py-3 px-4 font-medium">Snapshot</th>
                        <th className="text-left py-3 px-4 font-medium">Event</th>
                        <th className="text-left py-3 px-4 font-medium">Camera / Zone</th>
                        <th className="text-right py-3 px-4 font-medium">Confidence</th>
                        <th className="text-center py-3 px-4 font-medium">Status</th>
                        <th className="text-center py-3 px-4 font-medium">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paged.map(ev => (
                        <tr key={ev.id} onClick={() => setSelectedId(ev.id)} className={cn(
                          'border-b border-cv-border last:border-b-0 hover:bg-white/[0.02] transition-colors group cursor-pointer',
                          selectedId === ev.id && 'bg-white/[0.03] border-l-[3px] border-l-cv-cyan'
                        )}>
                          <td className="py-3 px-4 text-xs tabular-nums text-cv-fg2 whitespace-nowrap">{formatTime(ev.timestamp)}</td>
                          <td className="py-3 px-4">
                            {ev.snapshotUrl ? (
                              <img src={ev.snapshotUrl} alt="" className="w-[86px] h-12 object-cover rounded-md border border-cv-border" loading="lazy" />
                            ) : (
                              <div className="w-[86px] h-12 rounded-md border border-dashed border-cv-border flex items-center justify-center text-[9px] text-cv-muted">No frame</div>
                            )}
                          </td>
                          <td className="py-3 px-4">
                            <div className="flex items-center gap-2">
                              <SeverityIcon severity={ev.severity} />
                              <div>
                                <div className="font-medium text-cv-fg">{eventLabel(ev.eventType)}</div>
                                {ev.repeatCount && ev.repeatCount > 1 && <span className="text-[10px] text-[#FF6B6B]">×{ev.repeatCount}</span>}
                              </div>
                            </div>
                          </td>
                          <td className="py-3 px-4">
                            <div className="flex items-center gap-2">
                              <Camera className="w-3.5 h-3.5 text-cv-muted flex-shrink-0" />
                              <div>
                                <div className="text-cv-fg">{ev.cameraName}</div>
                                <div className="text-[11px] text-cv-muted flex items-center gap-1"><MapPin className="w-3 h-3" />{ev.zone}</div>
                              </div>
                            </div>
                          </td>
                          <td className="py-3 px-4 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <div className="h-1.5 w-12 rounded-full bg-cv-track overflow-hidden">
                                <div className={cn('h-full rounded-full', ev.confidence >= 90 ? 'bg-[#19E58C]' : ev.confidence >= 70 ? 'bg-[#FF9A2E]' : 'bg-cv-muted')} style={{ width: `${ev.confidence}%` }} />
                              </div>
                              <span className="text-xs tabular-nums text-cv-fg2">{ev.confidence}%</span>
                            </div>
                          </td>
                          <td className="py-3 px-4 text-center"><StatusChip status={ev.status} /></td>
                          <td className="py-3 px-4 text-center">
                            <button className="text-cv-cyan text-xs hover:underline">Open</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Mobile Cards */}
                <div className="sm:hidden divide-y divide-cv-border">
                  {paged.map(ev => (
                    <div key={ev.id} onClick={() => setSelectedId(ev.id)} className={cn('p-4 space-y-2 cursor-pointer', selectedId === ev.id && 'bg-white/[0.03] border-l-[3px] border-l-cv-cyan')}>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <SeverityIcon severity={ev.severity} />
                          <span className="font-medium text-cv-fg text-sm">{eventLabel(ev.eventType)}</span>
                          <StatusChip status={ev.status} />
                        </div>
                        <span className="text-[11px] tabular-nums text-cv-muted">{formatTime(ev.timestamp)}</span>
                      </div>
                      <div className="flex items-center gap-4 text-[12px] text-cv-fg2">
                        <span className="flex items-center gap-1"><Camera className="w-3 h-3" />{ev.cameraName}</span>
                        <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{ev.zone}</span>
                        <span className="tabular-nums">{ev.confidence}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Pagination Footer */}
        <div className="px-4 sm:px-6 py-3 border-t border-cv-border flex items-center justify-between text-[11px] text-cv-muted">
          <span>Page {page + 1} of {totalPages} · {filtered.length} events</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0} className="p-1 rounded hover:bg-white/5 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
            <button onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1} className="p-1 rounded hover:bg-white/5 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
          </div>
        </div>
      </div>

      {/* Detail Drawer */}
      {selected && (
        <EventDrawer event={selected} onClose={() => setSelectedId(null)} onReview={handleReview} />
      )}
    </div>
  );
}

/* ─── Detail Drawer ─── */

function EventDrawer({ event, onClose, onReview }: { event: EventRow; onClose: () => void; onReview: (id: string, status: string, notes?: string) => Promise<void> }) {
  const [reviewNotes, setReviewNotes] = useState('');
  const [acting, setActing] = useState(false);

  const doReview = async (status: string) => {
    setActing(true);
    await onReview(event.id, status, reviewNotes || undefined);
    setActing(false);
  };

  return (
    <div className="fixed top-0 right-0 w-full sm:w-[480px] h-full bg-cv-panel border-l border-cv-border z-40 flex flex-col overflow-hidden">
      {/* Drawer Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-cv-border">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-cv-fg truncate">{event.id.slice(0, 8)}…</span>
          <StatusChip status={event.status} />
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-white/5 text-cv-fg2"><X className="w-5 h-5" /></button>
      </div>

      {/* Drawer Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        {/* Event Facts */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <Fact label="Captured" value={formatTimeFull(event.timestamp)} />
          <Fact label="Event type" value={eventLabel(event.eventType)} />
          <Fact label="Severity" value={event.severity} />
          <Fact label="Confidence" value={`${event.confidence}%`} />
          <Fact label="Zone" value={event.zone} />
          <Fact label="Camera" value={event.cameraName} />
          {event.dwellTime != null && <Fact label="Dwell time" value={`${event.dwellTime.toFixed(1)}s`} />}
          {event.location && <Fact label="Location" value={event.location} />}
        </div>

        {/* Evidence */}
        <div>
          <h3 className="text-xs uppercase text-cv-muted font-medium mb-3">Evidence</h3>
          {event.snapshotUrl ? (
            <img src={event.snapshotUrl} alt="Evidence" className="w-full aspect-video object-cover rounded-lg border border-cv-border" />
          ) : (
            <div className="w-full aspect-video rounded-lg border border-dashed border-cv-border flex items-center justify-center text-sm text-cv-muted">No evidence captured</div>
          )}
          <div className="flex gap-2 mt-3">
            <button onClick={() => playTts(event)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs border border-cv-border text-cv-fg2 hover:bg-white/5">
              <Volume2 className="w-3.5 h-3.5" /> Play alert
            </button>
            {event.snapshotUrl && (
              <a href={event.snapshotUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs border border-cv-border text-cv-fg2 hover:bg-white/5">
                <Eye className="w-3.5 h-3.5" /> View full
              </a>
            )}
          </div>
        </div>

        {/* Event Description */}
        {(event.title || event.message) && (
          <div>
            <h3 className="text-xs uppercase text-cv-muted font-medium mb-2">Description</h3>
            {event.title && <p className="text-sm font-medium text-cv-fg mb-1">{event.title}</p>}
            {event.message && <p className="text-sm text-cv-fg2">{event.message}</p>}
          </div>
        )}

        {/* Notes */}
        {event.notes && (
          <div>
            <h3 className="text-xs uppercase text-cv-muted font-medium mb-2">Existing notes</h3>
            <p className="text-sm text-cv-fg2 bg-cv-bg rounded-md p-3 border border-cv-border">{event.notes}</p>
          </div>
        )}

        {/* Review Notes Input */}
        <div>
          <h3 className="text-xs uppercase text-cv-muted font-medium mb-2">Review notes</h3>
          <textarea
            value={reviewNotes} onChange={e => setReviewNotes(e.target.value)}
            placeholder="Add review notes (optional)…"
            rows={3}
            className="w-full px-3 py-2 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg placeholder:text-cv-muted focus:outline-none focus:border-cv-cyan resize-none"
          />
        </div>
      </div>

      {/* Review Actions — sticky bottom */}
      <div className="px-5 py-4 border-t border-cv-border flex items-center gap-2 bg-cv-panel">
        <button disabled={acting} onClick={() => doReview('resolved')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium bg-[#19E58C] text-[#07111F] hover:brightness-110 disabled:opacity-50">
          <CheckCircle2 className="w-4 h-4" /> Confirm
        </button>
        <button disabled={acting} onClick={() => doReview('acknowledged')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium border border-cv-border text-cv-fg2 hover:bg-white/5 disabled:opacity-50">
          <XCircle className="w-4 h-4" /> Dismiss
        </button>
        <button disabled={acting} onClick={() => doReview('active')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium border border-[#FF9A2E]/50 text-[#FF9A2E] hover:bg-[#FF9A2E]/10 disabled:opacity-50">
          <ArrowUpRight className="w-4 h-4" /> Escalate
        </button>
      </div>
    </div>
  );
}

/* ─── Helpers ─── */

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-cv-muted">{label}</p>
      <p className="text-sm text-cv-fg">{value}</p>
    </div>
  );
}

function KpiCard({ accent, label, value }: { accent: string; label: string; value: string }) {
  return (
    <div className="rounded-lg p-2 sm:p-4 bg-cv-panel border border-cv-border">
      <p className="text-[10px] sm:text-xs text-cv-fg2 mb-0.5 sm:mb-1 truncate">{label}</p>
      <p className="text-base sm:text-2xl font-bold tabular-nums text-cv-fg">{value}</p>
      <div className="mt-1 sm:mt-2 h-0.5 w-6 sm:w-8 rounded-full" style={{ backgroundColor: accent }} />
    </div>
  );
}

function StatusChip({ status }: { status: StatusKey }) {
  const styles: Record<StatusKey, string> = {
    active: 'bg-[#FF6B6B]/15 text-[#FF6B6B]',
    acknowledged: 'bg-[#FF9A2E]/15 text-[#FF9A2E]',
    resolved: 'bg-[#19E58C]/15 text-[#19E58C]',
  };
  const icons: Record<StatusKey, typeof AlertTriangle> = { active: AlertTriangle, acknowledged: Clock, resolved: CheckCircle2 };
  const Icon = icons[status];
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium', styles[status])}>
      <Icon className="w-3 h-3" />{status}
    </span>
  );
}

function SeverityIcon({ severity }: { severity: SeverityKey }) {
  const colors: Record<SeverityKey, string> = { critical: 'text-[#FF6B6B]', warning: 'text-[#FF9A2E]', info: 'text-[#3B9BFF]' };
  return <AlertTriangle className={cn('w-4 h-4', colors[severity])} />;
}

function FilterSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)}
        className="h-[42px] px-3 pr-8 rounded-md text-sm bg-cv-panel border border-cv-border text-cv-fg cursor-pointer focus:outline-none focus:border-cv-cyan text-left whitespace-nowrap">
        {value}
      </button>
      <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-cv-muted pointer-events-none" />
      {open && (
        <div className="absolute z-[9999] top-full left-0 mt-1 min-w-full bg-cv-panel border border-cv-border rounded-md shadow-lg py-1 max-h-48 overflow-y-auto">
          {options.map(o => (
            <button key={o} type="button" onClick={() => { onChange(o); setOpen(false); }} className={cn('w-full text-left px-3 py-2 text-sm hover:bg-white/5 whitespace-nowrap', o === value && 'text-cv-cyan')}>
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTime(d: Date) {
  return new Intl.DateTimeFormat('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

function formatTimeFull(d: Date) {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZoneName: 'short' }).format(d);
}

function eventLabel(t: string) {
  if (t === 'dumping_detected') return 'Illegal Dumping';
  if (t === 'repeat_offender') return 'Repeat Offender';
  return 'Suspicious Activity';
}

function playTts(ev: EventRow) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(`WARNING. ${ev.title}. ${ev.message}. THIS AREA IS UNDER SURVEILLANCE.`);
  u.rate = 1.1; u.pitch = 0.9; u.volume = 1.0;
  speechSynthesis.speak(u);
}
