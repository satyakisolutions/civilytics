import { describe, it, expect } from 'vitest';
import { computeAlertBorderClass } from './CameraFeed';
import type { CameraStatus } from '@/types/monitoring';

/**
 * CI-52 regression tests — the camera tile must pulse a red border on a dump.
 *
 * The original bug: three competing border systems on one tile, and the only
 * static-red path (`.camera-feed.alert`) referenced a keyframe that never
 * existed, so the border read as transparent. `animate-alert-border` is now the
 * sole driver. These tests pin that contract at the decision point.
 */
describe('computeAlertBorderClass (CI-52)', () => {
  it('drives the border on a confirmed alert status', () => {
    expect(computeAlertBorderClass('alert', false)).toBe('animate-alert-border');
  });

  it('drives the border while an escalation is held, even if status has fallen back', () => {
    // The FSM reports "violation" only on the firing frame(s); the tile holds
    // the border for a few seconds after (alertHeldUntil). status can be
    // 'online' again while isAlertHeld is still true.
    expect(computeAlertBorderClass('online', true)).toBe('animate-alert-border');
  });

  it('shows no border for a healthy, non-held camera', () => {
    expect(computeAlertBorderClass('online', false)).toBe('');
  });

  it.each<CameraStatus>(['offline', 'warning'])(
    'shows no border for %s when not held (warning intentionally does not pulse)',
    (status) => {
      expect(computeAlertBorderClass(status, false)).toBe('');
    },
  );

  it('never emits the removed `alert` class (the dead-keyframe path)', () => {
    const outputs = (['online', 'offline', 'warning', 'alert'] as CameraStatus[])
      .flatMap((s) => [computeAlertBorderClass(s, true), computeAlertBorderClass(s, false)]);
    for (const cls of outputs) {
      expect(cls.split(/\s+/)).not.toContain('alert');
    }
  });
});
