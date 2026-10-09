import { useState, useRef, useEffect } from 'react';
import {
  Settings as SettingsIcon, Users, Camera, HardDrive, Bell, Workflow, FileText,
  Check, ChevronRight, X, Plus, MoreVertical, Download, Eye, Clock,
  ArrowRight, AlertTriangle, RefreshCw, MessageCircle, Phone, Volume2, Globe, Mail,
} from 'lucide-react';
import { CV_COLORS } from '@/lib/cv-colors';
import { cn } from '@/lib/utils';
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
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 2500); };

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
        <div className="fixed bottom-6 right-6 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg animate-in fade-in slide-in-from-bottom-2"
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
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 py-2"><span className="text-xs font-medium" style={{ color: CV_COLORS.fg2 }}>{label}</span>{children}</div>;
}
function Inp({ value, onChange, className }: { value: string; onChange: (v: string) => void; className?: string }) {
  return <input value={value} onChange={e => onChange(e.target.value)} className={cn('px-3 py-1.5 rounded-md text-xs outline-none', className)} style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />;
}
function Sel({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  return <select value={value} onChange={e => onChange(e.target.value)} className="px-3 py-1.5 rounded-md text-xs outline-none appearance-none cursor-pointer" style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }}>
    {options.map(o => <option key={o} value={o}>{o}</option>)}
  </select>;
}
function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return <button onClick={onToggle} className="w-9 h-5 rounded-full relative transition-colors" style={{ background: on ? CV_COLORS.cyan : CV_COLORS.border }}>
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
  const mark = (fn: (v: string) => void) => (v: string) => { fn(v); setDirty(true); };
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
        <Btn primary disabled={!dirty} onClick={() => { onSave(); setDirty(false); }}>Save changes</Btn>
      </div>
    </Panel>
  </>;
}

/* ── 2. Users & Roles ── */
const DEMO_USERS = [
  { name: 'Sandesh C', email: 'sandesh@letsbethechange.in', role: 'Organisation admin', lastActive: '09 Oct 2026, 10:14 IST', status: 'Active' },
  { name: 'Neeraj Kumar SJ', email: 'neeraj@satyaki.co.in', role: 'Platform admin', lastActive: '09 Oct 2026, 10:02 IST', status: 'Active' },
  { name: 'Operations Review Desk', email: 'operations@letsbethechange.in', role: 'Reviewer', lastActive: '08 Oct 2026, 17:18 IST', status: 'Active' },
  { name: 'Field Supervisor', email: 'field.supervisor@letsbethechange.in', role: 'Viewer', lastActive: '06 Oct 2026, 09:24 IST', status: 'Invitation pending' },
];

