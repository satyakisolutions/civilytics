import { Camera as CameraIcon, WifiOff, SignalZero, Tv } from 'lucide-react';
import { CameraStatus } from '@/types/monitoring';
import { cn } from '@/lib/utils';

interface CameraPlaceholderProps {
  status: CameraStatus;
  cameraName: string;
}

export function CameraPlaceholder({ status, cameraName }: CameraPlaceholderProps) {
  const isOffline = status === 'offline';

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-br from-muted/80 to-muted">
      {/* Scanline overlay */}
      <div className="absolute inset-0 bg-scanline opacity-30 pointer-events-none" />

      {/* Grid pattern */}
      <div className="absolute inset-0 bg-grid-pattern opacity-10" />

      {/* Static noise effect */}
      <div className={cn(
        'absolute inset-0 opacity-[0.03]',
        isOffline && 'opacity-[0.06]'
      )} style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.5'/%3E%3C/svg%3E")`,
        backgroundSize: '150px 150px',
      }} />

      {/* Center icon */}
      <div className="relative z-10 flex flex-col items-center gap-2 sm:gap-3">
        <div className={cn(
          'w-10 h-10 sm:w-16 sm:h-16 rounded-full border-2 flex items-center justify-center',
          isOffline
            ? 'border-muted-foreground/30 bg-muted-foreground/5'
            : 'border-primary/30 bg-primary/5'
        )}>
          {isOffline ? (
            <WifiOff className="w-4 h-4 sm:w-7 sm:h-7 text-muted-foreground/60" />
          ) : (
            <Tv className="w-4 h-4 sm:w-7 sm:h-7 text-primary/60" />
          )}
        </div>

        <div className="text-center space-y-0.5 sm:space-y-1">
          <p className={cn(
            'text-[9px] sm:text-[11px] font-semibold uppercase tracking-wider',
            isOffline ? 'text-muted-foreground/60' : 'text-primary/60'
          )}>
            {isOffline ? 'No Signal' : 'No Stream'}
          </p>
          <p className="text-[8px] sm:text-[10px] text-muted-foreground/40 font-mono truncate max-w-[120px] sm:max-w-none">
            {cameraName}
          </p>
        </div>
      </div>

      {/* Corner markers — surveillance style */}
      <div className="absolute top-3 left-3 w-4 h-4 border-l-2 border-t-2 border-muted-foreground/20" />
      <div className="absolute top-3 right-3 w-4 h-4 border-r-2 border-t-2 border-muted-foreground/20" />
      <div className="absolute bottom-3 left-3 w-4 h-4 border-l-2 border-b-2 border-muted-foreground/20" />
      <div className="absolute bottom-3 right-3 w-4 h-4 border-r-2 border-b-2 border-muted-foreground/20" />
    </div>
  );
}
