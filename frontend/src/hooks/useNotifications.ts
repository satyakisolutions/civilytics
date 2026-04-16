import { useCallback, useEffect, useRef } from 'react';
import { useMonitoring } from '@/context/MonitoringContext';

export function useNotifications() {
  const { isSoundEnabled } = useMonitoring();
  const audioContextRef = useRef<AudioContext | null>(null);

  const getAudioContext = useCallback(() => {
    if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    return audioContextRef.current;
  }, []);

  const playAlertSound = useCallback((severity: 'critical' | 'warning' | 'info') => {
    if (!isSoundEnabled) return;

    try {
      const ctx = getAudioContext();
      if (ctx.state === 'suspended') ctx.resume();

      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.connect(gain);
      gain.connect(ctx.destination);

      if (severity === 'critical') {
        // Urgent dual-tone siren
        oscillator.frequency.value = 880;
        oscillator.type = 'square';
        gain.gain.setValueAtTime(0.25, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.6);
        oscillator.frequency.setValueAtTime(880, ctx.currentTime);
        oscillator.frequency.setValueAtTime(660, ctx.currentTime + 0.15);
        oscillator.frequency.setValueAtTime(880, ctx.currentTime + 0.3);
        oscillator.frequency.setValueAtTime(660, ctx.currentTime + 0.45);
        oscillator.start(ctx.currentTime);
        oscillator.stop(ctx.currentTime + 0.6);
      } else if (severity === 'warning') {
        oscillator.frequency.value = 600;
        oscillator.type = 'sine';
        gain.gain.setValueAtTime(0.2, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
        oscillator.start(ctx.currentTime);
        oscillator.stop(ctx.currentTime + 0.3);
      } else {
        oscillator.frequency.value = 440;
        oscillator.type = 'sine';
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.2);
        oscillator.start(ctx.currentTime);
        oscillator.stop(ctx.currentTime + 0.2);
      }
    } catch (e) {
      console.error('Failed to play alert sound:', e);
    }
  }, [isSoundEnabled, getAudioContext]);

  const requestPermission = useCallback(async () => {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    const result = await Notification.requestPermission();
    return result === 'granted';
  }, []);

  const sendPushNotification = useCallback(async (
    title: string,
    body: string,
    severity: 'critical' | 'warning' | 'info',
    tag?: string
  ) => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;

    const icon = severity === 'critical' ? '🚨' : severity === 'warning' ? '⚠️' : 'ℹ️';

    try {
      new Notification(`${icon} ${title}`, {
        body,
        tag: tag || `violation-${Date.now()}`,
        icon: '/favicon.ico',
        requireInteraction: severity === 'critical',
        silent: true, // We handle sound ourselves
      });
    } catch (e) {
      console.error('Push notification failed:', e);
    }
  }, []);

  const playTTSAudio = useCallback(async (audioUrl: string) => {
    if (!isSoundEnabled) return;
    try {
      const audio = new Audio(audioUrl);
      audio.volume = 0.8;
      await audio.play();
    } catch (e) {
      console.warn('TTS audio playback failed, falling back to tone:', e);
      playAlertSound('critical');
    }
  }, [isSoundEnabled, playAlertSound]);

  const notifyViolation = useCallback((
    title: string,
    message: string,
    severity: 'critical' | 'warning' | 'info',
    tag?: string,
    ttsAudioUrl?: string
  ) => {
    if (ttsAudioUrl) {
      playTTSAudio(ttsAudioUrl);
    } else {
      playAlertSound(severity);
    }
    sendPushNotification(title, message, severity, tag);
  }, [playAlertSound, playTTSAudio, sendPushNotification]);

  // Request permission on mount
  useEffect(() => {
    requestPermission();
  }, [requestPermission]);

  return {
    notifyViolation,
    playAlertSound,
    playTTSAudio,
    sendPushNotification,
    requestPermission,
  };
}
