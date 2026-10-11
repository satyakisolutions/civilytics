import { describe, expect, it } from 'vitest';
import { mapSubjectFields, subjectBadgeInfo } from '@/lib/subjects';

describe('mapSubjectFields', () => {
  it('maps API snake_case fields', () => {
    expect(
      mapSubjectFields({
        incident_id: 'i1',
        face_status: 'matched',
        subject_id: 's1',
        subject_code: 'S-001',
        subject_incident_count: 2,
        subject_review_pending: true,
      }),
    ).toEqual({
      incidentId: 'i1',
      faceStatus: 'matched',
      subjectId: 's1',
      subjectCode: 'S-001',
      subjectIncidentCount: 2,
      subjectReviewPending: true,
    });
  });

  it('defaults missing fields', () => {
    expect(mapSubjectFields({})).toEqual({
      incidentId: undefined,
      faceStatus: null,
      subjectId: undefined,
      subjectCode: undefined,
      subjectIncidentCount: undefined,
      subjectReviewPending: false,
    });
  });
});

describe('subjectBadgeInfo', () => {
  const matched = { faceStatus: 'matched' as const, subjectId: 's1', subjectCode: 'S-007' };

  it('shows nothing while pending, deleted, or without a code', () => {
    expect(subjectBadgeInfo({ faceStatus: null })).toBeNull();
    expect(subjectBadgeInfo({ faceStatus: 'deleted' })).toBeNull();
    expect(subjectBadgeInfo({ faceStatus: 'matched' })).toBeNull();
  });

  it('labels no-face incidents', () => {
    expect(subjectBadgeInfo({ faceStatus: 'no_face' })).toEqual({ label: 'Face not captured', tone: 'muted' });
  });

  it('labels new, repeat and review states', () => {
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 1 })).toEqual({
      label: 'S-007 · New', tone: 'new', subjectId: 's1',
    });
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 3 })).toEqual({
      label: 'S-007 · Repeat ×3', tone: 'repeat', subjectId: 's1',
    });
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 1, subjectReviewPending: true })).toEqual({
      label: 'S-007 · Match under review', tone: 'review', subjectId: 's1',
    });
  });

  it('never uses accusatory wording', () => {
    const labels = [1, 2, 5].map((n) => subjectBadgeInfo({ ...matched, subjectIncidentCount: n })?.label ?? '');
    for (const label of labels) expect(label.toLowerCase()).not.toMatch(/offender|culprit|criminal/);
  });
});
