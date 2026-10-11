import { API_BASE } from '@/lib/api-base';

export interface SubjectSummary {
  id: string;
  code: string;
  incident_count: number;
  first_seen_at: string;
  last_seen_at: string;
  cover_face_id: string | null;
  camera_ids: string[];
}

export interface SubjectFace {
  id: string;
  incident_id: string;
  quality: number;
  created_at: string;
}

export interface SubjectIncident {
  incident_id: string;
  camera_id: string;
  detected_at: string;
  match_type: 'new' | 'auto' | 'review_merged' | 'detached' | string;
  similarity: number | null;
  snapshot_url: string | null;
  zone: string | null;
  title: string | null;
}

export interface SubjectDetail extends SubjectSummary {
  status: string;
  faces: SubjectFace[];
  incidents: SubjectIncident[];
}

export interface ReviewSide {
  subject_id: string;
  code: string;
  face_ids: string[];
}

export interface Review {
  id: string;
  incident_id: string;
  similarity: number;
  status: string;
  created_at: string;
  new_subject: ReviewSide;
  candidate: ReviewSide;
}

export interface ActionResult {
  ok: boolean;
  subject_id: string | null;
}

/**
 * Headers for subject endpoints. The dashboard has no login yet (spec §11,
 * known gap), so this is empty; add the Keycloak bearer token here once
 * dashboard login exists and every subject request picks it up.
 */
export function authHeaders(): Record<string, string> {
  return {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!resp.ok) throw new Error(`${init?.method ?? 'GET'} ${path} failed (${resp.status})`);
  return resp.json() as Promise<T>;
}

export const subjectsApi = {
  list: (repeatOnly: boolean) =>
    request<SubjectSummary[]>(`/api/subjects?repeat_only=${repeatOnly}&sort=incidents`),
  get: (id: string) => request<SubjectDetail>(`/api/subjects/${id}`),
  reviews: () => request<Review[]>('/api/subjects/reviews?status=pending'),
  reviewCount: () => request<{ pending: number }>('/api/subjects/reviews/count'),
  merge: (reviewId: string) =>
    request<ActionResult>(`/api/subjects/reviews/${reviewId}/merge`, { method: 'POST' }),
  reject: (reviewId: string) =>
    request<ActionResult>(`/api/subjects/reviews/${reviewId}/reject`, { method: 'POST' }),
  detach: (subjectId: string, incidentId: string) =>
    request<ActionResult>(`/api/subjects/${subjectId}/incidents/${incidentId}/detach`, { method: 'POST' }),
  remove: (subjectId: string) => request<ActionResult>(`/api/subjects/${subjectId}`, { method: 'DELETE' }),
  faceUrl: (faceId: string) => `${API_BASE}/api/subjects/faces/${faceId}`,
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
