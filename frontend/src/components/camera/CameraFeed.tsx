import { useState, useEffect, useMemo, useRef } from 'react';
import { 
  Camera as CameraIcon, 
  Maximize2, 
  Pin, 
  Volume2, 
  VolumeX,
  MoreVertical,
  MapPin,
  AlertTriangle,
  ScanSearch,
  Loader2,
} from 'lucide-react';
import { Camera, CameraStatus } from '@/types/monitoring';
import { useMonitoring } from '@/context/MonitoringContext';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { WebRTCPlayer, WebRTCPlayerHandle } from './WebRTCPlayer';
import { CameraPlaceholder } from './CameraPlaceholder';
import { useMediaMTXConfig } from '@/hooks/useMediaMTXConfig';
import { useFrameAnalysis } from '@/hooks/useFrameAnalysis';

interface CameraFeedProps {
  camera: Camera;
  isFullscreen?: boolean;
}

export function CameraFeed({ camera, isFullscreen = false }: CameraFeedProps) {
  const { setSelectedCamera, setGridLayout } = useMonitoring();
  const { serverUrl, autoReconnect } = useMediaMTXConfig();
  const { analyzeFrame, isAnalyzing } = useFrameAnalysis();
  const playerRef = useRef<WebRTCPlayerHandle>(null);
  const [isHovered, setIsHovered] = useState(false);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [isStreamActive, setIsStreamActive] = useState(false);
  const [isMuted, setIsMuted] = useState(true);

  useEffect(() => {
    const interval = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  // Derive WebRTC stream path from camera data
  const streamPath = useMemo(() => {
    if (!camera.streamUrl) return null;
    
    // If it's already a path (e.g., "cam-zone-a-north"), use it directly
    if (!camera.streamUrl.includes('://')) {
      return camera.streamUrl;
    }
    
    // Generate a path based on camera zone and name
    const safeName = camera.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const safeZone = camera.zone.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    return `cam-${safeZone}-${safeName}`;
  }, [camera.streamUrl, camera.name, camera.zone]);

  const hasStreamUrl = Boolean(camera.streamUrl);

  const handleFullscreen = () => {
    setSelectedCamera(camera.id);
    setGridLayout('1x1');
  };

  const handleManualAnalysis = async () => {
    if (!playerRef.current) return;
    const frameData = playerRef.current.captureFrame();
    if (!frameData) return;
    const base64Data = frameData.replace(/^data:image\/\w+;base64,/, '');
    await analyzeFrame(camera, base64Data);
  };

  const statusConfig: Record<CameraStatus, { color: string; label: string; dotClass: string }> = {
    online: { color: 'text-success', label: 'LIVE', dotClass: 'status-dot-online' },
    offline: { color: 'text-muted-foreground', label: 'OFFLINE', dotClass: 'status-dot-offline' },
    warning: { color: 'text-warning', label: 'WARNING', dotClass: 'status-dot-warning' },
    alert: { color: 'text-destructive', label: 'ALERT', dotClass: 'status-dot-critical' },
  };

  const status = statusConfig[camera.status];

  return (
    <div
      className={cn(
        'camera-feed group flex flex-col',
        camera.status === 'alert' && 'alert',
        isFullscreen && 'h-full'
      )}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Camera View */}
      <div className={cn(
        'relative flex-1 bg-gradient-to-br from-muted/50 to-muted overflow-hidden',
        isFullscreen ? 'min-h-[400px]' : 'aspect-video'
      )}>
        {/* WebRTC Stream or Placeholder */}
        {hasStreamUrl && streamPath && camera.status !== 'offline' ? (
          <WebRTCPlayer
            ref={playerRef}
            streamPath={streamPath}
            serverUrl={serverUrl}
            className="absolute inset-0"
            onStreamReady={() => setIsStreamActive(true)}
            onStreamError={() => setIsStreamActive(false)}
            autoReconnect={autoReconnect}
            muted={isMuted}
          />
        ) : (
          <CameraPlaceholder status={camera.status} cameraName={camera.name} />
        )}
        
        {/* Overlay gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-background/80 via-transparent to-transparent pointer-events-none" />

        {/* Status indicator (top-left) */}
        <div className="absolute top-2 left-2 flex items-center gap-2 z-10">
          <div className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-semibold uppercase',
            camera.status === 'online' && 'bg-success/20 text-success',
            camera.status === 'alert' && 'bg-destructive/20 text-destructive',
            camera.status === 'warning' && 'bg-warning/20 text-warning',
            camera.status === 'offline' && 'bg-muted text-muted-foreground'
          )}>
            <span className={cn('status-dot', status.dotClass)} />
            {isStreamActive ? 'LIVE' : status.label}
          </div>

          {camera.status === 'alert' && (
            <div className="flex items-center gap-1 px-2 py-1 rounded bg-destructive/20 text-destructive text-[10px]">
              <AlertTriangle className="w-3 h-3" />
              VIOLATION
            </div>
          )}
        </div>

        {/* Camera ID (top-right) */}
        <div className="absolute top-2 right-2 flex items-center gap-1.5 z-10">
          {camera.isPinned && (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              <Pin className="w-2.5 h-2.5 mr-1" />
              PINNED
            </Badge>
          )}
          <Badge variant="secondary" className="h-5 px-1.5 text-[10px] font-mono">
            {camera.id.slice(0, 8).toUpperCase()}
          </Badge>
        </div>

        {/* Hover overlay with actions */}
        <div className={cn(
          'absolute inset-0 bg-background/40 backdrop-blur-sm transition-opacity duration-200 z-20',
          isHovered ? 'opacity-100' : 'opacity-0'
        )}>
          <div className="absolute inset-0 flex items-center justify-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              className="h-8"
              onClick={handleFullscreen}
            >
              <Maximize2 className="w-4 h-4 mr-1.5" />
              Expand
            </Button>
            {hasStreamUrl && (
              <Button
                variant="secondary"
                size="sm"
                className="h-8"
                onClick={() => setIsMuted(!isMuted)}
              >
                {isMuted ? (
                  <VolumeX className="w-4 h-4" />
                ) : (
                  <Volume2 className="w-4 h-4" />
                )}
              </Button>
            )}
            {isStreamActive && (
              <Button
                variant="secondary"
                size="sm"
                className="h-8"
                onClick={handleManualAnalysis}
                disabled={isAnalyzing}
              >
                {isAnalyzing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <ScanSearch className="w-4 h-4" />
                )}
              </Button>
            )}
          </div>
        </div>

        {/* Timestamp (bottom-right) */}
        <div className="absolute bottom-2 right-2 text-[10px] font-mono text-foreground/70 bg-background/60 px-1.5 py-0.5 rounded z-10">
          {currentTime.toLocaleTimeString('en-US', { hour12: false })}
        </div>
      </div>

      {/* Camera Info Bar */}
      <div className="p-2 bg-card flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <CameraIcon className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
            <span className="text-xs font-medium text-foreground truncate">
              {camera.name}
            </span>
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            <MapPin className="w-3 h-3 text-muted-foreground" />
            <span className="text-[10px] text-muted-foreground truncate">
              {camera.zone} • {camera.location}
            </span>
          </div>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-7 w-7 flex-shrink-0">
              <MoreVertical className="w-4 h-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={handleFullscreen}>
              <Maximize2 className="w-4 h-4 mr-2" />
              Fullscreen
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Pin className="w-4 h-4 mr-2" />
              {camera.isPinned ? 'Unpin' : 'Pin Camera'}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setIsMuted(!isMuted)}>
              {isMuted ? (
                <>
                  <Volume2 className="w-4 h-4 mr-2" />
                  Unmute Audio
                </>
              ) : (
                <>
                  <VolumeX className="w-4 h-4 mr-2" />
                  Mute Audio
                </>
              )}
            </DropdownMenuItem>
            {isStreamActive && (
              <DropdownMenuItem onClick={handleManualAnalysis} disabled={isAnalyzing}>
                <ScanSearch className="w-4 h-4 mr-2" />
                {isAnalyzing ? 'Analyzing...' : 'AI Analyze'}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
