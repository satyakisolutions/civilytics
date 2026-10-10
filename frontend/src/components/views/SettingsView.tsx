import { useState, useRef, useEffect } from 'react';
import {
  Settings as SettingsIcon, Users, Camera, HardDrive, Bell, Workflow, FileText,
  Check, ChevronRight, X, Plus, MoreVertical, Download, Eye, Clock,
  ArrowRight, AlertTriangle, RefreshCw, MessageCircle, Phone, Volume2, Mail, Database,
} from 'lucide-react';
import { CV_COLORS } from '@/lib/cv-colors';
import { cn } from '@/lib/utils';
import { API_BASE } from '@/lib/api-base';
import { useMonitoring } from '@/context/MonitoringContext';

type Section = 'general' | 'users' | 'cameras' | 'evidence' | 'notifications' | 'workflows' | 'audit';

const SECTIONS: { id: Section; label: string; icon: typeof SettingsIcon }[] = [
  { id: 'general', label: 'General', icon: SettingsIcon },
  { id: 'users', label: 'Users & roles', icon: Users },
  { id: 'cameras', label: 'Cameras & detection', icon: Camera },
  { id: 'evidence', label: 'Evidence & retention', icon: HardDrive },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'workflows', label: 'Workflow operations', icon: Workflow },
  { id: 'audit', label: 'Audit log', icon: FileText },
];

export function SettingsView() {
  const [section, setSection] = useState<Section>('general');
  const [toast, setToast] = useState('');
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3500); };

  return (
    <div className="flex flex-col h-full" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <p className="text-[11px] mb-1" style={{ color: CV_COLORS.muted }}>Bengaluru South / Settings</p>
        <h1 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>Settings</h1>
        <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>Manage workspace configuration, users, retention, alerts, and worker operations.</p>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left nav */}
        <nav className="hidden md:flex flex-col w-[224px] flex-shrink-0 border-r py-2 overflow-y-auto" style={{ borderColor: CV_COLORS.border }}>
          {SECTIONS.map(s => {
            const Icon = s.icon;
            const active = section === s.id;
            return (
              <button key={s.id} onClick={() => setSection(s.id)}
                className={cn('flex items-center gap-3 px-4 py-2.5 text-[13px] font-medium relative transition-colors',
                  active ? '' : 'hover:bg-white/5')}
                style={{ color: active ? CV_COLORS.cyan : CV_COLORS.fg2 }}>
                {active && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-sm" style={{ background: CV_COLORS.cyan }} />}
                <Icon className="w-4 h-4" />
                {s.label}
              </button>
            );
          })}
        </nav>
        {/* Mobile nav */}
        <div className="md:hidden flex overflow-x-auto border-b px-2 py-1 gap-1 flex-shrink-0" style={{ borderColor: CV_COLORS.border }}>
          {SECTIONS.map(s => (
            <button key={s.id} onClick={() => setSection(s.id)}
              className="px-3 py-1.5 rounded text-[11px] font-medium whitespace-nowrap"
              style={{ color: section === s.id ? CV_COLORS.cyan : CV_COLORS.fg2, background: section === s.id ? `${CV_COLORS.cyan}15` : 'transparent' }}>
              {s.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-8">
          {section === 'general' && <GeneralSection onSave={() => showToast('Settings saved')} />}
          {section === 'users' && <UsersSection onToast={showToast} />}
          {section === 'cameras' && <CamerasSection onSave={() => showToast('Detection settings saved')} />}
          {section === 'evidence' && <EvidenceSection onSave={() => showToast('Retention policy saved')} />}
          {section === 'notifications' && <NotificationsSection onSave={() => showToast('Notification rules saved')} />}
          {section === 'workflows' && <WorkflowsSection />}
          {section === 'audit' && <AuditSection />}
        </div>
      </div>

      {toast && (
        <div role="status" aria-live="polite" className="fixed bottom-6 right-6 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg animate-in fade-in slide-in-from-bottom-2"
          style={{ background: CV_COLORS.green, color: CV_COLORS.bg }}>
          <Check className="w-4 h-4 inline mr-1.5 -mt-0.5" />{toast}
        </div>
      )}
    </div>
  );
}

/* ── Shared ── */
function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return <div className="mb-6"><h2 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{title}</h2><p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>{subtitle}</p></div>;
}
function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('rounded-[10px] p-5 mb-5', className)} style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>{children}</div>;
}
function Field({ label, children, htmlFor }: { label: string; children: React.ReactNode; htmlFor?: string }) {
  return <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 py-2"><label htmlFor={htmlFor} className="text-xs font-medium" style={{ color: CV_COLORS.fg2 }}>{label}</label>{children}</div>;
}
function Inp({ value, onChange, className, id }: { value: string; onChange: (v: string) => void; className?: string; id?: string }) {
  return <input id={id} value={value} onChange={e => onChange(e.target.value)} className={cn('px-3 py-1.5 rounded-md text-xs outline-none focus:ring-1 focus:ring-[#12D8F2]', className)} style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />;
}
function Sel({ value, options, onChange, id }: { value: string; options: string[]; onChange: (v: string) => void; id?: string }) {
  return <select id={id} value={value} onChange={e => onChange(e.target.value)} className="px-3 py-1.5 rounded-md text-xs outline-none appearance-none cursor-pointer focus:ring-1 focus:ring-[#12D8F2]" style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }}>
    {options.map(o => <option key={o} value={o}>{o}</option>)}
  </select>;
}
function Toggle({ on, onToggle, label }: { on: boolean; onToggle: () => void; label?: string }) {
  return <button onClick={onToggle} role="switch" aria-checked={on} aria-label={label}
    className="w-9 h-5 rounded-full relative transition-colors cursor-pointer p-2 -m-2"
    style={{ background: on ? CV_COLORS.cyan : CV_COLORS.border }}>
    <div className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform" style={{ left: on ? 18 : 2 }} />
  </button>;
}
function Chip({ label, color }: { label: string; color: string }) {
  return <span className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase" style={{ background: `${color}22`, color }}>{label}</span>;
}
function Btn({ children, primary, danger, onClick, disabled }: { children: React.ReactNode; primary?: boolean; danger?: boolean; onClick?: () => void; disabled?: boolean }) {
  const bg = danger ? '#ef4444' : primary ? CV_COLORS.cyan : 'transparent';
  const fg = primary || danger ? CV_COLORS.bg : CV_COLORS.fg2;
  const border = primary || danger ? bg : CV_COLORS.border;
  return <button onClick={onClick} disabled={disabled} className="px-4 py-1.5 rounded-md text-xs font-medium transition-all hover:brightness-110 disabled:opacity-40" style={{ background: bg, color: fg, border: `1px solid ${border}` }}>{children}</button>;
}

