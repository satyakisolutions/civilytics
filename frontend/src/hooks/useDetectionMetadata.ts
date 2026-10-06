// frontend/src/hooks/useDetectionMetadata.ts
import { useEffect, useRef, useState, useCallback } from "react";
import type { FrameMetadata } from "@/types/monitoring";
import { WS_BASE as WS_BASE_URL } from "@/lib/api-base";
import { openAuthedWebSocket } from "@/lib/api-client";
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_ATTEMPTS = 10;

interface UseDetectionMetadataOptions {
  cameraId: string;
  enabled?: boolean;
}

export function useDetectionMetadata({ cameraId, enabled = true }: UseDetectionMetadataOptions) {
  const [metadata, setMetadata] = useState<FrameMetadata | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(async () => {
    if (!enabled || !cameraId) return;

    const url = `${WS_BASE_URL}/metadata/${cameraId}`;
    // Token travels as a subprotocol -- see openAuthedWebSocket.
    const ws = await openAuthedWebSocket(url);
    if (!ws) return;
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      reconnectAttempts.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data: FrameMetadata = JSON.parse(event.data);
        setMetadata(data);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;

      if (enabled && reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts.current += 1;
        const delay = RECONNECT_DELAY_MS * Math.min(reconnectAttempts.current, 5);
        reconnectTimer.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [cameraId, enabled]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { metadata, isConnected };
}
