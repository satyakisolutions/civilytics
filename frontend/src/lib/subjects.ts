import type { Alert } from '@/types/monitoring';

export type SubjectFields = Pick<
  Alert,
  'incidentId' | 'faceStatus' | 'subjectId' | 'subjectCode' | 'subjectIncidentCount' | 'subjectReviewPending'
>;

export type BadgeTone = 'new' | 'repeat' | 'review' | 'muted';

export interface SubjectBadgeInfo {
  label: string;
  tone: BadgeTone;
  subjectId?: string;
}

/** Map the snake_case subject fields of a GET /api/alerts item. */
export function mapSubjectFields(a: Record<string, unknown>): SubjectFields {
  return {
    incidentId: (a.incident_id as string | undefined) ?? undefined,
    faceStatus: (a.face_status as SubjectFields['faceStatus']) ?? null,
    subjectId: (a.subject_id as string | undefined) ?? undefined,
    subjectCode: (a.subject_code as string | undefined) ?? undefined,
    subjectIncidentCount: (a.subject_incident_count as number | undefined) ?? undefined,
    subjectReviewPending: Boolean(a.subject_review_pending),
  };
}

/** Neutral badge text for an alert's subject; null while pending or when there is nothing to show. */
export function subjectBadgeInfo(f: SubjectFields): SubjectBadgeInfo | null {
  if (f.faceStatus === 'no_face') return { label: 'Face not captured', tone: 'muted' };
  if (f.faceStatus !== 'matched' || !f.subjectCode) return null;
  if (f.subjectReviewPending) {
    return { label: `${f.subjectCode} · Match under review`, tone: 'review', subjectId: f.subjectId };
  }
  const count = f.subjectIncidentCount ?? 1;
  if (count >= 2) return { label: `${f.subjectCode} · Repeat ×${count}`, tone: 'repeat', subjectId: f.subjectId };
  return { label: `${f.subjectCode} · New`, tone: 'new', subjectId: f.subjectId };
}
