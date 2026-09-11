import { useState, useEffect, useMemo, useRef } from 'react';
import { 
  Camera as CameraIcon, 
  Maximize2, 
  Pin, 
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
  // LSG live feeds are video-only (no audio track on RTSP publish), so
  // we hard-mute the <video> element. Browser autoplay policies also
  // require muted=true for unattended autoplay.
  const isMuted = true;
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

  // Derive WebRTC stream path from camera data.
  //
  // The pipeline (connection_monitor.register_camera) publishes its
  // annotated output to mediamtx at `{camera.id}-out` — keyed by the
  // camera's DB UUID, not by any part of its RTSP streamUrl. So the
  // path here must be built from camera.id, not parsed out of
  // streamUrl (that used to break as soon as streamUrl's last path
  // segment diverged from the camera's id, e.g. after re-pointing a
  // camera at a different source file).
  const basePath = useMemo(() => {
    if (!camera.id) return null;
    return `${camera.id}-out`;
  }, [camera.id]);

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

  // Alert border — react to both camera.status AND real-time FSM escalations
  const hasEscalation = metadata?.detections?.some(
    (d) => d.fsm_state === 'violation' || d.fsm_state === 'alerting' || d.fsm_state === 'escalated'
  ) ?? false;
  const hasObserve = metadata?.detections?.some((d) => d.fsm_state === 'observe') ?? false;

  const alertBorderClass =
    camera.status === 'alert' || hasEscalation
      ? 'animate-alert-border'
      : camera.status === 'warning' || hasObserve
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
        {hasStreamUrl && streamPath ? (
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
              isEditing={roi.isEditing}
              currentPoints={roi.currentPoints}
              onCanvasClick={roi.addPoint}
              onUndoPoint={roi.undoPoint}
              onFinish={roi.finishPolygon}
              onCancel={roi.cancelEditing}
              onRemovePolygon={roi.removePolygon}
            />
          </>
        ) : (
          <CameraPlaceholder status={camera.status} cameraName={camera.name} />
        )}
        
        {/* Overlay gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-background/80 via-transparent to-transparent pointer-events-none" />

        {/* Status indicator (top-left) */}
        <div className="absolute top-2 left-2 flex items-center gap-2 z-10">
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-semibold uppercase"
            style={isStreamActive || camera.status === 'online' ? {
              backgroundColor: 'rgba(34, 197, 94, 0.2)',
              color: '#22c55e',
            } : camera.status === 'alert' ? {
              backgroundColor: 'rgba(239, 68, 68, 0.2)',
              color: '#ef4444',
            } : camera.status === 'warning' ? {
              backgroundColor: 'rgba(245, 158, 11, 0.2)',
              color: '#f59e0b',
            } : {
              backgroundColor: 'rgba(100, 100, 100, 0.2)',
              color: '#9ca3af',
            }}
          >
            <span
              style={{
                width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
                backgroundColor: isStreamActive || camera.status === 'online' ? '#22c55e' :
                  camera.status === 'alert' ? '#ef4444' :
                  camera.status === 'warning' ? '#f59e0b' : '#6b7280',
                boxShadow: isStreamActive ? '0 0 6px #22c55e' : undefined,
              }}
            />
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
          {hasStreamUrl && basePath && (
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

        {/* Inline action icons (top-right of the video panel) — replaces
            the old centred hover overlay that backdrop-blurred the whole
            feed and blocked all clicks. Volume controls dropped because
            the LSG live streams are video-only (no audio track). */}
        <div className={cn(
          'absolute top-12 right-2 flex items-center gap-1 z-20 transition-opacity duration-150',
          isHovered ? 'opacity-100' : 'opacity-0 pointer-events-none'
        )}>
          <Button
            variant="secondary"
            size="icon"
            className="h-7 w-7 bg-background/70 hover:bg-background backdrop-blur-sm"
            onClick={handleFullscreen}
            title="Maximise this camera"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </Button>
          {isStreamActive && (
            <Button
              variant="secondary"
              size="icon"
              className="h-7 w-7 bg-background/70 hover:bg-background backdrop-blur-sm"
              onClick={handleManualAnalysis}
              disabled={isAnalyzing}
              title="Analyse current frame"
            >
              {isAnalyzing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <ScanSearch className="w-3.5 h-3.5" />
              )}
            </Button>
          )}
        </div>

        {/* Quality chip (bottom-left) — shows the active transcoded tier */}
        {hasStreamUrl && basePath && (
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