function UsersSection({ onToast }: { onToast: (m: string) => void }) {
  const [showInvite, setShowInvite] = useState(false);
  return <>
    <SectionTitle title="Users & roles" subtitle="Control who can review events and change configuration." />
    <Panel>
      <div className="flex justify-between items-center mb-4">
        <span className="text-xs font-medium" style={{ color: CV_COLORS.fg2 }}>{DEMO_USERS.length} users</span>
        <Btn primary onClick={() => setShowInvite(true)}><Plus className="w-3 h-3 inline mr-1" />Invite user</Btn>
      </div>
      {/* Desktop table */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b" >
            {['Name', 'Email', 'Role', 'Last active', 'Status'].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {DEMO_USERS.map(u => (
              <tr key={u.email} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2.5 pr-3 font-medium">{u.name}</td>
                <td className="pr-3" style={{ color: CV_COLORS.fg2 }}>{u.email}</td>
                <td className="pr-3"><Chip label={u.role} color={CV_COLORS.fg2} /></td>
                <td className="pr-3" style={{ color: CV_COLORS.muted }}>{u.lastActive}</td>
                <td><Chip label={u.status} color={u.status === 'Active' ? CV_COLORS.green : CV_COLORS.orange} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Mobile cards */}
      <div className="sm:hidden space-y-2">
        {DEMO_USERS.map(u => (
          <div key={u.email} className="rounded-lg p-3" style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}` }}>
            <div className="flex justify-between"><span className="text-xs font-medium" style={{ color: CV_COLORS.fg }}>{u.name}</span><Chip label={u.status} color={u.status === 'Active' ? CV_COLORS.green : CV_COLORS.orange} /></div>
            <p className="text-[11px] mt-1" style={{ color: CV_COLORS.fg2 }}>{u.email}</p>
            <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>{u.role} · {u.lastActive}</p>
          </div>
        ))}
      </div>
    </Panel>
    {showInvite && <Modal title="Invite user" onClose={() => setShowInvite(false)}>
      <Field label="Name"><Inp value="" onChange={() => {}} /></Field>
      <Field label="Email"><Inp value="" onChange={() => {}} /></Field>
      <Field label="Role"><Sel value="Viewer" options={['Organisation admin', 'Reviewer', 'Viewer']} onChange={() => {}} /></Field>
      <div className="flex justify-end gap-2 mt-4">
        <Btn onClick={() => setShowInvite(false)}>Cancel</Btn>
        <Btn primary onClick={() => { setShowInvite(false); onToast('Invitation sent'); }}>Send invitation</Btn>
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
  </>;
}

/* ── 4. Evidence & Retention ── */
function EvidenceSection({ onSave }: { onSave: () => void }) {
  const [meta, setMeta] = useState('12 months');
  const [frames, setFrames] = useState('30 days');
  const [clips, setClips] = useState('30 days');
  return <>
    <SectionTitle title="Evidence & retention" subtitle="Set how long Civilytics keeps event records and evidence." />
    <Panel>
      <Field label="Event metadata retention"><Sel value={meta} options={['6 months', '12 months', '18 months', '24 months']} onChange={setMeta} /></Field>
      <Field label="Evidence frame retention"><Sel value={frames} options={['7 days', '30 days', '60 days', '90 days']} onChange={setFrames} /></Field>
      <Field label="Recorded clip retention"><Sel value={clips} options={['7 days', '30 days', '60 days', '90 days']} onChange={setClips} /></Field>
      <Field label="Evidence download access"><span className="text-xs" style={{ color: CV_COLORS.fg }}>Reviewers and administrators</span></Field>
    </Panel>
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Storage usage</h3>
      <Field label="Current evidence storage"><span className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>6.8 GB</span></Field>
      <Field label="Included storage window"><span className="text-xs" style={{ color: CV_COLORS.fg }}>30 days</span></Field>
      <Field label="Storage warning"><Chip label="No warning" color={CV_COLORS.green} /></Field>
      <div className="flex justify-end mt-4"><Btn primary onClick={onSave}>Save retention policy</Btn></div>
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

type ChannelId = 'email' | 'whatsapp' | 'telegram' | 'ip_speaker' | 'sip_phone' | 'webhook';
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
  { id: 'whatsapp', label: 'WhatsApp', icon: MessageCircle, enabled: false, fields: [
    { key: 'wa_api_url', label: 'API URL', placeholder: 'https://graph.facebook.com/v18.0/...' },
    { key: 'wa_phone_id', label: 'Phone number ID', placeholder: '123456789' },
    { key: 'wa_token', label: 'Access token', type: 'password', placeholder: 'Bearer token' },
    { key: 'wa_recipients', label: 'Recipient numbers (comma-separated)', placeholder: '+91XXXXXXXXXX, +91XXXXXXXXXX' },
  ]},
  { id: 'telegram', label: 'Telegram', icon: MessageCircle, enabled: false, fields: [
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
  { id: 'webhook', label: 'Webhook', icon: Globe, enabled: false, fields: [
    { key: 'webhook_url', label: 'Webhook URL', placeholder: 'https://hooks.example.com/alerts' },
    { key: 'webhook_secret', label: 'Signing secret (optional)', type: 'password', placeholder: 'whsec_...' },
    { key: 'webhook_headers', label: 'Custom headers (JSON, optional)', placeholder: '{"X-Source": "civilytics"}' },
  ]},
];

function NotificationsSection({ onSave }: { onSave: () => void }) {
  const [rules, setRules] = useState(NOTIF_RULES);
  const [channels, setChannels] = useState(DELIVERY_CHANNELS);
  const [configuring, setConfiguring] = useState<ChannelConfig | null>(null);
  const [channelForm, setChannelForm] = useState<Record<string, string>>({});

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
      setChannels(channels.map(c => c.id === id ? { ...c, enabled: false } : c));
    }
  };
  const saveChannelConfig = () => {
    if (!configuring) return;
    setChannels(channels.map(c => c.id === configuring.id ? { ...c, enabled: true } : c));
    setConfiguring(null);
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
                  <button onClick={() => { setConfiguring(ch); setChannelForm({}); }}
                    className="text-[10px] font-medium" style={{ color: CV_COLORS.fg2 }}>Edit</button>
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
      <div className="flex justify-end mt-4"><Btn primary onClick={onSave}>Save notification rules</Btn></div>
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
          <Btn primary onClick={saveChannelConfig}>Save & enable</Btn>
        </div>
      </Modal>
    )}
  </>;
}

/* ── 6. Workflow Operations ── */
const PIPELINE_STAGES = [
  { name: 'Event validation', task: 'validate_dumping_event', dur: '180 ms' },
  { name: 'Evidence extraction', task: 'extract_evidence', dur: '4.2 s' },
  { name: 'LPR analysis', task: 'run_lpr_analysis', dur: '1.8 s' },
  { name: 'Person & vehicle clustering', task: 'cluster_subject_sightings', dur: '2.6 s' },
  { name: 'Analytics update', task: 'update_analytics', dur: '310 ms' },
  { name: 'Event publication', task: 'publish_event_record', dur: '220 ms' },
];

const WORKERS = [
  { task: 'validate_dumping_event', service: 'Event Intake Worker', queue: 'Active', polling: 2, last: '10:18:24 IST', fail: '0.0%' },
  { task: 'extract_evidence', service: 'Evidence Worker', queue: 'Active', polling: 2, last: '10:18:20 IST', fail: '0.4%' },
  { task: 'run_lpr_analysis', service: 'LPR Worker', queue: 'Active', polling: 1, last: '10:18:18 IST', fail: '0.2%' },
  { task: 'cluster_subject_sightings', service: 'Clustering Worker', queue: 'Active', polling: 1, last: '10:18:15 IST', fail: '0.1%' },
  { task: 'update_analytics', service: 'Analytics Worker', queue: 'Active', polling: 1, last: '10:18:21 IST', fail: '0.0%' },
  { task: 'publish_event_record', service: 'Event Publication Worker', queue: 'Active', polling: 1, last: '10:18:23 IST', fail: '0.0%' },
];

function WorkflowsSection() {
  const [drawer, setDrawer] = useState<typeof WORKERS[0] | null>(null);
  return <>
    <SectionTitle title="Workflow operations" subtitle="Monitor the Civilytics event-processing workflow orchestrated through Conductor OSS." />
    <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Kafka event input starts a controlled workflow for detection, evidence, matching, and analytics.</p>

    {/* Health metrics */}
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4 mb-5">
      <MetricCard label="Workflow service" value="Healthy" sub="Conductor OSS is reachable" color={CV_COLORS.green} />
      <MetricCard label="Kafka intake" value="Connected" sub="civilytics.dumping.events" color={CV_COLORS.green} />
      <MetricCard label="Active workers" value="5 / 5" sub="All required task types polling" color={CV_COLORS.green} />
      <MetricCard label="Failed tasks, 24h" value="2" sub="Both retries completed" color={CV_COLORS.orange} />
    </div>

    {/* Workflow definition */}
    <Panel>
      <div className="flex justify-between items-start">
        <div>
          <h3 className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>dumping-event-processing-v1</h3>
          <p className="text-[11px] mt-0.5" style={{ color: CV_COLORS.fg2 }}>Process a validated dumping event into evidence, matching, analytics, and review records.</p>
          <p className="text-[10px] mt-1" style={{ color: CV_COLORS.muted }}>Published, version 1.4 · Last updated: 09 Oct 2026, 09:28 IST</p>
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
              <p className="text-[9px] mt-0.5" style={{ color: CV_COLORS.fg2 }}>{s.dur}</p>
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
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
            {['Worker task type', 'Worker service', 'Queue', 'Polling', 'Last completion', 'Fail 24h', ''].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {WORKERS.map(w => (
              <tr key={w.task} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2 pr-3 font-mono text-[11px]">{w.task}</td>
                <td className="pr-3">{w.service}</td>
                <td className="pr-3"><Chip label={w.queue} color={CV_COLORS.green} /></td>
                <td className="pr-3">{w.polling}</td>
                <td className="pr-3" style={{ color: CV_COLORS.muted }}>{w.last}</td>
                <td className="pr-3" style={{ color: parseFloat(w.fail) > 0 ? CV_COLORS.orange : CV_COLORS.green }}>{w.fail}</td>
                <td><button onClick={() => setDrawer(w)} className="text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>View</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>

    {/* Kafka intake */}
    <Panel>
      <h3 className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Kafka event intake</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
        <div><span style={{ color: CV_COLORS.muted }}>Status</span><br /><Chip label="Connected" color={CV_COLORS.green} /></div>
        <div><span style={{ color: CV_COLORS.muted }}>Topic</span><br /><span style={{ color: CV_COLORS.fg }} className="font-mono text-[11px]">civilytics.dumping.events</span></div>
        <div><span style={{ color: CV_COLORS.muted }}>Consumer group</span><br /><span style={{ color: CV_COLORS.fg }}>civilytics-workflow-intake</span></div>
        <div><span style={{ color: CV_COLORS.muted }}>Latest message</span><br /><span style={{ color: CV_COLORS.fg }}>10:18:24 IST</span></div>
        <div><span style={{ color: CV_COLORS.muted }}>Processing lag</span><br /><span style={{ color: CV_COLORS.green }}>0 messages</span></div>
        <div><span style={{ color: CV_COLORS.muted }}>Dead-letter queue</span><br /><span style={{ color: CV_COLORS.green }}>0 messages</span></div>
      </div>
    </Panel>

    {/* Worker detail drawer */}
    {drawer && <WorkerDrawer worker={drawer} onClose={() => setDrawer(null)} />}
  </>;
}

function MetricCard({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return <div className="rounded-[10px] p-3" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
    <p className="text-[10px] sm:text-xs" style={{ color: CV_COLORS.fg2 }}>{label}</p>
    <p className="text-sm sm:text-base font-bold mt-0.5" style={{ color }}>{value}</p>
    <p className="text-[10px] mt-0.5" style={{ color: CV_COLORS.muted }}>{sub}</p>
  </div>;
}

function WorkerDrawer({ worker, onClose }: { worker: typeof WORKERS[0]; onClose: () => void }) {
  return <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
    <div className="absolute inset-0 bg-black/40" />
    <div className="relative w-full max-w-[480px] h-full overflow-y-auto" style={{ background: CV_COLORS.panel }} onClick={e => e.stopPropagation()}>
      <div className="p-5 border-b flex justify-between items-center" style={{ borderColor: CV_COLORS.border }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{worker.service}</h3>
          <p className="text-[11px] font-mono" style={{ color: CV_COLORS.fg2 }}>{worker.task}</p>
        </div>
        <div className="flex items-center gap-2">
          <Chip label={worker.queue} color={CV_COLORS.green} />
          <button onClick={onClose}><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
        </div>
      </div>
      <div className="p-5 space-y-4 text-xs" style={{ color: CV_COLORS.fg }}>
        <div className="grid grid-cols-2 gap-3">
          <div><span style={{ color: CV_COLORS.muted }}>Orchestrator</span><br />Conductor OSS</div>
          <div><span style={{ color: CV_COLORS.muted }}>Input source</span><br />Workflow task input</div>
          <div><span style={{ color: CV_COLORS.muted }}>Polling workers</span><br />{worker.polling}</div>
          <div><span style={{ color: CV_COLORS.muted }}>Poll interval</span><br />500 ms</div>
          <div><span style={{ color: CV_COLORS.muted }}>Retry policy</span><br />3 attempts</div>
          <div><span style={{ color: CV_COLORS.muted }}>Retry delay</span><br />Exponential backoff</div>
          <div><span style={{ color: CV_COLORS.muted }}>Last heartbeat</span><br />{worker.last}</div>
          <div><span style={{ color: CV_COLORS.muted }}>Failure rate 24h</span><br />{worker.fail}</div>
        </div>
        <div className="border-t pt-3" style={{ borderColor: CV_COLORS.border }}>
          <h4 className="text-xs font-semibold mb-2" style={{ color: CV_COLORS.fg }}>Operational health</h4>
          <div className="grid grid-cols-2 gap-3">
            <div><span style={{ color: CV_COLORS.muted }}>Tasks completed, 24h</span><br />186</div>
            <div><span style={{ color: CV_COLORS.muted }}>Average duration</span><br />4.2 s</div>
            <div><span style={{ color: CV_COLORS.muted }}>P95 duration</span><br />6.8 s</div>
            <div><span style={{ color: CV_COLORS.muted }}>Failed tasks, 24h</span><br />1</div>
          </div>
        </div>
        <div className="flex gap-2 pt-2">
          <Btn>View logs</Btn>
          <Btn disabled>Retry failed task</Btn>
        </div>
      </div>
    </div>
  </div>;
}

/* ── 7. Audit Log ── */
const AUDIT_DATA = [
  { time: '09 Oct 2026, 10:12 IST', user: 'Neeraj Kumar SJ', action: 'Updated evidence retention', target: '30 days', result: 'Success' },
  { time: '09 Oct 2026, 09:54 IST', user: 'Sandesh C', action: 'Confirmed event', target: 'EVT-240930-027', result: 'Success' },
  { time: '09 Oct 2026, 08:42 IST', user: 'System', action: 'Retried workflow task', target: 'extract_evidence', result: 'Success' },
  { time: '08 Oct 2026, 17:18 IST', user: 'Neeraj Kumar SJ', action: 'Updated notification rules', target: 'Workflow failures', result: 'Success' },
];

function AuditSection() {
  return <>
    <SectionTitle title="Audit log" subtitle="Review configuration and controlled workflow actions." />
    <Panel>
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs" style={{ color: CV_COLORS.muted }}>{AUDIT_DATA.length} entries</span>
        <Btn><Download className="w-3 h-3 inline mr-1" />Export CSV</Btn>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
          <thead><tr style={{ color: CV_COLORS.muted }} className="border-b">
            {['Time', 'User', 'Action', 'Target', 'Result'].map(h => <th key={h} className="text-left py-2 pr-3 font-medium" style={{ borderColor: CV_COLORS.border }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {AUDIT_DATA.map((a, i) => (
              <tr key={i} className="border-b" style={{ borderColor: CV_COLORS.border }}>
                <td className="py-2 pr-3 whitespace-nowrap" style={{ color: CV_COLORS.muted }}>{a.time}</td>
                <td className="pr-3">{a.user}</td>
                <td className="pr-3">{a.action}</td>
                <td className="pr-3 font-mono text-[11px]" style={{ color: CV_COLORS.fg2 }}>{a.target}</td>
                <td><Chip label={a.result} color={CV_COLORS.green} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
    <div className="relative w-full max-w-md rounded-[10px] p-5" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }} onClick={e => e.stopPropagation()}>
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{title}</h3>
        <button onClick={onClose}><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
      </div>
      {children}
    </div>
  </div>;
}