/* ── 1. General ── */
function GeneralSection({ onSave }: { onSave: () => void }) {
  const [ws, setWs] = useState('Bengaluru South');
  const [org, setOrg] = useState("Let's Be The Change");
  const [tz, setTz] = useState('IST (UTC+05:30)');
  const [df, setDf] = useState('DD MMM YYYY');
  const [lang, setLang] = useState('English');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/config/workspace_settings`).then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.value) return;
        const v = data.value;
        if (v.workspace_name) setWs(v.workspace_name);
        if (v.org_name) setOrg(v.org_name);
        if (v.timezone) setTz(v.timezone);
        if (v.date_format) setDf(v.date_format);
        if (v.language) setLang(v.language);
      })
      .catch(() => {});
  }, []);

  const mark = (fn: (v: string) => void) => (v: string) => { fn(v); setDirty(true); };

  const save = async () => {
    setSaving(true);
    try {
      await fetch(`${API_BASE}/api/config/workspace_settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: { workspace_name: ws, org_name: org, timezone: tz, date_format: df, language: lang } }),
      });
      setDirty(false);
      onSave();
    } finally { setSaving(false); }
  };

  return <>
    <SectionTitle title="Workspace details" subtitle="Organisation and operating defaults." />
    <Panel>
      <Field label="Workspace name"><Inp value={ws} onChange={mark(setWs)} /></Field>
      <Field label="Organisation name"><Inp value={org} onChange={mark(setOrg)} /></Field>
      <Field label="Time zone"><Sel value={tz} options={['IST (UTC+05:30)']} onChange={mark(setTz)} /></Field>
      <Field label="Default date format"><Sel value={df} options={['DD MMM YYYY', 'YYYY-MM-DD', 'MM/DD/YYYY']} onChange={mark(setDf)} /></Field>
      <Field label="Default evidence language"><Sel value={lang} options={['English', 'Kannada', 'Hindi']} onChange={mark(setLang)} /></Field>
      <div className="flex justify-end gap-2 mt-4">
        <Btn onClick={() => setDirty(false)}>Cancel</Btn>
        <Btn primary disabled={!dirty || saving} onClick={save}>{saving ? 'Saving…' : 'Save changes'}</Btn>
      </div>
    </Panel>
  </>;
}

/* ── 2. Users & Roles ── */
type UserRow = { name: string; email: string; role: string; last_active: string; status: string };

function UsersSection({ onToast }: { onToast: (m: string) => void }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [invName, setInvName] = useState('');
  const [invEmail, setInvEmail] = useState('');
  const [invRole, setInvRole] = useState('Viewer');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/config/workspace_users`).then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.value && Array.isArray(data.value)) setUsers(data.value); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const saveUsers = async (updated: UserRow[]) => {
    setSaving(true);
    try {
      await fetch(`${API_BASE}/api/config/workspace_users`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: updated }),
      });
      setUsers(updated);
    } finally { setSaving(false); }
  };

  const handleInvite = async () => {
    if (!invName.trim() || !invEmail.trim()) return;
    const newUser: UserRow = { name: invName.trim(), email: invEmail.trim(), role: invRole, last_active: 'Never', status: 'Invitation pending' };
    await saveUsers([...users, newUser]);
    setShowInvite(false);
    setInvName(''); setInvEmail(''); setInvRole('Viewer');
    onToast('Invitation sent');
  };

  return <>
    <SectionTitle title="Users & roles" subtitle="Control who can review events and change configuration." />
    <Panel>
      <div className="flex justify-between items-center mb-4">
        <span className="text-xs font-medium" style={{ color: CV_COLORS.fg2 }}>{loading ? '…' : `${users.length} users`}</span>
        <Btn primary onClick={() => setShowInvite(true)}><Plus className="w-3 h-3 inline mr-1" />Invite user</Btn>
      </div>
      {loading ? (
        <p className="text-xs py-4 text-center" style={{ color: CV_COLORS.muted }}>Loading…</p>
      ) : users.length === 0 ? (
        <p className="text-xs py-4 text-center" style={{ color: CV_COLORS.muted }}>No users configured. Invite your first team member.</p>
      ) : <>
        {/* Desktop table */}
        <div className="hidden sm:block overflow-x-auto">
          <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
            <thead><tr style={{ color: CV_COLORS.muted }} className="border-b" >
              {['Name', 'Email', 'Role', 'Last active', 'Status'].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {users.map(u => (
                <tr key={u.email} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                  <td className="py-2.5 pr-3 font-medium">{u.name}</td>
                  <td className="pr-3" style={{ color: CV_COLORS.fg2 }}>{u.email}</td>
                  <td className="pr-3"><Chip label={u.role} color={CV_COLORS.fg2} /></td>
                  <td className="pr-3" style={{ color: CV_COLORS.muted }}>{u.last_active}</td>
                  <td><Chip label={u.status} color={u.status === 'Active' ? CV_COLORS.green : CV_COLORS.orange} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* Mobile cards */}
        <div className="sm:hidden space-y-2">
          {users.map(u => (
            <div key={u.email} className="rounded-lg p-3" style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}` }}>
              <div className="flex justify-between"><span className="text-xs font-medium" style={{ color: CV_COLORS.fg }}>{u.name}</span><Chip label={u.status} color={u.status === 'Active' ? CV_COLORS.green : CV_COLORS.orange} /></div>
              <p className="text-[11px] mt-1" style={{ color: CV_COLORS.fg2 }}>{u.email}</p>
              <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>{u.role} · {u.last_active}</p>
            </div>
          ))}
        </div>
      </>}
    </Panel>
    {showInvite && <Modal title="Invite user" onClose={() => setShowInvite(false)}>
      <Field label="Name"><Inp value={invName} onChange={setInvName} /></Field>
      <Field label="Email"><Inp value={invEmail} onChange={setInvEmail} /></Field>
      <Field label="Role"><Sel value={invRole} options={['Organisation admin', 'Reviewer', 'Viewer']} onChange={setInvRole} /></Field>
      <div className="flex justify-end gap-2 mt-4">
        <Btn onClick={() => setShowInvite(false)}>Cancel</Btn>
        <Btn primary disabled={saving || !invName.trim() || !invEmail.trim()} onClick={handleInvite}>{saving ? 'Saving…' : 'Send invitation'}</Btn>
      </div>
    </Modal>}
  </>;
}

