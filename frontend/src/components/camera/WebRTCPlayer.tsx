import { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from 'react';
import { Loader2, WifiOff, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { MEDIAMTX_BASE } from '@/lib/api-base';

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

const MAX_RECONNECT_ATTEMPTS = 5;

/**
 * WHEP (WebRTC-HTTP Egress Protocol) player for MediaMTX.
 *
 * The previous version of this component had a subtle re-entrancy
 * bug: the `connect` function was a `useCallback` that depended on
 * `reconnectAttempts`, and the `useEffect` calling `connect` listed
 * `connect` in its dep array. Every failed connection bumped
 * `reconnectAttempts` -> new `connect` ref -> useEffect re-ran ->
 * the cleanup tore down the FRESH RTCPeerConnection that had just
 * been created -> the next attempt bumped the counter again -> repeat.
 * End result: the player looked like it was "keeping disconnected"
 * because it was thrashing peer connections in a loop instead of
 * actually settling on one.
 *
 * This version keeps all the volatile state (reconnect counter,
 * the mutable callbacks the user passes, the current pc) in refs,
 * and the only thing the main useEffect watches is `streamPath` +
 * `resolvedServerUrl`. That way the connection is created once per
 * path change and reconnects happen inside a single stable lifecycle.
 *
 * Negotiation pattern mirrors the standalone WHEP test page exactly:
 *   1. `new RTCPeerConnection()` with no explicit iceServers — mediamtx
 *      advertises reachable host candidates via webrtcAdditionalHosts,
 *      so LAN host-only negotiation works.
 *   2. Pre-create a `MediaStream`, attach it to `video.srcObject`
 *      immediately, and append each inbound track via `stream.addTrack`
 *      in `ontrack`. More robust than `event.streams[0]`, which is
 *      undefined when mediamtx's SDP answer lacks stream IDs per m-line.
 *   3. POST `offer.sdp` to WHEP right after `setLocalDescription` —
 *      no wait for ICE gathering. mediamtx handles non-trickle ICE
 *      on the answer side.
 */
export const WebRTCPlayer = forwardRef<WebRTCPlayerHandle, WebRTCPlayerProps>(({
  streamPath,
  serverUrl,
  className,
  onStreamReady,
  onStreamError,
  autoReconnect = true,
  muted = true,
}, ref) => {
  // Resolve the server URL from the prop OR our runtime-derived
  // MEDIAMTX_BASE. The prop can override it, but we never fall back
  // to a hard-coded localhost string — that breaks LAN access.
  const resolvedServerUrl = serverUrl || MEDIAMTX_BASE;

  const videoRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectCountRef = useRef<number>(0);
  const mountedRef = useRef<boolean>(true);

  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [reconnectDisplay, setReconnectDisplay] = useState<number>(0);

  // Stash the latest user callbacks in refs so `connectOnce`'s
  // identity never changes. Otherwise the useEffect re-runs on
  // every parent re-render and tears down the healthy connection.
  const callbacksRef = useRef({
    onStreamReady,
    onStreamError,
    autoReconnect,
  });
  callbacksRef.current = { onStreamReady, onStreamError, autoReconnect };

  const cleanupInternal = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (pcRef.current) {
      try {
        pcRef.current.close();
      } catch {
        /* noop */
      }
      pcRef.current = null;
    }
    if (streamRef.current) {
      try {
        streamRef.current.getTracks().forEach((t) => t.stop());
      } catch {
        /* noop */
      }
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const connectOnceRef = useRef<() => Promise<void>>(async () => undefined);

  connectOnceRef.current = async () => {
    cleanupInternal();
    if (!mountedRef.current) return;
    setConnectionState('connecting');

    try {
      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      // Pre-created MediaStream attached to the <video> before any
      // tracks arrive. Append each inbound track on ontrack.
      const stream = new MediaStream();
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }

      pc.ontrack = (event) => {
        stream.addTrack(event.track);
        if (!mountedRef.current) return;
        setConnectionState('connected');
        reconnectCountRef.current = 0;
        setReconnectDisplay(0);
        callbacksRef.current.onStreamReady?.();
      };

      pc.onconnectionstatechange = () => {
        if (!mountedRef.current) return;
        if (!pcRef.current || pc !== pcRef.current) return;
        switch (pc.connectionState) {
          case 'connected':
            setConnectionState('connected');
            break;
          case 'disconnected':
          case 'failed':
            setConnectionState('failed');
            callbacksRef.current.onStreamError?.(`Connection ${pc.connectionState}`);
            if (
              callbacksRef.current.autoReconnect &&
              reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS
            ) {
              scheduleReconnectInternal();
            }
            break;
          case 'closed':
            setConnectionState('idle');
            break;
        }
      };

      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      const offer = await pc.createOffer();
      if (!mountedRef.current || pc !== pcRef.current) return;
      await pc.setLocalDescription(offer);
      if (!mountedRef.current || pc !== pcRef.current) return;

      const whepUrl = `${resolvedServerUrl.replace(/\/+$/, '')}/${streamPath}/whep`;
      // eslint-disable-next-line no-console
      console.debug('[WebRTCPlayer] WHEP POST', whepUrl);

      const response = await fetch(whepUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: offer.sdp,
      });

      if (!mountedRef.current || pc !== pcRef.current) return;
      if (!response.ok) {
        throw new Error(`WHEP request failed: ${response.status} ${response.statusText}`);
      }

      const answerSdp = await response.text();
      if (!mountedRef.current || pc !== pcRef.current) return;
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('[WebRTCPlayer] connection error:', error);
      if (!mountedRef.current) return;
      setConnectionState('failed');
      callbacksRef.current.onStreamError?.(
        error instanceof Error ? error.message : 'Connection failed'
      );
      if (
        callbacksRef.current.autoReconnect &&
        reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS
      ) {
        scheduleReconnectInternal();
      }
    }
  };

  const scheduleReconnectInternal = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (!mountedRef.current) return;
    setConnectionState('reconnecting');
    const delay = Math.min(1000 * Math.pow(2, reconnectCountRef.current), 30000);
    reconnectTimeoutRef.current = setTimeout(() => {
      if (!mountedRef.current) return;
      reconnectCountRef.current += 1;
      setReconnectDisplay(reconnectCountRef.current);
      void connectOnceRef.current();
    }, delay);
  }, []);

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

  // THE ONLY useEffect that manages the peer connection lifecycle.
  // Depends on just streamPath + resolvedServerUrl — nothing that
  // changes on every render. So flipping quality tiers (which changes
  // streamPath) correctly tears down and creates a new connection,
  // but ordinary re-renders don't touch the pc.
  useEffect(() => {
    mountedRef.current = true;
    if (streamPath) {
      reconnectCountRef.current = 0;
      setReconnectDisplay(0);
      void connectOnceRef.current();
    }
    return () => {
      mountedRef.current = false;
      cleanupInternal();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamPath, resolvedServerUrl]);

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
                Reconnecting... (attempt {reconnectDisplay + 1}/{MAX_RECONNECT_ATTEMPTS})
              </span>
            </>
          )}

          {connectionState === 'failed' && reconnectDisplay >= MAX_RECONNECT_ATTEMPTS && (
            <>
              <WifiOff className="w-8 h-8 text-destructive mb-2" />
              <span className="text-sm text-muted-foreground mb-3">Stream unavailable</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  reconnectCountRef.current = 0;
                  setReconnectDisplay(0);
                  void connectOnceRef.current();
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
