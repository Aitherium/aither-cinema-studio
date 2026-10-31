/**
 * Thin JSON client for the studio backend.
 *
 * The base is chosen at runtime (`connection.ts`): same-origin when the page is
 * served by the backend itself, an absolute http(s) base when the visitor
 * connected their own local or remote backend, and the in-page demo backend when
 * they chose demo mode — so every route below works with no network at all.
 */
import { apiBase, isDemoMode } from './connection';
import { demoMediaUrl, demoRequest } from './demo/mockBackend';
import type { GradeLook, Health, Shot, StudioSettings, Surface } from './types';

export function apiBaseUrl(): string {
  return `${apiBase()}/api`;
}

/**
 * The access token for settings writes, when the backend was bound to a
 * network address (`CINEMA_TOKEN`, printed once at startup). Loopback use needs
 * no token. Kept in memory for this tab only — never in the URL or storage.
 */
let authToken: string | null = null;

export function setAuthToken(token: string | null | undefined): void {
  const clean = (token ?? '').trim();
  authToken = clean || null;
}

export const getAuthToken = (): string | null => authToken;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (isDemoMode()) return (await demoRequest<T>(path, init)) as T;
  const headers = new Headers(init?.headers);
  if (authToken) headers.set('authorization', `Bearer ${authToken}`);
  const res = await fetch(`${apiBase()}/api${path}`, { ...init, headers });
  const text = await res.text();
  if (!res.ok) {
    const detail = text.slice(0, 240);
    throw new Error(`${init?.method ?? 'GET'} ${path} → ${res.status}${detail ? ` ${detail}` : ''}`);
  }
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

export const getHealth = () => request<Health>('/health');

export const getVocab = () => request<unknown>('/camera/vocab');

export const listShots = () => request<{ shots?: Shot[] } | Shot[]>('/shots');

export const getShot = (shotId: string) => request<Shot>(`/shots/${encodeURIComponent(shotId)}`);

export interface CreateShotBody {
  mode: string;
  prompt: string;
  camera: { move: string; lens: string; speed: string; framing: string };
  quality: 'draft' | 'final';
  engine?: string;
  start_media_id?: string;
  end_media_id?: string;
  source_media_id?: string;
  seed?: number;
}

export const createShot = (body: CreateShotBody) =>
  request<{ shot_id: string; job_id: string }>('/shots', json(body));

export const deleteShot = (shotId: string) =>
  request<{ deleted: string }>(`/shots/${encodeURIComponent(shotId)}`, { method: 'DELETE' });

export const gradeShot = (shotId: string, body: { look: GradeLook; strength?: number }) =>
  request<{ job_id?: string; media_id?: string }>(`/shots/${encodeURIComponent(shotId)}/grade`, json(body));

export interface SequenceBody {
  shot_ids: string[];
  audio_media_id?: string;
  bpm?: number;
  beats_per_shot?: number[];
}

/** `POST /api/sequence` — plain cut, or a beat-cut music video when audio is passed. */
export const cutSequence = (body: SequenceBody | string[]) =>
  request<Record<string, unknown>>('/sequence', json(Array.isArray(body) ? { shot_ids: body } : body));

export interface JobRecord {
  job_id?: string;
  kind?: string;
  status?: string;
  progress?: number;
  params?: Record<string, unknown>;
  result?: { media_id?: string; [k: string]: unknown } | null;
  error?: string | null;
}

/** `GET /api/jobs/{id}` is the real job route; `/sequence/{id}` never existed. */
export const getJob = (jobId: string) => request<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);

export const getSequence = (jobId: string) => getJob(jobId);

/* ------------------------------------------------------------- settings --- */

export interface PutSettingsBody {
  surface?: Surface;
  base_url?: string;
  /** Only ever set when the human typed a new key into the drawer. */
  api_key?: string;
}

export const getSettings = () => request<Partial<StudioSettings> & Record<string, unknown>>('/settings');

export const putSettings = (body: PutSettingsBody) =>
  request<Partial<StudioSettings> & Record<string, unknown>>('/settings', json(body, 'PUT'));

export interface SettingsTestBody {
  surface?: Surface;
  base_url?: string;
  api_key?: string;
}

/** Tests the given values, or the saved ones when the body is empty. */
export const testSettings = (body: SettingsTestBody = {}) =>
  request<Record<string, unknown>>('/settings/test', json(body));

export const upload = (file: File) => {
  const form = new FormData();
  form.append('file', file, file.name);
  return request<{ media_id: string; kind: string; url?: string }>('/upload', {
    method: 'POST',
    body: form,
  });
};

/** Demo media ids map to pre-rendered proof clips; real ids go to the backend. */
export const mediaUrl = (mediaId: string) =>
  isDemoMode()
    ? demoMediaUrl(mediaId)
    : `${apiBase()}/api/media/${encodeURIComponent(mediaId)}`;
