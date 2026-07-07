import { TopBar } from '@/components/layout/TopBar';
import { CameraGrid } from '@/components/camera/CameraGrid';

export function MonitoringView() {
  return (
    <div className="flex flex-col h-full">
      <TopBar />
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 overflow-auto">
          <CameraGrid />
        </div>
      </div>
    </div>
  );
}
