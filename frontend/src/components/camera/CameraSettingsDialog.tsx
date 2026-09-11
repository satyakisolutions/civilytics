import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Camera } from '@/types/monitoring';
import { CameraFeed } from './CameraFeed';

interface Props {
  camera: Camera | null;
  onOpenChange: (open: boolean) => void;
}

// Settings today = live ROI (detection zone) configuration, the one
// per-camera setting the backend actually acts on (roi_polygons via
// PUT /api/cameras/{id}/roi, consumed by the pipeline's ROISyncer).
// Reuses CameraFeed as-is rather than rebuilding video + ROIOverlay +
// useROIEditor wiring a second time -- CameraFeed's own "Draw ROI"
// dropdown item already does exactly this against live video.
export function CameraSettingsDialog({ camera, onOpenChange }: Props) {
  const open = camera !== null;

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
          // isFullscreen relies on an ancestor giving it real height via
          // h-full — a plain DialogContent isn't a flex container, so
          // without this explicit height+flex wrapper the feed collapses
          // to its min-h-[400px] floor (same bug fixed in MonitoringView.tsx).
          <div className="rounded-md overflow-hidden border border-border h-[500px] flex flex-col">
            <CameraFeed camera={camera} isFullscreen />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
