import { useState } from 'react';
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

            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="cam-lat">GPS Latitude</Label>
                <Input
                  id="cam-lat"
                  type="number"
                  step="any"
                  placeholder="12.9716"
                  value={gpsLat}
                  onChange={(e) => setGpsLat(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="cam-lng">GPS Longitude</Label>
                <Input
                  id="cam-lng"
                  type="number"
                  step="any"
                  placeholder="77.5946"
                  value={gpsLng}
                  onChange={(e) => setGpsLng(e.target.value)}
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
