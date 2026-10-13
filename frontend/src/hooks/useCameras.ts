import { useState, useEffect, useCallback } from 'react';
import { Camera, CameraDetectionConfig } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';
import { API_BASE } from '@/lib/api-base';
import { logUiEvent } from '@/lib/client-log-capture';

type BackendCamera = {
  id: string;
  name: string;
  zone: string;
  location: string | null;
  gps_lat: number | null;
  gps_lng: number | null;
  status: Camera['status'];
  stream_url: string | null;
  source_type: string;
  thumbnail_url: string | null;
  is_pinned: boolean;
  last_event_at: string | null;
  detection_config: CameraDetectionConfig | null;
};

export type CameraCreateInput = {
  name: string;
  zone: string;
  location?: string;
  stream_url?: string;
  source_type?: string;
  gps_lat?: number;
  gps_lng?: number;
  detection_config?: Record<string, number>;
};

function mapCamera(c: BackendCamera): Camera {
  return {
    id: c.id,
    name: c.name,
    zone: c.zone,
    location: c.location ?? '',
    gps: c.gps_lat != null && c.gps_lng != null ? { lat: c.gps_lat, lng: c.gps_lng } : undefined,
    status: c.status,
    streamUrl: c.stream_url ?? undefined,
    thumbnail: c.thumbnail_url ?? undefined,
    lastEvent: c.last_event_at ? new Date(c.last_event_at) : undefined,
    isPinned: c.is_pinned,
    detectionConfig: c.detection_config ?? undefined,
  };
}

export function useCameras() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { toast } = useToast();

  const fetchCameras = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/cameras`);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const data: BackendCamera[] = await resp.json();
      setCameras(data.map(mapCamera));
    } catch (error) {
      console.error('Failed to fetch cameras:', error);
      toast({
        title: 'Error',
        description: 'Failed to load cameras',
        variant: 'destructive',
      });
    } finally {
      setIsLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchCameras();
    // Camera.status (online/offline/warning) is connection-health driven,
    // not something the frontend gets pushed live for -- without this,
    // a status flip (e.g. a camera reconnecting) never reaches the UI
    // until a manual page reload, which also silently disabled the
    // alert-border highlight on camera feeds (gated on status === 'online'
    // in useDetectionMetadata's `enabled` flag).
    const interval = setInterval(fetchCameras, 15_000);
    return () => clearInterval(interval);
  }, [fetchCameras]);

  const addCamera = async (input: CameraCreateInput) => {
    const resp = await fetch(`${API_BASE}/api/cameras`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (!resp.ok) {
      // FastAPI errors are {"detail": "..."}; show that, not the raw JSON.
      const body = await resp.json().catch(() => null);
      throw new Error(body?.detail || `${resp.status} ${resp.statusText}`);
    }
    const created: BackendCamera = await resp.json();
    toast({ title: 'Camera Added', description: `${created.name} has been added` });
    logUiEvent('camera.created', { id: created.id, name: created.name });
    await fetchCameras();
    return mapCamera(created);
  };

  const updateCamera = async (id: string, updates: Partial<Camera> & { stream_url?: string }) => {
    const body: Record<string, unknown> = {};
    if (updates.name !== undefined) body.name = updates.name;
    if (updates.zone !== undefined) body.zone = updates.zone;
    if (updates.location !== undefined) body.location = updates.location;
    if (updates.streamUrl !== undefined) body.stream_url = updates.streamUrl;
    if (updates.isPinned !== undefined) body.is_pinned = updates.isPinned;
    if (updates.detectionConfig !== undefined) body.detection_config = updates.detectionConfig;
    if (updates.gps !== undefined) {
      body.gps_lat = updates.gps?.lat;
      body.gps_lng = updates.gps?.lng;
    }

    const resp = await fetch(`${API_BASE}/api/cameras/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      toast({ title: 'Error', description: 'Failed to update camera', variant: 'destructive' });
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    toast({ title: 'Camera Updated', description: 'Camera settings saved' });
    logUiEvent('camera.updated', { id, fields: Object.keys(body) });
    await fetchCameras();
  };

  const deleteCamera = async (id: string) => {
    const resp = await fetch(`${API_BASE}/api/cameras/${id}`, { method: 'DELETE' });
    if (!resp.ok) {
      toast({ title: 'Error', description: 'Failed to remove camera', variant: 'destructive' });
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    toast({ title: 'Camera Removed', description: 'Camera removed from the system' });
    logUiEvent('camera.deleted', { id });
    await fetchCameras();
  };

  const togglePin = async (id: string) => {
    const camera = cameras.find((c) => c.id === id);
    if (camera) {
      await updateCamera(id, { isPinned: !camera.isPinned });
    }
  };

  return {
    cameras,
    isLoading,
    addCamera,
    updateCamera,
    deleteCamera,
    togglePin,
    refetch: fetchCameras,
  };
}
