import { useMonitoring } from '@/context/MonitoringContext';
import { useCameras } from '@/hooks/useCameras';
import { useCameraOrder } from '@/hooks/useCameraOrder';
import { CameraFeed } from './CameraFeed';
import { SortableCameraFeed } from './SortableCameraFeed';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { ArrowLeft, Loader2, Camera as CameraIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  DragEndEvent,
  DragStartEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  rectSortingStrategy,
} from '@dnd-kit/sortable';

export function CameraGrid() {
  const { gridLayout, selectedCamera, setSelectedCamera, setGridLayout } = useMonitoring();
  const { cameras, isLoading } = useCameras();
  const { getZoneOrder, setZoneOrder } = useCameraOrder();

  const gridClasses: Record<string, string> = {
    '1x1': 'grid-cols-1',
    '2x2': 'grid-cols-1 sm:grid-cols-2',
    '3x3': 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
    '4x4': 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4',
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  // Flat sorted list: pinned first, then by status
  const orderedCameras = useMemo(() => {
    const sorted = [...cameras].sort((a, b) => {
      if (a.isPinned && !b.isPinned) return -1;
      if (!a.isPinned && b.isPinned) return 1;
      const statusOrder = { alert: 0, warning: 1, online: 2, offline: 3 };
      return statusOrder[a.status] - statusOrder[b.status];
    });

    // Apply saved order if available
    const savedOrder = getZoneOrder('__all__');
    if (!savedOrder || savedOrder.length === 0) return sorted;

    const orderMap = new Map(savedOrder.map((id, idx) => [id, idx]));
    return [...sorted].sort((a, b) => {
      const aIdx = orderMap.get(a.id) ?? 999;
      const bIdx = orderMap.get(b.id) ?? 999;
      return aIdx - bIdx;
    });
  }, [cameras, getZoneOrder]);

  const [activeId, setActiveId] = useState<string | null>(null);
  const activeCamera = activeId ? orderedCameras.find(c => c.id === activeId) : null;

  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(String(event.active.id));
  };

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = orderedCameras.findIndex(c => c.id === active.id);
    const newIndex = orderedCameras.findIndex(c => c.id === over.id);
    const reordered = arrayMove(orderedCameras, oldIndex, newIndex);
    setZoneOrder('__all__', reordered.map(c => c.id));
  };

  // Fullscreen single camera view
  if (gridLayout === '1x1' && selectedCamera) {
    const camera = cameras.find(c => c.id === selectedCamera);
    if (camera) {
      return (
        <div className="flex-1 p-4 flex flex-col">
          <div className="mb-4">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSelectedCamera(null);
                setGridLayout('3x3');
              }}
              className="gap-2"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Grid
            </Button>
          </div>
          <div className="flex-1">
            <CameraFeed camera={camera} isFullscreen />
          </div>
        </div>
      );
    }
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (cameras.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
        <p className="text-lg">No cameras configured</p>
        <p className="text-sm">Add cameras in the Camera Management section</p>
      </div>
    );
  }

  return (
    <div className="flex-1 p-3 overflow-auto custom-scrollbar">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={orderedCameras.map(c => c.id)}
          strategy={rectSortingStrategy}
        >
          <div className={cn('grid gap-3', gridClasses[gridLayout])}>
            {orderedCameras.map(camera => (
              <SortableCameraFeed key={camera.id} camera={camera} />
            ))}
          </div>
        </SortableContext>
        <DragOverlay>
          {activeCamera && (
            // A static placeholder, not a live CameraFeed — CameraFeed
            // mounts its own WebRTCPlayer (a real RTCPeerConnection/WHEP
            // session). SortableCameraFeed only CSS-hides the original
            // during drag rather than unmounting it, so a second live
            // CameraFeed here briefly doubled that camera's WebRTC
            // connection (and mediamtx session) for the drag's duration.
            <div className="shadow-2xl ring-2 ring-primary rounded-md overflow-hidden aspect-video bg-muted flex flex-col items-center justify-center gap-2 w-64">
              <CameraIcon className="w-8 h-8 text-muted-foreground" />
              <span className="text-sm font-medium text-foreground truncate px-2">{activeCamera.name}</span>
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
