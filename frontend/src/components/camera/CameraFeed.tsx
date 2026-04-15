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
import {
  StreamQualityToggle,
  STREAM_QUALITY_META,
  resolveStreamQuality,
  withQualitySuffix,
  type StreamQualitySelection,
} from './StreamQualityToggle';
import { useMediaMTXConfig } from '@/hooks/useMediaMTXConfig';
import { useFrameAnalysis } from '@/hooks/useFrameAnalysis';
import { useDetectionMetadata } from '@/hooks/useDetectionMetadata';
import { useROIEditor } from '@/hooks/useROIEditor';
import { useAutoStreamQuality } from '@/hooks/useAutoStreamQuality';
import { MetadataOverlay } from './MetadataOverlay';
import { ROIOverlay } from './ROIOverlay';

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
  const { metadata } = useDetectionMetadata({ cameraId: camera.id, enabled: camera.status === 'online' });
  const roi = useROIEditor({ cameraId: camera.id });
  const videoContainerRef = useRef<HTMLDivElement>(null);
  const [containerDimensions, setContainerDimensions] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!videoContainerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerDimensions({ width: entry.contentRect.width, height: entry.contentRect.height });
      }
    });
    observer.observe(videoContainerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => { roi.fetchPolygons(); }, [roi.fetchPolygons]);

  useEffect(() => {
    const interval = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  // Derive WebRTC stream path from camera data
  const basePath = useMemo(() => {
    if (!camera.streamUrl) return null;

    // If it's already a path (e.g., "cam-zone-a-north"), use it directly.
    if (!camera.streamUrl.includes('://')) {
      return camera.streamUrl;
    }

    // Prefer the last segment of an rtsp://.../<path> URL (keeps the
    // native publisher path intact — e.g. "cam-01" — so the transcoded
    // variants "-sm"/"-md" line up with the paths the RTSP streamer
    // sidecar actually publishes to mediamtx).
    try {
      const segments = camera.streamUrl.split('/').filter(Boolean);
      const last = segments[segments.length - 1];
      if (last) return last;
    } catch {
      // fall through to legacy derivation
    }

    // Legacy fallback: derive a path from zone + name.
    const safeName = camera.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const safeZone = camera.zone.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    return `cam-${safeZone}-${safeName}`;
  }, [camera.streamUrl, camera.name, camera.zone]);

  // Stream quality — "auto" drives off the panel's actual rendered
  // width via a ResizeObserver hook so small grid cells request the
  // small transcoded stream and fullscreen requests the large one.
  const {
    ref: qualityProbeRef,
    autoQuality,
    width: probedWidth,
    height: probedHeight,
  } = useAutoStreamQuality();
  const [qualitySelection, setQualitySelection] = useState<StreamQualitySelection>('auto');
  const effectiveQuality = resolveStreamQuality(qualitySelection, autoQuality);

  const streamPath = useMemo(
    () => (basePath ? withQualitySuffix(basePath, effectiveQuality) : null),
    [basePath, effectiveQuality]
  );

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

  // Merge the probe ref with the existing videoContainerRef so both the
  // auto-quality ResizeObserver and whatever else videoContainerRef is
  // used for get the same element. Must be memoised so React doesn't
  // re-invoke on every render.
  const mergedContainerRef = useMemo(
    () => (node: HTMLDivElement | null) => {
      // @ts-expect-error — videoContainerRef is a MutableRefObject<HTMLDivElement | null>
      videoContainerRef.current = node;
      // @ts-expect-error — qualityProbeRef is a RefObject<HTMLDivElement>
      qualityProbeRef.current = node;
    },
    [videoContainerRef, qualityProbeRef]
  );

  const alertBorderClass =
    camera.status === 'alert'
      ? 'animate-alert-border'
      : camera.status === 'warning'
        ? 'animate-alert-border-warning'
        : '';

  return (
    <div
      className={cn(
        'camera-feed group flex flex-col rounded-md border-2 border-transparent transition-shadow',
        alertBorderClass,
        camera.status === 'alert' && 'alert',
        isFullscreen && 'h-full'
      )}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Camera View */}
      <div
        ref={mergedContainerRef}
        className={cn(
          'relative flex-1 bg-gradient-to-br from-muted/50 to-muted overflow-hidden',
          isFullscreen ? 'min-h-[400px]' : 'aspect-video'
        )}
      >
        {/* WebRTC Stream or Placeholder */}
        {hasStreamUrl && streamPath && camera.status !== 'offline' ? (
          <>
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
            <MetadataOverlay
              metadata={metadata}
              width={containerDimensions.width}
              height={containerDimensions.height}
            />
            <ROIOverlay
              polygons={roi.polygons}
              width={containerDimensions.width}
              height={containerDimensions.height}
              isEditing={roi.isEditing}
              currentPoints={roi.currentPoints}
              onCanvasClick={roi.addPoint}
            />
          </>
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

        {/* Camera ID + quality toggle (top-right) */}
        <div className="absolute top-2 right-2 flex items-center gap-1.5 z-10">
          {camera.isPinned && (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              <Pin className="w-2.5 h-2.5 mr-1" />
              PINNED
            </Badge>
          )}
          {hasStreamUrl && basePath && camera.status !== 'offline' && (
            <StreamQualityToggle
              value={qualitySelection}
              onChange={setQualitySelection}
              autoQuality={autoQuality}
              size="xs"
              variant="pill"
              compact
            />
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

        {/* Quality chip (bottom-left) — shows the active transcoded tier */}
        {hasStreamUrl && basePath && camera.status !== 'offline' && (
          <div
            className="absolute bottom-2 left-2 text-[10px] font-mono text-foreground/80 bg-background/60 px-1.5 py-0.5 rounded z-10 flex items-center gap-1"
            title={`${STREAM_QUALITY_META[effectiveQuality].fullLabel} · ${STREAM_QUALITY_META[effectiveQuality].resolution} · ${STREAM_QUALITY_META[effectiveQuality].bitrateKbps} kbps\nPanel: ${probedWidth}x${probedHeight}${qualitySelection === 'auto' ? ' · auto' : ' · manual'}`}
          >
            <span className="uppercase font-semibold">
              {STREAM_QUALITY_META[effectiveQuality].shortLabel}
            </span>
            <span className="opacity-70">
              {STREAM_QUALITY_META[effectiveQuality].resolution}
            </span>
            {qualitySelection === 'auto' && (
              <span className="opacity-60 text-[9px]">auto</span>
            )}
          </div>
        )}

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
            <DropdownMenuItem onClick={() => roi.setIsEditing(!roi.isEditing)}>
              {roi.isEditing ? 'Cancel ROI' : 'Draw ROI'}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
