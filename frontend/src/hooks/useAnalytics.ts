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
  hour: string;
  count: number;
}

export interface DailyPoint {
  date: string;
  count: number;
}

export interface ZoneCount {
  zone: string;
  event_count: number;
}

export interface CategoryData {
  categories: string[];
  data: Array<Record<string, number | string>>;
}

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return await resp.json();
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

const EMPTY_CAT: CategoryData = { categories: [], data: [] };

export function useAnalytics() {
  const [summary, setSummary] = useState<AnalyticsSummary>(EMPTY_SUMMARY);
  const [hourly, setHourly] = useState<HourlyPoint[]>([]);
  const [daily, setDaily] = useState<DailyPoint[]>([]);
  const [byZone, setByZone] = useState<ZoneCount[]>([]);
  const [heatmap, setHeatmap] = useState<CategoryData>(EMPTY_CAT);
  const [dailyByCategory, setDailyByCategory] = useState<CategoryData>(EMPTY_CAT);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(false);

  const refetch = useCallback(async () => {
    setIsLoading(true);
    setError(false);
    try {
      const [s, h, d, z, hm, dbc] = await Promise.all([
        fetchJson<AnalyticsSummary>('/api/analytics/summary'),
        fetchJson<HourlyPoint[]>('/api/analytics/hourly'),
        fetchJson<DailyPoint[]>('/api/analytics/daily'),
        fetchJson<ZoneCount[]>('/api/analytics/by-zone'),
        fetchJson<CategoryData>('/api/analytics/heatmap'),
        fetchJson<CategoryData>('/api/analytics/daily-by-category'),
      ]);
      setSummary(s);
      setHourly(h);
      setDaily(d);
      setByZone(z);
      setHeatmap(hm);
      setDailyByCategory(dbc);
    } catch {
      setError(true);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { summary, hourly, daily, byZone, heatmap, dailyByCategory, isLoading, error, refetch };
}
