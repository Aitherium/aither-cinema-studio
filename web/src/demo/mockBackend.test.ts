import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FALLBACK_VOCAB, ids } from '../cameraVocab';
import {
  SAMPLE_CLIPS,
  demoMediaUrl,
  demoRequest,
  resetDemoBackend,
  sampleUrl,
} from './mockBackend';

beforeEach(() => {
  resetDemoBackend();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const jsonInit = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

describe('the demo backend speaks the real routes', () => {
  it('answers /health the way the studio expects', async () => {
    const health = await demoRequest<Record<string, any>>('/health');
    expect(health.ok).toBe(true);
    expect(health.comfy.reachable).toBe(true);
    expect(health.grade.available).toBe(true);
  });

  it('serves the same camera vocabulary the UI offers', async () => {
    const vocab = await demoRequest<Record<string, string[]>>('/camera/vocab');
    expect(vocab.moves).toEqual(ids(FALLBACK_VOCAB.moves));
    expect(vocab.lenses).toEqual(ids(FALLBACK_VOCAB.lenses));
    expect(vocab.framings).toEqual(ids(FALLBACK_VOCAB.framings));
  });

  it('rings a shot through queued → running → done and points at a proof clip', async () => {
    let now = 1_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);

    const created = await demoRequest<{ shot_id: string; job_id: string }>(
      '/shots',
      jsonInit({
        mode: 't2v',
        prompt: 'a kite over a grey beach',
        camera: { move: 'dolly_in', lens: '35mm', speed: 'slow', framing: 'medium' },
      }),
    );

    const early = await demoRequest<Record<string, any>>(`/shots/${created.shot_id}`);
    expect(early.status).toBe('running');
    expect(early.progress).toBeLessThan(100);

    now += 4_000;
    const middle = await demoRequest<Record<string, any>>(`/shots/${created.shot_id}`);
    expect(middle.progress).toBeGreaterThan(0);

    now += 5_000;
    const done = await demoRequest<Record<string, any>>(`/shots/${created.shot_id}`);
    expect(done.status).toBe('done');
    expect(done.progress).toBe(100);
    expect(done.media_id).toBe('demo-clip-0');
    expect(done.media_url).toContain(SAMPLE_CLIPS[0]);
  });

  it('finishes a cut sequence on its own timeline', async () => {
    let now = 5_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    const { job_id } = await demoRequest<{ job_id: string }>(
      '/sequence',
      jsonInit({ shot_ids: ['a', 'b'] }),
    );
    const running = await demoRequest<Record<string, any>>(`/jobs/${job_id}`);
    expect(running.status).toBe('running');
    now += 6_000;
    const done = await demoRequest<Record<string, any>>(`/jobs/${job_id}`);
    expect(done.status).toBe('done');
    expect(String(done.result.media_id)).toMatch(/^demo-sequence/);
  });

  it('grades without waiting on a render', async () => {
    const created = await demoRequest<{ shot_id: string }>(
      '/shots',
      jsonInit({ mode: 't2v', prompt: 'x', camera: { move: 'static', lens: '35mm', speed: 'slow', framing: 'medium' } }),
    );
    const graded = await demoRequest<{ media_id: string }>(
      `/shots/${created.shot_id}/grade`,
      jsonInit({ look: 'teal', strength: 0.5 }),
    );
    expect(graded.media_id).toBe('demo-clip-0');
  });

  it('fails loudly on a route it does not implement', async () => {
    await expect(demoRequest('/nope')).rejects.toThrow(/not in the demo backend/);
    await expect(demoRequest('/shots/missing')).rejects.toThrow(/404/);
  });
});

describe('sample media', () => {
  it('resolves against the build base, so a sub-path deploy works', () => {
    expect(sampleUrl('wan_t2v_draft.mp4')).toMatch(/^\/samples\/wan_t2v_draft\.mp4$/);
    expect(demoMediaUrl('demo-clip-3')).toContain(SAMPLE_CLIPS[3]);
    expect(demoMediaUrl('demo-sequence-1')).toContain(SAMPLE_CLIPS[1]);
  });
});
