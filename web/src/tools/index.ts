/**
 * The studio's WebMCP tools — the exact list in CONTRACT.md.
 *
 * Every tool drives the same zustand actions as the UI controls, so an agent in
 * the browser and a human at the desk share one state. Input schemas are built
 * from the camera vocabulary the backend serves, so the enums can never drift
 * from `camera.py`.
 */
import { ids, type CameraVocab } from '../cameraVocab';
import { asActor, useStudio, type RenderOverrides, type StudioApi } from '../store';
import {
  ENGINES,
  GRADE_LOOKS,
  SHOT_MODES,
  SURFACES,
  type EngineId,
  type GradeLook,
  type Quality,
  type ShotMode,
  type Surface,
} from '../types';
import type { JsonSchema, ToolDefinition } from '../webmcp/polyfill';

export const CONTRACT_TOOL_NAMES = [
  'list-camera-moves',
  'plan-shot',
  'set-camera',
  'render-shot',
  'reshoot-shot',
  'grade-shot',
  'list-shots',
  'get-shot',
  'cut-sequence',
  'configure-backend',
  'cut-music-video',
] as const;

export type ContractToolName = (typeof CONTRACT_TOOL_NAMES)[number];

const MODE_IDS = SHOT_MODES.map((m) => m.id);
const QUALITY_IDS: Quality[] = ['draft', 'final'];
const LOOK_IDS = GRADE_LOOKS.map((g) => g.id);
const ENGINE_IDS = ENGINES.map((e) => e.id);
const SURFACE_IDS = SURFACES.map((s) => s.id);

type Args = Record<string, unknown>;

const str = (args: Args, key: string): string | undefined => {
  const v = args[key];
  return typeof v === 'string' && v.trim() ? v.trim() : undefined;
};

const num = (args: Args, key: string): number | undefined => {
  const v = args[key];
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
};

