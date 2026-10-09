import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Search, ChevronDown, ChevronLeft, ChevronRight, X, Car, Clock, Camera, MapPin, Eye, AlertTriangle, CheckCircle2, Shield } from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';
import { API_BASE } from '@/lib/api-base';
import { useCameras } from '@/hooks/useCameras';

type StatusKey = 'active' | 'flagged' | 'cleared';

interface VehicleRecord {
  id: string;
  plateNumber: string;
  plateNormalised: string;
  vehicleType: string;
  firstSeen: Date;
  lastSeen: Date;
  camera: string;
  cameraId: string;
  zone: string;
  sightings: number;
  status: StatusKey;
  confidence: number;
  plateImageUrl?: string;
  events: { time: Date; camera: string; plate: string }[];
}

// ponytail: demo data fallback — real data from /api/lpr/plates when available
const DEMO_VEHICLES: VehicleRecord[] = [
  { id: '1', plateNumber: 'KA 01 AB 1234', plateNormalised: 'KA01AB1234', vehicleType: 'Truck', firstSeen: new Date('2026-10-07T05:30:00'), lastSeen: new Date('2026-10-09T13:45:00'), camera: 'Entry', cameraId: '', zone: 'Zone A / Main', sightings: 5, status: 'flagged', confidence: 94, events: [
    { time: new Date('2026-10-09T13:45:00'), camera: 'Entry', plate: 'KA 01 AB 1234' },
    { time: new Date('2026-10-08T22:10:00'), camera: 'Entry', plate: 'KA 01 AB 1234' },
    { time: new Date('2026-10-08T06:00:00'), camera: 'Cam-02', plate: 'KA 01 AB 1234' },
  ]},
  { id: '2', plateNumber: 'KA 05 MN 9876', plateNormalised: 'KA05MN9876', vehicleType: 'Auto', firstSeen: new Date('2026-10-09T11:00:00'), lastSeen: new Date('2026-10-09T11:00:00'), camera: 'Cam-03', cameraId: '', zone: 'Zone B', sightings: 1, status: 'active', confidence: 72, events: [
    { time: new Date('2026-10-09T11:00:00'), camera: 'Cam-03', plate: 'KA 05 MN 9876' },
  ]},
  { id: '3', plateNumber: 'KA 03 CD 4567', plateNormalised: 'KA03CD4567', vehicleType: 'Pickup', firstSeen: new Date('2026-10-06T19:20:00'), lastSeen: new Date('2026-10-09T02:15:00'), camera: 'Cam-02', cameraId: '', zone: 'Zone A', sightings: 6, status: 'flagged', confidence: 91, events: [
    { time: new Date('2026-10-09T02:15:00'), camera: 'Cam-02', plate: 'KA 03 CD 4567' },
    { time: new Date('2026-10-08T01:30:00'), camera: 'Cam-03', plate: 'KA 03 CD 4567' },
    { time: new Date('2026-10-07T23:45:00'), camera: 'Cam-02', plate: 'KA 03 CD 4567' },
  ]},
  { id: '4', plateNumber: 'KA 09 EF 2222', plateNormalised: 'KA09EF2222', vehicleType: 'Van', firstSeen: new Date('2026-10-05T08:00:00'), lastSeen: new Date('2026-10-05T08:00:00'), camera: 'Entry', cameraId: '', zone: 'Zone A / Main', sightings: 1, status: 'cleared', confidence: 60, events: [
    { time: new Date('2026-10-05T08:00:00'), camera: 'Entry', plate: 'KA 09 EF 2222' },
  ]},
];

const PAGE_SIZE = 25;

