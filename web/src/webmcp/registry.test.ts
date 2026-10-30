import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FALLBACK_VOCAB } from '../cameraVocab';
import { setPollingEnabled, useStudio } from '../store';
import { ENGINES, GRADE_LOOKS, SHOT_MODES, SURFACES } from '../types';
import { CONTRACT_TOOL_NAMES } from '../tools';
import { ModelContext, resetModelContextForTests } from './polyfill';
import { createStudioRegistry } from './registry';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
  setPollingEnabled(false);
  useStudio.getState().reset();
  resetModelContextForTests();
  vi.unstubAllGlobals();
});

describe('studio tool registry', () => {
  it('exposes exactly the contract tools with valid JSON Schemas', async () => {
    const registry = await createStudioRegistry({
      vocab: FALLBACK_VOCAB,
      context: new ModelContext(),
      expose: false,
    });

    expect(registry.names().sort()).toEqual([...CONTRACT_TOOL_NAMES].sort());
    expect(registry.tools()).toHaveLength(CONTRACT_TOOL_NAMES.length);

    const allowedEnums: Record<string, Set<string>> = {
      move: new Set(FALLBACK_VOCAB.moves.map((m) => m.id)),
      lens: new Set(FALLBACK_VOCAB.lenses.map((l) => l.id)),
      speed: new Set(FALLBACK_VOCAB.speeds.map((s) => s.id)),
      framing: new Set(FALLBACK_VOCAB.framings.map((f) => f.id)),
      mode: new Set(SHOT_MODES.map((m) => m.id)),
      engine: new Set(ENGINES.map((e) => e.id)),
      surface: new Set(SURFACES.map((s) => s.id)),
      quality: new Set(['draft', 'final']),
      look: new Set(GRADE_LOOKS.map((g) => g.id)),
    };

    for (const tool of registry.tools()) {
      expect(tool.name).toMatch(/^[a-z0-9][a-z0-9.-]*$/);
      expect(tool.description.length).toBeGreaterThan(10);

      const schema = tool.inputSchema;
      expect(schema).toMatchObject({ type: 'object' });
      const properties = schema.properties ?? {};
      expect(typeof properties).toBe('object');

      for (const key of schema.required ?? []) {
        expect(Object.keys(properties)).toContain(key);
      }

      for (const [key, prop] of Object.entries(properties)) {
        if (!prop.enum) continue;
        expect(prop.enum.length).toBeGreaterThan(0);
        const allowed = allowedEnums[key];
        expect(allowed, `enum on "${key}" must come from the camera vocabulary`).toBeDefined();
        for (const value of prop.enum) {
          expect(allowed.has(String(value))).toBe(true);
        }
      }
    }
  });

  it('builds enums from a vocab the backend served', async () => {
    const custom = {
      ...FALLBACK_VOCAB,
      moves: [{ id: 'static', label: 'Static' }, { id: 'vertigo', label: 'Vertigo' }],
    };
    const registry = await createStudioRegistry({ vocab: custom, context: new ModelContext(), expose: false });
    const setCamera = registry.tools().find((t) => t.name === 'set-camera');
    expect(setCamera?.inputSchema.properties?.move.enum).toEqual(['static', 'vertigo']);
  });

  it('registers every tool into the model context', async () => {
    const ctx = new ModelContext();
    await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: ctx, expose: false });
    const registered = await ctx.getTools();
    expect(registered.map((t) => t.name).sort()).toEqual([...CONTRACT_TOOL_NAMES].sort());
  });

  it('drops every tool when disposed', async () => {
    const ctx = new ModelContext();
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: ctx, expose: false });
    registry.dispose();
    expect(await ctx.getTools()).toHaveLength(0);
  });
});

