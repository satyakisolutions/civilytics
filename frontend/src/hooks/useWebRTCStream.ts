import { useState, useCallback, useRef, useEffect } from 'react';

interface StreamConfig {
  serverUrl: string;
  streamPath: string;
}

interface UseWebRTCStreamOptions {
  autoConnect?: boolean;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (error: string) => void;
}

export function useWebRTCStream(
  config: StreamConfig | null,
  options: UseWebRTCStreamOptions = {}
) {
  const { autoConnect = true, onConnected, onDisconnected, onError } = options;
  
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  
  const pcRef = useRef<RTCPeerConnection | null>(null);

  const disconnect = useCallback(() => {
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    setStream(null);
    setIsConnected(false);
    setIsConnecting(false);
    onDisconnected?.();
  }, [onDisconnected]);

  const connect = useCallback(async () => {
    if (!config) {
      setError('No stream configuration provided');
      return;
    }

    disconnect();
    setIsConnecting(true);
    setError(null);

    try {
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: 'stun:stun.l.google.com:19302' },
          { urls: 'stun:stun1.l.google.com:19302' },
        ],
      });
      pcRef.current = pc;

      // Create a new MediaStream to collect tracks
      const mediaStream = new MediaStream();

      pc.ontrack = (event) => {
        event.streams[0].getTracks().forEach(track => {
          mediaStream.addTrack(track);
        });
        setStream(mediaStream);
        setIsConnected(true);
        setIsConnecting(false);
        onConnected?.();
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setIsConnected(false);
          setError(`Connection ${pc.connectionState}`);
          onError?.(`Connection ${pc.connectionState}`);
        }
      };

      // Add transceivers
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      // Create and send offer
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Wait for ICE gathering
      await new Promise<void>((resolve) => {
        if (pc.iceGatheringState === 'complete') {
          resolve();
        } else {
          const checkState = () => {
            if (pc.iceGatheringState === 'complete') {
              resolve();
            }
          };
          pc.onicegatheringstatechange = checkState;
          setTimeout(resolve, 5000);
        }
      });

      // WHEP endpoint
      const whepUrl = `${config.serverUrl}/${config.streamPath}/whep`;
      const response = await fetch(whepUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription?.sdp,
      });

      if (!response.ok) {
        throw new Error(`WHEP failed: ${response.status}`);
      }

      const answerSdp = await response.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

    } catch (err) {
      const message = err instanceof Error ? err.message : 'Connection failed';
      setError(message);
      setIsConnecting(false);
      onError?.(message);
    }
  }, [config, disconnect, onConnected, onError]);

  useEffect(() => {
    if (autoConnect && config) {
      connect();
    }
    return disconnect;
  }, [config?.serverUrl, config?.streamPath, autoConnect]);

  return {
    stream,
    isConnected,
    isConnecting,
    error,
    connect,
    disconnect,
  };
}
