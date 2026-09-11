import { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, MapPin } from 'lucide-react';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  rectSortingStrategy,
} from '@dnd-kit/sortable';
import { Camera } from '@/types/monitoring';
import { SortableCameraFeed } from './SortableCameraFeed';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

interface ZoneCameraGroupProps {
  zone: string;
  cameras: Camera[];
  gridCols: string;
  defaultOpen?: boolean;
  cameraOrder?: string[];
  onReorder?: (zone: string, orderedIds: string[]) => void;
}

export function ZoneCameraGroup({ zone, cameras, gridCols, defaultOpen = true, cameraOrder, onReorder }: ZoneCameraGroupProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  // Apply saved order
  const orderedCameras = useMemo(() => {
    if (!cameraOrder || cameraOrder.length === 0) return cameras;
    const orderMap = new Map(cameraOrder.map((id, idx) => [id, idx]));
    return [...cameras].sort((a, b) => {
      const aIdx = orderMap.get(a.id) ?? 999;
      const bIdx = orderMap.get(b.id) ?? 999;
      return aIdx - bIdx;
    });
  }, [cameras, cameraOrder]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = orderedCameras.findIndex(c => c.id === active.id);
    const newIndex = orderedCameras.findIndex(c => c.id === over.id);
    const reordered = arrayMove(orderedCameras, oldIndex, newIndex);
    onReorder?.(zone, reordered.map(c => c.id));
  };

  const onlineCount = cameras.filter(c => c.status === 'online').length;
  const alertCount = cameras.filter(c => c.status === 'alert').length;
  const warningCount = cameras.filter(c => c.status === 'warning').length;

  return (
    <div className="space-y-2">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded-md hover:bg-accent/50 transition-colors group"
      >
        {isOpen ? (
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="w-4 h-4 text-muted-foreground" />
        )}
        <MapPin className="w-3.5 h-3.5 text-primary" />
        <span className="text-sm font-semibold text-foreground">{zone}</span>
        <span className="text-xs text-muted-foreground">({cameras.length})</span>

        <div className="flex items-center gap-1.5 ml-auto">
          {alertCount > 0 && (
            <Badge variant="destructive" className="h-5 px-1.5 text-[10px]">
              {alertCount} alert{alertCount > 1 ? 's' : ''}
            </Badge>
          )}
          {warningCount > 0 && (
            <Badge className="h-5 px-1.5 text-[10px] bg-warning/20 text-warning border-warning/30">
              {warningCount}
            </Badge>
          )}
          <span className="text-[10px] text-muted-foreground font-mono">
            {onlineCount}/{cameras.length} online
          </span>
        </div>
      </button>

      {isOpen && (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={orderedCameras.map(c => c.id)}
            strategy={rectSortingStrategy}
          >
            <div className={cn('grid gap-3', gridCols)}>
              {orderedCameras.map(camera => (
                <SortableCameraFeed key={camera.id} camera={camera} />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
}
