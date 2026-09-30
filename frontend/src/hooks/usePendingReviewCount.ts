import { useEffect, useState } from 'react';
import { subjectsApi } from '@/lib/subjectsApi';

/** Pending face-match reviews, polled for the sidebar badge (endpoint is not access-logged). */
export function usePendingReviewCount(intervalMs = 30_000): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      subjectsApi
        .reviewCount()
        .then((r) => {
          if (!cancelled) setCount(r.pending);
        })
        .catch(() => {});
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [intervalMs]);
  return count;
}
