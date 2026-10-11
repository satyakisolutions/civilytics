import { useEffect, useState } from 'react';
import { authHeaders } from '@/lib/subjectsApi';

/** Fetch a private image with auth headers and expose it as a blob: URL (revoked on change/unmount). */
export function useAuthedImage(url: string | null): string | null {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    // Clear first: a previous subject's face must never show for another id.
    setSrc(null);
    if (!url) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    fetch(url, { headers: authHeaders(), cache: 'no-store' })
      .then((resp) => {
        if (!resp.ok) {
          if (!cancelled) setSrc(null);
          return null;
        }
        return resp.blob();
      })
      .then((blob) => {
        if (!blob || cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return src;
}
