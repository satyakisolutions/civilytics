import { TopBar } from '@/components/layout/TopBar';
import { CameraMapView } from '@/components/camera/CameraMapView';
import { useCameras } from '@/hooks/useCameras';
import { Loader2 } from 'lucide-react';

export function MapView() {
  const { cameras, isLoading } = useCameras();

  return (
    <div className="flex flex-col h-full">
      <div className="h-12 bg-card border-b border-border flex items-center px-4">
        <h1 className="text-sm font-semibold text-foreground">Camera Map</h1>
      </div>
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <CameraMapView cameras={cameras} />
      )}
    </div>
  );
}
