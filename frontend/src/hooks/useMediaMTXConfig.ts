import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'wastewatch_mediamtx_config';

interface MediaMTXConfig {
  serverUrl: string;
  autoReconnect: boolean;
  healthCheckInterval: number; // in seconds
  frameAnalysisInterval: number; // in seconds
}

const DEFAULT_CONFIG: MediaMTXConfig = {
  serverUrl: import.meta.env.VITE_MEDIAMTX_URL || 'http://localhost:8889',
  autoReconnect: true,
  healthCheckInterval: 30,
  frameAnalysisInterval: 10,
};

export function useMediaMTXConfig() {
  const [config, setConfig] = useState<MediaMTXConfig>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        return { ...DEFAULT_CONFIG, ...JSON.parse(stored) };
      }
    } catch (error) {
      console.error('Failed to load MediaMTX config:', error);
    }
    return DEFAULT_CONFIG;
  });

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
