import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Alert } from '@/types/monitoring';
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

  useEffect(() => {
    fetchAlerts();

    // Subscribe to realtime alert updates
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
          
          // Play sound + browser push notification
          notifyViolation(
            newAlert.title,
            newAlert.message,
            newAlert.severity as 'critical' | 'warning' | 'info',
            `alert-${newAlert.id}`
          );

          // Show toast notification
          toast({
            title: newAlert.title,
            description: newAlert.message,
            variant: newAlert.severity === 'critical' ? 'destructive' : 'default'
          });

          // Refresh alerts list
          fetchAlerts();
        }
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'alerts'
        },
        () => {
          fetchAlerts();
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [fetchAlerts, toast, notifyViolation]);



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
