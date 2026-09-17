import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Camera, CameraDetectionConfig } from '@/types/monitoring';
import { useCameras } from '@/hooks/useCameras';
import { CameraFeed } from './CameraFeed';

interface Props {
  camera: Camera | null;
  onOpenChange: (open: boolean) => void;
}

const DEFAULTS: Required<CameraDetectionConfig> = {
  dwell_enter_threshold_s: 1.5,
  dwell_violation_threshold_s: 3.0,
  waste_proximity_px: 60,
  bag_proximity_px: 15,
};

const FIELDS: { key: keyof CameraDetectionConfig; label: string; hint: string; step: number }[] = [
  {
    key: 'dwell_enter_threshold_s',
    label: 'Suspicious threshold (s)',
    hint: 'How long a person dwells in the ROI before the yellow "suspicious" alert fires.',
    step: 0.1,
  },
  {
    key: 'dwell_violation_threshold_s',
    label: 'Violation threshold (s)',
    hint: 'Dwell time before a non-person track (e.g. vehicle) escalates to a red violation.',
    step: 0.1,
  },
  {
    key: 'waste_proximity_px',
    label: 'Waste proximity (px)',
    hint: 'Max edge-to-edge distance between a waste object and a person to attribute the dump.',
    step: 1,
  },
  {
    key: 'bag_proximity_px',
    label: 'Bag proximity (px)',
    hint: 'Max edge-to-edge distance between a carried bag and a person to count as "carrying".',
    step: 1,
  },
];

// Settings = live ROI (detection zone) configuration (the CameraFeed
// below, reusing its own "Draw ROI" wiring) plus per-camera detection
// timing/proximity overrides (cameras.detection_config, read live by the
// pipeline's ROISyncer -- see pipeline/app/pipeline.py's `_dcfg.get(...)`
// calls). Missing keys fall back to the pipeline-wide defaults, so this
// form only needs to send the keys the operator actually changed.
export function CameraSettingsDialog({ camera, onOpenChange }: Props) {
  const open = camera !== null;
  const { updateCamera } = useCameras();
  const [values, setValues] = useState<CameraDetectionConfig>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValues(camera?.detectionConfig ?? {});
  }, [camera?.id, camera?.detectionConfig]);

  const handleChange = (key: keyof CameraDetectionConfig, raw: string) => {
    const num = raw === '' ? undefined : Number(raw);
    setValues((prev) => ({ ...prev, [key]: num }));
  };

  const handleSave = async () => {
    if (!camera) return;
    setSaving(true);
    try {
      await updateCamera(camera.id, { detectionConfig: values });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>Camera Settings — {camera?.name}</DialogTitle>
          <DialogDescription>
            Use "Draw ROI" below (on the camera feed's menu) to configure the
            detection zone this camera watches. Zones save immediately.
          </DialogDescription>
        </DialogHeader>
        {camera && (
          <>
            {/* isFullscreen relies on an ancestor giving it real height via
                h-full — a plain DialogContent isn't a flex container, so
                without this explicit height+flex wrapper the feed collapses
                to its min-h-[400px] floor (same bug fixed in MonitoringView.tsx). */}
            <div className="rounded-md overflow-hidden border border-border h-[500px] flex flex-col">
              <CameraFeed camera={camera} isFullscreen />
            </div>

            <div className="space-y-3 pt-2">
              <h3 className="text-sm font-semibold text-foreground">Detection tuning</h3>
              <p className="text-xs text-muted-foreground -mt-2">
                Leave a field blank to use the pipeline-wide default.
              </p>
              <div className="grid grid-cols-2 gap-4">
                {FIELDS.map((f) => (
                  <div key={f.key} className="space-y-1">
                    <Label htmlFor={f.key} className="text-xs">{f.label}</Label>
                    <Input
                      id={f.key}
                      type="number"
                      step={f.step}
                      placeholder={String(DEFAULTS[f.key])}
                      value={values[f.key] ?? ''}
                      onChange={(e) => handleChange(f.key, e.target.value)}
                      className="h-8 text-sm"
                    />
                    <p className="text-[10px] text-muted-foreground">{f.hint}</p>
                  </div>
                ))}
              </div>
              <div className="flex justify-end">
                <Button size="sm" onClick={handleSave} disabled={saving}>
                  {saving ? 'Saving…' : 'Save detection settings'}
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
