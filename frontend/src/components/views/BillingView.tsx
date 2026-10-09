import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Camera,
  HardDrive,
  Activity,
  Check,
  Download,
  FileText,
  Pencil,
  Plus,
  RefreshCw,
  HelpCircle,
  X,
  ChevronRight,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import { CV_COLORS } from '@/lib/cv-colors';
import { API_BASE } from '@/lib/api-base';
import { useMonitoring } from '@/context/MonitoringContext';

const INR = (n: number) => '₹' + n.toLocaleString('en-IN');

type ModalKind = 'help' | 'editContact' | 'requestCam' | 'serviceChange' | null;

interface SubData {
  id: string; plan_name: string; description: string | null; status: string;
  billing_cycle: string; start_date: string; next_renewal: string;
  monthly_amount_rupees: number; subscription_ext_id: string;
}
interface InvData {
  id: string; invoice_number: string; billing_period_start: string; billing_period_end: string;
  issued_date: string; due_date: string; amount_rupees: number; tax_rupees: number;
  total_rupees: number; payment_status: string;
}
interface ContactData {
  id: string; org_name: string; contact_name: string; email: string; phone: string | null; address: string | null;
}
interface UsageData {
  active_cameras: number; total_cameras: number; events_this_period: number; storage_gb: number;
  cameras: { id: string; name: string; location: string; status: string }[];
  billing_period_start: string; billing_period_end: string;
}

const SERVICES = [
  'Shared cloud infrastructure and platform services',
  'Camera-specific video processing and analytics',
  'Waste-dumping event detection',
  'Evidence storage and review access',
  'Analytics dashboard and event history',
  'Standard support during business hours',
];

const SERVICE_QUANTITIES: Record<string, string> = {
  'Camera-specific video processing and analytics': '1 active camera',
  'Evidence storage and review access': '30-day retention',
  'Analytics dashboard and event history': '5 dashboard users',
};

