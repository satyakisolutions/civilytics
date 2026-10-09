import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Search, ChevronDown, ChevronLeft, ChevronRight, X, User, Clock, Camera, MapPin, Eye, AlertTriangle, CheckCircle2, Shield } from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';
import { API_BASE } from '@/lib/api-base';

type StatusKey = 'active' | 'resolved' | 'flagged';

interface Subject {
  id: string;
  subjectId: string;
  firstSeen: Date;
  lastSeen: Date;
  camera: string;
  zone: string;
  sightings: number;
  status: StatusKey;
  confidence: number;
  faceUrl?: string;
  description?: string;
  events: { time: Date; camera: string; type: string }[];
}

// ponytail: demo data — no /api/subjects endpoint yet
const DEMO_SUBJECTS: Subject[] = [
  { id: '1', subjectId: 'SUB-001', firstSeen: new Date('2026-10-08T06:12:00'), lastSeen: new Date('2026-10-09T14:30:00'), camera: 'Entry', zone: 'Zone A / Main', sightings: 7, status: 'flagged', confidence: 92, description: 'Male, dark jacket, recurring dumping activity', events: [
    { time: new Date('2026-10-09T14:30:00'), camera: 'Entry', type: 'dumping_detected' },
    { time: new Date('2026-10-09T08:15:00'), camera: 'Entry', type: 'suspicious_activity' },
    { time: new Date('2026-10-08T18:45:00'), camera: 'Cam-02', type: 'dumping_detected' },
  ]},
  { id: '2', subjectId: 'SUB-002', firstSeen: new Date('2026-10-09T10:20:00'), lastSeen: new Date('2026-10-09T10:20:00'), camera: 'Cam-02', zone: 'Zone A', sightings: 1, status: 'active', confidence: 78, description: 'Person with large bag, single sighting', events: [
    { time: new Date('2026-10-09T10:20:00'), camera: 'Cam-02', type: 'suspicious_activity' },
  ]},
  { id: '3', subjectId: 'SUB-003', firstSeen: new Date('2026-10-07T22:10:00'), lastSeen: new Date('2026-10-09T05:40:00'), camera: 'Cam-03', zone: 'Zone B', sightings: 4, status: 'flagged', confidence: 88, description: 'Repeat offender, night-time dumping near drain', events: [
    { time: new Date('2026-10-09T05:40:00'), camera: 'Cam-03', type: 'dumping_detected' },
    { time: new Date('2026-10-08T23:15:00'), camera: 'Cam-03', type: 'dumping_detected' },
    { time: new Date('2026-10-08T04:30:00'), camera: 'Cam-03', type: 'suspicious_activity' },
  ]},
  { id: '4', subjectId: 'SUB-004', firstSeen: new Date('2026-10-06T09:00:00'), lastSeen: new Date('2026-10-06T09:00:00'), camera: 'Entry', zone: 'Zone A / Main', sightings: 1, status: 'resolved', confidence: 65, description: 'Low-confidence single sighting, cleared after review', events: [
    { time: new Date('2026-10-06T09:00:00'), camera: 'Entry', type: 'suspicious_activity' },
  ]},
  { id: '5', subjectId: 'SUB-005', firstSeen: new Date('2026-10-05T15:30:00'), lastSeen: new Date('2026-10-08T16:20:00'), camera: 'Cam-02', zone: 'Zone A', sightings: 3, status: 'active', confidence: 85, description: 'Vehicle driver depositing construction debris', events: [
    { time: new Date('2026-10-08T16:20:00'), camera: 'Cam-02', type: 'dumping_detected' },
    { time: new Date('2026-10-07T11:00:00'), camera: 'Cam-02', type: 'dumping_detected' },
  ]},
];

const PAGE_SIZE = 25;

