import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { CameraCreateInput } from '@/hooks/useCameras';
import { useToast } from '@/hooks/use-toast';

// ---------------------------------------------------------------------------
// Lightweight map picker — click on the map to set GPS coordinates.
// Uses a single <div> with Leaflet loaded from CDN (no npm dep).
// ---------------------------------------------------------------------------

function MapPicker({ lat, lng, onSelect }: {
  lat: number | null;
  lng: number | null;
  onSelect: (lat: number, lng: number) => void;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const markerRef = useRef<any>(null);

  const initMap = useCallback(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    const L = (window as any).L;
    if (!L) return;

    const center: [number, number] = [lat ?? 12.9716, lng ?? 77.5946]; // Default: Bangalore
    const map = L.map(mapRef.current).setView(center, 15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OSM',
      maxZoom: 19,
    }).addTo(map);

    if (lat != null && lng != null) {
      markerRef.current = L.marker(center).addTo(map);
    }

    map.on('click', (e: any) => {
      const { lat: newLat, lng: newLng } = e.latlng;
      onSelect(parseFloat(newLat.toFixed(6)), parseFloat(newLng.toFixed(6)));
      if (markerRef.current) {
        markerRef.current.setLatLng(e.latlng);
      } else {
        markerRef.current = L.marker(e.latlng).addTo(map);
      }
    });

    mapInstanceRef.current = map;
  }, [lat, lng, onSelect]);

  useEffect(() => {
    // Load Leaflet CSS + JS from CDN if not already loaded
    if ((window as any).L) {
      initMap();
      return;
    }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(link);

    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = () => initMap();
    document.head.appendChild(script);

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
        markerRef.current = null;
      }
    };
  }, [initMap]);

  return (
    <div
      ref={mapRef}
      style={{
        width: '100%',
        height: 200,
        borderRadius: 6,
        border: '1px solid hsl(var(--border))',
        cursor: 'crosshair',
      }}
    />
  );
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (input: CameraCreateInput) => Promise<unknown>;
}

const EMPTY: CameraCreateInput = {
  name: '',
  zone: '',
  location: '',
  stream_url: '',
};

export function AddCameraDialog({ open, onOpenChange, onAdd }: Props) {
  const [form, setForm] = useState<CameraCreateInput>(EMPTY);
  const [gpsLat, setGpsLat] = useState('');
  const [gpsLng, setGpsLng] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { toast } = useToast();

  const reset = () => {
    setForm(EMPTY);
    setGpsLat('');
    setGpsLng('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.zone.trim()) {
      toast({
        title: 'Missing fields',
        description: 'Name and Zone are required',
        variant: 'destructive',
      });
      return;
    }

    const payload: CameraCreateInput = {
      name: form.name.trim(),
      zone: form.zone.trim(),
      location: form.location?.trim() || undefined,
      stream_url: form.stream_url?.trim() || undefined,
      source_type: 'rtsp',
    };
    const lat = parseFloat(gpsLat);
    const lng = parseFloat(gpsLng);
    if (!Number.isNaN(lat)) payload.gps_lat = lat;
    if (!Number.isNaN(lng)) payload.gps_lng = lng;

    setSubmitting(true);
    try {
      await onAdd(payload);
      reset();
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Failed to add camera',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="sm:max-w-[480px]">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add Camera</DialogTitle>
            <DialogDescription>
              Register a new camera in the monitoring system. Only Name and Zone are required.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="cam-name">
                Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="cam-name"
                placeholder="e.g. Gate 1 North"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                autoFocus
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="cam-zone">
                Zone <span className="text-destructive">*</span>
              </Label>
              <Input
                id="cam-zone"
                placeholder="e.g. Zone A"
                value={form.zone}
                onChange={(e) => setForm({ ...form, zone: e.target.value })}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="cam-location">Location</Label>
              <Input
                id="cam-location"
                placeholder="e.g. Main entrance"
                value={form.location ?? ''}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="cam-stream">RTSP Stream URL</Label>
              <Input
                id="cam-stream"
                placeholder="rtsp://user:pass@192.168.1.10:554/stream"
                value={form.stream_url ?? ''}
                onChange={(e) => setForm({ ...form, stream_url: e.target.value })}
              />
            </div>

            <div className="grid gap-2">
              <Label>GPS Location <span className="text-muted-foreground text-xs">(click on map to set)</span></Label>
              <MapPicker
                lat={gpsLat ? parseFloat(gpsLat) : null}
                lng={gpsLng ? parseFloat(gpsLng) : null}
                onSelect={(lat, lng) => { setGpsLat(String(lat)); setGpsLng(String(lng)); }}
              />
              <div className="grid grid-cols-2 gap-3">
                <Input
                  type="number"
                  step="any"
                  placeholder="Latitude"
                  value={gpsLat}
                  onChange={(e) => setGpsLat(e.target.value)}
                  className="text-xs h-7"
                />
                <Input
                  type="number"
                  step="any"
                  placeholder="Longitude"
                  value={gpsLng}
                  onChange={(e) => setGpsLng(e.target.value)}
                  className="text-xs h-7"
                />
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? 'Adding…' : 'Add Camera'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
