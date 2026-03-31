import { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from 'react';
import { Loader2, WifiOff, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface WebRTCPlayerHandle {
  captureFrame: () => string | null;
  getVideoElement: () => HTMLVideoElement | null;
}

interface WebRTCPlayerProps {
  streamPath: string;
  serverUrl?: string;
  className?: string;
  onStreamReady?: () => void;
  onStreamError?: (error: string) => void;
  autoReconnect?: boolean;
  muted?: boolean;
}

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'failed' | 'reconnecting';

export const WebRTCPlayer = forwardRef<WebRTCPlayerHandle, WebRTCPlayerProps>(({
  streamPath,
  serverUrl = 'http://localhost:8889',
  className,
  onStreamReady,
  onStreamError,
  autoReconnect = true,
  muted = true,
}, ref) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const maxReconnectAttempts = 5;

  const cleanup = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const connect = useCallback(async () => {
    cleanup();
    setConnectionState('connecting');

    try {
      // Create RTCPeerConnection with STUN servers
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: 'stun:stun.l.google.com:19302' },
          { urls: 'stun:stun1.l.google.com:19302' },
        ],
      });
      pcRef.current = pc;

      // Handle incoming tracks
      pc.ontrack = (event) => {
        if (videoRef.current && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0];
          setConnectionState('connected');
          setReconnectAttempts(0);
          onStreamReady?.();
        }
      };

      // Monitor connection state
      pc.onconnectionstatechange = () => {
        switch (pc.connectionState) {
          case 'connected':
            setConnectionState('connected');
            break;
          case 'disconnected':
          case 'failed':
            setConnectionState('failed');
            onStreamError?.(`Connection ${pc.connectionState}`);
            if (autoReconnect && reconnectAttempts < maxReconnectAttempts) {
              scheduleReconnect();
            }
            break;
          case 'closed':
            setConnectionState('idle');
            break;
        }
      };

      // Add transceivers for receiving video and audio
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      // Create offer
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Wait for ICE gathering to complete
      await new Promise<void>((resolve) => {
        if (pc.iceGatheringState === 'complete') {
          resolve();
        } else {
          pc.onicegatheringstatechange = () => {
            if (pc.iceGatheringState === 'complete') {
              resolve();
            }
          };
          // Timeout after 5 seconds
          setTimeout(resolve, 5000);
        }
      });

      // Send offer to MediaMTX WHEP endpoint
      const whepUrl = `${serverUrl}/${streamPath}/whep`;
      const response = await fetch(whepUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/sdp',
        },
        body: pc.localDescription?.sdp,
      });

      if (!response.ok) {
        throw new Error(`WHEP request failed: ${response.status} ${response.statusText}`);
      }

      // Set remote description from answer
      const answerSdp = await response.text();
      await pc.setRemoteDescription({
        type: 'answer',
        sdp: answerSdp,
      });

    } catch (error) {
      console.error('WebRTC connection error:', error);
      setConnectionState('failed');
      onStreamError?.(error instanceof Error ? error.message : 'Connection failed');
      
      if (autoReconnect && reconnectAttempts < maxReconnectAttempts) {
        scheduleReconnect();
      }
    }
  }, [streamPath, serverUrl, cleanup, onStreamReady, onStreamError, autoReconnect, reconnectAttempts]);

  const scheduleReconnect = useCallback(() => {
    setConnectionState('reconnecting');
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
    
    reconnectTimeoutRef.current = setTimeout(() => {
      setReconnectAttempts(prev => prev + 1);
      connect();
    }, delay);
  }, [reconnectAttempts, connect]);

  // Capture frame for AI analysis - returns base64 string
  const captureFrame = useCallback((): string | null => {
    if (!videoRef.current || connectionState !== 'connected') return null;
    
    const video = videoRef.current;
    if (video.videoWidth === 0 || video.videoHeight === 0) return null;

    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    ctx.drawImage(video, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.8);
  }, [connectionState]);

  // Expose methods via ref
  useImperativeHandle(ref, () => ({
    captureFrame,
    getVideoElement: () => videoRef.current,
  }), [captureFrame]);

  useEffect(() => {
    if (streamPath) {
      connect();
    }
    return cleanup;
  }, [streamPath, connect, cleanup]);

  return (
    <div className={cn('relative bg-muted rounded-lg overflow-hidden', className)}>
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted={muted}
        className={cn(
          'w-full h-full object-cover',
          connectionState !== 'connected' && 'opacity-0'
        )}
      />

      {/* Loading/Error Overlay */}
      {connectionState !== 'connected' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-muted/80 backdrop-blur-sm">
          {connectionState === 'connecting' && (
            <>
              <Loader2 className="w-8 h-8 animate-spin text-primary mb-2" />
              <span className="text-sm text-muted-foreground">Connecting to stream...</span>
            </>
          )}

          {connectionState === 'reconnecting' && (
            <>
              <RefreshCw className="w-8 h-8 animate-spin text-warning mb-2" />
              <span className="text-sm text-muted-foreground">
                Reconnecting... (attempt {reconnectAttempts + 1}/{maxReconnectAttempts})
              </span>
            </>
          )}

          {connectionState === 'failed' && reconnectAttempts >= maxReconnectAttempts && (
            <>
              <WifiOff className="w-8 h-8 text-destructive mb-2" />
              <span className="text-sm text-muted-foreground mb-3">Stream unavailable</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setReconnectAttempts(0);
                  connect();
                }}
              >
                <RefreshCw className="w-4 h-4 mr-2" />
                Retry
              </Button>
            </>
          )}

          {connectionState === 'idle' && (
            <>
              <WifiOff className="w-8 h-8 text-muted-foreground mb-2" />
              <span className="text-sm text-muted-foreground">No stream configured</span>
            </>
          )}
        </div>
      )}

      {/* Live indicator */}
      {connectionState === 'connected' && (
        <div className="absolute top-2 left-2 flex items-center gap-1.5 px-2 py-1 rounded bg-success/20 text-success text-[10px] font-semibold uppercase">
          <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
          LIVE
        </div>
      )}
    </div>
  );
});

WebRTCPlayer.displayName = 'WebRTCPlayer';