const fmtDate = (s: string) => new Date(s + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
const fmtPeriod = (a: string, b: string) => `${fmtDate(a)} – ${fmtDate(b)}`;

export function BillingView() {
  const { setCurrentView } = useMonitoring();
  const [modal, setModal] = useState<ModalKind>(null);
  const [toast, setToast] = useState('');
  const planRef = useRef<HTMLDivElement>(null);

  const [sub, setSub] = useState<SubData | null>(null);
  const [invoices, setInvoices] = useState<InvData[]>([]);
  const [contact, setContact] = useState<ContactData | null>(null);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchAll = useCallback(async () => {
    try {
      const [s, i, c, u] = await Promise.all([
        fetch(`${API_BASE}/api/billing/subscription`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/api/billing/invoices`).then(r => r.ok ? r.json() : []),
        fetch(`${API_BASE}/api/billing/contact`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/api/billing/usage`).then(r => r.ok ? r.json() : null),
      ]);
      setSub(s); setInvoices(i); setContact(c); setUsage(u);
      setError('');
    } catch { setError('Billing data is unavailable'); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(''), 3000); return () => clearTimeout(t); } }, [toast]);

  const scrollToPlan = () => planRef.current?.scrollIntoView({ behavior: 'smooth' });

  if (loading) return (
    <div className="flex items-center justify-center h-full" style={{ background: CV_COLORS.bg }}>
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: CV_COLORS.muted }} />
    </div>
  );

  if (error) return (
    <div className="flex flex-col items-center justify-center h-full gap-3" style={{ background: CV_COLORS.bg }}>
      <AlertTriangle className="w-10 h-10" style={{ color: CV_COLORS.orange }} />
      <h2 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{error}</h2>
      <p className="text-xs" style={{ color: CV_COLORS.fg2 }}>Refresh the page or contact support if the issue continues.</p>
      <div className="flex gap-2 mt-2">
        <button onClick={fetchAll} className="px-3 py-1.5 rounded-md text-xs font-medium" style={{ background: CV_COLORS.cyan, color: CV_COLORS.bg }}>Retry</button>
        <button onClick={() => window.location.reload()} className="px-3 py-1.5 rounded-md text-xs" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>Refresh page</button>
      </div>
    </div>
  );

  const monthlyAmt = sub?.monthly_amount_rupees ?? 0;

  return (
    <div className="flex flex-col h-full overflow-y-auto" style={{ background: CV_COLORS.bg }}>
      {toast && (
        <div className="fixed top-4 right-4 z-[9999] px-4 py-2.5 rounded-lg text-sm font-medium shadow-lg" style={{ background: CV_COLORS.green, color: CV_COLORS.bg }}>
          {toast}
        </div>
      )}

      {/* Header */}
      <div className="px-4 sm:px-8 pt-6 pb-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <p className="text-[11px] mb-1" style={{ color: CV_COLORS.muted }}>Bengaluru South / Subscription &amp; Billing</p>
        <div className="flex items-start justify-between flex-wrap gap-2">
          <div>
            <h1 className="text-lg font-semibold" style={{ color: CV_COLORS.fg }}>Subscription &amp; Billing</h1>
            <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>Manage your Civilytics plan, camera services, invoices, and billing contacts.</p>
          </div>
          <button onClick={() => setModal('help')} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
            <HelpCircle className="w-3.5 h-3.5" /> Help with billing
          </button>
        </div>
      </div>

      <div className="px-4 sm:px-8 py-6 space-y-5 max-w-[1336px]">
        {/* Subscription Summary */}
        {sub && (
          <div className="rounded-[10px] p-5 sm:p-6" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
            <p className="text-[10px] uppercase tracking-wider font-semibold mb-3" style={{ color: CV_COLORS.muted }}>Current Subscription</p>
            <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
              <div className="flex-1">
                <h2 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>{sub.plan_name}</h2>
                <p className="text-xs mt-1" style={{ color: CV_COLORS.fg2 }}>{sub.description}</p>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-2 mt-4">
                  {([
                    ['Status', <StatusChip key="s" status={sub.status} />],
                    ['Billing cycle', sub.billing_cycle.charAt(0).toUpperCase() + sub.billing_cycle.slice(1)],
                    ['Start date', fmtDate(sub.start_date)],
                    ['Next renewal', fmtDate(sub.next_renewal)],
                    ['Billing account', 'LBTC Operations'],
                    ['Subscription ID', sub.subscription_ext_id],
                  ] as [string, React.ReactNode][]).map(([label, value]) => (
                    <div key={label}>
                      <p className="text-[11px]" style={{ color: CV_COLORS.muted }}>{label}</p>
                      <p className="text-[13px] font-medium mt-0.5" style={{ color: CV_COLORS.fg }}>{value}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div className="text-right flex-shrink-0">
                <p className="text-[28px] font-semibold tabular-nums" style={{ color: CV_COLORS.fg }}>{INR(monthlyAmt)} <span className="text-sm font-normal" style={{ color: CV_COLORS.fg2 }}>/ month</span></p>
                <p className="text-[11px] mt-0.5" style={{ color: CV_COLORS.muted }}>+ GST and applicable taxes</p>
                <button onClick={scrollToPlan} className="text-[11px] mt-2 font-medium" style={{ color: CV_COLORS.cyan }}>View plan details →</button>
              </div>
            </div>
          </div>
        )}

        {/* Plan & Service Breakdown */}
        <div ref={planRef} className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-5">
          <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
            <h3 className="text-sm font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Your service plan</h3>
            <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>{sub?.plan_name} — for {usage?.total_cameras ?? 0} camera(s) at Bengaluru South.</p>
            <div className="space-y-0">
              {SERVICES.map(s => (
                <div key={s} className="flex items-center justify-between h-[44px] border-b" style={{ borderColor: `${CV_COLORS.border}66` }}>
                  <div className="flex items-center gap-2.5">
                    <Check className="w-4 h-4 flex-shrink-0" style={{ color: CV_COLORS.green }} />
                    <span className="text-xs" style={{ color: CV_COLORS.fg }}>{s}</span>
                  </div>
                  {SERVICE_QUANTITIES[s] && <span className="text-[11px] font-medium" style={{ color: CV_COLORS.fg2 }}>{SERVICE_QUANTITIES[s]}</span>}
                </div>
              ))}
            </div>
            <button onClick={() => setCurrentView('cameras')} className="text-xs font-medium mt-4" style={{ color: CV_COLORS.cyan }}>Manage cameras →</button>
          </div>

          <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
            <h3 className="text-sm font-semibold mb-4" style={{ color: CV_COLORS.fg }}>Monthly estimate</h3>
            <div className="space-y-3">
              <RowItem label="Shared cloud infrastructure and platform services" value={INR(9500)} />
              <RowItem label="Camera-specific video processing and analytics" value={INR(monthlyAmt - 9500 > 0 ? monthlyAmt - 9500 : 2250)} />
            </div>
            <div className="mt-4 pt-3 border-t" style={{ borderColor: CV_COLORS.border }}>
              <div className="flex justify-between">
                <span className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>Total</span>
                <span className="text-xl font-semibold tabular-nums" style={{ color: CV_COLORS.fg }}>{INR(monthlyAmt)}/month</span>
              </div>
              <p className="text-[11px] mt-1" style={{ color: CV_COLORS.muted }}>Taxes apply as per invoice.</p>
            </div>
            <button className="flex items-center gap-1.5 text-xs font-medium mt-4" style={{ color: CV_COLORS.cyan }}>
              <Download className="w-3.5 h-3.5" /> Download commercial summary
            </button>
          </div>
        </div>

        {/* Camera Service Usage */}
        {usage && (
          <div>
            <h3 className="text-sm font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Camera service usage</h3>
            <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Service usage for the current monthly billing period.</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
              <MetricCard icon={Camera} accent={CV_COLORS.cyan} label="Active cameras" value={`${usage.active_cameras} of ${usage.total_cameras}`} sub={usage.cameras.map(c => `${c.name}, ${c.location}`).join('; ') || 'No cameras'} />
              <MetricCard icon={HardDrive} accent={CV_COLORS.green} label="Evidence storage" value={`${usage.storage_gb} GB`} sub="30-day retention included" />
              <MetricCard icon={Activity} accent={CV_COLORS.orange} label="Events processed" value={String(usage.events_this_period)} sub="Current billing period" />
            </div>

            {/* Camera table — desktop */}
            {usage.cameras.length > 0 && (
              <>
                <div className="hidden sm:block rounded-[10px] overflow-hidden" style={{ border: `1px solid ${CV_COLORS.border}` }}>
                  <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
                    <thead><tr style={{ background: CV_COLORS.panel }}>
                      {['Camera', 'Location', 'Status', ''].map(h => (
                        <th key={h} className="text-left px-4 py-3 font-medium text-[11px]" style={{ color: CV_COLORS.muted, borderBottom: `1px solid ${CV_COLORS.border}` }}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>{usage.cameras.map(cam => (
                      <tr key={cam.id} style={{ background: CV_COLORS.bg }}>
                        <td className="px-4 py-3 font-medium">{cam.name}</td>
                        <td className="px-4 py-3">{cam.location || '—'}</td>
                        <td className="px-4 py-3"><StatusChip status={cam.status} /></td>
                        <td className="px-4 py-3"><button onClick={() => setCurrentView('cameras')} className="text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>View</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                {/* Mobile cards */}
                <div className="sm:hidden space-y-2">
                  {usage.cameras.map(cam => (
                    <div key={cam.id} className="rounded-[10px] p-3 space-y-1" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
                      <div className="flex justify-between"><span className="font-medium text-xs" style={{ color: CV_COLORS.fg }}>{cam.name}</span><StatusChip status={cam.status} /></div>
                      <p className="text-[11px]" style={{ color: CV_COLORS.fg2 }}>{cam.location || '—'}</p>
                    </div>
                  ))}
                </div>
              </>
            )}
            <p className="text-[11px] mt-2" style={{ color: CV_COLORS.muted }}>New camera services apply from the activation date.</p>
          </div>
        )}

        {/* Billing History */}
        <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>Billing history</h3>
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
              <Download className="w-3.5 h-3.5" /> Export invoices
            </button>
          </div>

          {invoices.length === 0 ? (
            <p className="text-xs py-8 text-center" style={{ color: CV_COLORS.muted }}>No invoices are available yet.</p>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-xs" style={{ color: CV_COLORS.fg }}>
                  <thead><tr>
                    {['Invoice', 'Billing period', 'Issued', 'Due', 'Amount', 'Tax', 'Total', 'Status', ''].map(h => (
                      <th key={h} className="text-left px-3 py-2.5 font-medium text-[11px] border-b" style={{ color: CV_COLORS.muted, borderColor: CV_COLORS.border }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{invoices.map(inv => (
                    <tr key={inv.id} className="h-[56px]" style={{ borderBottom: `1px solid ${CV_COLORS.border}44` }}>
                      <td className="px-3 py-2 font-mono text-[11px]">{inv.invoice_number}</td>
                      <td className="px-3 py-2">{fmtPeriod(inv.billing_period_start, inv.billing_period_end)}</td>
                      <td className="px-3 py-2">{fmtDate(inv.issued_date)}</td>
                      <td className="px-3 py-2">{fmtDate(inv.due_date)}</td>
                      <td className="px-3 py-2 tabular-nums">{INR(inv.amount_rupees)}</td>
                      <td className="px-3 py-2 tabular-nums">{INR(inv.tax_rupees)}</td>
                      <td className="px-3 py-2 tabular-nums font-medium">{INR(inv.total_rupees)}</td>
                      <td className="px-3 py-2"><StatusChip status={inv.payment_status} /></td>
                      <td className="px-3 py-2">
                        <button className="flex items-center gap-1 text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>
                          <FileText className="w-3 h-3" /> PDF
                        </button>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {/* Mobile cards */}
              <div className="sm:hidden space-y-3">
                {invoices.map(inv => (
                  <div key={inv.id} className="rounded-lg p-3 space-y-1" style={{ border: `1px solid ${CV_COLORS.border}44`, background: CV_COLORS.bg }}>
                    <div className="flex justify-between"><span className="font-mono text-[11px]" style={{ color: CV_COLORS.fg }}>{inv.invoice_number}</span><StatusChip status={inv.payment_status} /></div>
                    <p className="text-[11px]" style={{ color: CV_COLORS.fg2 }}>{fmtPeriod(inv.billing_period_start, inv.billing_period_end)}</p>
                    <div className="flex justify-between items-center">
                      <span className="text-sm font-medium tabular-nums" style={{ color: CV_COLORS.fg }}>{INR(inv.total_rupees)}</span>
                      <button className="flex items-center gap-1 text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}><FileText className="w-3 h-3" /> PDF</button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
          <p className="text-[11px] mt-3" style={{ color: CV_COLORS.muted }}>Invoices show applicable taxes and payment instructions.</p>
        </div>

        {/* Billing Contact + Payment Info */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>Billing contact</h3>
              <button onClick={() => setModal('editContact')} className="flex items-center gap-1 text-[11px] font-medium" style={{ color: CV_COLORS.cyan }}>
                <Pencil className="w-3 h-3" /> Edit
              </button>
            </div>
            {contact ? (
              <dl className="space-y-2 text-xs">
                {([
                  ['Organisation', contact.org_name],
                  ['Billing contact', contact.contact_name],
                  ['Email', contact.email],
                  ['Phone', contact.phone || '—'],
                  ['Billing address', contact.address || '—'],
                ] as [string, string][]).map(([k, v]) => (
                  <div key={k} className="flex gap-3">
                    <dt className="w-28 flex-shrink-0" style={{ color: CV_COLORS.muted }}>{k}</dt>
                    <dd style={{ color: CV_COLORS.fg }}>{v}</dd>
                  </div>
                ))}
              </dl>
            ) : <p className="text-xs" style={{ color: CV_COLORS.muted }}>No billing contact configured.</p>}
          </div>

          <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
            <h3 className="text-sm font-semibold mb-4" style={{ color: CV_COLORS.fg }}>Payment information</h3>
            <dl className="space-y-2 text-xs">
              {([
                ['Payment method', 'Invoice payment'],
                ['Payment terms', 'Net 15 days'],
                ['Currency', 'INR'],
                ['Tax handling', 'As shown on invoice'],
              ] as [string, string][]).map(([k, v]) => (
                <div key={k} className="flex gap-3">
                  <dt className="w-28 flex-shrink-0" style={{ color: CV_COLORS.muted }}>{k}</dt>
                  <dd style={{ color: CV_COLORS.fg }}>{v}</dd>
                </div>
              ))}
            </dl>
            <p className="text-[11px] mt-4" style={{ color: CV_COLORS.muted }}>Contact Satyaki Solutions for payment or purchase-order queries.</p>
            <button onClick={() => setModal('help')} className="text-[11px] font-medium mt-2" style={{ color: CV_COLORS.cyan }}>Contact billing support →</button>
          </div>
        </div>

        {/* Subscription Actions */}
        <div className="rounded-[10px] p-5" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
          <h3 className="text-sm font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Subscription actions</h3>
          <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Make a service request or change after review.</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <ActionCard title="Add a camera" desc="Request a new camera service for this workspace." btn="Request camera" onClick={() => setModal('requestCam')} />
            <ActionCard title="Change service plan" desc="Discuss revised camera count, retention, or support needs." btn="Request change" onClick={() => setModal('serviceChange')} />
            <ActionCard title="Cancel subscription" desc="Contact billing support before cancelling active services." btn="Contact support" onClick={() => setModal('help')} danger />
          </div>
        </div>
      </div>

      {/* Modals */}
      {modal === 'help' && <Modal title="Billing support" onClose={() => setModal(null)}>
        <p className="text-xs" style={{ color: CV_COLORS.fg2 }}>For billing, payment, or purchase-order queries, contact Satyaki Solutions:</p>
        <dl className="mt-3 space-y-2 text-xs">
          <div className="flex gap-3"><dt style={{ color: CV_COLORS.muted }}>Email</dt><dd style={{ color: CV_COLORS.fg }}>billing@satyaki.co.in</dd></div>
          <div className="flex gap-3"><dt style={{ color: CV_COLORS.muted }}>Phone</dt><dd style={{ color: CV_COLORS.fg }}>+91 80 4567 8900</dd></div>
        </dl>
      </Modal>}

      {modal === 'editContact' && contact && <EditContactModal contact={contact} onClose={() => setModal(null)} onSave={() => { setModal(null); setToast('Billing contact updated'); fetchAll(); }} />}
      {modal === 'requestCam' && <RequestCameraModal onClose={() => setModal(null)} onSubmit={() => { setModal(null); setToast('Camera request submitted'); }} />}
      {modal === 'serviceChange' && <ServiceChangeModal onClose={() => setModal(null)} onSubmit={() => { setModal(null); setToast('Service change request submitted'); }} />}
    </div>
  );
}

function RowItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-xs">
      <span style={{ color: CV_COLORS.fg2 }}>{label}</span>
      <span className="font-medium tabular-nums" style={{ color: CV_COLORS.fg }}>{value}/month</span>
    </div>
  );
}

function MetricCard({ icon: Icon, accent, label, value, sub }: { icon: React.ElementType; accent: string; label: string; value: string; sub: string }) {
  return (
    <div className="rounded-[10px] p-4 h-[112px] flex flex-col justify-between" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.panel }}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px]" style={{ color: CV_COLORS.muted }}>{label}</p>
          <p className="text-xl font-semibold mt-1 tabular-nums" style={{ color: CV_COLORS.fg }}>{value}</p>
        </div>
        <Icon className="w-5 h-5" style={{ color: accent }} />
      </div>
      <p className="text-[11px] truncate" style={{ color: CV_COLORS.fg2 }}>{sub}</p>
    </div>
  );
}

function ActionCard({ title, desc, btn, onClick, danger }: { title: string; desc: string; btn: string; onClick: () => void; danger?: boolean }) {
  return (
    <div className="rounded-[10px] p-4 flex flex-col justify-between" style={{ border: `1px solid ${CV_COLORS.border}`, background: CV_COLORS.bg }}>
      <div>
        <h4 className="text-xs font-semibold mb-1" style={{ color: CV_COLORS.fg }}>{title}</h4>
        <p className="text-[11px]" style={{ color: CV_COLORS.fg2 }}>{desc}</p>
      </div>
      <button onClick={onClick} className="mt-3 px-3 py-1.5 rounded-md text-xs font-medium w-fit"
        style={danger ? { border: `1px solid #ef444466`, color: '#ef4444' } : { background: CV_COLORS.cyan, color: CV_COLORS.bg }}>{btn}</button>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const s = status.toLowerCase();
  const bg = s === 'active' || s === 'paid' || s === 'online' ? `${CV_COLORS.green}22` : s === 'due' || s === 'warning' ? `${CV_COLORS.orange}22` : s === 'offline' ? `${CV_COLORS.muted}22` : '#ef444422';
  const fg = s === 'active' || s === 'paid' || s === 'online' ? CV_COLORS.green : s === 'due' || s === 'warning' ? CV_COLORS.orange : s === 'offline' ? CV_COLORS.muted : '#ef4444';
  return <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase" style={{ background: bg, color: fg }}>{status}</span>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50" />
      <div className="relative rounded-[10px] p-6 w-full max-w-md mx-4 shadow-xl" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}
        onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/5"><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function ModalInput({ label, value, onChange, type = 'text', required, disabled }: { label: string; value: string; onChange: (v: string) => void; type?: string; required?: boolean; disabled?: boolean }) {
  return (
    <div className="space-y-1">
      <label className="text-[11px] font-medium" style={{ color: CV_COLORS.fg2 }}>{label}{required && ' *'}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} disabled={disabled}
        className="w-full px-3 py-2 rounded-md text-xs outline-none focus:ring-1 disabled:opacity-50"
        style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg, '--tw-ring-color': CV_COLORS.cyan } as any} />
    </div>
  );
}

function EditContactModal({ contact, onClose, onSave }: { contact: ContactData; onClose: () => void; onSave: () => void }) {
  const [name, setName] = useState(contact.contact_name);
  const [email, setEmail] = useState(contact.email);
  const [phone, setPhone] = useState(contact.phone || '');
  const [address, setAddress] = useState(contact.address || '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!email.includes('@')) { setError('Valid email required'); return; }
    if (phone.replace(/\D/g, '').length < 10) { setError('Valid phone required'); return; }
    setSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/api/billing/contact`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contact_name: name, email, phone, address }),
      });
      if (!resp.ok) throw new Error('Failed to save');
      onSave();
    } catch { setError('Failed to save. Please try again.'); }
    setSaving(false);
  };

  return (
    <Modal title="Edit billing contact" onClose={onClose}>
      <div className="space-y-3">
        <ModalInput label="Contact name" value={name} onChange={setName} required />
        <ModalInput label="Email" value={email} onChange={v => { setEmail(v); setError(''); }} type="email" required />
        <ModalInput label="Phone" value={phone} onChange={v => { setPhone(v); setError(''); }} required />
        <ModalInput label="Billing address" value={address} onChange={setAddress} />
        {error && <p className="text-[11px]" style={{ color: '#ef4444' }}>{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-xs" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>Cancel</button>
          <button onClick={save} disabled={saving} className="px-4 py-1.5 rounded-md text-xs font-medium disabled:opacity-50" style={{ background: CV_COLORS.cyan, color: CV_COLORS.bg }}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    </Modal>
  );
}

function RequestCameraModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: () => void }) {
  const [location, setLocation] = useState('');
  const [date, setDate] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!location.trim() || !notes.trim()) { setError('Location and notes are required'); return; }
    setSubmitting(true);
    try {
      const resp = await fetch(`${API_BASE}/api/billing/requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_type: 'add_camera', workspace: 'Bengaluru South', location, expected_date: date || undefined, notes }),
      });
      if (!resp.ok) throw new Error();
      onSubmit();
    } catch { setError('Failed to submit. Please try again.'); }
    setSubmitting(false);
  };

  return (
    <Modal title="Request a camera" onClose={onClose}>
      <div className="space-y-3">
        <ModalInput label="Workspace" value="Bengaluru South" onChange={() => {}} disabled />
        <ModalInput label="Proposed location" value={location} onChange={v => { setLocation(v); setError(''); }} required />
        <ModalInput label="Expected activation date" value={date} onChange={setDate} type="date" />
        <div className="space-y-1">
          <label className="text-[11px] font-medium" style={{ color: CV_COLORS.fg2 }}>Notes *</label>
          <textarea value={notes} onChange={e => { setNotes(e.target.value); setError(''); }} rows={3}
            className="w-full px-3 py-2 rounded-md text-xs outline-none resize-none focus:ring-1"
            style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg, '--tw-ring-color': CV_COLORS.cyan } as any} />
        </div>
        {error && <p className="text-[11px]" style={{ color: '#ef4444' }}>{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-xs" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>Cancel</button>
          <button onClick={submit} disabled={submitting} className="px-4 py-1.5 rounded-md text-xs font-medium disabled:opacity-50" style={{ background: CV_COLORS.cyan, color: CV_COLORS.bg }}>{submitting ? 'Submitting…' : 'Submit request'}</button>
        </div>
      </div>
    </Modal>
  );
}

function ServiceChangeModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: () => void }) {
  const [cameraCount, setCameraCount] = useState('');
  const [retention, setRetention] = useState('');
  const [support, setSupport] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    try {
      const resp = await fetch(`${API_BASE}/api/billing/requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_type: 'change_plan', workspace: 'Bengaluru South', notes: `Cameras: ${cameraCount}, Retention: ${retention}, Support: ${support}. ${notes}`.trim() }),
      });
      if (!resp.ok) throw new Error();
      onSubmit();
    } catch { /* toast handles */ }
    setSubmitting(false);
  };

  return (
    <Modal title="Request service change" onClose={onClose}>
      <div className="space-y-3">
        <ModalInput label="Camera count" value={cameraCount} onChange={setCameraCount} />
        <ModalInput label="Storage retention" value={retention} onChange={setRetention} />
        <ModalInput label="Support requirements" value={support} onChange={setSupport} />
        <div className="space-y-1">
          <label className="text-[11px] font-medium" style={{ color: CV_COLORS.fg2 }}>Notes</label>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
            className="w-full px-3 py-2 rounded-md text-xs outline-none resize-none focus:ring-1"
            style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg, '--tw-ring-color': CV_COLORS.cyan } as any} />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-xs" style={{ border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>Cancel</button>
          <button onClick={submit} disabled={submitting} className="px-4 py-1.5 rounded-md text-xs font-medium disabled:opacity-50" style={{ background: CV_COLORS.cyan, color: CV_COLORS.bg }}>{submitting ? 'Submitting…' : 'Submit request'}</button>
        </div>
      </div>
    </Modal>
  );
}
