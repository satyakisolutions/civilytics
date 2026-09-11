import { useEffect, useMemo, useRef, useState } from "react";
import type { StreamQuality } from "@/components/camera/StreamQualityToggle";

/**
 * Breakpoints (in CSS pixels, at devicePixelRatio=1) for the three
 * transcoded stream tiers. A panel narrower than `small` keeps the
 * `small` stream; panels between `small` and `medium` get the medium
 * tier; anything wider gets the native large tier.
 *
 * Tuned for a monitoring wall:
 *   - 4x4 grid on 1080p    -> ~480 px panel  -> medium
 *   - 2x2 grid on 1080p    -> ~960 px panel  -> large
 *   - 1x1 fullscreen       -> 1920 px panel  -> large
 *   - thumbnail strip      -> ~260 px panel  -> small
 */
export const AUTO_QUALITY_BREAKPOINTS = {
  smallMaxWidth: 480,
  mediumMaxWidth: 960,
} as const;

function pickQualityForWidth(width: number): StreamQuality {
  if (width <= 0) return "medium";
  if (width < AUTO_QUALITY_BREAKPOINTS.smallMaxWidth) return "small";
  if (width < AUTO_QUALITY_BREAKPOINTS.mediumMaxWidth) return "medium";
  return "large";
}

export interface UseAutoStreamQualityResult {
  /** React ref — attach to the element whose size should drive quality. */
  ref: React.RefObject<HTMLDivElement>;
  /** The quality the hook would auto-select based on current width. */
  autoQuality: StreamQuality;
  /** Measured width of the observed element, in CSS pixels. */
  width: number;
  /** Measured height of the observed element, in CSS pixels. */
  height: number;
}

/**
 * Observes the size of an element and returns the ideal stream tier
 * for its current rendered width. The observation is debounced to
 * avoid flapping between tiers during drag-resize or grid reflow.
 */
export function useAutoStreamQuality(
  debounceMs: number = 200
): UseAutoStreamQualityResult {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const pendingRef = useRef<number | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;

      if (pendingRef.current) window.clearTimeout(pendingRef.current);
      pendingRef.current = window.setTimeout(() => {
        setSize((prev) =>
          Math.abs(prev.width - width) < 2 && Math.abs(prev.height - height) < 2
            ? prev
            : { width: Math.round(width), height: Math.round(height) }
        );
      }, debounceMs);
    });

    observer.observe(element);
    // Seed initial size synchronously so first render doesn't jump.
    const rect = element.getBoundingClientRect();
    setSize({ width: Math.round(rect.width), height: Math.round(rect.height) });

    return () => {
      observer.disconnect();
      if (pendingRef.current) window.clearTimeout(pendingRef.current);
    };
  }, [debounceMs]);

  const autoQuality = useMemo(() => pickQualityForWidth(size.width), [size.width]);

  return {
    ref,
    autoQuality,
    width: size.width,
    height: size.height,
  };
}
