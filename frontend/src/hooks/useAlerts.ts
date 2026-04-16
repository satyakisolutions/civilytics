import { useState, useEffect, useCallback, useRef } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Alert } from '@/types/monitoring';
import { API_BASE } from '@/lib/api-base';
import { useToast } from '@/hooks/use-toast';
import { useNotifications } from '@/hooks/useNotifications';

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

  useEffect(() => {
    // Initial fetch from both sources
    fetchAlerts();
    fetchFromApi();

    // Poll backend API every 5s for new alerts (reliable fallback)
    const pollInterval = setInterval(fetchFromApi, 5000);

    // Also try Supabase realtime (may not work with raw PostgreSQL)
    const channel = supabase
      .channel('alerts-realtime')
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'alerts'
        },
        (payload) => {
          const newAlert = payload.new as any;
          notifyViolation(
            newAlert.title,
            newAlert.message,
            newAlert.severity as 'critical' | 'warning' | 'info',
            `alert-${newAlert.id}`,
            newAlert.tts_audio_url || undefined
          );
          toast({
            title: newAlert.title,
            description: newAlert.message,
            variant: newAlert.severity === 'critical' ? 'destructive' : 'default'
          });
          fetchFromApi();
        }
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'alerts' },
        () => fetchFromApi()
      )
      .subscribe();

    return () => {
      clearInterval(pollInterval);
      supabase.removeChannel(channel);
    };
  }, [fetchAlerts, fetchFromApi, toast, notifyViolation]);



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
