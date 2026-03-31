import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { ViolationEvent } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';

interface ViolationFilters {
  dateFrom?: Date;
  dateTo?: Date;
  cameraId?: string;
  zone?: string;
  status?: string;
  eventType?: string;
}

export function useViolationEvents(filters?: ViolationFilters) {
  const [events, setEvents] = useState<ViolationEvent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { toast } = useToast();

  const fetchEvents = useCallback(async () => {
    try {
      let query = supabase
        .from('violation_events')
        .select(`
          *,
          cameras!inner(name, zone, location)
        `)
        .order('timestamp', { ascending: false })
        .limit(200);

      if (filters?.dateFrom) {
        query = query.gte('timestamp', filters.dateFrom.toISOString());
      }
      if (filters?.dateTo) {
        query = query.lte('timestamp', filters.dateTo.toISOString());
      }
      if (filters?.cameraId) {
        query = query.eq('camera_id', filters.cameraId);
      }
      if (filters?.status && ['active', 'acknowledged', 'resolved'].includes(filters.status)) {
        query = query.eq('status', filters.status as 'active' | 'acknowledged' | 'resolved');
      }
      if (filters?.eventType && ['dumping_detected', 'suspicious_activity', 'repeat_offender'].includes(filters.eventType)) {
        query = query.eq('event_type', filters.eventType as 'dumping_detected' | 'suspicious_activity' | 'repeat_offender');
      }

      const { data, error } = await query;

      if (error) throw error;

      const mappedEvents: ViolationEvent[] = (data || []).map(event => ({
        id: event.id,
        cameraId: event.camera_id,
        cameraName: (event.cameras as any)?.name || 'Unknown Camera',
        zone: (event.cameras as any)?.zone || 'Unknown Zone',
        location: (event.cameras as any)?.location || 'Unknown Location',
        timestamp: new Date(event.timestamp),
        eventType: event.event_type as ViolationEvent['eventType'],
        severity: event.severity as ViolationEvent['severity'],
        confidence: event.confidence || 0,
        personId: event.person_id || undefined,
        repeatCount: event.repeat_count || 1,
        snapshotUrl: event.snapshot_url || undefined,
        videoClipUrl: event.video_clip_url || undefined,
        status: event.status as ViolationEvent['status'],
        acknowledgedBy: event.acknowledged_by || undefined,
        acknowledgedAt: event.acknowledged_at ? new Date(event.acknowledged_at) : undefined,
        notes: event.notes || undefined,
        isValid: event.is_valid
      }));

      // Filter by zone if specified (since zone is in joined table)
      let filteredEvents = mappedEvents;
      if (filters?.zone) {
        filteredEvents = mappedEvents.filter(e => e.zone === filters.zone);
      }

      setEvents(filteredEvents);
    } catch (error) {
      console.error('Failed to fetch violation events:', error);
      toast({
        title: 'Error',
        description: 'Failed to load violation events',
        variant: 'destructive'
      });
    } finally {
      setIsLoading(false);
    }
  }, [filters, toast]);

  useEffect(() => {
    fetchEvents();

    // Subscribe to realtime updates
    const channel = supabase
      .channel('violation-events-changes')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'violation_events'
        },
        () => {
          fetchEvents();
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [fetchEvents]);

  const markAsValid = async (id: string, isValid: boolean, notes?: string) => {
    try {
      const { error } = await supabase
        .from('violation_events')
        .update({
          is_valid: isValid,
          notes,
          status: 'resolved'
        })
        .eq('id', id);

      if (error) throw error;

      toast({
        title: isValid ? 'Marked as Valid' : 'Marked as False Positive',
        description: 'Event has been updated'
      });
    } catch (error) {
      console.error('Failed to update event:', error);
      toast({
        title: 'Error',
        description: 'Failed to update event',
        variant: 'destructive'
      });
    }
  };

  const updateEventNotes = async (id: string, notes: string) => {
    try {
      const { error } = await supabase
        .from('violation_events')
        .update({ notes })
        .eq('id', id);

      if (error) throw error;

      toast({
        title: 'Notes Updated',
        description: 'Event notes have been saved'
      });
    } catch (error) {
      console.error('Failed to update notes:', error);
      toast({
        title: 'Error',
        description: 'Failed to update notes',
        variant: 'destructive'
      });
    }
  };

  return {
    events,
    isLoading,
    markAsValid,
    updateEventNotes,
    refetch: fetchEvents
  };
}