/* ── 3. Cameras & Detection ── */
function CamerasSection({ onSave }: { onSave: () => void }) {
  const { setCurrentView } = useMonitoring();
  const [personDump, setPersonDump] = useState(true);
  const [vehicleDump, setVehicleDump] = useState(true);
  const [personMatch, setPersonMatch] = useState(true);
  const [vehiclePlate, setVehiclePlate] = useState(true);
  const [autoMark, setAutoMark] = useState(false);
  const [reqConfirm, setReqConfirm] = useState(true);
  const [escalate, setEscalate] = useState(true);
  return <>
    <SectionTitle title="Cameras & detection" subtitle="Control detection windows and review rules for this workspace." />
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Detection windows</h3>
      <Field label="Midnight window"><span className="text-xs" style={{ color: CV_COLORS.fg }}>12:00 AM – 5:00 AM IST</span></Field>
      <Field label="Daytime window"><span className="text-xs" style={{ color: CV_COLORS.fg }}>5:00 AM – 9:00 AM IST</span></Field>
    </Panel>
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Detection types</h3>
      <Field label="Person dumping"><Toggle on={personDump} onToggle={() => setPersonDump(!personDump)} /></Field>
      <Field label="Vehicle dumping"><Toggle on={vehicleDump} onToggle={() => setVehicleDump(!vehicleDump)} /></Field>
      <Field label="Person visual match groups"><Toggle on={personMatch} onToggle={() => setPersonMatch(!personMatch)} /></Field>
      <Field label="Vehicle plate evidence"><Toggle on={vehiclePlate} onToggle={() => setVehiclePlate(!vehiclePlate)} /></Field>
    </Panel>
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Review rules</h3>
      <Field label="Auto-mark confidence ≥ 95%"><Toggle on={autoMark} onToggle={() => setAutoMark(!autoMark)} /></Field>
      {autoMark && <p className="text-[10px] px-1 py-1 rounded" style={{ color: CV_COLORS.orange, background: `${CV_COLORS.orange}15` }}>Warning: auto-confirmation bypasses reviewer oversight.</p>}
      <Field label="Require reviewer confirmation"><Toggle on={reqConfirm} onToggle={() => setReqConfirm(!reqConfirm)} /></Field>
      <Field label="Create escalation for high priority events"><Toggle on={escalate} onToggle={() => setEscalate(!escalate)} /></Field>
      <div className="flex justify-between items-center mt-4">
        <button onClick={() => setCurrentView('cameras')} className="text-xs font-medium" style={{ color: CV_COLORS.cyan }}>Manage cameras →</button>
        <Btn primary onClick={onSave}>Save changes</Btn>
      </div>
    </Panel>

    {/* Per-camera thresholds */}
    <Panel>
      <h3 className="text-xs font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Per-camera detection thresholds</h3>
      <p className="text-[10px] mb-3" style={{ color: CV_COLORS.muted }}>Override workspace defaults for individual cameras. Cameras not listed use workspace settings above.</p>
      <CameraThresholds />
    </Panel>
  </>;
}

interface CameraDetectionConfig {
  dwell_violation_threshold_s: number;
  waste_proximity_alert_window_s: number;
  waste_proximity_px: number;
}
interface CameraRow {
  id: string; name: string; zone_name?: string;
  detection_config: CameraDetectionConfig;
}

