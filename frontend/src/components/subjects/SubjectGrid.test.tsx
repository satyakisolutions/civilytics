import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SubjectGrid } from '@/components/subjects/SubjectGrid';

const subjects = [
  { id: 's3', code: 'S-003', incident_count: 3, first_seen_at: '2026-09-01T08:00:00Z', last_seen_at: '2026-09-20T08:00:00Z', cover_face_id: null, camera_ids: ['cam-01'] },
  { id: 's1', code: 'S-001', incident_count: 1, first_seen_at: '2026-09-02T08:00:00Z', last_seen_at: '2026-09-02T08:00:00Z', cover_face_id: null, camera_ids: ['cam-02'] },
];

describe('SubjectGrid', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => subjects }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists subjects with neutral repeat labels and opens one', async () => {
    const onOpen = vi.fn();
    render(<SubjectGrid onOpen={onOpen} />);
    expect(await screen.findByText('S-003')).toBeInTheDocument();
    expect(screen.getByText('Repeat ×3')).toBeInTheDocument();
    expect(screen.getByText('1 incident')).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('subject-card')[0]);
    expect(onOpen).toHaveBeenCalledWith('s3');
  });

  it('refetches with repeat_only when the toggle is switched on', async () => {
    render(<SubjectGrid onOpen={vi.fn()} />);
    await screen.findByText('S-003');
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('repeat_only=true'))).toBe(true),
    );
  });
});
