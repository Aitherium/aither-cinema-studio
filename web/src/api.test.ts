import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listShots, createShot, getShot, getJob, mediaUrl, apiBaseUrl } from './api';
import { LOCAL_BASE, resetConnectionForTests, setConnection } from './connection';
import { resetDemoBackend } from './demo/mockBackend';

beforeEach(() => {
  localStorage.clear();
  resetConnectionForTests();
  resetDemoBackend();
  vi.unstubAllGlobals();
});

describe('demo mode', () => {
  it('answers without a single network call', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    setConnection({ mode: 'demo' });

    const shots = await listShots();
    expect(shots).toEqual([]);

    const created = await createShot({
      mode: 't2v',
      prompt: 'a paper boat on a puddle',
      camera: { move: 'dolly_in', lens: '35mm', speed: 'slow', framing: 'medium' },
      quality: 'draft',
    });
    const shot = await getShot(created.shot_id);
    expect(shot.shot_id).toBe(created.shot_id);

    const job = await getJob(created.job_id);
    expect(job.status).toMatch(/running|done/);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(apiBaseUrl()).toBe('/api');
  });

  it('turns synthetic media ids into pre-rendered proof clips', () => {
    setConnection({ mode: 'demo' });
    expect(mediaUrl('demo-clip-0')).toContain('samples/wan_camera_draft.mp4');
    expect(mediaUrl('demo-still')).toContain('samples/keyframe_draft.png');
    expect(mediaUrl('anything-else')).toContain('samples/');
  });
});

describe('a connected backend', () => {
  it('sends the request to the chosen base', async () => {
    setConnection({ mode: 'local', baseUrl: LOCAL_BASE });
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ shots: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    await listShots();
    expect(fetchSpy).toHaveBeenCalledWith(`${LOCAL_BASE}/api/shots`, undefined);
    expect(mediaUrl('abc 123')).toBe(`${LOCAL_BASE}/api/media/abc%20123`);
  });

  it('surfaces the status and a detail slice instead of a bare failure', async () => {
    setConnection({ mode: 'remote', baseUrl: 'https://cine.example.com' });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('backend exploded', { status: 500 })));

    await expect(listShots()).rejects.toThrow(/GET \/shots → 500 backend exploded/);
  });
});
