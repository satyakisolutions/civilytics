import { useState, useCallback, useEffect } from "react";
import type { ROIPoint, ROIPolygon } from "@/types/monitoring";
import { API_BASE } from "@/lib/api-base";
import { toast } from "@/hooks/use-toast";
import { logUiEvent } from "@/lib/client-log-capture";

interface UseROIEditorOptions {
  cameraId: string;
}

export function useROIEditor({ cameraId }: UseROIEditorOptions) {
  const [polygons, setPolygons] = useState<ROIPolygon[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [currentPoints, setCurrentPoints] = useState<ROIPoint[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchPolygons = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/cameras/${cameraId}/roi`);
      if (resp.ok) {
        const data = await resp.json();
        setPolygons(data.polygons || []);
      }
    } catch { /* API may not be available */ }
  }, [cameraId]);

  const savePolygons = useCallback(async (newPolygons: ROIPolygon[], previousPolygons: ROIPolygon[]) => {
    setIsLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/api/cameras/${cameraId}/roi`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newPolygons),
      });
      if (resp.ok) {
        const data = await resp.json();
        setPolygons(data.polygons || []);
        logUiEvent("roi.saved", { camera: cameraId, count: newPolygons.length });
      } else {
        // Roll back the optimistic update — an unconfirmed failed save
        // must not leave the UI showing changes that never persisted.
        setPolygons(previousPolygons);
        toast({
          title: "Couldn't save ROI",
          description: `Server responded with ${resp.status}. Your change was not saved — try again.`,
          variant: "destructive",
        });
      }
    } catch {
      setPolygons(previousPolygons);
      toast({
        title: "Couldn't save ROI",
        description: "Network error — your change was not saved. Try again.",
        variant: "destructive",
      });
    } finally {
      setIsLoading(false);
    }
  }, [cameraId]);

  const addPoint = useCallback((point: ROIPoint) => {
    setCurrentPoints(prev => [...prev, point]);
  }, []);

  const undoPoint = useCallback(() => {
    setCurrentPoints(prev => prev.slice(0, -1));
  }, []);

  const finishPolygon = useCallback((name: string) => {
    if (currentPoints.length < 3) {
      toast({
        title: "Need at least 3 points",
        description: "Click 3 or more points on the video to draw a zone before saving.",
      });
      return;
    }
    const newPoly: ROIPolygon = { name, points: currentPoints };
    const updated = [...polygons, newPoly];
    setPolygons(updated);
    setCurrentPoints([]);
    setIsEditing(false);
    savePolygons(updated, polygons);
  }, [currentPoints, polygons, savePolygons]);

  const removePolygon = useCallback((index: number) => {
    const updated = polygons.filter((_, i) => i !== index);
    setPolygons(updated);
    savePolygons(updated, polygons);
  }, [polygons, savePolygons]);

  const cancelEditing = useCallback(() => {
    setCurrentPoints([]);
    setIsEditing(false);
  }, []);

  // Re-fetch on window focus so deleted/updated ROIs sync from backend
  useEffect(() => {
    const onFocus = () => { if (!isEditing) fetchPolygons(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [fetchPolygons, isEditing]);

  // Poll every 30s to stay in sync (covers cases where user doesn't switch tabs)
  useEffect(() => {
    const interval = setInterval(() => { if (!isEditing) fetchPolygons(); }, 30_000);
    return () => clearInterval(interval);
  }, [fetchPolygons, isEditing]);

  return {
    polygons, isEditing, setIsEditing, currentPoints, isLoading,
    fetchPolygons, addPoint, undoPoint, finishPolygon, removePolygon, cancelEditing,
  };
}
