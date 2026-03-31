import { useState, useEffect, useCallback, useRef } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { useToast } from '@/hooks/use-toast';
import { useMediaMTXConfig } from './useMediaMTXConfig';

interface StreamStatus {
  name: string;
  ready: boolean;
  readyTime?: string;
  tracks?: string[];
  bytesReceived?: number;
  bytesSent?: number;
}

interface MediaMTXResponse {
  itemCount: number;
  pageCount: number;
  items: StreamStatus[];
}

interface CameraHealthStatus {
  cameraId: string;
  streamPath: string;
  isHealthy: boolean;
  lastCheck: Date;
  bytesReceived?: number;
  readyTime?: string;
}

export function useCameraHealth(checkIntervalMs = 30000) {
  const [healthStatuses, setHealthStatuses] = useState<Map<string, CameraHealthStatus>>(new Map());
  const [isChecking, setIsChecking] = useState(false);
  const [lastCheckTime, setLastCheckTime] = useState<Date | null>(null);
  const { toast } = useToast();
  const { serverUrl } = useMediaMTXConfig();
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const checkHealth = useCallback(async () => {
    if (!serverUrl) return;
    
    setIsChecking(true);
    
    try {
      // MediaMTX API v3 endpoint
      const apiUrl = serverUrl.replace(':8889', ':9997');
      const response = await fetch(`${apiUrl}/v3/paths/list`, {
        method: 'GET',
        headers: {
          'Accept': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`MediaMTX API returned ${response.status}`);
      }

      const data: MediaMTXResponse = await response.json();
      const now = new Date();
      
      const newStatuses = new Map<string, CameraHealthStatus>();
      
      // Process each stream path from MediaMTX
      for (const item of data.items) {
        newStatuses.set(item.name, {
          cameraId: item.name,
          streamPath: item.name,
          isHealthy: item.ready,
          lastCheck: now,
          bytesReceived: item.bytesReceived,
          readyTime: item.readyTime,
        });
      }

      setHealthStatuses(newStatuses);
      setLastCheckTime(now);

      // Update camera statuses in the database based on health
      const { data: cameras, error: fetchError } = await supabase
        .from('cameras')
        .select('id, name, stream_url, status');

      if (fetchError) {
        console.error('Failed to fetch cameras:', fetchError);
        return;
      }

      // Map camera stream URLs to MediaMTX paths and update status
      const statusUpdates: { id: string; status: 'online' | 'offline' | 'warning' }[] = [];
      
      for (const camera of cameras || []) {
        if (!camera.stream_url) continue;
        
        // Generate the expected MediaMTX path
        const safeName = camera.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        const streamPath = camera.stream_url.includes('://') 
          ? `cam-${safeName}`
          : camera.stream_url;
        
        const health = newStatuses.get(streamPath);
        
        if (health) {
          const newStatus = health.isHealthy ? 'online' : 'warning';
          if (camera.status !== newStatus && camera.status !== 'alert') {
            statusUpdates.push({ id: camera.id, status: newStatus });
          }
        } else if (camera.status === 'online') {
          // Stream not found in MediaMTX, mark as offline
          statusUpdates.push({ id: camera.id, status: 'offline' });
        }
      }

      // Batch update camera statuses
      for (const update of statusUpdates) {
        await supabase
          .from('cameras')
          .update({ status: update.status })
          .eq('id', update.id);
      }

      if (statusUpdates.length > 0) {
        console.log(`Updated ${statusUpdates.length} camera statuses`);
      }

    } catch (error) {
      console.error('Camera health check failed:', error);
      // Don't show toast on every failure, just log it
    } finally {
      setIsChecking(false);
    }
  }, [serverUrl]);

  // Start periodic health checks
  useEffect(() => {
    if (!serverUrl) return;

    // Initial check
    checkHealth();

    // Set up interval
    intervalRef.current = setInterval(checkHealth, checkIntervalMs);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [serverUrl, checkIntervalMs, checkHealth]);

  const getHealthStatus = useCallback((streamPath: string): CameraHealthStatus | undefined => {
    return healthStatuses.get(streamPath);
  }, [healthStatuses]);

  const forceCheck = useCallback(() => {
    checkHealth();
  }, [checkHealth]);

  return {
    healthStatuses,
    isChecking,
    lastCheckTime,
    getHealthStatus,
    forceCheck,
    streamCount: healthStatuses.size,
    healthyCount: Array.from(healthStatuses.values()).filter(s => s.isHealthy).length,
  };
}