function CameraThresholds() {
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<CameraDetectionConfig>({ dwell_violation_threshold_s: 15, waste_proximity_alert_window_s: 300, waste_proximity_px: 120 });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/cameras`).then(r => r.ok ? r.json() : [])
      .then((data: any[]) => setCameras(data.map(c => ({
        id: c.id, name: c.name, zone_name: c.zone_name,
        detection_config: c.detection_config || { dwell_violation_threshold_s: 15, waste_proximity_alert_window_s: 300, waste_proximity_px: 120 },
      }))))
      .catch(() => {});
  }, []);

  const openEdit = (cam: CameraRow) => { setEditing(cam.id); setForm({ ...cam.detection_config }); };
  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await fetch(`${API_BASE}/api/cameras/${editing}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detection_config: form }),
      });
      setCameras(cameras.map(c => c.id === editing ? { ...c, detection_config: form } : c));
      setEditing(null);
    } finally { setSaving(false); }
  };

  return <>
    <div className="overflow-x-auto">
      <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
        <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
          {['Camera', 'Zone', 'Dwell threshold (s)', 'Proximity window (s)', 'Proximity (px)', ''].map(h =>
            <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>
          )}
        </tr></thead>
        <tbody>
          {cameras.map(c => (
            <tr key={c.id} className="border-b" style={{ borderColor: CV_COLORS.border }}>
              <td className="py-2 pr-3 font-medium">{c.name}</td>
              <td className="pr-3" style={{ color: CV_COLORS.fg2 }}>{c.zone_name || '—'}</td>
              <td className="pr-3 font-mono">{c.detection_config.dwell_violation_threshold_s}s</td>
              <td className="pr-3 font-mono">{c.detection_config.waste_proximity_alert_window_s}s</td>
              <td className="pr-3 font-mono">{c.detection_config.waste_proximity_px}px</td>
              <td><button onClick={() => openEdit(c)} className="text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>Edit</button></td>
            </tr>
          ))}
          {cameras.length === 0 && <tr><td colSpan={6} className="py-4 text-center" style={{ color: CV_COLORS.muted }}>No cameras configured</td></tr>}
        </tbody>
      </table>
    </div>

    {editing && (
      <Modal title={`Thresholds — ${cameras.find(c => c.id === editing)?.name}`} onClose={() => setEditing(null)}>
        <div className="space-y-3">
          <div>
            <label className="text-[11px] font-medium block mb-1" style={{ color: CV_COLORS.fg2 }}>Dwell violation threshold (seconds)</label>
            <input type="number" min={1} max={300} value={form.dwell_violation_threshold_s}
              onChange={e => setForm({ ...form, dwell_violation_threshold_s: Number(e.target.value) })}
              className="w-full px-3 py-1.5 rounded-md text-xs outline-none"
              style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />
            <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>How long a person/vehicle must dwell before triggering a violation.</p>
          </div>
          <div>
            <label className="text-[11px] font-medium block mb-1" style={{ color: CV_COLORS.fg2 }}>Waste proximity alert window (seconds)</label>
            <input type="number" min={10} max={3600} value={form.waste_proximity_alert_window_s}
              onChange={e => setForm({ ...form, waste_proximity_alert_window_s: Number(e.target.value) })}
              className="w-full px-3 py-1.5 rounded-md text-xs outline-none"
              style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />
            <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>Time window for detecting waste proximity to a person/vehicle.</p>
          </div>
          <div>
            <label className="text-[11px] font-medium block mb-1" style={{ color: CV_COLORS.fg2 }}>Waste proximity distance (pixels)</label>
            <input type="number" min={10} max={1000} value={form.waste_proximity_px}
              onChange={e => setForm({ ...form, waste_proximity_px: Number(e.target.value) })}
              className="w-full px-3 py-1.5 rounded-md text-xs outline-none"
              style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />
            <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>Max pixel distance between waste and subject for attribution.</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <Btn onClick={() => setEditing(null)}>Cancel</Btn>
          <Btn primary disabled={saving} onClick={saveEdit}>{saving ? 'Saving…' : 'Save thresholds'}</Btn>
        </div>
      </Modal>
    )}
  </>;
}

/* ── 4. Evidence & Retention ── */
function EvidenceSection({ onSave }: { onSave: () => void }) {
  const [meta, setMeta] = useState('12 months');
  const [frames, setFrames] = useState('30 days');
  const [clips, setClips] = useState('30 days');
  const [storageGb, setStorageGb] = useState(6.8);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/config/retention_settings`).then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.value) return;
        const v = data.value;
        if (v.metadata_retention) setMeta(v.metadata_retention);
        if (v.frame_retention) setFrames(v.frame_retention);
        if (v.clip_retention) setClips(v.clip_retention);
        if (v.storage_used_gb != null) setStorageGb(v.storage_used_gb);
      })
      .catch(() => {});
  }, []);

  const mark = (fn: (v: string) => void) => (v: string) => { fn(v); setDirty(true); };

  const save = async () => {
    setSaving(true);
    try {
      await fetch(`${API_BASE}/api/config/retention_settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: { metadata_retention: meta, frame_retention: frames, clip_retention: clips, storage_used_gb: storageGb } }),
      });
      setDirty(false);
      onSave();
    } finally { setSaving(false); }
  };

  return <>
    <SectionTitle title="Evidence & retention" subtitle="Set how long Civilytics keeps event records and evidence." />
    <Panel>
      <Field label="Event metadata retention"><Sel value={meta} options={['6 months', '12 months', '18 months', '24 months']} onChange={mark(setMeta)} /></Field>
      <Field label="Evidence frame retention"><Sel value={frames} options={['7 days', '30 days', '60 days', '90 days']} onChange={mark(setFrames)} /></Field>
      <Field label="Recorded clip retention"><Sel value={clips} options={['7 days', '30 days', '60 days', '90 days']} onChange={mark(setClips)} /></Field>
      <Field label="Evidence download access"><span className="text-xs" style={{ color: CV_COLORS.fg }}>Reviewers and administrators</span></Field>
    </Panel>
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Storage usage</h3>
      <Field label="Current evidence storage"><span className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>{storageGb} GB</span></Field>
      <Field label="Included storage window"><span className="text-xs" style={{ color: CV_COLORS.fg }}>30 days</span></Field>
      <Field label="Storage warning"><Chip label="No warning" color={CV_COLORS.green} /></Field>
      <div className="flex justify-end mt-4"><Btn primary disabled={!dirty || saving} onClick={save}>{saving ? 'Saving…' : 'Save retention policy'}</Btn></div>
    </Panel>
  </>;
}

