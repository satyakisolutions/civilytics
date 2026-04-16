import { useState, useCallback, useEffect } from "react";
import type { ROIPoint, ROIPolygon } from "@/types/monitoring";
import { API_BASE } from "@/lib/api-base";

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

  const savePolygons = useCallback(async (newPolygons: ROIPolygon[]) => {
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
      }
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
    if (currentPoints.length < 3) return;
    const newPoly: ROIPolygon = { name, points: currentPoints };
    const updated = [...polygons, newPoly];
    setPolygons(updated);
    setCurrentPoints([]);
    setIsEditing(false);
    savePolygons(updated);
  }, [currentPoints, polygons, savePolygons]);

  const removePolygon = useCallback((index: number) => {
    const updated = polygons.filter((_, i) => i !== index);
    setPolygons(updated);
    savePolygons(updated);
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
