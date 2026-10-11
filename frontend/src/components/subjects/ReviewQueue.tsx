import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { FaceImage } from '@/components/subjects/FaceImage';
import { errorMessage, subjectsApi, type Review, type ReviewSide } from '@/lib/subjectsApi';

function Side({ title, side }: { title: string; side: ReviewSide }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{title}</p>
      <p className="font-mono font-semibold text-sm">{side.code}</p>
      <div className="flex gap-1">
        {side.face_ids.map((id) => (
          <FaceImage key={id} faceId={id} className="w-20 h-20" alt={`${side.code} face`} />
        ))}
      </div>
    </div>
  );
}

export function ReviewQueue({ onChanged }: { onChanged?: () => void }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    subjectsApi.reviews().then(setReviews).catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (id: string, action: 'merge' | 'reject') => {
    setBusyId(id);
    setError(null);
    try {
      await (action === 'merge' ? subjectsApi.merge(id) : subjectsApi.reject(id));
      onChanged?.();
    } catch (e) {
      setError(`Could not ${action}: ${errorMessage(e)}`);
    } finally {
      setBusyId(null);
      load();
    }
  };

  if (error && reviews === null) return <p className="text-sm text-destructive">{error}</p>;
  if (reviews === null) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      {reviews.length === 0 && <p className="text-sm text-muted-foreground">No matches waiting for review.</p>}
      {reviews.map((r) => (
        <div key={r.id} className="rounded-lg border border-border bg-card p-3 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Possible same person</p>
            <span className="text-xs text-muted-foreground">{Math.round(r.similarity * 100)}% similar</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Side title="New incident" side={r.new_subject} />
            <Side title="Existing subject" side={r.candidate} />
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={busyId === r.id} onClick={() => act(r.id, 'merge')}>
              Same person
            </Button>
            <Button size="sm" variant="outline" disabled={busyId === r.id} onClick={() => act(r.id, 'reject')}>
              Different
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