describe('tool calls drive the store and the feed', () => {
  it('set-camera moves the rig and logs one agent entry', async () => {
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });

    await registry.call('set-camera', { move: 'orbit_left', lens: '85mm' }, 'agent');

    const state = useStudio.getState();
    expect(state.rig.move).toBe('orbit_left');
    expect(state.rig.lens).toBe('85mm');
    expect(state.feed).toHaveLength(1);
    expect(state.feed[0]).toMatchObject({
      tool: 'set-camera',
      actor: 'agent',
      status: 'ok',
      args: { move: 'orbit_left', lens: '85mm' },
    });
  });

  it('plan-shot writes mode, prompt, quality and rig without rendering', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });

    const plan = (await registry.call('plan-shot', {
      prompt: 'a train leaves at dusk',
      mode: 'i2v',
      quality: 'final',
      move: 'crane_up',
      framing: 'dutch',
    })) as Record<string, unknown>;

    const state = useStudio.getState();
    expect(state.mode).toBe('i2v');
    expect(state.prompt).toBe('a train leaves at dusk');
    expect(state.quality).toBe('final');
    expect(state.rig.move).toBe('crane_up');
    expect(state.rig.framing).toBe('dutch');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(plan.camera).toMatchObject({ move: 'crane_up' });
    expect(state.feed[0].tool).toBe('plan-shot');
  });

  it('render-shot posts to /api/shots, adds the shot and logs it', async () => {
    const fetchSpy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      if (String(url).endsWith('/api/shots') && init?.method === 'POST') {
        return jsonResponse({ shot_id: 'shot-1', job_id: 'job-1' });
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchSpy);

    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });
    const result = (await registry.call(
      'render-shot',
      { prompt: 'rain on the platform', mode: 't2v', quality: 'draft', move: 'dolly_in' },
      'agent',
    )) as { shot_id: string; job_id: string };

    expect(result).toMatchObject({ shot_id: 'shot-1', job_id: 'job-1' });

    const call = fetchSpy.mock.calls[0];
    const body = JSON.parse(String(call[1]?.body));
    expect(body).toMatchObject({ mode: 't2v', prompt: 'rain on the platform', quality: 'draft' });
    expect(body.camera).toMatchObject({ move: 'dolly_in' });
    // the engine reaches the wire as a builder id the backend knows
    expect(body.engine).toBe('wan_t2v');

    const state = useStudio.getState();
    expect(state.shots[0].shot_id).toBe('shot-1');
    expect(state.selectedId).toBe('shot-1');
    expect(state.timeline).toContain('shot-1');
    expect(state.feed[0]).toMatchObject({ tool: 'render-shot', actor: 'agent', status: 'ok' });
  });

  it('a failed tool call is logged as an error and re-thrown', async () => {
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });

    await expect(registry.call('get-shot', { shot_id: 'nope' }, 'agent')).rejects.toThrow(/Unknown shot/);

    const state = useStudio.getState();
    expect(state.feed[0]).toMatchObject({ tool: 'get-shot', actor: 'agent', status: 'error' });
    expect(String(state.feed[0].result)).toMatch(/Unknown shot/);
  });

  it('a UI render is logged as you, exactly once', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        if (String(url).endsWith('/api/shots') && init?.method === 'POST') {
          return jsonResponse({ shot_id: 'shot-ui', job_id: 'job-ui' });
        }
        return jsonResponse({});
      }),
    );

    useStudio.getState().setPrompt('a lighthouse in fog');
    await useStudio.getState().renderShot();

    const state = useStudio.getState();
    expect(state.feed).toHaveLength(1);
    expect(state.feed[0]).toMatchObject({ tool: 'render-shot', actor: 'you', status: 'ok' });
  });

  it('configure-backend switches surface without ever touching the key', async () => {
    const fetchSpy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      if (String(url).endsWith('/api/settings') && init?.method === 'PUT') {
        return jsonResponse({
          surface: 'cloud',
          base_url: 'https://cloud.comfy.org',
          key_set: true,
          key_last4: 'abcd',
        });
      }
      return jsonResponse({ ok: true });
    });
    vi.stubGlobal('fetch', fetchSpy);
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });
    const tool = registry.tools().find((t) => t.name === 'configure-backend');
    expect(tool).toBeDefined();
    // the schema has no key field at all — a key comes from the human
    expect(Object.keys(tool!.inputSchema.properties ?? {})).not.toContain('api_key');
    expect(tool!.inputSchema.additionalProperties).toBe(false);

    // even an api_key smuggled into the arguments is dropped on the floor
    const res = (await registry.call('configure-backend', {
      surface: 'cloud',
      api_key: 'not-a-real-key',
    })) as Record<string, unknown>;

    const put = fetchSpy.mock.calls.find(
      ([url, init]) => String(url).endsWith('/api/settings') && init?.method === 'PUT',
    );
    expect(put).toBeDefined();
    const body = JSON.parse(String(put![1]?.body));
    expect(body).toEqual({ surface: 'cloud', base_url: 'https://cloud.comfy.org' });
    expect(body.api_key).toBeUndefined();
    expect(res).toMatchObject({ surface: 'cloud', key_set: true });

    // ...and the feed records the call without echoing what was smuggled in
    const feed = useStudio.getState().feed;
    expect(feed).toHaveLength(1);
    expect(feed[0].tool).toBe('configure-backend');
    expect(feed[0].args).toMatchObject({ surface: 'cloud', api_key: '(not logged)' });
    expect(JSON.stringify(feed)).not.toContain('not-a-real-key');
  });

  it('cut-music-video sends the beat grid and admits when the music was ignored', async () => {
    const shot = (id: string) => ({
      shot_id: id,
      mode: 't2v' as const,
      prompt: 'a platform at dusk',
      camera: { move: 'static', lens: '35mm', speed: 'normal', framing: 'medium' },
      status: 'done' as const,
    });
    useStudio.setState({
      shots: [shot('a'), shot('b')],
      timeline: ['a', 'b'],
      audio: { mediaId: 'aud-1', name: 'take.wav' },
      bpm: 128,
      beats: { a: 8, b: 8 },
    });

    const fetchSpy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      if (String(url).endsWith('/api/sequence') && init?.method === 'POST') {
        return jsonResponse({ job_id: 'job-9' });
      }
      return jsonResponse({ status: 'running' });
    });
    vi.stubGlobal('fetch', fetchSpy);

    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });
    const res = (await registry.call('cut-music-video', {
      bpm: 90,
      beats_per_shot: [4, 12],
    })) as { musicApplied: boolean | null; note?: string; request: Record<string, unknown> };

    const post = fetchSpy.mock.calls.find(
      ([url, init]) => String(url).endsWith('/api/sequence') && init?.method === 'POST',
    );
    expect(post).toBeDefined();
    expect(JSON.parse(String(post![1]?.body))).toEqual({
      shot_ids: ['a', 'b'],
      audio_media_id: 'aud-1',
      bpm: 90,
      beats_per_shot: [4, 12],
    });
    // the response never echoed the music, so the studio says so instead of pretending
    expect(res.musicApplied).toBe(false);
    expect(String(res.note)).toMatch(/did not confirm the music track/i);
    expect(useStudio.getState().feed[0]).toMatchObject({ tool: 'cut-music-video', status: 'ok' });
  });

  it('cut-music-video reports a backend that really used the music', async () => {
    useStudio.setState({
      shots: [
        {
          shot_id: 'a',
          mode: 't2v' as const,
          prompt: 'x',
          camera: { move: 'static', lens: '35mm', speed: 'normal', framing: 'medium' },
          status: 'done' as const,
        },
      ],
      timeline: ['a'],
      audio: { mediaId: 'aud-1', name: 'take.wav' },
      bpm: 120,
      beats: { a: 4 },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        if (String(url).endsWith('/api/sequence') && init?.method === 'POST') {
          return jsonResponse({ media_id: 'seq-1', audio_media_id: 'aud-1', music_video: true });
        }
        return jsonResponse({});
      }),
    );

    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });
    const res = (await registry.call('cut-music-video', {})) as { musicApplied: boolean | null; mediaId?: string };
    expect(res.musicApplied).toBe(true);
    expect(res.mediaId).toBe('seq-1');
  });

  it('rejects an unknown tool name', async () => {
    const registry = await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext(), expose: false });
    await expect(registry.call('rm-rf', {})).rejects.toThrow(/Unknown tool/);
  });

  it('publishes window.cinemaStudio for demos and tests', async () => {
    await createStudioRegistry({ vocab: FALLBACK_VOCAB, context: new ModelContext() });
    const studio = (window as unknown as { cinemaStudio?: Record<string, unknown> }).cinemaStudio;

    expect(studio).toBeDefined();
    const names = (studio as { names: () => string[] }).names();
    expect(names).toHaveLength(CONTRACT_TOOL_NAMES.length);

    await (studio as { call: (n: string, a: unknown) => Promise<unknown> }).call('set-camera', { speed: 'fast' });
    expect(useStudio.getState().rig.speed).toBe('fast');
    // a direct demo call is the human, not an agent
    expect(useStudio.getState().feed[0].actor).toBe('you');
  });
});
