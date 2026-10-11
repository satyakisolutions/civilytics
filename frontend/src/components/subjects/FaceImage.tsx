import { User } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuthedImage } from '@/hooks/useAuthedImage';
import { subjectsApi } from '@/lib/subjectsApi';

interface FaceImageProps {
  faceId: string | null;
  className?: string;
  alt?: string;
}

export function FaceImage({ faceId, className, alt = 'Face' }: FaceImageProps) {
  const src = useAuthedImage(faceId ? subjectsApi.faceUrl(faceId) : null);
  if (!src) {
    return (
      <div className={cn('rounded border border-dashed border-border bg-muted flex items-center justify-center', className)}>
        <User className="w-1/3 h-1/3 text-muted-foreground" />
      </div>
    );
  }
  return <img src={src} alt={alt} className={cn('object-cover rounded border border-border', className)} />;
}
