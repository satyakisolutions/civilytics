import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { FaceImage } from '@/components/subjects/FaceImage';
import { API_BASE } from '@/lib/api-base';
import { errorMessage, subjectsApi, type SubjectDetail } from '@/lib/subjectsApi';

const MATCH_LABEL: Record<string, string> = {
  new: 'First seen',
  auto: 'Auto-matched',
  review_merged: 'Confirmed by operator',
  detached: 'Detached',
};

interface SubjectDetailPanelProps {
  subjectId: string;
  onBack: () => void;
  onOpen: (id: string | null) => void;
}

export function SubjectDetailPanel({ subjectId, onBack, onOpen }: SubjectDetailPanelProps) {
  const [detail, setDetail] = useState<SubjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    subjectsApi.get(subjectId).then(setDetail).catch((e) => setError(errorMessage(e)));
  }, [subjectId]);

  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);

  const detach = async (incidentId: string) => {
    if (!window.confirm('Move this incident to a new subject? Use this when the automatic match was wrong.')) return;
    setBusy(true);
    try {
      const result = await subjectsApi.detach(subjectId, incidentId);
      if (result.subject_id) onOpen(result.subject_id);
      else load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!detail || !window.confirm(`Delete ${detail.code} and all of its face data? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await subjectsApi.remove(subjectId);
      onBack();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack} className="gap-1">
        <ArrowLeft className="w-4 h-4" /> All subjects
      </Button>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!detail && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {detail && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold font-mono">{detail.code}</h2>
              <p className="text-sm text-muted-foreground">
                {detail.incident_count} {detail.incident_count === 1 ? 'incident' : 'incidents'} · first seen{' '}
                {new Date(detail.first_seen_at).toLocaleString()} · last seen{' '}
                {new Date(detail.last_seen_at).toLocaleString()}
              </p>
            </div>
            <Button variant="destructive" size="sm" onClick={remove} disabled={busy} className="gap-1">
              <Trash2 className="w-4 h-4" /> Delete subject
            </Button>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {detail.faces.map((f) => (
              <FaceImage key={f.id} faceId={f.id} className="w-24 h-24 flex-shrink-0" alt={`${detail.code} face`} />
            ))}
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Snapshot</TableHead>
                <TableHead>Camera / zone</TableHead>
                <TableHead>Match</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.incidents.map((inc) => (
                <TableRow key={inc.incident_id}>
                  <TableCell className="text-xs font-mono">{new Date(inc.detected_at).toLocaleString()}</TableCell>
                  <TableCell>
                    {inc.snapshot_url ? (
                      <img src={`${API_BASE}${inc.snapshot_url}`} alt="Evidence snapshot" className="w-16 h-12 object-cover rounded border border-border" />
                    ) : (
                      <span className="text-xs text-muted-foreground">No frame</span>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">
                    {inc.camera_id}
                    {inc.zone ? <span className="text-muted-foreground"> · {inc.zone}</span> : null}
                  </TableCell>
                  <TableCell className="text-xs">
                    {MATCH_LABEL[inc.match_type] ?? inc.match_type}
                    {inc.similarity != null && (
                      <span className="text-muted-foreground"> ({Math.round(inc.similarity * 100)}%)</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy || detail.incidents.length <= 1}
                      onClick={() => detach(inc.incident_id)}
                    >
                      Not this person
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
}
