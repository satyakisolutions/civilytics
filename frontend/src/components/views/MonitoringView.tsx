import { TopBar } from '@/components/layout/TopBar';
import { CameraGrid } from '@/components/camera/CameraGrid';
import { DetectionStatsWidget } from '@/components/camera/DetectionStatsWidget';

export function MonitoringView() {
  return (
    <div className="flex flex-col h-full">
      <TopBar />
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 overflow-auto">
          <CameraGrid />
        </div>
        <div className="w-72 border-l border-border p-3 overflow-auto custom-scrollbar hidden lg:block">
          <DetectionStatsWidget />
        </div>
      </div>
    </div>
  );
}