/* ── 5. Notifications ── */
const NOTIF_RULES = [
  { label: 'High-priority dumping event', email: true, inApp: true },
  { label: 'Camera offline for more than 10 minutes', email: true, inApp: true },
  { label: 'Stream warning for more than 5 minutes', email: false, inApp: true },
  { label: 'Workflow task failure', email: true, inApp: true },
  { label: 'Evidence storage nearing limit', email: true, inApp: true },
  { label: 'Daily operational summary', email: true, inApp: false },
];

type ChannelId = 'email' | 'whatsapp' | 'telegram' | 'ip_speaker' | 'sip_phone' | 'storage_drive';
interface ChannelConfig {
  id: ChannelId; label: string; icon: typeof Mail; enabled: boolean;
  fields: { key: string; label: string; type?: string; placeholder: string }[];
}

const DELIVERY_CHANNELS: ChannelConfig[] = [
  { id: 'email', label: 'Email (SMTP)', icon: Mail, enabled: true, fields: [
    { key: 'smtp_host', label: 'SMTP host', placeholder: 'smtp.gmail.com' },
    { key: 'smtp_port', label: 'Port', type: 'number', placeholder: '587' },
    { key: 'smtp_user', label: 'Username', placeholder: 'alerts@example.com' },
    { key: 'smtp_password', label: 'Password', type: 'password', placeholder: '••••••••' },
    { key: 'smtp_from', label: 'From address', placeholder: 'civilytics@satyaki.co.in' },
  ]},
  { id: 'whatsapp', label: 'WhatsApp (optional)', icon: MessageCircle, enabled: false, fields: [
    { key: 'wa_api_url', label: 'API URL', placeholder: 'https://graph.facebook.com/v18.0/...' },
    { key: 'wa_phone_id', label: 'Phone number ID', placeholder: '123456789' },
    { key: 'wa_token', label: 'Access token', type: 'password', placeholder: 'Bearer token' },
    { key: 'wa_recipients', label: 'Recipient numbers (comma-separated)', placeholder: '+91XXXXXXXXXX, +91XXXXXXXXXX' },
  ]},
  { id: 'telegram', label: 'Telegram (optional)', icon: MessageCircle, enabled: false, fields: [
    { key: 'tg_bot_token', label: 'Bot token', type: 'password', placeholder: '123456:ABC-DEF...' },
    { key: 'tg_chat_ids', label: 'Chat IDs (comma-separated)', placeholder: '-100123456789, -100987654321' },
  ]},
  { id: 'ip_speaker', label: 'IP Speaker', icon: Volume2, enabled: false, fields: [
    { key: 'speaker_host', label: 'Speaker IP / hostname', placeholder: '192.168.1.50' },
    { key: 'speaker_port', label: 'Port', type: 'number', placeholder: '8080' },
    { key: 'speaker_protocol', label: 'Protocol', placeholder: 'http' },
    { key: 'speaker_zone', label: 'Zone assignment', placeholder: 'Zone A' },
  ]},
  { id: 'sip_phone', label: 'SIP / Phone call', icon: Phone, enabled: false, fields: [
    { key: 'sip_server', label: 'SIP server', placeholder: 'sip.example.com' },
    { key: 'sip_username', label: 'Username', placeholder: 'civilytics' },
    { key: 'sip_password', label: 'Password', type: 'password', placeholder: '••••••••' },
    { key: 'sip_contacts', label: 'Escalation numbers (comma-separated)', placeholder: '+91XXXXXXXXXX' },
  ]},
  { id: 'storage_drive', label: 'Storage / Drive', icon: Database, enabled: false, fields: [
    { key: 'drive_type', label: 'Storage type', placeholder: 'Google Drive / S3 / NFS / Local' },
    { key: 'drive_endpoint', label: 'Endpoint / mount path', placeholder: 's3://bucket-name or /mnt/evidence' },
    { key: 'drive_access_key', label: 'Access key / service account', type: 'password', placeholder: 'AKIA... or service-account.json' },
    { key: 'drive_secret_key', label: 'Secret key', type: 'password', placeholder: '••••••••' },
    { key: 'drive_region', label: 'Region (cloud only)', placeholder: 'ap-south-1' },
    { key: 'drive_retention_days', label: 'Evidence retention (days)', type: 'number', placeholder: '30' },
  ]},
];

