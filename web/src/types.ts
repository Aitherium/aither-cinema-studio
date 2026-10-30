/** Shared types for the studio UI, the store and the WebMCP tools. */

export type ShotMode = 'keyframe' | 't2v' | 'i2v' | 'flf2v' | 'reshoot';
export type Quality = 'draft' | 'final';
export type ShotStatus = 'queued' | 'running' | 'done' | 'error';

/**
 * The engine the rig offers. The picker names a *family*; the mode refines it
 * into a builder id the backend knows (see `resolveEngine`), because "Wan 2.2"
 * means the T2V, I2V or FLF2V builder depending on the shot mode.
 */
export type EngineId = 'camera' | 'wan2.2' | 'ltx2' | 'keyframe';

/** The exact ids `server/cinema/graphs.py` accepts in `shot.engine`. */
export type ServerEngine = 'wan_camera' | 'wan_t2v' | 'wan_i2v' | 'wan_flf2v' | 'ltx' | 'sdxl';

export const SERVER_ENGINES: ServerEngine[] = [
  'wan_camera',
  'wan_t2v',
  'wan_i2v',
  'wan_flf2v',
  'ltx',
  'sdxl',
];

export interface EngineOption {
  id: EngineId;
  label: string;
  short: string;
  hint: string;
}

export const ENGINES: EngineOption[] = [
  {
    id: 'camera',
    label: 'Camera (Wan 2.2 Fun Camera)',
    short: 'FunCam',
    hint: 'the rig drives a Wan 2.2 Fun-Camera move',
  },
  { id: 'wan2.2', label: 'Wan 2.2', short: 'Wan 2.2', hint: 'Wan 2.2 text/image to video' },
  { id: 'ltx2', label: 'LTX-2', short: 'LTX-2', hint: 'LTX-2 video' },
  { id: 'keyframe', label: 'Keyframe still', short: 'Still', hint: 'one still frame (SDXL)' },
];

export const DEFAULT_ENGINE: EngineId = 'wan2.2';

/** The id sent as `shot.engine`: the family folded into the mode's builder. */
export function resolveEngine(engine: EngineId, mode: ShotMode): ServerEngine {
  if (engine === 'keyframe' || mode === 'keyframe') return 'sdxl';
  if (engine === 'camera') return 'wan_camera';
  if (engine === 'ltx2') return 'ltx';
  if (mode === 'i2v' || mode === 'reshoot') return 'wan_i2v';
  if (mode === 'flf2v') return 'wan_flf2v';
  return 'wan_t2v';
}

const SERVER_SHORT: Record<ServerEngine, string> = {
  wan_camera: 'FunCam',
  wan_t2v: 'Wan 2.2',
  wan_i2v: 'Wan 2.2',
  wan_flf2v: 'Wan 2.2',
  ltx: 'LTX-2',
  sdxl: 'Still',
};

export function engineLabel(id: string | undefined): string {
  return ENGINES.find((e) => e.id === id)?.label ?? id ?? '';
}

/** Accepts both a picker family and a server builder id (shot records carry the latter). */
export function engineShort(id: string | undefined): string {
  if (!id) return '';
  const family = ENGINES.find((e) => e.id === id);
  if (family) return family.short;
  return SERVER_SHORT[id as ServerEngine] ?? id;
}

export type GradeLook =
  | 'none'
  | 'warm'
  | 'cool'
  | 'tealOrange'
  | 'bleachBypass'
  | 'fadedFilm'
  | 'dayForNight'
  | 'monoContrast'
  | 'crossProcess';

export type Actor = 'agent' | 'you';

export interface CameraRig {
  move: string;
  lens: string;
  speed: string;
  framing: string;
}

export interface Shot {
  shot_id: string;
  mode: ShotMode;
  prompt: string;
  camera: CameraRig;
  status: ShotStatus;
  /** The builder id the backend echoed (`wan_t2v`, `sdxl`, …). */
  engine?: string;
  quality?: Quality;
  progress?: number;
  media_id?: string;
  media_url?: string;
  error?: string;
  compiled_prompt?: string;
  graph?: unknown;
  created_at?: number;
}

export interface ComfyHealth {
  reachable: boolean;
  base?: string;
  version?: string;
}

export interface Health {
  ok?: boolean;
  comfy?: ComfyHealth;
  grade?: { available: boolean; bin?: string };
}

export interface FeedEntry {
  id: string;
  tool: string;
  args: unknown;
  result: unknown;
  time: number;
  actor: Actor;
  status: 'ok' | 'error';
}

export interface SequenceState {
  status: 'idle' | 'submitted' | 'running' | 'done' | 'error';
  jobId?: string;
  mediaId?: string;
  error?: string;
}

/* ------------------------------------------------------------- settings --- */

export type Surface = 'cloud' | 'local';

export const SURFACES: { id: Surface; label: string; base: string; hint: string }[] = [
  { id: 'cloud', label: 'Comfy Cloud', base: 'https://cloud.comfy.org', hint: 'hosted Comfy · paid plan · /api prefix' },
  { id: 'local', label: 'Local ComfyUI', base: 'http://127.0.0.1:8188', hint: 'your own GPU box' },
];

export const DEFAULT_BASE_URL: Record<Surface, string> = {
  cloud: 'https://cloud.comfy.org',
  local: 'http://127.0.0.1:8188',
};

/** What `GET /api/settings` tells the studio — never the key itself. */
export interface StudioSettings {
  surface: Surface;
  base_url: string;
  key_set: boolean;
  key_last4?: string;
}

export interface SettingsTestResult {
  status: 'idle' | 'testing' | 'ok' | 'error';
  message?: string;
  version?: string;
}

/** The honest outcome of a music-video cut. */
export interface MusicState {
  status: 'idle' | 'submitted' | 'running' | 'done' | 'error';
  jobId?: string;
  mediaId?: string;
  /** the audio media id this cut asked for, echoed back or not */
  audioMediaId?: string;
  /** null = the backend has not told us yet; true/false once it has. */
  musicApplied: boolean | null;
  note?: string;
  error?: string;
}

/** An uploaded audio bed and the peaks the waveform draws. */
export interface AudioTrack {
  mediaId: string;
  name: string;
  url?: string;
  duration?: number;
  peaks?: number[];
  error?: string;
}

export const SHOT_MODES: { id: ShotMode; label: string; hint: string }[] = [
  { id: 'keyframe', label: 'Keyframe', hint: 'one still frame' },
  { id: 't2v', label: 'T2V', hint: 'text to video' },
  { id: 'i2v', label: 'I2V', hint: 'image to video' },
  { id: 'flf2v', label: 'FLF2V', hint: 'first to last frame' },
  { id: 'reshoot', label: 'Re-shoot', hint: 'same scene, new angle' },
];

export const GRADE_LOOKS: { id: GradeLook; label: string }[] = [
  { id: 'none', label: 'None' },
  { id: 'warm', label: 'Warm' },
  { id: 'cool', label: 'Cool' },
  { id: 'tealOrange', label: 'Teal & Orange' },
  { id: 'bleachBypass', label: 'Bleach bypass' },
  { id: 'fadedFilm', label: 'Faded film' },
  { id: 'dayForNight', label: 'Day for night' },
  { id: 'monoContrast', label: 'Mono contrast' },
  { id: 'crossProcess', label: 'Cross process' },
];

export const QUALITY_PRESETS: { id: Quality; label: string; hint: string }[] = [
  { id: 'draft', label: 'Draft', hint: '480 · 33f · 8 steps' },
  { id: 'final', label: 'Final', hint: '480×832 · 81f · 20 steps' },
];
