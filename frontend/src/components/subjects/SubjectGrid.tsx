import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { FaceImage } from '@/components/subjects/FaceImage';
import { errorMessage, subjectsApi, type SubjectSummary } from '@/lib/subjectsApi';

export function SubjectGrid({ onOpen }: { onOpen: (id: string) => void }) {
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [subjects, setSubjects] = useState<SubjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSubjects(null);
    setError(null);
    subjectsApi
      .list(repeatOnly)
      .then((data) => !cancelled && setSubjects(data))
      .catch((e) => !cancelled && setError(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [repeatOnly]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Switch id="repeat-only" checked={repeatOnly} onCheckedChange={setRepeatOnly} />
        <Label htmlFor="repeat-only" className="text-sm">Repeat only</Label>
      </div>
      {error && <p className="text-sm text-destructive">Could not load subjects: {error}</p>}
      {subjects === null && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {subjects?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No subjects yet. A subject is created when a violation captures a usable face.
        </p>
      )}
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(160px,1fr))]">
        {subjects?.map((s) => (
          <button
            key={s.id}
            type="button"
            data-testid="subject-card"
            onClick={() => onOpen(s.id)}
            className="text-left rounded-lg border border-border bg-card p-2 hover:border-primary/50 transition-colors"
          >
            <FaceImage faceId={s.cover_face_id} className="w-full aspect-square" alt={`${s.code} face`} />
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="font-mono font-semibold text-sm">{s.code}</span>
              <Badge variant={s.incident_count >= 2 ? 'destructive' : 'secondary'} className="text-[10px] h-5">
                {s.incident_count >= 2 ? `Repeat ×${s.incident_count}` : '1 incident'}
              </Badge>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">
              Last seen {new Date(s.last_seen_at).toLocaleString()}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
