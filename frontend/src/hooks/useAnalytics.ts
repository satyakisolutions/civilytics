import { useState, useEffect, useCallback } from 'react';
import { API_BASE } from '@/lib/api-base';

export interface AnalyticsSummary {
  total_events: number;
  events_today: number;
  events_this_week: number;
  critical_today: number;
  cameras_online: number;
  cameras_total: number;
  resolved_events: number;
  pending_events: number;
  repeat_offenders: number;
  avg_response_time_minutes: number | null;
}

export interface HourlyPoint {
  hour: string; // ISO timestamp
  count: number;
}

export interface DailyPoint {
  date: string; // YYYY-MM-DD
  count: number;
}

export interface ZoneCount {
  zone: string;
  event_count: number;
}

async function fetchJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const resp = await fetch(`${API_BASE}${path}`);
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return await resp.json();
  } catch (error) {
    console.error(`Failed to fetch ${path}:`, error);
    return fallback;
  }
}

const EMPTY_SUMMARY: AnalyticsSummary = {
  total_events: 0,
  events_today: 0,
  events_this_week: 0,
  critical_today: 0,
  cameras_online: 0,
  cameras_total: 0,
  resolved_events: 0,
  pending_events: 0,
  repeat_offenders: 0,
  avg_response_time_minutes: null,
};

export function useAnalytics() {
  const [summary, setSummary] = useState<AnalyticsSummary>(EMPTY_SUMMARY);
  const [hourly, setHourly] = useState<HourlyPoint[]>([]);
  const [daily, setDaily] = useState<DailyPoint[]>([]);
  const [byZone, setByZone] = useState<ZoneCount[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const refetch = useCallback(async () => {
    setIsLoading(true);
    const [s, h, d, z] = await Promise.all([
      fetchJson<AnalyticsSummary>('/api/analytics/summary', EMPTY_SUMMARY),
      fetchJson<HourlyPoint[]>('/api/analytics/hourly', []),
      fetchJson<DailyPoint[]>('/api/analytics/daily', []),
      fetchJson<ZoneCount[]>('/api/analytics/by-zone', []),
    ]);
    setSummary(s);
    setHourly(h);
    setDaily(d);
    setByZone(z);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { summary, hourly, daily, byZone, isLoading, refetch };
}
