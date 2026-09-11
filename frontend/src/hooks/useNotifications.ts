import { useCallback, useEffect, useRef } from 'react';
import { useMonitoring } from '@/context/MonitoringContext';
import { API_BASE } from '@/lib/api-base';

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

  // ttsAudioUrl was accepted here but never actually played -- alerts only
  // ever got the generic beep, never the real spoken message the pipeline/
  // backend already generate (backend/app/services/alert_consumer.py
  // publishes a TTS-render request and predicts this URL). Play it when
  // present; fall back to the beep when it's not (e.g. suppress_tts alerts).
  const playTtsAudio = useCallback((ttsAudioUrl: string) => {
    if (!isSoundEnabled) return;
    try {
      const audio = new Audio(ttsAudioUrl.startsWith('http') ? ttsAudioUrl : `${API_BASE}${ttsAudioUrl}`);
      audio.volume = 0.9;
      audio.play().catch(() => { /* autoplay blocked without a prior user gesture -- ignore */ });
    } catch { /* ignore */ }
  }, [isSoundEnabled]);

  const notifyViolation = useCallback((
    title: string, message: string,
    severity: 'critical' | 'warning' | 'info',
    tag?: string, ttsAudioUrl?: string
  ) => {
    // Cooldown: 3s so backlog still fires per-alert beep/push
    const now = Date.now();
    if (now - lastNotifyRef.current < 3_000) return;
    lastNotifyRef.current = now;

    if (ttsAudioUrl) {
      playTtsAudio(ttsAudioUrl);
    } else {
      playBeep();
    }
    sendPushNotification(title, message, severity, tag);
  }, [playBeep, playTtsAudio, sendPushNotification]);

  useEffect(() => { requestPermission(); }, [requestPermission]);

  return { notifyViolation, playBeep, playTtsAudio, sendPushNotification, requestPermission };
}
