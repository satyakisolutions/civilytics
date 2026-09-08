import { useState, useEffect, useCallback, useRef } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Alert } from '@/types/monitoring';
import { API_BASE, WS_BASE } from '@/lib/api-base';
import { useToast } from '@/hooks/use-toast';
import { useNotifications } from '@/hooks/useNotifications';

const WS_RECONNECT_DELAY_MS = 2000;
const WS_MAX_RECONNECT_DELAY_MS = 20_000;

export function useAlerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { toast } = useToast();
  const { notifyViolation } = useNotifications();

  const fetchAlerts = useCallback(async () => {
    try {
      const { data, error } = await supabase
        .from('alerts')
        .select('*')
        .order('timestamp', { ascending: false })
        .limit(50);

      if (error) throw error;

      const mappedAlerts: Alert[] = (data || []).map(alert => ({
        id: alert.id,
        eventId: alert.event_id || '',
        severity: alert.severity as Alert['severity'],
        title: alert.title,
        message: alert.message,
        timestamp: new Date(alert.timestamp),
        status: alert.status as Alert['status'],
        cameraId: alert.camera_id,
        zone: alert.zone
      }));

      setAlerts(mappedAlerts);
    } catch (error) {
      console.error('Failed to fetch alerts:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Track known alert IDs to detect new ones on poll
  const knownAlertIds = useRef<Set<string>>(new Set());
  const initialLoadDone = useRef(false);
  const lastNotifyTime = useRef(0);

  // Fetch from backend API (works without Supabase realtime)
  const fetchFromApi = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/alerts?limit=50`);
      if (!resp.ok) return;
      const data = await resp.json();
      const mapped: Alert[] = (data || []).map((a: any) => ({
        id: a.id,
        eventId: a.event_id || '',
        severity: a.severity,
        title: a.title,
        message: a.message,
        timestamp: new Date(a.timestamp),
        status: a.status,
        cameraId: a.camera_id,
        zone: a.zone,
        ttsAudioUrl: a.tts_audio_url,
      }));

      if (!initialLoadDone.current) {
        // First load — seed known IDs, don't notify
        for (const a of mapped) knownAlertIds.current.add(a.id);
        initialLoadDone.current = true;
      } else {
        // Subsequent polls — only notify for genuinely new CRITICAL alerts
        // Throttle: max 1 notification per 10 seconds
        const now = Date.now();
        for (const alert of mapped) {
          if (knownAlertIds.current.has(alert.id)) continue;
          knownAlertIds.current.add(alert.id);
          if (alert.status !== 'active') continue;
          if (alert.severity !== 'critical') continue; // Only notify critical
          if (now - lastNotifyTime.current < 10_000) continue; // Throttle

          lastNotifyTime.current = now;
          notifyViolation(
            alert.title,
            alert.message || '',
            alert.severity,
            `alert-${alert.id}`,
            (alert as any).ttsAudioUrl || undefined
          );
          toast({
            title: alert.title,
            description: alert.message,
            variant: 'destructive',
          });
          break; // Only 1 notification per poll cycle
        }
      }

      setAlerts(mapped);
    } catch { /* API may not be available */ }
  }, [notifyViolation, toast]);

  // Live push over /ws/alerts (backend's AlertConsumer broadcasts here,
  // see backend/app/ws/alerts.py + app/services/alert_consumer.py) instead
  // of blind interval polling. The WS payload already carries
  // severity/title/message, so a genuinely-new CRITICAL alert triggers a
  // toast/notification immediately on receipt; the list itself is then
  // resynced from the REST endpoint (single source of truth for
  // id/status/timestamp shaping) rather than hand-mapping the partial WS
  // payload into local state.
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const reconnectAttempts = useRef(0);
  const fetchFromApiRef = useRef(fetchFromApi);
  fetchFromApiRef.current = fetchFromApi;

  const connectWs = useCallback(() => {
    const ws = new WebSocket(`${WS_BASE}/alerts`);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      // Reconnects (network blip, backend restart) can miss events —
      // resync the list once the socket is back up.
      fetchFromApiRef.current();
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.type !== 'alert' && payload?.type !== 'vlm_result') return;

        const severity = payload.severity as Alert['severity'] | undefined;
        const now = Date.now();
        if (severity === 'critical' && now - lastNotifyTime.current >= 10_000) {
          lastNotifyTime.current = now;
          notifyViolation(
            payload.title || 'Alert',
            payload.message || '',
            severity,
            `alert-ws-${payload.id ?? now}`,
            payload.tts_audio_url || undefined
          );
          toast({
            title: payload.title || 'Alert',
            description: payload.message,
            variant: 'destructive',
          });
        }

        if (payload.id) {
          // Direct-alert path (pipeline.py's per-frame FSM alerts) has no
          // DB row -- this WS payload is the sole source of truth for it,
          // fully Alert-shaped (id/title/timestamp/status) by the backend.
          // Render it live instead of waiting on a REST resync that would
          // never find it.
          knownAlertIds.current.add(payload.id);
          const liveAlert: Alert = {
            id: payload.id,
            eventId: '',
            severity: severity as Alert['severity'],
            title: payload.title || 'Alert',
            message: payload.message || '',
            timestamp: new Date(payload.timestamp || now),
            status: (payload.status as Alert['status']) || 'active',
            cameraId: payload.camera_id,
            zone: payload.zone,
          };
          setAlerts(prev => [liveAlert, ...prev].slice(0, 50));
        } else {
          // Older/partial payload shape -- resync the canonical list.
          fetchFromApiRef.current();
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      reconnectAttempts.current += 1;
      const delay = Math.min(
        WS_RECONNECT_DELAY_MS * reconnectAttempts.current,
        WS_MAX_RECONNECT_DELAY_MS
      );
      reconnectTimer.current = setTimeout(connectWs, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [notifyViolation, toast]);

  useEffect(() => {
    fetchFromApi();
    connectWs();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [fetchFromApi, connectWs]);



  const acknowledgeAlert = async (id: string, acknowledgedBy?: string) => {
    try {
      const { error } = await supabase
        .from('alerts')
        .update({
          status: 'acknowledged'
        })
        .eq('id', id);

      if (error) throw error;

      // Also update the related violation event
      const alert = alerts.find(a => a.id === id);
      if (alert?.eventId) {
        await supabase
          .from('violation_events')
          .update({
            status: 'acknowledged',
            acknowledged_by: acknowledgedBy || 'Operator',
            acknowledged_at: new Date().toISOString()
          })
          .eq('id', alert.eventId);
      }

      toast({
        title: 'Alert Acknowledged',
        description: 'The alert has been marked as acknowledged'
      });
    } catch (error) {
      console.error('Failed to acknowledge alert:', error);
      toast({
        title: 'Error',
        description: 'Failed to acknowledge alert',
        variant: 'destructive'
      });
    }
  };

  const resolveAlert = async (id: string, notes?: string) => {
    try {
      const { error } = await supabase
        .from('alerts')
        .update({ status: 'resolved' })
        .eq('id', id);

      if (error) throw error;

      const alert = alerts.find(a => a.id === id);
      if (alert?.eventId) {
        await supabase
          .from('violation_events')
          .update({
            status: 'resolved',
            notes
          })
          .eq('id', alert.eventId);
      }

      toast({
        title: 'Alert Resolved',
        description: 'The alert has been marked as resolved'
      });
    } catch (error) {
      console.error('Failed to resolve alert:', error);
      toast({
        title: 'Error',
        description: 'Failed to resolve alert',
        variant: 'destructive'
      });
    }
  };

  const activeAlerts = alerts.filter(a => a.status === 'active');
  const acknowledgedAlerts = alerts.filter(a => a.status === 'acknowledged');

  return {
    alerts,
    activeAlerts,
    acknowledgedAlerts,
    isLoading,
    acknowledgeAlert,
    resolveAlert,
    refetch: fetchAlerts
  };
}
