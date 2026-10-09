import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useCameras } from '@/hooks/useCameras';
import { useMonitoring } from '@/context/MonitoringContext';
import { Camera } from '@/types/monitoring';
import { CV_COLORS } from '@/lib/cv-colors';
import { cn } from '@/lib/utils';
import { Loader2, MapPin, Maximize2, ChevronDown } from 'lucide-react';

const STATUS_COLORS: Record<Camera['status'], string> = {
  online: CV_COLORS.green,
  offline: CV_COLORS.muted,
  warning: CV_COLORS.orange,
  alert: '#ef4444',
};

function ensureLeaflet(): Promise<any> {
  const w = window as any;
  if (w.L) return Promise.resolve(w.L);
  return new Promise(resolve => {
    if (!document.querySelector('link[href*="leaflet"]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = () => resolve(w.L);
    document.head.appendChild(script);
  });
}

function makeIcon(status: Camera['status'], L: any) {
  const color = STATUS_COLORS[status];
  const pulse = status === 'alert' ? 'animation:pulse-m 1.5s ease-in-out infinite;' : '';
  return L.divIcon({
    className: '',
    html: `<div style="width:28px;height:28px;background:${color};border:2px solid rgba(255,255,255,.85);border-radius:50%;box-shadow:0 2px 8px ${color}60;display:flex;align-items:center;justify-content:center;${pulse}">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
    </div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -18],
  });
}

export function MapView() {
  const { cameras, isLoading } = useCameras();
  const { setSelectedCamera, setGridLayout, setCurrentView } = useMonitoring();
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  const [zoneFilter, setZoneFilter] = useState('All zones');
  const [statusFilter, setStatusFilter] = useState('All status');

  const zones = useMemo(() => {
    const s = new Set(cameras.map(c => c.zone).filter(Boolean));
    return ['All zones', ...Array.from(s).sort()];
  }, [cameras]);

  const filtered = useMemo(() => {
    return cameras.filter(c => {
      if (!c.gps) return false;
      if (zoneFilter !== 'All zones' && c.zone !== zoneFilter) return false;
      if (statusFilter !== 'All status' && c.status !== statusFilter.toLowerCase()) return false;
      return true;
    });
  }, [cameras, zoneFilter, statusFilter]);

  const initMap = useCallback(async () => {
    if (!mapRef.current || mapInstance.current) return;
    const L = await ensureLeaflet();
    const map = L.map(mapRef.current, { zoomControl: true }).setView([12.97, 77.59], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OSM', maxZoom: 19,
    }).addTo(map);
    mapInstance.current = map;
    // inject pulse animation
    if (!document.getElementById('map-pulse-css')) {
      const style = document.createElement('style');
      style.id = 'map-pulse-css';
      style.textContent = `@keyframes pulse-m{0%,100%{transform:scale(1)}50%{transform:scale(1.15)}}
        .leaflet-popup-content-wrapper{background:${CV_COLORS.panel}!important;color:${CV_COLORS.fg}!important;border:1px solid ${CV_COLORS.border}!important;border-radius:8px!important;box-shadow:0 4px 20px rgba(0,0,0,.5)!important}
        .leaflet-popup-tip{background:${CV_COLORS.panel}!important;border:1px solid ${CV_COLORS.border}!important}
        .leaflet-popup-close-button{color:${CV_COLORS.muted}!important}
        .leaflet-control-zoom a{background:${CV_COLORS.panel}!important;color:${CV_COLORS.fg}!important;border-color:${CV_COLORS.border}!important}`;
      document.head.appendChild(style);
    }
  }, []);

  useEffect(() => { if (!isLoading) initMap(); }, [isLoading, initMap]);

  useEffect(() => {
    const map = mapInstance.current;
    if (!map) return;
    const L = (window as any).L;
    if (!L) return;
    // clear old markers
    markersRef.current.forEach(m => map.removeLayer(m));
    markersRef.current = [];

    filtered.forEach(cam => {
      const marker = L.marker([cam.gps!.lat, cam.gps!.lng], { icon: makeIcon(cam.status, L) }).addTo(map);
      marker.bindPopup(`
        <div style="min-width:180px;font-family:system-ui">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <b style="font-size:13px">${cam.name}</b>
            <span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${STATUS_COLORS[cam.status]}22;color:${STATUS_COLORS[cam.status]};font-weight:600;text-transform:uppercase">${cam.status}</span>
          </div>
          <div style="font-size:11px;color:${CV_COLORS.fg2};line-height:1.6">
            Zone: ${cam.zone}<br/>
            ${cam.location ? `Location: ${cam.location}<br/>` : ''}
            <span style="font-family:monospace;font-size:10px">${cam.gps!.lat.toFixed(5)}, ${cam.gps!.lng.toFixed(5)}</span>
          </div>
          <button onclick="window.__cvMapViewCam__('${cam.id}')" style="width:100%;margin-top:8px;padding:6px;background:${CV_COLORS.cyan}22;color:${CV_COLORS.cyan};border:1px solid ${CV_COLORS.cyan}44;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">View Camera</button>
        </div>
      `);
      markersRef.current.push(marker);
    });

    if (filtered.length > 0) {
      const bounds = L.latLngBounds(filtered.map(c => [c.gps!.lat, c.gps!.lng]));
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    }
  }, [filtered]);

  // global callback for popup button
  useEffect(() => {
    (window as any).__cvMapViewCam__ = (id: string) => {
      setSelectedCamera(id);
      setGridLayout('1x1');
      setCurrentView('monitoring');
    };
    return () => { delete (window as any).__cvMapViewCam__; };
  }, [setSelectedCamera, setGridLayout, setCurrentView]);

  const withGps = cameras.filter(c => c.gps);
  const onlineCount = withGps.filter(c => c.status === 'online').length;

  return (
    <div className="flex flex-col h-full" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h1 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>Camera Map</h1>
            <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>
              {withGps.length} cameras mapped · {onlineCount} online
            </p>
          </div>
          <div className="flex items-center gap-2">
            <FilterSelect value={zoneFilter} options={zones} onChange={setZoneFilter} />
            <FilterSelect value={statusFilter} options={['All status', 'Online', 'Warning', 'Alert', 'Offline']} onChange={setStatusFilter} />
          </div>
        </div>
      </div>

      {/* Map */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin" style={{ color: CV_COLORS.muted }} />
        </div>
      ) : withGps.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 p-8">
          <MapPin className="w-12 h-12 opacity-40" style={{ color: CV_COLORS.muted }} />
          <p className="text-sm font-medium" style={{ color: CV_COLORS.fg2 }}>No GPS data available</p>
          <p className="text-xs text-center max-w-sm" style={{ color: CV_COLORS.muted }}>
            Add GPS coordinates to cameras in Camera Management to see them here.
          </p>
        </div>
      ) : (
        <div className="flex-1 relative min-h-[300px]">
          <div ref={mapRef} style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 1 }} />
          {/* Legend */}
          <div className="absolute bottom-4 left-4 z-[1000] rounded-lg p-3 space-y-1.5 backdrop-blur-sm" style={{ background: `${CV_COLORS.panel}dd`, border: `1px solid ${CV_COLORS.border}` }}>
            <span className="text-[10px] uppercase font-semibold tracking-wider" style={{ color: CV_COLORS.muted }}>Status</span>
            {(['online', 'warning', 'alert', 'offline'] as const).map(s => {
              const count = filtered.filter(c => c.status === s).length;
              if (!count) return null;
              return (
                <div key={s} className="flex items-center gap-2 text-xs">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: STATUS_COLORS[s] }} />
                  <span style={{ color: CV_COLORS.fg2 }}>{s.charAt(0).toUpperCase() + s.slice(1)}</span>
                  <span className="font-mono ml-auto" style={{ color: CV_COLORS.muted }}>{count}</span>
                </div>
              );
            })}
          </div>

          {/* Zone chips */}
          {zoneFilter === 'All zones' && (
            <div className="absolute top-4 right-4 z-[1000] flex flex-col gap-1.5">
              {Array.from(new Set(filtered.map(c => c.zone))).sort().map(zone => {
                const count = filtered.filter(c => c.zone === zone).length;
                return (
                  <button key={zone} onClick={() => setZoneFilter(zone)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium backdrop-blur-sm cursor-pointer hover:brightness-110 transition-all"
                    style={{ background: `${CV_COLORS.panel}dd`, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
                    <MapPin className="w-3 h-3" style={{ color: CV_COLORS.cyan }} />
                    {zone}
                    <span className="font-mono" style={{ color: CV_COLORS.muted }}>{count}</span>
                  </button>
                );
              })}
            </div>
          )}
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
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium"
        style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
        {value} <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-[9999] rounded-md py-1 min-w-[140px] shadow-lg"
          style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          {options.map(o => (
            <button key={o} onClick={() => { onChange(o); setOpen(false); }}
              className={cn('w-full text-left px-3 py-1.5 text-xs hover:bg-white/5', o === value ? 'font-semibold' : '')}
              style={{ color: o === value ? CV_COLORS.cyan : CV_COLORS.fg2 }}>
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
