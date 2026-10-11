import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReviewQueue } from '@/components/subjects/ReviewQueue';

const review = {
  id: 'r1',
  incident_id: 'i2',
  similarity: 0.47,
  status: 'pending',
  created_at: '2026-09-29T10:00:00Z',
  new_subject: { subject_id: 's2', code: 'S-002', face_ids: ['f2'] },
  candidate: { subject_id: 's1', code: 'S-001', face_ids: ['f1'] },
};

describe('ReviewQueue', () => {
  let pending: unknown[];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    pending = [review];
    URL.createObjectURL = vi.fn(() => 'blob:face');
    URL.revokeObjectURL = vi.fn();
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/faces/')) return { ok: true, blob: async () => new Blob(['x']) };
      if (init?.method === 'POST') {
        pending = [];
        return { ok: true, json: async () => ({ ok: true, subject_id: 's1' }) };
      }
      return { ok: true, json: async () => pending };
    });
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows both sides and merges on "Same person"', async () => {
    const onChanged = vi.fn();
    render(<ReviewQueue onChanged={onChanged} />);
    expect(await screen.findByText('S-002')).toBeInTheDocument();
    expect(screen.getByText('S-001')).toBeInTheDocument();
    expect(screen.getByText('47% similar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Same person' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).endsWith('/api/subjects/reviews/r1/merge') && init?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('No matches waiting for review.')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it('rejects on "Different"', async () => {
    render(<ReviewQueue />);
    await screen.findByText('S-002');
    fireEvent.click(screen.getByRole('button', { name: 'Different' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/api/subjects/reviews/r1/reject'))).toBe(true),
    );
  });
});
