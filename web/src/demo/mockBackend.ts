/**
 * The demo backend: a tiny in-page stand-in for the FastAPI studio.
 *
 * It answers exactly the routes `api.ts` calls — health, vocab, shots, grade,
 * sequence, jobs, settings, upload — with no network at all. Renders follow a
 * real timeline (a job reports running/progress and finishes after a few
 * seconds), and when they finish they point at the pre-rendered proof clips
 * copied into `public/samples/` at build time.
 *
 * The WebMCP tools are untouched: they drive the store, the store calls the
 * same `api.ts`, and `api.ts` routes here. So a visitor's agent can direct a
 * shot in demo mode exactly as it would against a real backend.
 */
import { FALLBACK_VOCAB, ids } from '../cameraVocab';

/** Only the generic proof renders already in this repo. */
export const SAMPLE_CLIPS = [
  'wan_camera_draft.mp4',
  'wan_i2v_draft.mp4',
  'wan_t2v_draft.mp4',
  'ltx_t2v_draft.mp4',
] as const;
export const SAMPLE_STILL = 'keyframe_draft.png';

const SHOT_MS = 8000;
const SEQUENCE_MS = 5000;

function samplesDir(): string {
  const base = (import.meta.env.BASE_URL ?? '/').slice();
  return `${base.endsWith('/') ? base : `${base}/`}samples/`;
}

export function sampleUrl(file: string): string {
  return `${samplesDir()}${file}`;
}

function clipFor(index: number): string {
  const safe = ((index % SAMPLE_CLIPS.length) + SAMPLE_CLIPS.length) % SAMPLE_CLIPS.length;
  return SAMPLE_CLIPS[safe];
}

/** Media ids are synthetic; only the page knows how to turn one into a URL. */
export function demoMediaUrl(mediaId: string): string {
  const clip = /^demo-clip-(\d+)$/.exec(mediaId);
  if (clip) return sampleUrl(clipFor(Number(clip[1])));
  if (mediaId === 'demo-still') return sampleUrl(SAMPLE_STILL);
  if (mediaId === 'demo-audio') return sampleUrl(SAMPLE_CLIPS[0]);
  if (mediaId.startsWith('demo-sequence')) return sampleUrl(clipFor(1));
  return sampleUrl(SAMPLE_CLIPS[0]);
}

/* ----------------------------------------------------------------- state --- */

interface DemoShot {
  shot_id: string;
  job_id: string;
  clip: number;
  mode: string;
  prompt: string;
  camera: { move: string; lens: string; speed: string; framing: string };
  engine?: string;
  quality?: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  created_at: number;
  media_id?: string;
  media_url?: string;
  compiled_prompt?: string;
}

interface DemoJob {
  job_id: string;
  kind: 'sequence' | 'shot';
  created_at: number;
  shotId?: string;
  mediaId?: string;
}

let shots: DemoShot[] = [];
let jobs = new Map<string, DemoJob>();
let counter = 0;

export function resetDemoBackend(): void {
  shots = [];
  jobs = new Map();
  counter = 0;
}

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/* --------------------------------------------------------------- advance --- */

function advance(shot: DemoShot): DemoShot {
  if (shot.status !== 'done' && shot.status !== 'error') {
    const elapsed = Date.now() - shot.created_at;
    const pct = Math.min(100, Math.round((elapsed / SHOT_MS) * 100));
    shot.progress = pct;
    if (pct >= 100) {
      shot.status = 'done';
      shot.progress = 100;
      shot.media_id = `demo-clip-${shot.clip}`;
      shot.media_url = sampleUrl(clipFor(shot.clip));
    } else {
      shot.status = 'running';
    }
  }
  return shot;
}

function jobView(job: DemoJob): Record<string, unknown> {
  const elapsed = Date.now() - job.created_at;
  const done = elapsed >= SEQUENCE_MS;
  return {
    job_id: job.job_id,
    kind: job.kind,
    status: done ? 'done' : 'running',
    progress: Math.min(100, Math.round((elapsed / SEQUENCE_MS) * 100)),
    params: {},
    result: done ? { media_id: job.mediaId } : null,
    error: null,
  };
}

/* ----------------------------------------------------------------- routes --- */

function health(): Record<string, unknown> {
  return {
    ok: true,
    version: 'demo',
    comfy: { reachable: true, base: 'demo (in this page)', version: 'demo' },
    grade: { available: true, bin: 'demo' },
  };
}

function vocab(): Record<string, unknown> {
  return {
    version: 'demo',
    moves: ids(FALLBACK_VOCAB.moves),
    lenses: ids(FALLBACK_VOCAB.lenses),
    speeds: ids(FALLBACK_VOCAB.speeds),
    framings: ids(FALLBACK_VOCAB.framings),
  };
}

function settings(): Record<string, unknown> {
  return {
    surface: 'local',
    base_url: 'demo://in-page',
    key_set: true,
    key_last4: 'demo',
  };
}

