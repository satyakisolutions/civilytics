import { useState, useEffect, useCallback } from 'react';
import { ViolationEvent } from '@/types/monitoring';
import { API_BASE } from '@/lib/api-base';
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
      const params = new URLSearchParams({ limit: '50' });
      if (filters?.status) params.set('status', filters.status);
      if (filters?.eventType) params.set('event_type', filters.eventType);
      if (filters?.cameraId) params.set('camera_id', filters.cameraId);
      if (filters?.dateFrom) params.set('start_date', filters.dateFrom.toISOString());
      if (filters?.dateTo) params.set('end_date', filters.dateTo.toISOString());

      const resp = await fetch(`${API_BASE}/api/events?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      const mapped: ViolationEvent[] = (data || []).map((e: any) => ({
        id: e.id,
        cameraId: e.camera_id,
        cameraName: e.camera_id?.slice(0, 8) || 'Unknown',
        zone: '',
        location: '',
        timestamp: new Date(e.timestamp),
        eventType: e.event_type as ViolationEvent['eventType'],
        severity: e.severity as ViolationEvent['severity'],
        confidence: e.confidence || 0,
        personId: e.person_id || undefined,
        repeatCount: e.repeat_count || 1,
        snapshotUrl: e.snapshot_url || undefined,
        videoClipUrl: e.video_clip_url || undefined,
        status: e.status as ViolationEvent['status'],
        acknowledgedBy: e.acknowledged_by || undefined,
        acknowledgedAt: e.acknowledged_at ? new Date(e.acknowledged_at) : undefined,
        notes: e.notes || undefined,
        isValid: e.is_valid,
      }));

      setEvents(mapped);
    } catch (error) {
      console.error('Failed to fetch events:', error);
    } finally {
      setIsLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchEvents();
    // Poll every 30s instead of realtime subscription (prevents overload)
    const interval = setInterval(fetchEvents, 30_000);
    return () => clearInterval(interval);
  }, [fetchEvents]);

  const markAsValid = async (id: string, isValid: boolean, notes?: string) => {
    try {
      await fetch(`${API_BASE}/api/events/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_valid: isValid, notes, status: 'resolved' }),
      });
      toast({ title: isValid ? 'Marked as Valid' : 'Marked as False Positive' });
      fetchEvents();
    } catch {
      toast({ title: 'Error', description: 'Failed to update', variant: 'destructive' });
    }
  };

  const updateEventNotes = async (id: string, notes: string) => {
    try {
      await fetch(`${API_BASE}/api/events/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes }),
      });
      toast({ title: 'Notes Updated' });
    } catch {
      toast({ title: 'Error', description: 'Failed to update', variant: 'destructive' });
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