export function PersonTrackingView() {
  const [subjects] = useState<Subject[]>(DEMO_SUBJECTS);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All status');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Subject | null>(null);

  const filtered = useMemo(() => {
    return subjects.filter(s => {
      if (statusFilter !== 'All status' && s.status !== statusFilter.toLowerCase()) return false;
      if (!search) return true;
      const q = search.toLowerCase();
      return s.subjectId.toLowerCase().includes(q) || s.camera.toLowerCase().includes(q) || s.zone.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q);
    });
  }, [subjects, search, statusFilter]);

  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const active = subjects.filter(s => s.status === 'active').length;
  const flagged = subjects.filter(s => s.status === 'flagged').length;
  const resolved = subjects.filter(s => s.status === 'resolved').length;

  const fmt = (d: Date) => d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });

  return (
    <div className="flex flex-col h-full" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>Person Tracking</h1>
            <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>Track and identify persons of interest across cameras</p>
          </div>
        </div>
      </div>

      {/* KPI Row */}
      <div className="px-4 sm:px-6 py-3">
        <div className="grid grid-cols-4 gap-2 sm:gap-4">
          <KpiCard accent={CV_COLORS.cyan} label="Total subjects" value={String(subjects.length)} />
          <KpiCard accent={CV_COLORS.blue} label="Active tracks" value={String(active)} />
          <KpiCard accent={CV_COLORS.orange} label="Flagged" value={String(flagged)} />
          <KpiCard accent={CV_COLORS.green} label="Resolved" value={String(resolved)} />
        </div>
      </div>

      {/* Filters */}
      <div className="px-4 sm:px-6 pb-3">
        <div className="flex flex-row flex-wrap items-center gap-2 sm:gap-3">
          <FilterSelect value={statusFilter} options={['All status', 'Active', 'Flagged', 'Resolved']} onChange={v => { setStatusFilter(v); setPage(0); }} />
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: CV_COLORS.muted }} />
            <input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Search subjects…"
              className="w-full pl-9 pr-3 py-2 rounded-md text-sm border bg-transparent" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg }} />
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 flex">
        <div className={cn('flex-1 min-h-0 overflow-auto', selected && 'hidden md:block')}>
          {/* Desktop table */}
          <table className="w-full text-sm hidden md:table" style={{ color: CV_COLORS.fg }}>
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: CV_COLORS.muted, borderBottom: `1px solid ${CV_COLORS.border}` }}>
                <th className="px-6 py-3 font-medium">Subject</th>
                <th className="px-4 py-3 font-medium">First seen</th>
                <th className="px-4 py-3 font-medium">Last seen</th>
                <th className="px-4 py-3 font-medium">Camera</th>
                <th className="px-4 py-3 font-medium">Sightings</th>
                <th className="px-4 py-3 font-medium">Confidence</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {paged.map(s => (
                <tr key={s.id} onClick={() => setSelected(s)} className="cursor-pointer hover:bg-white/[.03] transition-colors"
                  style={{ borderBottom: `1px solid ${CV_COLORS.border}`, ...(selected?.id === s.id ? { borderLeft: `3px solid ${CV_COLORS.cyan}` } : {}) }}>
                  <td className="px-6 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: `${CV_COLORS.cyan}22` }}>
                        <User className="w-4 h-4" style={{ color: CV_COLORS.cyan }} />
                      </div>
                      <div>
                        <p className="font-medium text-sm">{s.subjectId}</p>
                        <p className="text-[11px]" style={{ color: CV_COLORS.muted }}>{s.zone}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{fmt(s.firstSeen)}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{fmt(s.lastSeen)}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{s.camera}</td>
                  <td className="px-4 py-3 tabular-nums font-medium">{s.sightings}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 rounded-full" style={{ background: CV_COLORS.track }}>
                        <div className="h-full rounded-full" style={{ width: `${s.confidence}%`, background: s.confidence >= 80 ? CV_COLORS.green : s.confidence >= 60 ? CV_COLORS.orange : CV_COLORS.muted }} />
                      </div>
                      <span className="text-xs tabular-nums" style={{ color: CV_COLORS.fg2 }}>{s.confidence}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3"><StatusChip status={s.status} /></td>
                  <td className="px-4 py-3">
                    <button onClick={e => { e.stopPropagation(); setSelected(s); }} className="text-xs font-medium" style={{ color: CV_COLORS.cyan }}>View</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Mobile cards */}
          <div className="md:hidden px-4 space-y-2 pb-4">
            {paged.map(s => (
              <div key={s.id} onClick={() => setSelected(s)} className="rounded-lg p-3 cursor-pointer" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <User className="w-4 h-4" style={{ color: CV_COLORS.cyan }} />
                    <span className="font-medium text-sm" style={{ color: CV_COLORS.fg }}>{s.subjectId}</span>
                  </div>
                  <StatusChip status={s.status} />
                </div>
                <div className="flex items-center gap-4 text-[12px]" style={{ color: CV_COLORS.fg2 }}>
                  <span className="flex items-center gap-1"><Camera className="w-3 h-3" />{s.camera}</span>
                  <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{s.zone}</span>
                  <span className="ml-auto tabular-nums">{s.sightings} sightings</span>
                </div>
              </div>
            ))}
          </div>

          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center h-40 gap-2" style={{ color: CV_COLORS.muted }}>
              <User className="w-8 h-8 opacity-40" />
              <p className="text-sm">No subjects found</p>
            </div>
          )}
        </div>

        {/* Detail drawer */}
        {selected && (
          <div className="w-full md:w-[400px] border-l overflow-y-auto flex-shrink-0" style={{ borderColor: CV_COLORS.border, background: CV_COLORS.panel }}>
            <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: CV_COLORS.border }}>
              <h2 className="text-sm font-semibold" style={{ color: CV_COLORS.fg }}>{selected.subjectId}</h2>
              <button onClick={() => setSelected(null)} className="p-1 rounded hover:bg-white/5"><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
            </div>
            <div className="p-5 space-y-4">
              <div className="flex items-center gap-3">
                <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: `${CV_COLORS.cyan}22` }}>
                  <User className="w-8 h-8" style={{ color: CV_COLORS.cyan }} />
                </div>
                <div>
                  <StatusChip status={selected.status} />
                  <p className="text-xs mt-1" style={{ color: CV_COLORS.fg2 }}>{selected.sightings} sightings · {selected.confidence}% confidence</p>
                </div>
              </div>
              {selected.description && (
                <p className="text-xs leading-relaxed" style={{ color: CV_COLORS.fg2 }}>{selected.description}</p>
              )}
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>First seen</p><p style={{ color: CV_COLORS.fg2 }}>{fmt(selected.firstSeen)}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Last seen</p><p style={{ color: CV_COLORS.fg2 }}>{fmt(selected.lastSeen)}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Camera</p><p style={{ color: CV_COLORS.fg2 }}>{selected.camera}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Zone</p><p style={{ color: CV_COLORS.fg2 }}>{selected.zone}</p></div>
              </div>

              <div>
                <h3 className="text-xs font-semibold mb-2" style={{ color: CV_COLORS.fg }}>Sighting timeline</h3>
                <div className="space-y-2">
                  {selected.events.map((ev, i) => (
                    <div key={i} className="flex items-start gap-3 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: ev.type === 'dumping_detected' ? '#FF6B6B' : CV_COLORS.orange }} />
                      <div>
                        <p style={{ color: CV_COLORS.fg2 }}>{fmt(ev.time)}</p>
                        <p style={{ color: CV_COLORS.muted }}>{ev.camera} · {ev.type.replace(/_/g, ' ')}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Pagination */}
      <div className="px-4 sm:px-6 py-3 border-t flex items-center justify-between text-xs" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.muted }}>
        <span>Page {page + 1} of {totalPages} · {filtered.length} subjects</span>
        <div className="flex items-center gap-1">
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0} className="p-1 rounded hover:bg-white/5 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
          <button onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1} className="p-1 rounded hover:bg-white/5 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
        </div>
      </div>
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
    active: 'bg-[#3B9BFF]/15 text-[#3B9BFF]',
    flagged: 'bg-[#FF9A2E]/15 text-[#FF9A2E]',
    resolved: 'bg-[#19E58C]/15 text-[#19E58C]',
  };
  const icons: Record<StatusKey, typeof AlertTriangle> = { active: Eye, flagged: AlertTriangle, resolved: CheckCircle2 };
  const Icon = icons[status];
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium', styles[status])}>
      <Icon className="w-3 h-3" />{status}
    </span>
  );
}

function FilterSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium border" style={{ background: CV_COLORS.panel, borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>
        {value} <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-[9999] rounded-md py-1 min-w-[140px] shadow-lg" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          {options.map(o => (
            <button key={o} onClick={() => { onChange(o); setOpen(false); }} className={cn('w-full text-left px-3 py-1.5 text-xs hover:bg-white/5', o === value ? 'font-semibold' : '')}
              style={{ color: o === value ? CV_COLORS.cyan : CV_COLORS.fg2 }}>{o}</button>
          ))}
        </div>
      )}
    </div>
  );
}