export function VehicleTrackingView() {
  const { cameras } = useCameras();
  const [vehicles, setVehicles] = useState<VehicleRecord[]>(DEMO_VEHICLES);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All status');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<VehicleRecord | null>(null);
  const [loading, setLoading] = useState(true);

  const camMap = useMemo(() => {
    const m: Record<string, { name: string; zone: string }> = {};
    cameras.forEach(c => { m[c.id] = { name: c.name, zone: c.zone }; });
    return m;
  }, [cameras]);

  const fetchPlates = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/lpr/plates?limit=200`);
      if (!resp.ok) throw new Error();
      const data: any[] = await resp.json();
      if (data.length === 0) { setLoading(false); return; }
      // group by normalised plate
      const byPlate: Record<string, any[]> = {};
      data.forEach(r => {
        const key = r.plate_number?.replace(/[\s-]/g, '').toUpperCase() || r.plate_number;
        (byPlate[key] = byPlate[key] || []).push(r);
      });
      const records: VehicleRecord[] = Object.entries(byPlate).map(([norm, reads], i) => {
        const sorted = reads.sort((a: any, b: any) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        const first = sorted[sorted.length - 1];
        const last = sorted[0];
        const cam = camMap[last.camera_id] || { name: last.camera_id?.slice(0, 8) || '?', zone: '—' };
        return {
          id: String(i),
          plateNumber: last.plate_number,
          plateNormalised: norm,
          vehicleType: 'Unknown',
          firstSeen: new Date(first.timestamp),
          lastSeen: new Date(last.timestamp),
          camera: cam.name,
          cameraId: last.camera_id,
          zone: cam.zone,
          sightings: reads.length,
          status: reads.length >= 3 ? 'flagged' as StatusKey : 'active' as StatusKey,
          confidence: Math.round(Math.max(...reads.map((r: any) => r.confidence || 0))),
          events: sorted.slice(0, 5).map((r: any) => ({
            time: new Date(r.timestamp),
            camera: camMap[r.camera_id]?.name || r.camera_id?.slice(0, 8) || '?',
            plate: r.plate_number,
          })),
        };
      });
      setVehicles(records);
    } catch {
      // keep demo data
    }
    setLoading(false);
  }, [camMap]);

  useEffect(() => { fetchPlates(); }, [fetchPlates]);

  const filtered = useMemo(() => {
    return vehicles.filter(v => {
      if (statusFilter !== 'All status' && v.status !== statusFilter.toLowerCase()) return false;
      if (!search) return true;
      const q = search.toLowerCase();
      return v.plateNumber.toLowerCase().includes(q) || v.plateNormalised.toLowerCase().includes(q) || v.camera.toLowerCase().includes(q) || v.zone.toLowerCase().includes(q);
    });
  }, [vehicles, search, statusFilter]);

  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const active = vehicles.filter(v => v.status === 'active').length;
  const flagged = vehicles.filter(v => v.status === 'flagged').length;
  const cleared = vehicles.filter(v => v.status === 'cleared').length;

  const fmt = (d: Date) => d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });

  return (
    <div className="flex flex-col h-full" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>Vehicle Tracking</h1>
            <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>License plate recognition and vehicle tracking across cameras</p>
          </div>
        </div>
      </div>

      {/* KPI Row */}
      <div className="px-4 sm:px-6 py-3">
        <div className="grid grid-cols-4 gap-2 sm:gap-4">
          <KpiCard accent={CV_COLORS.cyan} label="Total vehicles" value={String(vehicles.length)} />
          <KpiCard accent={CV_COLORS.blue} label="Active tracks" value={String(active)} />
          <KpiCard accent={CV_COLORS.orange} label="Flagged plates" value={String(flagged)} />
          <KpiCard accent={CV_COLORS.green} label="Cleared" value={String(cleared)} />
        </div>
      </div>

      {/* Filters */}
      <div className="px-4 sm:px-6 pb-3">
        <div className="flex flex-row flex-wrap items-center gap-2 sm:gap-3">
          <FilterSelect value={statusFilter} options={['All status', 'Active', 'Flagged', 'Cleared']} onChange={v => { setStatusFilter(v); setPage(0); }} />
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: CV_COLORS.muted }} />
            <input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Search plates…"
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
                <th className="px-6 py-3 font-medium">Plate</th>
                <th className="px-4 py-3 font-medium">Type</th>
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
              {paged.map(v => (
                <tr key={v.id} onClick={() => setSelected(v)} className="cursor-pointer hover:bg-white/[.03] transition-colors"
                  style={{ borderBottom: `1px solid ${CV_COLORS.border}`, ...(selected?.id === v.id ? { borderLeft: `3px solid ${CV_COLORS.cyan}` } : {}) }}>
                  <td className="px-6 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded flex items-center justify-center" style={{ background: `${CV_COLORS.purple}22` }}>
                        <Car className="w-4 h-4" style={{ color: CV_COLORS.purple }} />
                      </div>
                      <span className="font-mono font-medium text-sm">{v.plateNumber}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{v.vehicleType}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{fmt(v.firstSeen)}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{fmt(v.lastSeen)}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: CV_COLORS.fg2 }}>{v.camera}</td>
                  <td className="px-4 py-3 tabular-nums font-medium">{v.sightings}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 rounded-full" style={{ background: CV_COLORS.track }}>
                        <div className="h-full rounded-full" style={{ width: `${v.confidence}%`, background: v.confidence >= 80 ? CV_COLORS.green : v.confidence >= 60 ? CV_COLORS.orange : CV_COLORS.muted }} />
                      </div>
                      <span className="text-xs tabular-nums" style={{ color: CV_COLORS.fg2 }}>{v.confidence}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3"><StatusChip status={v.status} /></td>
                  <td className="px-4 py-3">
                    <button onClick={e => { e.stopPropagation(); setSelected(v); }} className="text-xs font-medium" style={{ color: CV_COLORS.cyan }}>View</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Mobile cards */}
          <div className="md:hidden px-4 space-y-2 pb-4">
            {paged.map(v => (
              <div key={v.id} onClick={() => setSelected(v)} className="rounded-lg p-3 cursor-pointer" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Car className="w-4 h-4" style={{ color: CV_COLORS.purple }} />
                    <span className="font-mono font-medium text-sm" style={{ color: CV_COLORS.fg }}>{v.plateNumber}</span>
                  </div>
                  <StatusChip status={v.status} />
                </div>
                <div className="flex items-center gap-4 text-[12px]" style={{ color: CV_COLORS.fg2 }}>
                  <span className="flex items-center gap-1"><Camera className="w-3 h-3" />{v.camera}</span>
                  <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{v.zone}</span>
                  <span className="ml-auto tabular-nums">{v.sightings} sightings</span>
                </div>
              </div>
            ))}
          </div>

          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center h-40 gap-2" style={{ color: CV_COLORS.muted }}>
              <Car className="w-8 h-8 opacity-40" />
              <p className="text-sm">No vehicles found</p>
            </div>
          )}
        </div>

        {/* Detail drawer */}
        {selected && (
          <div className="w-full md:w-[400px] border-l overflow-y-auto flex-shrink-0" style={{ borderColor: CV_COLORS.border, background: CV_COLORS.panel }}>
            <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: CV_COLORS.border }}>
              <h2 className="text-sm font-semibold font-mono" style={{ color: CV_COLORS.fg }}>{selected.plateNumber}</h2>
              <button onClick={() => setSelected(null)} className="p-1 rounded hover:bg-white/5"><X className="w-4 h-4" style={{ color: CV_COLORS.muted }} /></button>
            </div>
            <div className="p-5 space-y-4">
              <div className="flex items-center gap-3">
                <div className="w-16 h-16 rounded-lg flex items-center justify-center" style={{ background: `${CV_COLORS.purple}22` }}>
                  <Car className="w-8 h-8" style={{ color: CV_COLORS.purple }} />
                </div>
                <div>
                  <StatusChip status={selected.status} />
                  <p className="text-xs mt-1" style={{ color: CV_COLORS.fg2 }}>{selected.vehicleType} · {selected.sightings} sightings · {selected.confidence}%</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>First seen</p><p style={{ color: CV_COLORS.fg2 }}>{fmt(selected.firstSeen)}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Last seen</p><p style={{ color: CV_COLORS.fg2 }}>{fmt(selected.lastSeen)}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Camera</p><p style={{ color: CV_COLORS.fg2 }}>{selected.camera}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Zone</p><p style={{ color: CV_COLORS.fg2 }}>{selected.zone}</p></div>
                <div><p className="text-[11px]" style={{ color: CV_COLORS.muted }}>Normalised</p><p className="font-mono" style={{ color: CV_COLORS.fg2 }}>{selected.plateNormalised}</p></div>
              </div>

              <div>
                <h3 className="text-xs font-semibold mb-2" style={{ color: CV_COLORS.fg }}>Sighting timeline</h3>
                <div className="space-y-2">
                  {selected.events.map((ev, i) => (
                    <div key={i} className="flex items-start gap-3 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: CV_COLORS.purple }} />
                      <div>
                        <p style={{ color: CV_COLORS.fg2 }}>{fmt(ev.time)}</p>
                        <p style={{ color: CV_COLORS.muted }}>{ev.camera} · {ev.plate}</p>
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
        <span>Page {page + 1} of {totalPages} · {filtered.length} vehicles</span>
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
    cleared: 'bg-[#19E58C]/15 text-[#19E58C]',
  };
  const icons: Record<StatusKey, typeof AlertTriangle> = { active: Eye, flagged: AlertTriangle, cleared: CheckCircle2 };
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
