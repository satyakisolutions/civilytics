import { useState, useEffect, useCallback, useRef, createContext, useContext, ReactNode } from 'react';
import { GridLayout, Alert, ViolationEvent } from '@/types/monitoring';
import { mockViolationEvents } from '@/data/mockData';
import { API_BASE } from '@/lib/api-base';

interface MonitoringContextType {
  gridLayout: GridLayout;
  setGridLayout: (layout: GridLayout) => void;
  selectedCamera: string | null;
  setSelectedCamera: (id: string | null) => void;
  alerts: Alert[];
  events: ViolationEvent[];
  acknowledgeAlert: (id: string) => void;
  clearAlerts: () => void;
  isAlertPanelOpen: boolean;
  setIsAlertPanelOpen: (open: boolean) => void;
  isSoundEnabled: boolean;
  setIsSoundEnabled: (enabled: boolean) => void;
  currentView: 'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline';
  setCurrentView: (view: 'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline') => void;
}

const MonitoringContext = createContext<MonitoringContextType | undefined>(undefined);

export function MonitoringProvider({ children }: { children: ReactNode }) {
  const [gridLayout, setGridLayout] = useState<GridLayout>('3x3');
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [events] = useState<ViolationEvent[]>(mockViolationEvents);
  const [isAlertPanelOpen, setIsAlertPanelOpen] = useState(false);
  const clearedAtRef = useRef<Date | null>(null);

  // Poll real alerts from backend API every 5s
  const fetchAlerts = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/alerts?limit=50`);
      if (!resp.ok) return;
      const data = await resp.json();
      let mapped = (data || []).map((a: any) => ({
        id: a.id,
        eventId: a.event_id || '',
        severity: a.severity,
        title: a.title,
        message: a.message,
        timestamp: new Date(a.timestamp),
        status: a.status,
        cameraId: a.camera_id,
        zone: a.zone,
      }));
      // After clear, only show alerts newer than the clear timestamp
      if (clearedAtRef.current) {
        mapped = mapped.filter((a: Alert) => a.timestamp > clearedAtRef.current!);
      }
      setAlerts(mapped);
    } catch { /* API may not be available */ }
  }, []);

  useEffect(() => {
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 5000);
    return () => clearInterval(interval);
  }, [fetchAlerts]);
  const [isSoundEnabled, setIsSoundEnabled] = useState(true);
  const [currentView, setCurrentView] = useState<'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline'>('monitoring');

  const acknowledgeAlert = async (id: string) => {
    // Update locally first (instant UI feedback)
    setAlerts(prev =>
      prev.map(alert =>
        alert.id === id
          ? { ...alert, status: 'acknowledged' as const }
          : alert
      )
    );
    // Persist to backend
    try {
      await fetch(`${API_BASE}/api/alerts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'acknowledged' }),
      });
    } catch { /* best effort */ }
  };

  const clearAlerts = () => {
    clearedAtRef.current = new Date();
    setAlerts([]);
  };

  return (
    <MonitoringContext.Provider
      value={{
        gridLayout,
        setGridLayout,
        selectedCamera,
        setSelectedCamera,
        alerts,
        events,
        acknowledgeAlert,
        clearAlerts,
        isAlertPanelOpen,
        setIsAlertPanelOpen,
        isSoundEnabled,
        setIsSoundEnabled,
        currentView,
        setCurrentView,
      }}
    >
      {children}
    </MonitoringContext.Provider>
  );
}

export function useMonitoring() {
  const context = useContext(MonitoringContext);
  if (context === undefined) {
    throw new Error('useMonitoring must be used within a MonitoringProvider');
  }
  return context;
}