function NotificationsSection({ onSave }: { onSave: () => void }) {
  const [rules, setRules] = useState(NOTIF_RULES);
  const [channels, setChannels] = useState(DELIVERY_CHANNELS);
  const [configuring, setConfiguring] = useState<ChannelConfig | null>(null);
  const [channelForm, setChannelForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/config/delivery_channels`).then(r => {
      if (!r.ok) return;
      return r.json();
    }).then(data => {
      if (!data?.value) return;
      const saved = data.value as Record<string, { enabled: boolean; config?: Record<string, string> }>;
      setChannels(prev => prev.map(ch => {
        const s = saved[ch.id];
        return s ? { ...ch, enabled: !!s.enabled } : ch;
      }));
    }).catch(() => {});
    fetch(`${API_BASE}/api/config/notification_rules`).then(r => {
      if (!r.ok) return;
      return r.json();
    }).then(data => {
      if (!data?.value || !Array.isArray(data.value)) return;
      setRules(data.value);
    }).catch(() => {});
  }, []);

  const persistChannels = async (next: typeof channels) => {
    const payload: Record<string, { enabled: boolean; config?: Record<string, string> }> = {};
    next.forEach(ch => { payload[ch.id] = { enabled: ch.enabled }; });
    await fetch(`${API_BASE}/api/config/delivery_channels`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: payload }),
    });
  };

  const toggle = (i: number, field: 'email' | 'inApp') => {
    const next = [...rules];
    next[i] = { ...next[i], [field]: !next[i][field] };
    setRules(next);
  };
  const toggleChannel = (id: ChannelId) => {
    const ch = channels.find(c => c.id === id)!;
    if (!ch.enabled) {
      setConfiguring(ch);
      setChannelForm({});
    } else {
      const next = channels.map(c => c.id === id ? { ...c, enabled: false } : c);
      setChannels(next);
      persistChannels(next);
    }
  };
  const saveChannelConfig = async () => {
    if (!configuring) return;
    setSaving(true);
    // Save channel-specific config (excluding passwords from local state)
    await fetch(`${API_BASE}/api/config/delivery_channel_${configuring.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: channelForm }),
    });
    const next = channels.map(c => c.id === configuring.id ? { ...c, enabled: true } : c);
    setChannels(next);
    await persistChannels(next);
    setSaving(false);
    setConfiguring(null);
  };
  const saveRules = async () => {
    setSaving(true);
    await fetch(`${API_BASE}/api/config/notification_rules`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: rules }),
    });
    setSaving(false);
    onSave();
  };

  return <>
    <SectionTitle title="Notifications" subtitle="Choose operational alerts and delivery channels for this workspace." />

    {/* Delivery channels */}
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Delivery channels</h3>
      <p className="text-[10px] mb-3" style={{ color: CV_COLORS.muted }}>Enable a channel and configure its connection details. Alerts are sent to all enabled channels.</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {channels.map(ch => {
          const Icon = ch.icon;
          return (
            <div key={ch.id} className="flex items-center justify-between rounded-lg p-3"
              style={{ background: CV_COLORS.bg, border: `1px solid ${ch.enabled ? CV_COLORS.cyan + '44' : CV_COLORS.border}` }}>
              <div className="flex items-center gap-2.5">
                <Icon className="w-4 h-4" style={{ color: ch.enabled ? CV_COLORS.cyan : CV_COLORS.muted }} />
                <div>
                  <p className="text-xs font-medium" style={{ color: CV_COLORS.fg }}>{ch.label}</p>
                  <p className="text-[10px]" style={{ color: ch.enabled ? CV_COLORS.green : CV_COLORS.muted }}>
                    {ch.enabled ? 'Connected' : 'Not configured'}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {ch.enabled && (
                  <button onClick={() => {
                    setConfiguring(ch);
                    fetch(`${API_BASE}/api/config/delivery_channel_${ch.id}`).then(r => r.ok ? r.json() : null)
                      .then(d => setChannelForm(d?.value || {})).catch(() => setChannelForm({}));
                  }} className="text-[10px] font-medium" style={{ color: CV_COLORS.fg2 }}>Edit</button>
                )}
                <Toggle on={ch.enabled} onToggle={() => toggleChannel(ch.id)} />
              </div>
            </div>
          );
        })}
      </div>
    </Panel>

    {/* Notification rules */}
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Notification rules</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
            <th className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>Rule</th>
            <th className="text-center py-2 px-3 font-medium" style={{ borderColor: CV_COLORS.border }}>Email</th>
            <th className="text-center py-2 px-3 font-medium" style={{ borderColor: CV_COLORS.border }}>In-app</th>
          </tr></thead>
          <tbody>
            {rules.map((r, i) => (
              <tr key={i} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2.5 pr-3">{r.label}</td>
                <td className="text-center"><Toggle on={r.email} onToggle={() => toggle(i, 'email')} /></td>
                <td className="text-center"><Toggle on={r.inApp} onToggle={() => toggle(i, 'inApp')} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 pt-3 border-t" style={{ borderColor: CV_COLORS.border }}>
        <p className="text-[11px] mb-1" style={{ color: CV_COLORS.muted }}>Notification recipients</p>
        <p className="text-xs" style={{ color: CV_COLORS.fg2 }}>Sandesh C, Operations Review Desk</p>
      </div>
      <div className="flex justify-end mt-4"><Btn primary onClick={saveRules}>{saving ? 'Saving…' : 'Save notification rules'}</Btn></div>
    </Panel>

    {/* Channel configuration modal */}
    {configuring && (
      <Modal title={`Configure ${configuring.label}`} onClose={() => setConfiguring(null)}>
        <div className="space-y-3">
          {configuring.fields.map(f => (
            <div key={f.key}>
              <label className="text-[11px] font-medium block mb-1" style={{ color: CV_COLORS.fg2 }}>{f.label}</label>
              <input
                type={f.type || 'text'}
                value={channelForm[f.key] || ''}
                onChange={e => setChannelForm({ ...channelForm, [f.key]: e.target.value })}
                placeholder={f.placeholder}
                className="w-full px-3 py-1.5 rounded-md text-xs outline-none"
                style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }}
              />
            </div>
          ))}
        </div>
        <p className="text-[10px] mt-3" style={{ color: CV_COLORS.muted }}>
          Credentials are stored server-side and never exposed in the browser.
        </p>
        <div className="flex justify-end gap-2 mt-4">
          <Btn onClick={() => setConfiguring(null)}>Cancel</Btn>
          <Btn primary onClick={saveChannelConfig}>{saving ? 'Saving…' : 'Save & enable'}</Btn>
        </div>
      </Modal>
    )}
  </>;
}

