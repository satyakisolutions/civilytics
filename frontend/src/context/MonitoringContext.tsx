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
  const knownAlertIds = useRef<Set<string>>(new Set());
  const initialLoadDone = useRef(false);
  const lastNotifyTime = useRef(0);
  const [isSoundEnabled, setIsSoundEnabled] = useState(true);
  const soundEnabledRef = useRef(isSoundEnabled);
  useEffect(() => { soundEnabledRef.current = isSoundEnabled; }, [isSoundEnabled]);

  // Inline beep + push — can't use `useNotifications` here because that hook
  // reads this very context (would cause a circular init error).
  const fireNotification = useCallback((title: string, body: string, severity: 'critical' | 'warning' | 'info', tag?: string, ttsAudioUrl?: string) => {
    if (soundEnabledRef.current) {
      // Real spoken alert when the backend generated one (see
      // backend/app/services/alert_consumer.py) -- ttsAudioUrl was
      // previously never passed through to this function at all, so
      // every alert only ever got the generic beep, never an actual
      // "alerting audio message". Beep stays as the fallback for alerts
      // with suppress_tts / no message to speak.
      if (ttsAudioUrl) {
        try {
          const audio = new Audio(`${API_BASE}${ttsAudioUrl}`);
          audio.volume = 0.9;
          audio.play().catch(() => { /* autoplay blocked without a prior user gesture */ });
        } catch { /* ignore */ }
      } else {
        try {
          const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.frequency.value = 880;
          osc.type = 'sine';
          gain.gain.setValueAtTime(0.15, ctx.currentTime);
          gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.2);
          osc.start(ctx.currentTime);
          osc.stop(ctx.currentTime + 0.2);
        } catch { /* ignore */ }
      }
    }
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification(`${severity === 'critical' ? '🚨' : '⚠️'} ${title}`, {
          body, tag: tag || `alert-${Date.now()}`, icon: '/favicon.ico',
          requireInteraction: severity === 'critical', silent: true,
        });
      } catch { /* ignore */ }
    }
  }, []);

  // Poll real alerts from backend API every 5s + fire notifications for new critical alerts.
  const fetchAlerts = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/alerts?limit=50`);
      if (!resp.ok) return;
      const data = await resp.json();
      let mapped = (data || [])
        .filter((a: any) => a.status !== 'resolved') // hide residues from previous escalations
        .map((a: any) => ({
          id: a.id,
          eventId: a.event_id || '',
          severity: a.severity,
          title: a.title,
          message: a.message,
          timestamp: new Date(a.timestamp),
          status: a.status,
          cameraId: a.camera_id,
          zone: a.zone,
          ttsAudioUrl: a.tts_audio_url,
        }));
      // After clear, only show alerts newer than the clear timestamp
      if (clearedAtRef.current) {
        mapped = mapped.filter((a: Alert) => a.timestamp > clearedAtRef.current!);
      }

      if (!initialLoadDone.current) {
        for (const a of mapped) knownAlertIds.current.add(a.id);
        initialLoadDone.current = true;
      } else {
        const now = Date.now();
        // Tighter cooldown (3s) + fire on every new critical, not just one
        // per poll. After ClearAll we reset the dedup set so the next
        // genuine alert still notifies.
        let fired = 0;
        for (const alert of mapped) {
          if (knownAlertIds.current.has(alert.id)) continue;
          knownAlertIds.current.add(alert.id);
          if (alert.status !== 'active') continue;
          // critical AND warning both notify (browser push + audio) --
          // warning covers the waste-dump "suspicious proximity" fast-track
          // alert (pipeline/app/pipeline.py), which fires before the full
          // dwell-confirmed critical alert and would otherwise never surface
          // here at all since it was previously critical-only.
          if (alert.severity !== 'critical' && alert.severity !== 'warning') continue;
          if (now - lastNotifyTime.current < 3_000) continue;
          lastNotifyTime.current = now;
          fireNotification(alert.title, alert.message || '', alert.severity, `alert-${alert.id}`, alert.ttsAudioUrl);
          setIsAlertPanelOpen(true);
          fired += 1;
          if (fired >= 3) break; // cap per poll so a backlog doesn't spam
        }
      }

      setAlerts(mapped);
    } catch { /* API may not be available */ }
  }, [fireNotification]);

  useEffect(() => {
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 5000);
    return () => clearInterval(interval);
  }, [fetchAlerts]);

  // Bootstrap audio + notification permission on first user gesture.
  // Browsers block AudioContext and Notification.requestPermission without a gesture.
  useEffect(() => {
    let done = false;
    const unlock = () => {
      if (done) return;
      done = true;
      try {
        const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        gain.gain.value = 0;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.01);
      } catch { /* ignore */ }
      if ('Notification' in window && Notification.permission === 'default') {
        try { Notification.requestPermission(); } catch { /* ignore */ }
      }
      window.removeEventListener('click', unlock);
      window.removeEventListener('keydown', unlock);
      window.removeEventListener('touchstart', unlock);
    };
    window.addEventListener('click', unlock, { once: false });
    window.addEventListener('keydown', unlock, { once: false });
    window.addEventListener('touchstart', unlock, { once: false });
    return () => {
      window.removeEventListener('click', unlock);
      window.removeEventListener('keydown', unlock);
      window.removeEventListener('touchstart', unlock);
    };
  }, []);
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
    // Also reset the dedup set + cooldown so subsequent NEW alerts still
    // beep/blink/push. Without this, every alert id seen before clear
    // stays in `knownAlertIds` and the notification path early-returns.
    knownAlertIds.current = new Set();
    lastNotifyTime.current = 0;
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
