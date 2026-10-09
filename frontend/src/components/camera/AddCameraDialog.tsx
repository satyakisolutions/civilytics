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

function MapPicker({ lat, lng, onSelect, onAddress }: {
  lat: number | null;
  lng: number | null;
  onSelect: (lat: number, lng: number) => void;
  onAddress?: (address: string) => void;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const markerRef = useRef<any>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<{ display_name: string; lat: string; lon: string }[]>([]);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const placeMarker = useCallback((map: any, L: any, newLat: number, newLng: number) => {
    if (markerRef.current) {
      markerRef.current.setLatLng([newLat, newLng]);
    } else {
      markerRef.current = L.marker([newLat, newLng]).addTo(map);
    }
    map.setView([newLat, newLng], 16);
  }, []);

  const initMap = useCallback(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    const L = (window as any).L;
    if (!L) return;

    const center: [number, number] = [lat ?? 12.9716, lng ?? 77.5946];
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
      const rLat = parseFloat(newLat.toFixed(6));
      const rLng = parseFloat(newLng.toFixed(6));
      onSelect(rLat, rLng);
      placeMarker(map, L, rLat, rLng);
    });

    mapInstanceRef.current = map;
  }, [lat, lng, onSelect, placeMarker]);

  useEffect(() => {
    if ((window as any).L) { initMap(); return; }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(link);
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = () => initMap();
    document.head.appendChild(script);
    return () => {
      if (mapInstanceRef.current) { mapInstanceRef.current.remove(); mapInstanceRef.current = null; markerRef.current = null; }
    };
  }, [initMap]);

  const searchAddress = useCallback((q: string) => {
    if (q.length < 3) { setResults([]); return; }
    setSearching(true);
    fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(q)}&email=neeraj.sj@satyaki.co.in`)
      .then(r => r.json())
      .then(setResults)
      .catch(() => setResults([]))
      .finally(() => setSearching(false));
  }, []);

  const handleQueryChange = (v: string) => {
    setQuery(v);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => searchAddress(v), 400);
  };

  const pickResult = (r: { display_name: string; lat: string; lon: string }) => {
    const rLat = parseFloat(parseFloat(r.lat).toFixed(6));
    const rLng = parseFloat(parseFloat(r.lon).toFixed(6));
    onSelect(rLat, rLng);
    onAddress?.(r.display_name);
    setQuery(r.display_name);
    setResults([]);
    if (mapInstanceRef.current && (window as any).L) {
      placeMarker(mapInstanceRef.current, (window as any).L, rLat, rLng);
    }
  };

  return (
    <div className="space-y-2">
      <div className="relative">
        <Input
          placeholder="Search address or place name…"
          value={query}
          onChange={e => handleQueryChange(e.target.value)}
          className="text-sm"
        />
        {(results.length > 0 || searching) && (
          <div className="absolute z-[9999] top-full left-0 right-0 mt-1 bg-popover border border-border rounded-md shadow-lg max-h-48 overflow-y-auto">
            {searching && <div className="px-3 py-2 text-xs text-muted-foreground">Searching…</div>}
            {results.map((r, i) => (
              <button
                key={i}
                type="button"
                onClick={() => pickResult(r)}
                className="w-full text-left px-3 py-2 text-xs hover:bg-accent truncate"
              >
                {r.display_name}
              </button>
            ))}
          </div>
        )}
      </div>
      <div
        ref={mapRef}
        style={{ width: '100%', height: 200, borderRadius: 6, border: '1px solid hsl(var(--border))', cursor: 'crosshair' }}
      />
    </div>
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
      <DialogContent className="sm:max-w-[480px] overflow-visible">
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
              <Label htmlFor="cam-stream">RTSP Stream URL</Label>
              <Input
                id="cam-stream"
                placeholder="rtsp://user:pass@192.168.1.10:554/stream"
                value={form.stream_url ?? ''}
                onChange={(e) => setForm({ ...form, stream_url: e.target.value })}
              />
            </div>

            <div className="grid gap-2">
              <Label>Location <span className="text-muted-foreground text-xs">(search address or click map)</span></Label>
              <MapPicker
                lat={gpsLat ? parseFloat(gpsLat) : null}
                lng={gpsLng ? parseFloat(gpsLng) : null}
                onSelect={(lat, lng) => { setGpsLat(String(lat)); setGpsLng(String(lng)); }}
                onAddress={(addr) => setForm(f => ({ ...f, location: addr }))}
              />
              {gpsLat && gpsLng && (
                <p className="text-[11px] text-muted-foreground">
                  {gpsLat}, {gpsLng}
                </p>
              )}
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
