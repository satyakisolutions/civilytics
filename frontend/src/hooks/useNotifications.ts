import { useCallback, useEffect, useRef } from 'react';
import { useMonitoring } from '@/context/MonitoringContext';
import { API_BASE } from '@/lib/api-base';

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
      const resp = await fetch(audioUrl, { method: 'HEAD' });
      if (resp.ok) {
        const audio = new Audio(audioUrl);
        audio.volume = 0.8;
        await audio.play();
        return;
      }
    } catch { /* fall through to browser TTS */ }
    // Fallback: browser's built-in speech synthesis
    playAlertSound('critical');
  }, [isSoundEnabled, playAlertSound]);

  // Browser-native speech synthesis — works without Piper TTS server
  const speakText = useCallback((text: string) => {
    if (!isSoundEnabled || !('speechSynthesis' in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 0.9;
    // Prefer an English voice
    const voices = speechSynthesis.getVoices();
    const enVoice = voices.find(v => v.lang.startsWith('en'));
    if (enVoice) utterance.voice = enVoice;
    speechSynthesis.speak(utterance);
  }, [isSoundEnabled]);

  // Try to play TTS audio by computing the hash of the TTS text
  // TTS worker generates: /tts/tts_{sha256(text)[:16]}.wav
  const tryPlayTTS = useCallback(async (text: string, severity: 'critical' | 'warning' | 'info') => {
    if (!isSoundEnabled || !text) { playAlertSound(severity); return; }
    try {
      // Build the TTS text the pipeline sends (matches kafka_producer.send_tts_request)
      const ttsText = severity === 'critical'
        ? `Alert. Violation confirmed at camera ${text}`
        : `Warning. Suspicious activity detected at camera ${text}`;
      // SHA256 hash (first 16 chars) — same as backend TTSEngine
      const encoder = new TextEncoder();
      const data = encoder.encode(text);
      const hashBuffer = await crypto.subtle.digest('SHA-256', data);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
      const ttsUrl = `${API_BASE}/tts/tts_${hashHex}.wav`;
      // Try to fetch — if 404, fall back to beep
      const resp = await fetch(ttsUrl, { method: 'HEAD' });
      if (resp.ok) {
        playTTSAudio(ttsUrl);
      } else {
        playAlertSound(severity);
      }
    } catch {
      playAlertSound(severity);
    }
  }, [isSoundEnabled, playAlertSound, playTTSAudio]);

  const notifyViolation = useCallback((
    title: string,
    message: string,
    severity: 'critical' | 'warning' | 'info',
    tag?: string,
    ttsAudioUrl?: string
  ) => {
    // Play alert sound first (beep)
    playAlertSound(severity);
    // Then speak the message using browser's speech synthesis
    if (message) {
      speakText(`${severity === 'critical' ? 'Alert.' : 'Warning.'} ${title}. ${message}`);
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
    speakText,
    sendPushNotification,
    requestPermission,
  };
}
