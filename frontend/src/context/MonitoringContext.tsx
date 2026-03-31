import { useState, createContext, useContext, ReactNode } from 'react';
import { GridLayout, Alert, ViolationEvent } from '@/types/monitoring';
import { mockAlerts, mockViolationEvents } from '@/data/mockData';

interface MonitoringContextType {
  gridLayout: GridLayout;
  setGridLayout: (layout: GridLayout) => void;
  selectedCamera: string | null;
  setSelectedCamera: (id: string | null) => void;
  alerts: Alert[];
  events: ViolationEvent[];
  acknowledgeAlert: (id: string) => void;
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
  const [alerts, setAlerts] = useState<Alert[]>(mockAlerts);
  const [events] = useState<ViolationEvent[]>(mockViolationEvents);
  const [isAlertPanelOpen, setIsAlertPanelOpen] = useState(false);
  const [isSoundEnabled, setIsSoundEnabled] = useState(true);
  const [currentView, setCurrentView] = useState<'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline'>('monitoring');

  const acknowledgeAlert = (id: string) => {
    setAlerts(prev => 
      prev.map(alert => 
        alert.id === id 
          ? { ...alert, status: 'acknowledged' as const }
          : alert
      )
    );
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
