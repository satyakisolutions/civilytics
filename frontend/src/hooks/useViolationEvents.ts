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
      // Backing table `violation_events` is empty; derive timeline from
      // /api/alerts (populated by AlertConsumer). Also fetch /api/evidence/files
      // for snapshot + video URLs keyed by camera_id + timestamp.
      const [alertsResp, evidenceResp] = await Promise.all([
        fetch(`${API_BASE}/api/alerts?limit=100`).catch(() => null),
        fetch(`${API_BASE}/api/evidence/files?limit=200`).catch(() => null),
      ]);

      const alerts = alertsResp?.ok ? await alertsResp.json() : [];
      const evidence = evidenceResp?.ok ? await evidenceResp.json() : [];

      // Build evidence lookup: camera_id → array of {timestamp, keyframe_url, video_url}
      const evidenceByCamera = new Map<string, any[]>();
      for (const ev of evidence) {
        const list = evidenceByCamera.get(ev.camera_id) || [];
        list.push(ev);
        evidenceByCamera.set(ev.camera_id, list);
      }

      const mapped: ViolationEvent[] = (alerts || []).map((a: any) => {
        const title = (a.title || '').toLowerCase();
        const eventType = title.includes('dumping') || title.includes('violation')
          ? 'dumping_detected'
          : title.includes('suspicious') || title.includes('observe')
            ? 'suspicious_activity'
            : 'suspicious_activity';

        // Match evidence record whose timestamp is within 3min of alert
        const alertTime = new Date(a.timestamp).getTime();
        const cands = evidenceByCamera.get(a.camera_id) || [];
        let match: any = null;
        let bestDelta = Number.POSITIVE_INFINITY;
        for (const ev of cands) {
          const delta = Math.abs(new Date(ev.timestamp_start || ev.created_at).getTime() - alertTime);
          if (delta < bestDelta && delta < 180_000) {
            bestDelta = delta;
            match = ev;
          }
        }

        const snapshotUrl = match?.keyframe_url
          ? `${API_BASE}/api/evidence/file?path=${encodeURIComponent(match.keyframe_url)}`
          : undefined;
        const videoClipUrl = match?.video_url
          ? `${API_BASE}/api/evidence/file?path=${encodeURIComponent(match.video_url)}`
          : undefined;

        return {
          id: a.id,
          cameraId: a.camera_id,
          cameraName: a.camera_id?.slice(0, 8) || 'Unknown',
          zone: a.zone || '',
          location: '',
          timestamp: new Date(a.timestamp),
          eventType: eventType as ViolationEvent['eventType'],
          severity: a.severity as ViolationEvent['severity'],
          confidence: 85,
          personId: undefined,
          repeatCount: 1,
          snapshotUrl,
          videoClipUrl,
          status: (a.status === 'active' ? 'active' : 'resolved') as ViolationEvent['status'],
          acknowledgedBy: undefined,
          acknowledgedAt: undefined,
          notes: a.message || undefined,
          isValid: undefined,
        };
      });

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
