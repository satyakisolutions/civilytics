import { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Camera } from '@/types/monitoring';
import { useMonitoring } from '@/context/MonitoringContext';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Maximize2, AlertTriangle, Wifi, WifiOff } from 'lucide-react';

interface CameraMapViewProps {
  cameras: Camera[];
}

// Custom marker icons based on camera status
function createStatusIcon(status: Camera['status']): L.DivIcon {
  const colors: Record<Camera['status'], string> = {
    online: 'hsl(142, 71%, 45%)',
    offline: 'hsl(215, 20%, 55%)',
    warning: 'hsl(38, 92%, 50%)',
    alert: 'hsl(0, 72%, 51%)',
  };

  const color = colors[status];
  const isAlert = status === 'alert';
  const pulseClass = isAlert ? 'animation: pulse-marker 1.5s ease-in-out infinite;' : '';

  return L.divIcon({
    className: 'custom-camera-marker',
    html: `
      <div style="
        width: 32px; height: 32px;
        background: ${color};
        border: 3px solid rgba(255,255,255,0.9);
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.4), 0 0 12px ${color}80;
        display: flex; align-items: center; justify-content: center;
        ${pulseClass}
      ">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
          <circle cx="12" cy="13" r="3"/>
        </svg>
      </div>
      ${isAlert ? `<div style="
        position: absolute; top: -4px; left: -4px;
        width: 40px; height: 40px;
        border-radius: 50%;
        border: 2px solid ${color};
        opacity: 0.6;
        animation: ping-marker 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;
      "></div>` : ''}
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -20],
  });
}

// Auto-fit map to markers
function FitBounds({ cameras }: { cameras: Camera[] }) {
  const map = useMap();

  useEffect(() => {
    const withGps = cameras.filter(c => c.gps);
    if (withGps.length === 0) return;

    const bounds = L.latLngBounds(
      withGps.map(c => [c.gps!.lat, c.gps!.lng] as L.LatLngTuple)
    );
    map.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
  }, [cameras, map]);

  return null;
}

export function CameraMapView({ cameras }: CameraMapViewProps) {
  const { setSelectedCamera, setGridLayout, setCurrentView } = useMonitoring();

  const camerasWithGps = cameras.filter(c => c.gps);

  // Default center (will be overridden by FitBounds)
  const defaultCenter: [number, number] = camerasWithGps.length > 0
    ? [camerasWithGps[0].gps!.lat, camerasWithGps[0].gps!.lng]
    : [0, 0];

  const statusLabels: Record<Camera['status'], string> = {
    online: 'Online',
    offline: 'Offline',
    warning: 'Warning',
    alert: 'Alert',
  };

  if (camerasWithGps.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-2 p-8">
        <WifiOff className="w-12 h-12 opacity-50" />
        <p className="text-lg font-medium">No GPS Data Available</p>
        <p className="text-sm text-center max-w-md">
          Add GPS coordinates to your cameras in Camera Management to see them on the map.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 relative rounded-lg overflow-hidden border border-border m-3">
      <style>{`
        .custom-camera-marker { background: transparent; border: none; }
        @keyframes ping-marker {
          75%, 100% { transform: scale(2); opacity: 0; }
        }
        @keyframes pulse-marker {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.1); }
        }
        .leaflet-popup-content-wrapper {
          background: hsl(222, 47%, 8%) !important;
          color: hsl(210, 40%, 96%) !important;
          border: 1px solid hsl(217, 33%, 18%) !important;
          border-radius: 8px !important;
          box-shadow: 0 4px 20px rgba(0,0,0,0.5) !important;
        }
        .leaflet-popup-tip {
          background: hsl(222, 47%, 8%) !important;
          border: 1px solid hsl(217, 33%, 18%) !important;
        }
        .leaflet-popup-close-button {
          color: hsl(215, 20%, 55%) !important;
        }
        .leaflet-control-zoom a {
          background: hsl(222, 47%, 10%) !important;
          color: hsl(210, 40%, 96%) !important;
          border-color: hsl(217, 33%, 18%) !important;
        }
        .leaflet-control-zoom a:hover {
          background: hsl(217, 33%, 17%) !important;
        }
      `}</style>
      <MapContainer
        center={defaultCenter}
        zoom={13}
        className="w-full h-full min-h-[500px]"
        style={{ background: 'hsl(222, 47%, 6%)' }}
        zoomControl={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        />
        <FitBounds cameras={cameras} />
        
        {camerasWithGps.map(camera => (
          <Marker
            key={camera.id}
            position={[camera.gps!.lat, camera.gps!.lng]}
            icon={createStatusIcon(camera.status)}
          >
            <Popup>
              <div className="min-w-[200px] space-y-2 p-1">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-sm">{camera.name}</span>
                  <span className={cn(
                    'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase',
                    camera.status === 'online' && 'bg-success/20 text-success',
                    camera.status === 'alert' && 'bg-destructive/20 text-destructive',
                    camera.status === 'warning' && 'bg-warning/20 text-warning',
                    camera.status === 'offline' && 'bg-muted text-muted-foreground'
                  )}>
                    {camera.status === 'alert' && <AlertTriangle className="w-3 h-3" />}
                    {statusLabels[camera.status]}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground space-y-0.5">
                  <p>Zone: {camera.zone}</p>
                  <p>Location: {camera.location}</p>
                  <p className="font-mono text-[10px]">
                    {camera.gps!.lat.toFixed(5)}, {camera.gps!.lng.toFixed(5)}
                  </p>
                </div>
                <button
                  onClick={() => {
                    setSelectedCamera(camera.id);
                    setGridLayout('1x1');
                    setCurrentView('monitoring');
                  }}
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-medium rounded transition-colors"
                >
                  <Maximize2 className="w-3 h-3" />
                  View Camera
                </button>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>

      {/* Legend */}
      <div className="absolute bottom-4 left-4 z-[1000] bg-card/90 backdrop-blur-sm border border-border rounded-lg p-3 space-y-1.5">
        <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider">Status</span>
        {(['online', 'warning', 'alert', 'offline'] as const).map(status => {
          const count = camerasWithGps.filter(c => c.status === status).length;
          if (count === 0) return null;
          return (
            <div key={status} className="flex items-center gap-2 text-xs">
              <span className={cn(
                'w-2.5 h-2.5 rounded-full',
                status === 'online' && 'bg-success',
                status === 'warning' && 'bg-warning',
                status === 'alert' && 'bg-destructive',
                status === 'offline' && 'bg-muted-foreground',
              )} />
              <span className="text-foreground/80">{statusLabels[status]}</span>
              <span className="text-muted-foreground font-mono ml-auto">{count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