/* ── 6. Workflow Operations ── */
const PIPELINE_STAGES = [
  { name: 'Event validation', task: 'validate_dumping_event' },
  { name: 'Evidence extraction', task: 'extract_evidence' },
  { name: 'LPR analysis', task: 'run_lpr_analysis' },
  { name: 'Person & vehicle clustering', task: 'cluster_subject_sightings' },
  { name: 'Analytics update', task: 'update_analytics' },
  { name: 'Event publication', task: 'publish_event_record' },
];

type HealthSummary = { conductor: { status: string }; kafka: { status: string }; active_workers: number; running_workflows: number; failed_workflows_24h: number; open_alerts: number };
type WorkerHB = { task_type: string; worker_id: string; last_heartbeat_at: string; status?: string };

function WorkflowsSection() {
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [workers, setWorkers] = useState<WorkerHB[]>([]);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState<WorkerHB | null>(null);

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/api/operations/health-summary`).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(`${API_BASE}/api/operations/worker-heartbeats`).then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(([h, w]) => { setHealth(h); setWorkers(w); setLoading(false); });
  }, []);

  const conductorUp = health?.conductor?.status === 'healthy';
  const kafkaUp = health?.kafka?.status === 'healthy';

  return <>
    <SectionTitle title="Workflow operations" subtitle="Monitor the Civilytics event-processing workflow orchestrated through Conductor OSS." />
    <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Kafka event input starts a controlled workflow for detection, evidence, matching, and analytics.</p>

    {/* Health metrics */}
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4 mb-5">
      <MetricCard label="Workflow service" value={loading ? '…' : conductorUp ? 'Healthy' : 'Down'} sub="Conductor OSS" color={loading ? CV_COLORS.muted : conductorUp ? CV_COLORS.green : CV_COLORS.orange} />
      <MetricCard label="Kafka intake" value={loading ? '…' : kafkaUp ? 'Connected' : 'Down'} sub="civilytics.dumping.events" color={loading ? CV_COLORS.muted : kafkaUp ? CV_COLORS.green : CV_COLORS.orange} />
      <MetricCard label="Active workers" value={loading ? '…' : String(health?.active_workers ?? 0)} sub={`${workers.length} heartbeats`} color={loading ? CV_COLORS.muted : (health?.active_workers ?? 0) > 0 ? CV_COLORS.green : CV_COLORS.orange} />
      <MetricCard label="Failed tasks, 24h" value={loading ? '…' : String(health?.failed_workflows_24h ?? 0)} sub="" color={loading ? CV_COLORS.muted : (health?.failed_workflows_24h ?? 0) > 0 ? CV_COLORS.orange : CV_COLORS.green} />
    </div>

    {/* Workflow definition */}
    <Panel>
      <div className="flex justify-between items-start">
        <div>
          <h3 className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>dumping-event-processing-v1</h3>
          <p className="text-[11px] mt-0.5" style={{ color: CV_COLORS.fg2 }}>Process a validated dumping event into evidence, matching, analytics, and review records.</p>
        </div>
        <Chip label="Published" color={CV_COLORS.green} />
      </div>
    </Panel>

    {/* Pipeline */}
    <Panel>
      <h3 className="text-xs font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Event processing pipeline</h3>
      <p className="text-[10px] mb-4" style={{ color: CV_COLORS.muted }}>Kafka topic: civilytics.dumping.events → Validated dumping-event metadata</p>
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-1">
        {PIPELINE_STAGES.map((s, i) => (
          <div key={s.task} className="flex items-center gap-1 flex-1">
            <div className="flex-1 rounded-lg p-2.5 text-center" style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}` }}>
              <div className="w-2 h-2 rounded-full mx-auto mb-1" style={{ background: CV_COLORS.green }} />
              <p className="text-[10px] font-medium" style={{ color: CV_COLORS.fg }}>{s.name}</p>
              <p className="text-[9px] font-mono" style={{ color: CV_COLORS.muted }}>{s.task}</p>
            </div>
            {i < PIPELINE_STAGES.length - 1 && <ArrowRight className="w-3 h-3 flex-shrink-0 hidden sm:block" style={{ color: CV_COLORS.muted }} />}
          </div>
        ))}
      </div>
      <p className="text-[10px] mt-3" style={{ color: CV_COLORS.muted }}>Workers poll Conductor OSS for assigned task types.</p>
    </Panel>

    {/* Worker registry */}
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Worker registry</h3>
      {loading ? <p className="text-xs" style={{ color: CV_COLORS.muted }}>Loading…</p> : workers.length === 0 ? <p className="text-xs" style={{ color: CV_COLORS.muted }}>No worker heartbeats received.</p> : (
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
            {['Task type', 'Worker ID', 'Status', 'Last heartbeat', ''].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {workers.map(w => {
              const age = Date.now() - new Date(w.last_heartbeat_at).getTime();
              const alive = age < 5 * 60 * 1000;
              return (
              <tr key={`${w.task_type}-${w.worker_id}`} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2 pr-3 font-mono text-[11px]">{w.task_type}</td>
                <td className="pr-3 text-[11px]">{w.worker_id}</td>
                <td className="pr-3"><Chip label={alive ? 'Active' : 'Stale'} color={alive ? CV_COLORS.green : CV_COLORS.orange} /></td>
                <td className="pr-3" style={{ color: CV_COLORS.muted }}>{new Date(w.last_heartbeat_at).toLocaleString()}</td>
                <td><button onClick={() => setDrawer(w)} className="text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>View</button></td>
              </tr>);
            })}
          </tbody>
        </table>
      </div>)}
    </Panel>

    {/* Kafka intake */}
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Kafka event intake</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
        <div><span style={{ color: CV_COLORS.muted }}>Status</span><br /><Chip label={loading ? '…' : kafkaUp ? 'Connected' : 'Down'} color={kafkaUp ? CV_COLORS.green : CV_COLORS.orange} /></div>
        <div><span style={{ color: CV_COLORS.muted }}>Topic</span><br /><span style={{ color: CV_COLORS.fg }} className="font-mono text-[11px]">civilytics.dumping.events</span></div>
        <div><span style={{ color: CV_COLORS.muted }}>Consumer group</span><br /><span style={{ color: CV_COLORS.fg }}>civilytics-workflow-intake</span></div>
      </div>
    </Panel>

    {drawer && <WorkerDrawer worker={drawer} onClose={() => setDrawer(null)} />}
  </>;
}

