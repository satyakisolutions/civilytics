import { useState, useCallback, useEffect } from 'react';

const STORAGE_KEY = 'wastewatch-camera-order';

interface CameraOrderState {
  [zone: string]: string[]; // zone -> ordered camera IDs
}

export function useCameraOrder() {
  const [cameraOrder, setCameraOrder] = useState<CameraOrderState>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : {};
    } catch {
      return {};
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cameraOrder));
    } catch {
      // Storage full or unavailable
    }
  }, [cameraOrder]);

  const setZoneOrder = useCallback((zone: string, orderedIds: string[]) => {
    setCameraOrder(prev => ({
      ...prev,
      [zone]: orderedIds,
    }));
  }, []);

  const getZoneOrder = useCallback((zone: string): string[] | undefined => {
    return cameraOrder[zone];
  }, [cameraOrder]);

  const resetOrder = useCallback(() => {
    setCameraOrder({});
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  return {
    cameraOrder,
    setZoneOrder,
    getZoneOrder,
    resetOrder,
  };
}
