import { useCallback, useEffect, useRef } from 'react';
import { useMonitoring } from '@/context/MonitoringContext';

export function useNotifications() {
  const { isSoundEnabled } = useMonitoring();
  const lastNotifyRef = useRef(0);

  const requestPermission = useCallback(async () => {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    return (await Notification.requestPermission()) === 'granted';
  }, []);

  const sendPushNotification = useCallback((
    title: string, body: string, severity: 'critical' | 'warning' | 'info', tag?: string
  ) => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      new Notification(`${severity === 'critical' ? '🚨' : '⚠️'} ${title}`, {
        body, tag: tag || `alert-${Date.now()}`, icon: '/favicon.ico',
        requireInteraction: severity === 'critical', silent: true,
      });
    } catch { /* ignore */ }
  }, []);

  // Single short beep — no speech, no audio files
  const playBeep = useCallback(() => {
    if (!isSoundEnabled) return;
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      osc.type = 'sine';
      gain.gain.setValueAtTime(0.12, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.15);
    } catch { /* ignore */ }
  }, [isSoundEnabled]);

  const notifyViolation = useCallback((
    title: string, message: string,
    severity: 'critical' | 'warning' | 'info',
    tag?: string, _ttsAudioUrl?: string
  ) => {
    // Cooldown: 3s so backlog still fires per-alert beep/push
    const now = Date.now();
    if (now - lastNotifyRef.current < 3_000) return;
    lastNotifyRef.current = now;

    playBeep();
    sendPushNotification(title, message, severity, tag);
  }, [playBeep, sendPushNotification]);

  useEffect(() => { requestPermission(); }, [requestPermission]);

  return { notifyViolation, playBeep, sendPushNotification, requestPermission };
}
