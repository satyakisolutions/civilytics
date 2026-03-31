import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Camera } from '@/types/monitoring';
import { CameraFeed } from './CameraFeed';
import { GripVertical } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SortableCameraFeedProps {
  camera: Camera;
  isDragging?: boolean;
}

export function SortableCameraFeed({ camera }: SortableCameraFeedProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: camera.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 50 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'relative group/sortable',
        isDragging && 'opacity-50 scale-[0.98]'
      )}
    >
      {/* Drag handle */}
      <button
        {...attributes}
        {...listeners}
        className="absolute top-2 left-1/2 -translate-x-1/2 z-30 opacity-0 group-hover/sortable:opacity-100 transition-opacity bg-background/80 backdrop-blur-sm rounded px-2 py-0.5 cursor-grab active:cursor-grabbing"
        title="Drag to reorder"
      >
        <GripVertical className="w-4 h-4 text-muted-foreground" />
      </button>
      <CameraFeed camera={camera} />
    </div>
  );
}
