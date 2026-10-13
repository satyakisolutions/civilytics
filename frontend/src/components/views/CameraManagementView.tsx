import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import {
  Camera as CameraIcon,
  Plus,
  Search,
  AlertTriangle,
  Map,
  ChevronDown,
  MoreVertical,
  Eye,
  Pencil,
  Trash2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';
import { useMonitoring } from '@/context/MonitoringContext';
import { useCameras } from '@/hooks/useCameras';
import { Camera, CameraStatus } from '@/types/monitoring';
import { API_BASE } from '@/lib/api-base';
import { AddCameraDialog } from '@/components/camera/AddCameraDialog';
import { parseGoogleMapsLatLng, isGoogleMapsShortLink } from '@/lib/google-maps-link';

type HealthLabel = 'Online' | 'Warning' | 'Offline';

function statusToHealth(s: CameraStatus): HealthLabel {
  if (s === 'online') return 'Online';
  if (s === 'warning' || s === 'alert') return 'Warning';
  return 'Offline';
}

function formatLastEvent(d?: Date): string {
  if (!d) return '—';
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

interface CameraEventCounts {
  [cameraId: string]: number;
}

export function CameraManagementView() {
  const [search, setSearch] = useState('');
  const [zone, setZone] = useState('All zones');
  const [health, setHealth] = useState('All health states');
  const [addOpen, setAddOpen] = useState(false);
  const [editCam, setEditCam] = useState<Camera | null>(null);
  const [menuOpen, setMenuOpen] = useState<string | null>(null);
  const [eventCounts, setEventCounts] = useState<CameraEventCounts>({});
  const { cameras, isLoading, addCamera, updateCamera, deleteCamera } = useCameras();
  const { setCurrentView, setSelectedCamera, setGridLayout } = useMonitoring();

  useEffect(() => {
    fetch(`${API_BASE}/api/analytics/by-camera`)
      .then(r => r.ok ? r.json() : [])
      .then((data: { camera_id: string; event_count: number }[]) => {
        const map: CameraEventCounts = {};
        for (const d of data) map[d.camera_id] = d.event_count;
        setEventCounts(map);
      })
      .catch(() => {});
  }, []);

  const zones = useMemo(() => {
    const set = new Set(cameras.map(c => c.zone));
    return ['All zones', ...Array.from(set).sort()];
  }, [cameras]);

  const filtered = useMemo(() => {
    let list = cameras;
    if (zone !== 'All zones') list = list.filter(c => c.zone === zone);
    if (health !== 'All health states') list = list.filter(c => statusToHealth(c.status) === health);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(c =>
        c.id.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        c.zone.toLowerCase().includes(q) ||
        c.location.toLowerCase().includes(q)
      );
    }
    return list;
  }, [cameras, search, zone, health]);

  const online = cameras.filter(c => c.status === 'online').length;
  const warning = cameras.filter(c => c.status === 'warning' || c.status === 'alert').length;
  const offline = cameras.filter(c => c.status === 'offline').length;
  const zoneCount = new Set(cameras.map(c => c.zone)).size;

  const offlineNames = cameras.filter(c => c.status === 'offline').map(c => c.name.split(' ')[0]).slice(0, 3).join(', ');
  const warningNames = cameras.filter(c => c.status === 'warning' || c.status === 'alert').map(c => c.name).slice(0, 2).join(', ');

  const handleViewCamera = (cam: Camera) => {
    setSelectedCamera(cam.id);
    setGridLayout('1x1');
    setCurrentView('monitoring');
  };

  const handleDelete = async (cam: Camera) => {
    if (!confirm(`Delete camera "${cam.name}"? This cannot be undone.`)) return;
    await deleteCamera(cam.id);
    setMenuOpen(null);
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-cv-bg">
      <AddCameraDialog open={addOpen} onOpenChange={setAddOpen} onAdd={addCamera} />
      {editCam && (
        <EditCameraDialog
          camera={editCam}
          onClose={() => setEditCam(null)}
          onSave={async (id, data) => { await updateCamera(id, data); setEditCam(null); }}
        />
      )}

      {/* Header */}
      <div className="px-4 sm:px-6 pt-4 sm:pt-5 pb-3">
        <div className="text-[11px] text-cv-muted mb-1">Camera Management</div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-cv-fg">Camera Management</h1>
            <p className="text-[13px] text-cv-fg2 mt-0.5">Monitor camera availability, stream quality and event coverage</p>
          </div>
          <button
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium bg-cv-cyan text-[#07111F] hover:brightness-110 transition-all flex-shrink-0"
          >
            <Plus className="w-4 h-4" />
            Add camera
          </button>
        </div>
      </div>

      {/* KPI Row */}
      <div className="px-4 sm:px-6 pb-4">
        <div className="grid grid-cols-4 gap-2 sm:gap-4">
          <KpiCard icon={CameraIcon} accent={CV_COLORS.green}  label="Cameras online" value={`${online} / ${cameras.length}`} note={warning > 0 ? `Includes ${warning} degraded stream${warning > 1 ? 's' : ''}` : 'All streams healthy'} />
          <KpiCard icon={AlertTriangle} accent={CV_COLORS.orange} label="Cameras offline" value={String(offline)} note={offlineNames || 'None'} />
          <KpiCard icon={AlertTriangle} accent={CV_COLORS.orange} label="Stream warnings" value={String(warning)} note={warningNames || 'None'} />
          <KpiCard icon={Map} accent={CV_COLORS.cyan} label="Zones covered" value={String(zoneCount)} note={`${cameras.length} cameras total`} />
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="px-4 sm:px-6 pb-3">
        <div className="flex flex-row flex-wrap items-center gap-2 sm:gap-3">
          <FilterSelect value={zone} options={zones} onChange={setZone} />
          <FilterSelect value={health} options={['All health states', 'Online', 'Warning', 'Offline']} onChange={setHealth} />
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-cv-muted" />
            <input
              type="text"
              placeholder="Search cameras or locations"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full h-[42px] pl-9 pr-3 rounded-md text-sm bg-cv-panel border border-cv-border text-cv-fg placeholder:text-cv-muted focus:outline-none focus:border-cv-cyan"
            />
          </div>
          <span className="text-xs text-cv-muted whitespace-nowrap self-center">{filtered.length} cameras</span>
        </div>
      </div>

      {/* Camera Table / Card List */}
      <div className="flex-1 min-h-0 overflow-auto px-4 sm:px-6 pb-6">
        <div className="rounded-lg border border-cv-border bg-cv-panel">
          {isLoading ? (
            <div className="flex items-center justify-center h-40 text-sm text-cv-muted">Loading cameras…</div>
          ) : cameras.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 gap-2 text-cv-muted">
              <CameraIcon className="w-10 h-10 opacity-30" />
              <p className="text-sm">No cameras configured</p>
              <button onClick={() => setAddOpen(true)} className="text-cv-cyan text-sm hover:underline">Add your first camera</button>
            </div>
          ) : (
            <>
              {/* Desktop Table */}
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-cv-border text-[11px] uppercase text-cv-muted">
                      <th className="text-left py-3 px-4 font-medium sticky left-0 bg-cv-panel z-10">Camera</th>
                      <th className="text-left py-3 px-4 font-medium">Zone</th>
                      <th className="text-left py-3 px-4 font-medium">Health</th>
                      <th className="text-right py-3 px-4 font-medium">Events</th>
                      <th className="text-right py-3 px-4 font-medium">Last event</th>
                      <th className="text-center py-3 px-4 font-medium">Stream</th>
                      <th className="text-right py-3 px-4 font-medium">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(cam => (
                      <tr key={cam.id} className="border-b border-cv-border last:border-b-0 hover:bg-white/[0.02] transition-colors" style={{ minHeight: 52 }}>
                        <td className="py-3 px-4 sticky left-0 bg-cv-panel z-10">
                          <div className="flex items-center gap-2.5">
                            <CameraIcon className="w-4 h-4 text-cv-fg2 flex-shrink-0" />
                            <div>
                              <div className="font-medium text-cv-fg">{cam.name}</div>
                              <div className="text-[11px] text-cv-muted">{cam.location}</div>
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-cv-fg2">{cam.zone}</td>
                        <td className="py-3 px-4">
                          <HealthChip health={statusToHealth(cam.status)} />
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums text-cv-fg">{eventCounts[cam.id] ?? 0}</td>
                        <td className="py-3 px-4 text-right tabular-nums text-cv-fg2">{formatLastEvent(cam.lastEvent)}</td>
                        <td className="py-3 px-4 text-center">
                          <StreamDot health={statusToHealth(cam.status)} />
                        </td>
                        <td className="py-3 px-4 text-right">
                          <ActionMenu cam={cam} open={menuOpen === cam.id} onToggle={() => setMenuOpen(menuOpen === cam.id ? null : cam.id)} onView={() => { handleViewCamera(cam); setMenuOpen(null); }} onEdit={() => { setEditCam(cam); setMenuOpen(null); }} onDelete={() => handleDelete(cam)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile Card List */}
              <div className="sm:hidden divide-y divide-cv-border">
                {filtered.map(cam => (
                  <div key={cam.id} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <CameraIcon className="w-4 h-4 text-cv-fg2" />
                        <span className="font-medium text-cv-fg">{cam.name}</span>
                        <HealthChip health={statusToHealth(cam.status)} />
                      </div>
                      <div className="flex items-center gap-2">
                        <button onClick={() => handleViewCamera(cam)} className="text-cv-cyan text-xs hover:underline">View</button>
                        <button onClick={() => setEditCam(cam)} className="text-cv-fg2 text-xs hover:underline">Edit</button>
                        <button onClick={() => handleDelete(cam)} className="text-[#FF6B6B] text-xs hover:underline">Delete</button>
                      </div>
                    </div>
                    <div className="text-[12px] text-cv-muted">{cam.location}</div>
                    <div className="flex gap-4 text-[11px] text-cv-fg2">
                      <span>Events: <span className="text-cv-fg tabular-nums">{eventCounts[cam.id] ?? 0}</span></span>
                      <span>Last: <span className="text-cv-fg tabular-nums">{formatLastEvent(cam.lastEvent)}</span></span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Footer */}
              <div className="px-4 py-3 border-t border-cv-border flex items-center justify-between text-[11px] text-cv-muted">
                <span>Showing {filtered.length} of {cameras.length} cameras</span>
                <span>Counts retained for offline cameras</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, accent, label, value, note }: { icon: React.ElementType; accent: string; label: string; value: string; note: string }) {
  return (
    <div className="rounded-lg p-2 sm:p-[18px_20px] h-auto sm:h-[125px] flex flex-col justify-between relative bg-cv-panel border border-cv-border">
      <div>
        <p className="text-[10px] sm:text-xs mb-0.5 sm:mb-1 text-cv-fg2 truncate">{label}</p>
        <p className="text-base sm:text-[28px] font-bold tabular-nums text-cv-fg">{value}</p>
      </div>
      <p className="hidden sm:block text-[11px] text-cv-muted">{note}</p>
      <Icon className="hidden sm:block absolute top-[18px] right-5 w-5 h-5" style={{ color: accent }} />
    </div>
  );
}

function HealthChip({ health }: { health: HealthLabel }) {
  const styles: Record<HealthLabel, string> = {
    Online:  'bg-[#19E58C]/15 text-[#19E58C]',
    Warning: 'bg-[#FF9A2E]/15 text-[#FF9A2E]',
    Offline: 'bg-[#64768E]/15 text-[#64768E]',
  };
  return (
    <span className={cn('inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium', styles[health])}>
      {health}
    </span>
  );
}

function StreamDot({ health }: { health: HealthLabel }) {
  if (health === 'Offline') return <span className="inline-block w-2 h-2 rounded-full bg-cv-muted" />;
  if (health === 'Warning') return <span className="inline-block w-2 h-2 rounded-full bg-[#FF9A2E] animate-pulse" />;
  return <span className="inline-block w-2 h-2 rounded-full bg-[#19E58C]" />;
}

function EditCameraDialog({ camera, onClose, onSave }: { camera: Camera; onClose: () => void; onSave: (id: string, data: Record<string, unknown>) => Promise<void> }) {
  const [name, setName] = useState(camera.name);
  const [zoneName, setZoneName] = useState(camera.zone);
  const [location, setLocation] = useState(camera.location);
  const [streamUrl, setStreamUrl] = useState(camera.streamUrl ?? '');
  const [gpsLat, setGpsLat] = useState(camera.gps?.lat != null ? String(camera.gps.lat) : '');
  const [gpsLng, setGpsLng] = useState(camera.gps?.lng != null ? String(camera.gps.lng) : '');
  // Per-camera detection thresholds (cameras.detection_config). Blank/absent
  // means fall back to the workspace/pipeline default. Defaults mirror the
  // pipeline's own (dwell 15s, proximity window 300s, proximity 120px).
  const dc = camera.detectionConfig;
  const [dwell, setDwell] = useState(dc?.dwell_violation_threshold_s != null ? String(dc.dwell_violation_threshold_s) : '15');
  const [proxWin, setProxWin] = useState(dc?.waste_proximity_alert_window_s != null ? String(dc.waste_proximity_alert_window_s) : '300');
  const [proxPx, setProxPx] = useState(dc?.waste_proximity_px != null ? String(dc.waste_proximity_px) : '120');
  const [saving, setSaving] = useState(false);
  const [mapQuery, setMapQuery] = useState('');
  const [mapResults, setMapResults] = useState<{ display_name: string; lat: string; lon: string }[]>([]);
  const [mapSearching, setMapSearching] = useState(false);
  const [gmapLink, setGmapLink] = useState('');
  const [gmapError, setGmapError] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const markerRef = useRef<any>(null);

  const placeMarker = useCallback((lat: number, lng: number) => {
    const L = (window as any).L;
    const map = mapInstanceRef.current;
    if (!L || !map) return;
    if (markerRef.current) markerRef.current.setLatLng([lat, lng]);
    else markerRef.current = L.marker([lat, lng]).addTo(map);
    map.setView([lat, lng], 16);
  }, []);

  useEffect(() => {
    if (!mapRef.current) return;
    const tryInit = () => {
      const L = (window as any).L;
      if (!L || mapInstanceRef.current) return;
      const lat = gpsLat ? parseFloat(gpsLat) : 12.9716;
      const lng = gpsLng ? parseFloat(gpsLng) : 77.5946;
      const map = L.map(mapRef.current!).setView([lat, lng], 15);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OSM', maxZoom: 19 }).addTo(map);
      if (gpsLat && gpsLng) markerRef.current = L.marker([lat, lng]).addTo(map);
      map.on('click', (e: any) => {
        const rLat = parseFloat(e.latlng.lat.toFixed(6));
        const rLng = parseFloat(e.latlng.lng.toFixed(6));
        setGpsLat(String(rLat));
        setGpsLng(String(rLng));
        if (markerRef.current) markerRef.current.setLatLng([rLat, rLng]);
        else markerRef.current = L.marker([rLat, rLng]).addTo(map);
      });
      mapInstanceRef.current = map;
    };
    if ((window as any).L) tryInit();
    else {
      const iv = setInterval(() => { if ((window as any).L) { clearInterval(iv); tryInit(); } }, 200);
      return () => clearInterval(iv);
    }
    return () => { if (mapInstanceRef.current) { mapInstanceRef.current.remove(); mapInstanceRef.current = null; markerRef.current = null; } };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const searchAddress = (q: string) => {
    if (q.length < 3) { setMapResults([]); return; }
    setMapSearching(true);
    fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(q)}&email=neeraj.sj@satyaki.co.in`)
      .then(r => r.json()).then(setMapResults).catch(() => setMapResults([])).finally(() => setMapSearching(false));
  };

  const handleQueryChange = (v: string) => {
    setMapQuery(v);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => searchAddress(v), 400);
  };

  const pickResult = (r: { display_name: string; lat: string; lon: string }) => {
    const rLat = parseFloat(parseFloat(r.lat).toFixed(6));
    const rLng = parseFloat(parseFloat(r.lon).toFixed(6));
    setGpsLat(String(rLat)); setGpsLng(String(rLng));
    setLocation(r.display_name);
    setMapQuery(r.display_name); setMapResults([]);
    placeMarker(rLat, rLng);
  };

  const useGmapLink = () => {
    if (isGoogleMapsShortLink(gmapLink)) {
      setGmapError("Short links can't be read here — open it in Google Maps, then paste the full URL from the address bar.");
      return;
    }
    const coords = parseGoogleMapsLatLng(gmapLink);
    if (!coords) { setGmapError("Couldn't find coordinates in that link."); return; }
    const rLat = parseFloat(coords.lat.toFixed(6));
    const rLng = parseFloat(coords.lng.toFixed(6));
    setGmapError('');
    setGpsLat(String(rLat)); setGpsLng(String(rLng));
    placeMarker(rLat, rLng);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const data: Record<string, unknown> = { name: name.trim(), zone: zoneName.trim(), location: location.trim(), stream_url: streamUrl.trim() || undefined };
      const lat = parseFloat(gpsLat); const lng = parseFloat(gpsLng);
      if (!Number.isNaN(lat)) data.gps_lat = lat;
      if (!Number.isNaN(lng)) data.gps_lng = lng;
      data.detectionConfig = {
        dwell_violation_threshold_s: Number(dwell),
        waste_proximity_alert_window_s: Number(proxWin),
        waste_proximity_px: Number(proxPx),
      };
      await onSave(camera.id, data);
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-cv-panel border border-cv-border rounded-lg p-6 w-full max-w-md space-y-4 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h2 className="text-base font-semibold text-cv-fg">Edit Camera</h2>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-cv-fg2 block mb-1">Name</label>
            <input value={name} onChange={e => setName(e.target.value)} className="w-full h-9 px-3 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
          </div>
          <div>
            <label className="text-xs text-cv-fg2 block mb-1">Zone</label>
            <input value={zoneName} onChange={e => setZoneName(e.target.value)} className="w-full h-9 px-3 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
          </div>
          <div>
            <label className="text-xs text-cv-fg2 block mb-1">RTSP Stream URL</label>
            <input value={streamUrl} onChange={e => setStreamUrl(e.target.value)} placeholder="rtsp://user:pass@192.168.1.10:554/stream" className="w-full h-9 px-3 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
          </div>
          <div>
            <label className="text-xs text-cv-fg2 block mb-1">Location <span className="text-cv-muted">(search or click map)</span></label>
            <div className="relative">
              <input placeholder="Search address…" value={mapQuery || location} onChange={e => { handleQueryChange(e.target.value); setLocation(e.target.value); }} className="w-full h-9 px-3 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
              {(mapResults.length > 0 || mapSearching) && (
                <div className="absolute z-[9999] top-full left-0 right-0 mt-1 bg-cv-panel border border-cv-border rounded-md shadow-lg max-h-48 overflow-y-auto">
                  {mapSearching && <div className="px-3 py-2 text-xs text-cv-muted">Searching…</div>}
                  {mapResults.map((r, i) => (
                    <button key={i} type="button" onClick={() => pickResult(r)} className="w-full text-left px-3 py-2 text-xs text-cv-fg hover:bg-white/5 truncate">{r.display_name}</button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2 mt-2">
              <input placeholder="Paste Google Maps link" value={gmapLink} onChange={e => { setGmapLink(e.target.value); setGmapError(''); }} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); useGmapLink(); } }} className="flex-1 h-9 px-3 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
              <button type="button" onClick={useGmapLink} className="px-3 py-1.5 rounded-md text-sm text-cv-fg2 border border-cv-border hover:bg-white/5 whitespace-nowrap">Use link</button>
            </div>
            {gmapError && <p className="text-[11px] text-[#FF6B6B] mt-1">{gmapError}</p>}
            <div ref={mapRef} className="mt-2" style={{ width: '100%', height: 180, borderRadius: 6, border: `1px solid ${CV_COLORS.border}`, cursor: 'crosshair' }} />
            {gpsLat && gpsLng && <p className="text-[11px] text-cv-muted mt-1">{gpsLat}, {gpsLng}</p>}
          </div>
          <div className="pt-1">
            <label className="text-xs text-cv-fg2 block mb-1">Detection thresholds <span className="text-cv-muted">(override workspace defaults)</span></label>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <input type="number" min={0} step="any" value={dwell} onChange={e => setDwell(e.target.value)} className="w-full h-9 px-2 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
                <span className="text-[10px] text-cv-muted">Dwell (s)</span>
              </div>
              <div>
                <input type="number" min={0} step="any" value={proxWin} onChange={e => setProxWin(e.target.value)} className="w-full h-9 px-2 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
                <span className="text-[10px] text-cv-muted">Proximity window (s)</span>
              </div>
              <div>
                <input type="number" min={0} step="any" value={proxPx} onChange={e => setProxPx(e.target.value)} className="w-full h-9 px-2 rounded-md text-sm bg-cv-bg border border-cv-border text-cv-fg focus:outline-none focus:border-cv-cyan" />
                <span className="text-[10px] text-cv-muted">Proximity (px)</span>
              </div>
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-cv-fg2 border border-cv-border hover:bg-white/5">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="px-3 py-1.5 rounded-md text-sm font-medium bg-cv-cyan text-[#07111F] hover:brightness-110 disabled:opacity-50">{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    </div>
  );
}

function ActionMenu({ cam, open, onToggle, onView, onEdit, onDelete }: { cam: Camera; open: boolean; onToggle: () => void; onView: () => void; onEdit: () => void; onDelete: () => void }) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  useEffect(() => {
    if (!open || !btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    const dropH = 100;
    const flipUp = rect.bottom + dropH > window.innerHeight;
    setPos({
      top: flipUp ? rect.top - dropH : rect.bottom + 4,
      left: rect.right - 144,
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // The menu is portaled/fixed but still part of this component. Treat both
    // the trigger and its menu as inside; otherwise mousedown unmounts the
    // menu before a View/Edit/Delete click can reach its button.
    const close = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!btnRef.current?.contains(target) && !menuRef.current?.contains(target)) onToggle();
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open, onToggle]);

  return (
    <div className="relative inline-block">
      <button ref={btnRef} onClick={onToggle} className="p-1 rounded hover:bg-white/5 text-cv-fg2">
        <MoreVertical className="w-4 h-4" />
      </button>
      {open && (
        <div ref={menuRef} className="fixed z-[9999] w-36 rounded-md border border-cv-border bg-cv-panel shadow-lg py-1" style={{ top: pos.top, left: pos.left }}>
          <button onClick={onView} className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-cv-fg hover:bg-white/5"><Eye className="w-3.5 h-3.5" />View</button>
          <button onClick={onEdit} className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-cv-fg hover:bg-white/5"><Pencil className="w-3.5 h-3.5" />Edit</button>
          <button onClick={onDelete} className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-[#FF6B6B] hover:bg-white/5"><Trash2 className="w-3.5 h-3.5" />Delete</button>
        </div>
      )}
    </div>
  );
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
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="h-[42px] px-3 pr-8 rounded-md text-sm bg-cv-panel border border-cv-border text-cv-fg cursor-pointer focus:outline-none focus:border-cv-cyan text-left whitespace-nowrap"
      >
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