function parseBody(body: BodyInit | null | undefined): Record<string, unknown> {
  if (typeof body !== 'string' || !body.trim()) return {};
  try {
    const parsed = JSON.parse(body) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function createShot(body: Record<string, unknown>): Record<string, unknown> {
  const camera =
    body.camera && typeof body.camera === 'object'
      ? (body.camera as DemoShot['camera'])
      : { move: 'static', lens: '35mm', speed: 'slow', framing: 'medium' };
  const index = counter++;
  const id = `demo-shot-${index + 1}`;
  const shot: DemoShot = {
    shot_id: id,
    job_id: `demo-job-${index + 1}`,
    clip: index,
    mode: typeof body.mode === 'string' ? body.mode : 't2v',
    prompt: typeof body.prompt === 'string' ? body.prompt : '',
    camera,
    engine: typeof body.engine === 'string' ? body.engine : undefined,
    quality: typeof body.quality === 'string' ? body.quality : 'draft',
    status: 'running',
    progress: 0,
    created_at: Date.now(),
    compiled_prompt:
      typeof body.prompt === 'string' && body.prompt
        ? `${body.prompt} — ${camera.move}, ${camera.lens}, ${camera.speed}, ${camera.framing}`
        : undefined,
  };
  shots = [shot, ...shots];
  jobs.set(shot.job_id, { job_id: shot.job_id, kind: 'shot', created_at: shot.created_at, shotId: id });
  return { shot_id: shot.shot_id, job_id: shot.job_id };
}

function uploadRecord(init?: RequestInit): Record<string, unknown> {
  const body = init?.body;
  if (typeof FormData !== 'undefined' && body instanceof FormData) {
    const file = body.get('file');
    const type = file instanceof Blob ? file.type : '';
    if (type.startsWith('audio/')) {
      return { media_id: 'demo-audio', kind: 'audio', url: sampleUrl(SAMPLE_CLIPS[0]) };
    }
  }
  return { media_id: 'demo-still', kind: 'image', url: sampleUrl(SAMPLE_STILL) };
}

/**
 * The one entry point: `api.ts` hands over the same path and init it would
 * hand to `fetch`. Unknown routes throw loudly — a silent {} would hide bugs.
 */
export async function demoRequest<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase();
  const clean = path.split('?')[0];
  const body = parseBody(init?.body);

  if (clean === '/health' && method === 'GET') return health() as T;
  if (clean === '/camera/vocab' && method === 'GET') return vocab() as T;

  if (clean === '/settings' && method === 'GET') return settings() as T;
  if (clean === '/settings' && method === 'PUT') {
    return { ...settings(), surface: body.surface ?? 'local' } as T;
  }
  if (clean === '/settings/test') {
    return {
      ok: true,
      version: 'demo',
      message: 'Demo mode — no backend is connected; every render here is simulated in this page.',
    } as T;
  }

  if (clean === '/shots' && method === 'GET') {
    return shots.map((shot) => clone(advance(shot))) as T;
  }
  if (clean === '/shots' && method === 'POST') return createShot(body) as T;
  if (clean === '/upload') return uploadRecord(init) as T;

  const gradeMatch = /^\/shots\/([^/]+)\/grade$/.exec(clean);
  if (gradeMatch && method === 'POST') {
    const shot = shots.find((s) => s.shot_id === decodeURIComponent(gradeMatch[1]));
    if (!shot) throw new Error(`demo backend: no shot ${gradeMatch[1]}`);
    const done = advance(shot);
    // simulated grade: same clip, new media id so the monitor re-loads it
    return { media_id: done.media_id ?? `demo-clip-${shot.clip}` } as T;
  }

  const shotMatch = /^\/shots\/([^/]+)$/.exec(clean);
  if (shotMatch && method === 'GET') {
    const shot = shots.find((s) => s.shot_id === decodeURIComponent(shotMatch[1]));
    if (!shot) throw new Error(`GET /shots/${shotMatch[1]} → 404 (no such shot in the demo)`);
    return clone(advance(shot)) as T;
  }

  if (clean === '/sequence' && method === 'POST') {
    const n = counter++;
    const job: DemoJob = {
      job_id: `demo-seq-${n + 1}`,
      kind: 'sequence',
      created_at: Date.now(),
      mediaId: `demo-sequence-${n + 1}`,
    };
    jobs.set(job.job_id, job);
    return { job_id: job.job_id } as T;
  }

  const jobMatch = /^\/jobs\/([^/]+)$/.exec(clean);
  if (jobMatch && method === 'GET') {
    const id = decodeURIComponent(jobMatch[1]);
    const job = jobs.get(id);
    if (!job) throw new Error(`GET /jobs/${id} → 404 (no such job in the demo)`);
    return jobView(job) as T;
  }

  throw new Error(`${method} ${clean} is not in the demo backend`);
}
