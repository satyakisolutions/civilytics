import { useState, useCallback } from "react";
import type { ROIPoint, ROIPolygon } from "@/types/monitoring";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8080";

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

  const finishPolygon = useCallback((name: string) => {
    if (currentPoints.length < 3) return;
    const newPoly: ROIPolygon = { name, points: currentPoints };
    const updated = [...polygons, newPoly];
    setPolygons(updated);
    setCurrentPoints([]);
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

  return {
    polygons, isEditing, setIsEditing, currentPoints, isLoading,
    fetchPolygons, addPoint, finishPolygon, removePolygon, cancelEditing,
  };
}
