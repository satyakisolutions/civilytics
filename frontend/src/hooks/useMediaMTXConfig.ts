import { useState, useEffect, useCallback } from 'react';
import { MEDIAMTX_BASE } from '@/lib/api-base';

const STORAGE_KEY = 'wastewatch_mediamtx_config';

interface MediaMTXConfig {
  serverUrl: string;
  autoReconnect: boolean;
  healthCheckInterval: number; // in seconds
  frameAnalysisInterval: number; // in seconds
}

const DEFAULT_CONFIG: MediaMTXConfig = {
  serverUrl: MEDIAMTX_BASE,
  autoReconnect: true,
  healthCheckInterval: 30,
  frameAnalysisInterval: 10,
};

// Legacy / stale serverUrl values we should NEVER honour from localStorage.
// If one of these is cached from an earlier session, we override it with
// the runtime-derived MEDIAMTX_BASE (which uses window.location.hostname).
// Critical when the dashboard is opened on a LAN IP like 192.168.1.8:
// a cached "http://localhost:8889" would make the browser POST its WHEP
// offer to its OWN machine's localhost, which has nothing listening.
const LEGACY_STORED_URLS = new Set([
  'http://localhost:8889',
  'http://127.0.0.1:8889',
  'http://host.docker.internal:8889',
]);

export function useMediaMTXConfig() {
  const [config, setConfig] = useState<MediaMTXConfig>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Partial<MediaMTXConfig>;
        // Force the serverUrl to always come from MEDIAMTX_BASE so
        // that opening the app at a different host (localhost vs
        // 192.168.1.8) always targets the correct mediamtx.
        return {
          ...DEFAULT_CONFIG,
          ...parsed,
          serverUrl: MEDIAMTX_BASE,
        };
      }
    } catch (error) {
      console.error('Failed to load MediaMTX config:', error);
    }
    return DEFAULT_CONFIG;
  });

  // On every mount, if the cached serverUrl is one of the known-stale
  // values, flush it so validation + the WebRTC players pick up the
  // fresh hostname. Also ensure the stored copy stays in sync.
  useEffect(() => {
    if (
      config.serverUrl !== MEDIAMTX_BASE &&
      LEGACY_STORED_URLS.has(config.serverUrl)
    ) {
      setConfig((prev) => ({ ...prev, serverUrl: MEDIAMTX_BASE }));
    }
  }, [config.serverUrl]);

  const [isValidating, setIsValidating] = useState(false);
  const [isServerReachable, setIsServerReachable] = useState<boolean | null>(null);

  // Persist config changes
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    } catch (error) {
      console.error('Failed to save MediaMTX config:', error);
    }
  }, [config]);

  const updateConfig = useCallback((updates: Partial<MediaMTXConfig>) => {
    setConfig(prev => ({ ...prev, ...updates }));
  }, []);

  const resetConfig = useCallback(() => {
    setConfig(DEFAULT_CONFIG);
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  // Validate server connectivity
  const validateServer = useCallback(async (urlToTest?: string): Promise<boolean> => {
    const testUrl = urlToTest || config.serverUrl;
    setIsValidating(true);

    try {
      // Try to reach the MediaMTX API endpoint
      const apiUrl = testUrl.replace(':8889', ':9997');
      const response = await fetch(`${apiUrl}/v3/paths/list`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(5000),
      });

      const isReachable = response.ok;
      setIsServerReachable(isReachable);
      return isReachable;
    } catch (error) {
      console.error('MediaMTX server validation failed:', error);
      setIsServerReachable(false);
      return false;
    } finally {
      setIsValidating(false);
    }
  }, [config.serverUrl]);

  // Validate on mount and when URL changes (only once, not repeatedly)
  useEffect(() => {
    // Only validate if we haven't checked yet
    if (isServerReachable === null) {
      validateServer();
    }
    // Intentionally only depending on serverUrl here — validateServer
    // changes on every render via its own useCallback closure, and
    // including it would cause an infinite validate/set-state loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.serverUrl]);

  return {
    ...config,
    updateConfig,
    resetConfig,
    validateServer,
    isValidating,
    isServerReachable,
  };
}
