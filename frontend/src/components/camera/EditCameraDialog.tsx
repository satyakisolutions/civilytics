import { useState, useEffect } from 'react';
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
import { Camera } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';

interface Props {
  camera: Camera | null;
  onOpenChange: (open: boolean) => void;
  onSave: (id: string, updates: Partial<Camera> & { stream_url?: string }) => Promise<void>;
}

type FormState = {
  name: string;
  zone: string;
  location: string;
  streamUrl: string;
  gpsLat: string;
  gpsLng: string;
};

function toFormState(camera: Camera): FormState {
  return {
    name: camera.name,
    zone: camera.zone,
    location: camera.location ?? '',
    streamUrl: camera.streamUrl ?? '',
    gpsLat: camera.gps?.lat != null ? String(camera.gps.lat) : '',
    gpsLng: camera.gps?.lng != null ? String(camera.gps.lng) : '',
  };
}

export function EditCameraDialog({ camera, onOpenChange, onSave }: Props) {
  const [form, setForm] = useState<FormState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { toast } = useToast();

  // Re-sync form state whenever a different camera is opened for editing.
  useEffect(() => {
    if (camera) setForm(toFormState(camera));
  }, [camera]);

  const open = camera !== null && form !== null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!camera || !form) return;
    if (!form.name.trim() || !form.zone.trim()) {
      toast({
        title: 'Missing fields',
        description: 'Name and Zone are required',
        variant: 'destructive',
      });
      return;
    }

    const updates: Partial<Camera> & { stream_url?: string } = {
      name: form.name.trim(),
      zone: form.zone.trim(),
      location: form.location.trim(),
      streamUrl: form.streamUrl.trim(),
    };
    const lat = parseFloat(form.gpsLat);
    const lng = parseFloat(form.gpsLng);
    if (!Number.isNaN(lat) && !Number.isNaN(lng)) {
      updates.gps = { lat, lng };
    }

    setSubmitting(true);
    try {
      await onSave(camera.id, updates);
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Failed to update camera',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        {form && (
          <form onSubmit={handleSubmit}>
            <DialogHeader>
              <DialogTitle>Edit Camera</DialogTitle>
              <DialogDescription>Update this camera's details.</DialogDescription>
            </DialogHeader>

            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label htmlFor="edit-cam-name">
                  Name <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="edit-cam-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  autoFocus
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="edit-cam-zone">
                  Zone <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="edit-cam-zone"
                  value={form.zone}
                  onChange={(e) => setForm({ ...form, zone: e.target.value })}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="edit-cam-location">Location</Label>
                <Input
                  id="edit-cam-location"
                  value={form.location}
                  onChange={(e) => setForm({ ...form, location: e.target.value })}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="edit-cam-stream">RTSP Stream URL</Label>
                <Input
                  id="edit-cam-stream"
                  placeholder="rtsp://user:pass@192.168.1.10:554/stream"
                  value={form.streamUrl}
                  onChange={(e) => setForm({ ...form, streamUrl: e.target.value })}
                />
              </div>

              <div className="grid gap-2">
                <Label>GPS Location</Label>
                <div className="grid grid-cols-2 gap-3">
                  <Input
                    type="number"
                    step="any"
                    placeholder="Latitude"
                    value={form.gpsLat}
                    onChange={(e) => setForm({ ...form, gpsLat: e.target.value })}
                    className="text-xs h-8"
                  />
                  <Input
                    type="number"
                    step="any"
                    placeholder="Longitude"
                    value={form.gpsLng}
                    onChange={(e) => setForm({ ...form, gpsLng: e.target.value })}
                    className="text-xs h-8"
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
                {submitting ? 'Saving…' : 'Save Changes'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