function oneOf<T extends string>(value: string | undefined, allowed: readonly T[]): T | undefined {
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

function rigFrom(args: Args) {
  const patch: Record<string, string> = {};
  for (const key of ['move', 'lens', 'speed', 'framing'] as const) {
    const v = str(args, key);
    if (v) patch[key] = v;
  }
  return patch;
}

const rigProperties = (vocab: CameraVocab): Record<string, JsonSchema> => ({
  move: { type: 'string', enum: ids(vocab.moves), description: 'Camera move' },
  lens: { type: 'string', enum: ids(vocab.lenses), description: 'Lens focal length / character' },
  speed: { type: 'string', enum: ids(vocab.speeds), description: 'How fast the move plays' },
  framing: { type: 'string', enum: ids(vocab.framings), description: 'Framing of the subject' },
});

const objectSchema = (
  properties: Record<string, JsonSchema>,
  required: string[] = [],
): JsonSchema => ({ type: 'object', properties, required, additionalProperties: false });

function summarise(shot: {
  shot_id: string;
  mode: ShotMode;
  status: string;
  camera: { move: string; lens: string; speed: string; framing: string };
  progress?: number;
  media_id?: string;
  prompt: string;
}) {
  return {
    shot_id: shot.shot_id,
    mode: shot.mode,
    status: shot.status,
    progress: shot.progress ?? 0,
    camera: shot.camera,
    media_id: shot.media_id,
    prompt: shot.prompt,
  };
}

/**
 * Build the tools in CONTRACT.md order, plus the settings switch and the
 * music-video cut. `vocab` should come from GET /api/camera/vocab (the
 * bundled copy is the fallback); `store` is injectable so tests get a fresh one.
 */
export function buildToolDefinitions(vocab: CameraVocab, store: StudioApi = useStudio): ToolDefinition[] {
  const rig = rigProperties(vocab);
  const defs: ToolDefinition[] = [];

  defs.push({
    name: 'list-camera-moves',
    title: 'List camera moves',
    description:
      'List the camera vocabulary the studio can direct: moves, lenses, speeds and framings. Read-only.',
    inputSchema: objectSchema({}),
    annotations: { readOnlyHint: true },
    execute: () => ({
      moves: vocab.moves,
      lenses: vocab.lenses,
      speeds: vocab.speeds,
      framings: vocab.framings,
    }),
  });

  defs.push({
    name: 'plan-shot',
    title: 'Plan a shot',
    description:
      'Set the rig (move, lens, speed, framing), the prompt, the mode and the quality for the next shot without rendering it. Returns the planned settings.',
    inputSchema: objectSchema(
      {
        prompt: { type: 'string', description: 'What happens in the shot, subject first' },
        mode: { type: 'string', enum: MODE_IDS, description: 'Shot mode' },
        engine: { type: 'string', enum: ENGINE_IDS, description: 'Which model renders the shot' },
        quality: { type: 'string', enum: QUALITY_IDS, description: 'Draft is fast, final is the take' },
        ...rig,
      },
      [],
    ),
    execute: (args) => {
      const state = store.getState();
      const prompt = str(args, 'prompt');
      if (prompt !== undefined) state.setPrompt(prompt);
      const mode = oneOf<ShotMode>(str(args, 'mode'), MODE_IDS);
      if (mode) state.setMode(mode);
      const engine = oneOf<EngineId>(str(args, 'engine'), ENGINE_IDS);
      if (engine) state.setEngine(engine);
      const quality = oneOf<Quality>(str(args, 'quality'), QUALITY_IDS);
      if (quality) state.setQuality(quality);
      state.setRig(rigFrom(args));
      const next = store.getState();
      return {
        mode: next.mode,
        engine: next.engine,
        quality: next.quality,
        prompt: next.prompt,
        camera: next.rig,
        next: 'render-shot renders this plan',
      };
    },
  });

  defs.push({
    name: 'set-camera',
    title: 'Set the camera',
    description:
      'Move the rig without changing the prompt: pick any of move, lens, speed, framing. Returns the full rig.',
    inputSchema: objectSchema(rig, []),
    execute: (args) => {
      const patch = rigFrom(args);
      if (!Object.keys(patch).length) {
        throw new Error('set-camera needs at least one of: move, lens, speed, framing');
      }
      store.getState().setRig(patch);
      return store.getState().rig;
    },
  });

  defs.push({
    name: 'render-shot',
    title: 'Render a shot',
    description:
      'Render the shot described by the current plan (or the settings passed inline). Queues a job on ComfyUI and returns shot_id + job_id; poll with get-shot.',
    inputSchema: objectSchema({
      prompt: { type: 'string', description: 'Override the plan prompt' },
      mode: { type: 'string', enum: MODE_IDS, description: 'Override the shot mode' },
      engine: { type: 'string', enum: ENGINE_IDS, description: 'Override the engine for this shot' },
      quality: { type: 'string', enum: QUALITY_IDS, description: 'Render quality preset' },
      seed: { type: 'number', description: 'Optional seed' },
      start_media_id: { type: 'string', description: 'First-frame still (I2V / FLF2V)' },
      end_media_id: { type: 'string', description: 'Last-frame still (FLF2V)' },
      source_shot_id: { type: 'string', description: 'Re-shoot from this shot instead' },
      ...rig,
    }),
    execute: async (args) => {
      const overrides: RenderOverrides = {};
      const prompt = str(args, 'prompt');
      if (prompt) overrides.prompt = prompt;
      const mode = oneOf<ShotMode>(str(args, 'mode'), MODE_IDS);
      if (mode) overrides.mode = mode;
      const engine = oneOf<EngineId>(str(args, 'engine'), ENGINE_IDS);
      if (engine) overrides.engine = engine;
      const quality = oneOf<Quality>(str(args, 'quality'), QUALITY_IDS);
      if (quality) overrides.quality = quality;
      const seed = num(args, 'seed');
      if (seed !== undefined) overrides.seed = seed;
      const start = str(args, 'start_media_id');
      if (start) overrides.startMediaId = start;
      const end = str(args, 'end_media_id');
      if (end) overrides.endMediaId = end;
      const source = str(args, 'source_shot_id');
      if (source) {
        overrides.sourceShotId = source;
        overrides.mode = 'reshoot';
      }
      Object.assign(overrides, rigFrom(args));

      const res = await store.getState().renderShot(overrides);
      return { ...res, status: 'queued', hint: 'call get-shot to follow progress' };
    },
  });

  defs.push({
    name: 'reshoot-shot',
    title: 'Re-shoot a shot',
    description:
      'Take a rendered shot and shoot the same scene again from a new camera rig. Uses the shot media as the source frame.',
    inputSchema: objectSchema(
      {
        shot_id: { type: 'string', description: 'The shot to re-shoot' },
        prompt: { type: 'string', description: 'Optional new prompt (defaults to the shot prompt)' },
        quality: { type: 'string', enum: QUALITY_IDS, description: 'Render quality preset' },
        ...rig,
      },
      ['shot_id'],
    ),
    execute: async (args) => {
      const shotId = str(args, 'shot_id');
      if (!shotId) throw new Error('reshoot-shot needs shot_id');
      const state = store.getState();
      const shot = state.shots.find((s) => s.shot_id === shotId);
      if (!shot) throw new Error(`Unknown shot "${shotId}"`);
      state.reshootFrom(shotId);

      const overrides: RenderOverrides = { sourceShotId: shotId, mode: 'reshoot' };
      const prompt = str(args, 'prompt');
      overrides.prompt = prompt ?? shot.prompt;
      const quality = oneOf<Quality>(str(args, 'quality'), QUALITY_IDS);
      if (quality) overrides.quality = quality;
      Object.assign(overrides, rigFrom(args));

      const res = await store.getState().renderShot(overrides);
      return { ...res, from: shotId, camera: store.getState().rig };
    },
  });

  defs.push({
    name: 'grade-shot',
    title: 'Grade a shot',
    description:
      'Apply a film grade to a rendered shot. Looks: ' + LOOK_IDS.join(', ') + '. Returns the grade job.',
    inputSchema: objectSchema(
      {
        shot_id: { type: 'string', description: 'The shot to grade' },
        look: { type: 'string', enum: LOOK_IDS, description: 'Grade look' },
        strength: { type: 'number', minimum: 0, maximum: 1, description: 'Grade strength (0-1)' },
      },
      ['shot_id', 'look'],
    ),
    execute: async (args) => {
      const shotId = str(args, 'shot_id');
      if (!shotId) throw new Error('grade-shot needs shot_id');
      const look = oneOf<GradeLook>(str(args, 'look'), LOOK_IDS);
      if (!look) throw new Error(`grade-shot needs a look from: ${LOOK_IDS.join(', ')}`);
      const strength = num(args, 'strength');
      const res = await store.getState().gradeShot(shotId, look, strength);
      return { ...((res as Record<string, unknown>) ?? {}), shot_id: shotId, look };
    },
  });

  defs.push({
    name: 'list-shots',
    title: 'List shots',
    description: 'List every shot on the board with its mode, camera, status and media id. Read-only.',
    inputSchema: objectSchema({}),
    annotations: { readOnlyHint: true },
    execute: () => ({
      count: store.getState().shots.length,
      shots: store.getState().shots.map(summarise),
      selected: store.getState().selectedId,
      timeline: store.getState().timeline,
    }),
  });

  defs.push({
    name: 'get-shot',
    title: 'Get a shot',
    description: 'Fetch one shot: status, progress, media id, compiled prompt and the graph that rendered it. Read-only.',
    inputSchema: objectSchema({ shot_id: { type: 'string', description: 'The shot id' } }, ['shot_id']),
    annotations: { readOnlyHint: true },
    execute: (args) => {
      const shotId = str(args, 'shot_id');
      if (!shotId) throw new Error('get-shot needs shot_id');
      const shot = store.getState().shots.find((s) => s.shot_id === shotId);
      if (!shot) throw new Error(`Unknown shot "${shotId}"`);
      return shot;
    },
  });

  defs.push({
    name: 'cut-sequence',
    title: 'Cut a sequence',
    description:
      'Cut the shots into one sequence. Pass shot_ids in cut order; without it the timeline order is used.',
    inputSchema: objectSchema({
      shot_ids: {
        type: 'array',
        items: { type: 'string' },
        description: 'Shot ids in cut order (defaults to the timeline)',
      },
    }),
    execute: async (args) => {
      const raw = args.shot_ids;
      const shotIds = Array.isArray(raw) ? raw.filter((v): v is string => typeof v === 'string' && !!v) : undefined;
      const res = await store.getState().cutSequence(shotIds);
      return { ...((res as Record<string, unknown>) ?? {}), shot_ids: shotIds ?? store.getState().timeline };
    },
  });

  defs.push({
    name: 'configure-backend',
    title: 'Configure the backend',
    description:
      'Switch where shots render: Comfy Cloud or a local ComfyUI, and the base URL. It cannot set or read an API key — a key is entered by the human in the settings drawer only.',
    inputSchema: objectSchema({
      surface: { type: 'string', enum: SURFACE_IDS, description: 'cloud = hosted Comfy, local = your own GPU box' },
      base_url: { type: 'string', description: 'Base URL of the ComfyUI server (https://cloud.comfy.org for cloud)' },
    }),
    execute: async (args) => {
      const surface = oneOf<Surface>(str(args, 'surface'), SURFACE_IDS);
      const baseUrl = str(args, 'base_url');
      if (!surface && !baseUrl) {
        throw new Error('configure-backend needs surface and/or base_url');
      }
      const settings = await store.getState().configureBackend({
        surface,
        base_url: surface && !baseUrl ? SURFACES.find((s) => s.id === surface)?.base : baseUrl,
      });
      if (!settings) {
        throw new Error('the backend did not accept the settings change');
      }
      return {
        surface: settings.surface,
        base_url: settings.base_url,
        key_set: settings.key_set,
        note: settings.key_set
          ? 'ready to render'
          : 'no API key on this backend yet — ask the human to add one in the settings drawer',
      };
    },
  });

  defs.push({
    name: 'cut-music-video',
    title: 'Cut a music video',
    description:
      'Cut the shots on the beat grid of an uploaded audio track. Pass shot_ids in cut order (defaults to the timeline), the audio media id (defaults to the uploaded track), the BPM and one beat count per shot. The backend retimes each shot to beats_per_shot[i] * 60/bpm with hard cuts and muxes the audio as AAC.',
    inputSchema: objectSchema({
      shot_ids: {
        type: 'array',
        items: { type: 'string' },
        description: 'Shot ids in cut order (defaults to the timeline)',
      },
      audio_media_id: { type: 'string', description: 'Media id from an audio upload (defaults to the track on the timeline)' },
      bpm: { type: 'number', minimum: 20, maximum: 400, description: 'Tempo of the track' },
      beats_per_shot: {
        type: 'array',
        items: { type: 'number', minimum: 1, maximum: 64 },
        description: 'Beats each shot holds; length must match shot_ids',
      },
    }),
    execute: async (args) => {
      const rawShots = args.shot_ids;
      const shotIds = Array.isArray(rawShots)
        ? rawShots.filter((v): v is string => typeof v === 'string' && !!v)
        : undefined;
      const rawBeats = args.beats_per_shot;
      const beatsPerShot = Array.isArray(rawBeats)
        ? rawBeats.filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
        : undefined;
      const bpm = num(args, 'bpm');
      const res = await store.getState().cutMusicVideo({
        shotIds: shotIds?.length ? shotIds : undefined,
        audioMediaId: str(args, 'audio_media_id'),
        bpm,
        beatsPerShot: beatsPerShot?.length ? beatsPerShot : undefined,
      });
      return res;
    },
  });

  return defs;
}

/** Convenience for the status bar and tests. */
export function toolNames(defs: ToolDefinition[]): string[] {
  return defs.map((d) => d.name);
}

/** Re-export so callers can set the actor around a direct tool call. */
export { asActor };
