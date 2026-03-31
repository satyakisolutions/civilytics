import { useState, useEffect } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Camera } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';

export function useCameras() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { toast } = useToast();

  useEffect(() => {
    fetchCameras();

    // Subscribe to realtime updates
    const channel = supabase
      .channel('cameras-changes')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'cameras'
        },
        () => {
          fetchCameras();
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  const fetchCameras = async () => {
    try {
      const { data, error } = await supabase
        .from('cameras')
        .select('*')
        .order('is_pinned', { ascending: false })
        .order('name');

      if (error) throw error;

      const mappedCameras: Camera[] = (data || []).map(cam => ({
        id: cam.id,
        name: cam.name,
        zone: cam.zone,
        location: cam.location,
        gps: cam.gps_lat && cam.gps_lng 
          ? { lat: Number(cam.gps_lat), lng: Number(cam.gps_lng) }
          : undefined,
        status: cam.status as Camera['status'],
        streamUrl: cam.stream_url || undefined,
        thumbnail: cam.thumbnail_url || undefined,
        lastEvent: cam.last_event_at ? new Date(cam.last_event_at) : undefined,
        isPinned: cam.is_pinned || false
      }));

      setCameras(mappedCameras);
    } catch (error) {
      console.error('Failed to fetch cameras:', error);
      toast({
        title: 'Error',
        description: 'Failed to load cameras',
        variant: 'destructive'
      });
    } finally {
      setIsLoading(false);
    }
  };

  const addCamera = async (camera: Partial<Camera>) => {
    try {
      const { data, error } = await supabase
        .from('cameras')
        .insert({
          name: camera.name || 'New Camera',
          zone: camera.zone || 'Default Zone',
          location: camera.location || 'Unknown',
          stream_url: camera.streamUrl,
          gps_lat: camera.gps?.lat,
          gps_lng: camera.gps?.lng,
          status: 'offline'
        })
        .select()
        .single();

      if (error) throw error;

      toast({
        title: 'Camera Added',
        description: `${data.name} has been added to the system`
      });

      return data;
    } catch (error) {
      console.error('Failed to add camera:', error);
      toast({
        title: 'Error',
        description: 'Failed to add camera',
        variant: 'destructive'
      });
      throw error;
    }
  };

  const updateCamera = async (id: string, updates: Partial<Camera>) => {
    try {
      const { error } = await supabase
        .from('cameras')
        .update({
          name: updates.name,
          zone: updates.zone,
          location: updates.location,
          stream_url: updates.streamUrl,
          is_pinned: updates.isPinned,
          gps_lat: updates.gps?.lat,
          gps_lng: updates.gps?.lng
        })
        .eq('id', id);

      if (error) throw error;

      toast({
        title: 'Camera Updated',
        description: 'Camera settings have been saved'
      });
    } catch (error) {
      console.error('Failed to update camera:', error);
      toast({
        title: 'Error',
        description: 'Failed to update camera',
        variant: 'destructive'
      });
      throw error;
    }
  };

  const deleteCamera = async (id: string) => {
    try {
      const { error } = await supabase
        .from('cameras')
        .delete()
        .eq('id', id);

      if (error) throw error;

      toast({
        title: 'Camera Removed',
        description: 'Camera has been removed from the system'
      });
    } catch (error) {
      console.error('Failed to delete camera:', error);
      toast({
        title: 'Error',
        description: 'Failed to remove camera',
        variant: 'destructive'
      });
      throw error;
    }
  };

  const togglePin = async (id: string) => {
    const camera = cameras.find(c => c.id === id);
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
    refetch: fetchCameras
  };
}