function MetricCard({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return <div className="rounded-[10px] p-3" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
    <p className="text-[10px] sm:text-xs" style={{ color: CV_COLORS.fg2 }}>{label}</p>
    <p className="text-sm sm:text-base font-bold mt-0.5" style={{ color }}>{value}</p>
    {sub && <p className="text-[10px] mt-0.5" style={{ color: CV_COLORS.muted }}>{sub}</p>}
  </div>;
}

function WorkerDrawer({ worker, onClose }: { worker: WorkerHB; onClose: () => void }) {
  const age = Date.now() - new Date(worker.last_heartbeat_at).getTime();
  const alive = age < 5 * 60 * 1000;
  return <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
    <div className="absolute inset-0 bg-black/40" />
    <div className="relative w-full max-w-[480px] h-full overflow-y-auto" style={{ background: CV_COLORS.panel }} onClick={e => e.stopPropagation()}>
      <div className="p-5 border-b flex justify-between items-center" style={{ borderColor: CV_COLORS.border }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{worker.worker_id}</h3>
          <p className="text-[11px] font-mono" style={{ color: CV_COLORS.fg2 }}>{worker.task_type}</p>
        </div>
        <div className="flex items-center gap-2">
          <Chip label={alive ? 'Active' : 'Stale'} color={alive ? CV_COLORS.green : CV_COLORS.orange} />
          <button onClick={onClose}><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
        </div>
      </div>
      <div className="p-5 space-y-4 text-xs" style={{ color: CV_COLORS.fg }}>
        <div className="grid grid-cols-2 gap-3">
          <div><span style={{ color: CV_COLORS.muted }}>Orchestrator</span><br />Conductor OSS</div>
          <div><span style={{ color: CV_COLORS.muted }}>Input source</span><br />Workflow task input</div>
          <div><span style={{ color: CV_COLORS.muted }}>Task type</span><br />{worker.task_type}</div>
          <div><span style={{ color: CV_COLORS.muted }}>Worker ID</span><br />{worker.worker_id}</div>
          <div><span style={{ color: CV_COLORS.muted }}>Last heartbeat</span><br />{new Date(worker.last_heartbeat_at).toLocaleString()}</div>
          <div><span style={{ color: CV_COLORS.muted }}>Status</span><br />{alive ? 'Active' : 'Stale'}</div>
        </div>
      </div>
    </div>
  </div>;
}

/* ── 7. Audit Log ── */
function AuditSection() {
  const [entries, setEntries] = useState<{ id: string; actor_id: string; actor_role: string; action: string; target_type: string; target_id: string; result: string | null; created_at: string }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/api/operations/audit-log?limit=50`)
      .then(r => r.ok ? r.json() : [])
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, []);

  const fmt = (iso: string) => new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });

  return <>
    <SectionTitle title="Audit log" subtitle="Review configuration and controlled workflow actions." />
    <Panel>
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs" style={{ color: CV_COLORS.muted }}>{loading ? '…' : `${entries.length} entries`}</span>
        <Btn><Download className="w-3 h-3 inline mr-1" />Export CSV</Btn>
      </div>
      {loading ? <p className="text-xs py-4 text-center" style={{ color: CV_COLORS.muted }}>Loading…</p> : entries.length === 0 ? <p className="text-xs py-4 text-center" style={{ color: CV_COLORS.muted }}>No audit entries yet.</p> : (
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
            {['Time', 'Actor', 'Action', 'Target', 'Result'].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {entries.map(a => (
              <tr key={a.id} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2 pr-3 whitespace-nowrap" style={{ color: CV_COLORS.muted }}>{fmt(a.created_at)}</td>
                <td className="pr-3">{a.actor_id} <span style={{ color: CV_COLORS.muted }}>({a.actor_role})</span></td>
                <td className="pr-3">{a.action}</td>
                <td className="pr-3 font-mono text-[11px]" style={{ color: CV_COLORS.fg2 }}>{a.target_type}/{a.target_id}</td>
                <td><Chip label={a.result || 'OK'} color={a.result === 'FAILURE' ? CV_COLORS.orange : CV_COLORS.green} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </Panel>
  </>;
}

/* ── Modal shell ── */
function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);
  return <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
    <div className="absolute inset-0 bg-black/40" />
    <div role="dialog" aria-modal="true" aria-label={title} className="relative w-full max-w-md rounded-[10px] p-5" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }} onClick={e => e.stopPropagation()}>
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{title}</h3>
        <button onClick={onClose} aria-label="Close dialog"><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
      </div>
      {children}
    </div>
  </div>;
}
