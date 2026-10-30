/**
 * The one studio store. Every UI control and every WebMCP tool drives these
 * actions — there is no second code path for agents.
 */
import { create } from 'zustand';
import * as api from './api';
import { FALLBACK_VOCAB, normalizeVocab, type CameraVocab } from './cameraVocab';
import { normalizeSettings, normalizeTestResult, redact } from './settings';
import {
  DEFAULT_ENGINE,
  resolveEngine,
  type Actor,
  type AudioTrack,
  type CameraRig,
  type EngineId,
  type FeedEntry,
  type GradeLook,
  type Health,
  type MusicState,
  type Quality,
  type SequenceState,
  type SettingsTestResult,
  type Shot,
  type ShotMode,
  type StudioSettings,
  type Surface,
} from './types';

/* ------------------------------------------------------------------ actor ---
 * Which side fired the action. The WebMCP registry wraps a call in `asActor`
 * so the activity feed can say "agent" rather than "you".
 */
let currentActor: Actor = 'you';
export const getActor = (): Actor => currentActor;

export async function asActor<T>(a: Actor, fn: () => Promise<T> | T): Promise<T> {
  const prev = currentActor;
  currentActor = a;
  try {
    return await fn();
  } finally {
    currentActor = prev;
  }
}

/* --------------------------------------------------------------- helpers --- */

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

const POLL_MS = 2000;
const POLL_LIMIT = 900; // ~30 min at 2 s

let pollingEnabled = true;
/** Tests turn polling off so a scheduled poll can never outlive the test. */
export function setPollingEnabled(value: boolean): void {
  pollingEnabled = value;
}

let bootPromise: Promise<void> | null = null;
/** First-run drawer opens at most once per session, when cloud has no key. */
let autoOpenedSettings = false;

/* -------------------------------------------------------------- feed log ---
 * 'auto'   — actions log themselves (a human clicking Render).
 * 'manual' — the WebMCP wrapper logs the call once with the tool's own name,
 *            so an agent call never produces two rows.
 */
let feedMode: 'auto' | 'manual' = 'auto';
export function setFeedMode(mode: 'auto' | 'manual'): void {
  feedMode = mode;
}
export function getFeedMode(): 'auto' | 'manual' {
  return feedMode;
}

export function normalizeShots(raw: unknown): Shot[] {
  const list = Array.isArray(raw)
    ? raw
    : raw && typeof raw === 'object' && Array.isArray((raw as { shots?: unknown }).shots)
      ? ((raw as { shots: Shot[] }).shots as Shot[])
      : [];
  return list
    .filter((s): s is Shot => !!s && typeof s === 'object' && typeof (s as Shot).shot_id === 'string')
    .map((s) => ({
      ...s,
      camera: s.camera ?? { move: 'static', lens: '35mm', speed: 'slow', framing: 'medium' },
      status: s.status ?? 'queued',
    }));
}

export function mediaKind(shot?: {
  media_url?: string;
  media_id?: string;
  engine?: string;
  mode?: string;
}): 'image' | 'video' {
  const u = `${shot?.media_url ?? ''} ${shot?.media_id ?? ''}`.toLowerCase();
  if (/\.(mp4|webm|mov|mkv|m4v)(\?|$|\s)/.test(u)) return 'video';
  // Media ids carry no extension, so the builder decides: only the still
  // builder (or an explicit keyframe) is an image; every motion engine is video.
  if (shot?.engine === 'sdxl' || shot?.mode === 'keyframe') return 'image';
  if (shot?.engine) return 'video';
  return 'image';
}

export function shotMediaSrc(shot?: { media_url?: string; media_id?: string }): string | null {
  if (!shot) return null;
  if (shot.media_url) return shot.media_url;
  if (shot.media_id) return api.mediaUrl(shot.media_id);
  return null;
}

/* ----------------------------------------------------------------- music --- */

/** Beats a shot gets until the director says otherwise. */
export const DEFAULT_BEATS = 4;

/** One place decides whether a sequence response carried the music track. */
export function musicAccepted(
  response: unknown,
  requested: { audio_media_id?: string; beats_per_shot?: number[] },
): boolean | null {
  if (!response || typeof response !== 'object') return null;
  const o = response as Record<string, unknown>;
  const params =
    o.params && typeof o.params === 'object' ? (o.params as Record<string, unknown>) : null;
  const layers = [o, params].filter(Boolean) as Record<string, unknown>[];
  for (const layer of layers) {
    if (typeof layer.audio_media_id === 'string' && layer.audio_media_id === requested.audio_media_id) return true;
    if (layer.music_video === true || layer.music === true || layer.audio === true) return true;
    if (
      Array.isArray(layer.beats_per_shot) &&
      layer.beats_per_shot.length === (requested.beats_per_shot?.length ?? -1)
    ) {
      return true;
    }
  }
  if (o.music_video === false || o.music === false || o.audio_media_id === null) return false;
  return false;
}

const MUSIC_ACCEPTED_NOTE =
  'Music track accepted — the backend retimed the shots to the beat grid and muxed the audio bed.';
const MUSIC_IGNORED_NOTE =
  'The backend did not echo the audio track — this cut may be silent and the beat timing was not confirmed.';

/* ------------------------------------------------------------------ state --- */

export interface RenderOverrides {
  prompt?: string;
  mode?: ShotMode;
  quality?: Quality;
  engine?: EngineId;
  move?: string;
  lens?: string;
  speed?: string;
  framing?: string;
  startMediaId?: string;
  endMediaId?: string;
  sourceShotId?: string;
  seed?: number;
}

export interface MusicCutResult {
  request: api.SequenceBody;
  response: Record<string, unknown>;
  jobId?: string;
  mediaId?: string;
  musicApplied: boolean | null;
  note: string;
}

export interface StudioState {
  vocab: CameraVocab;
  vocabSource: 'remote' | 'bundled';
  shots: Shot[];
  selectedId: string | null;
  rig: CameraRig;
  mode: ShotMode;
  engine: EngineId;
  prompt: string;
  quality: Quality;
  startMediaId: string | null;
  endMediaId: string | null;
  sourceShotId: string | null;
  gradeLook: GradeLook;
  gradeStrength: number;
  /** shot_id -> graded media id (the grade job's new media) */
  grades: Record<string, string>;
  timeline: string[];
  feed: FeedEntry[];
  health: Health | null;
  healthAt: number | null;
  webmcp: { surface: string; tools: number };
  sequence: SequenceState;
  /** backend/key settings, as the server reports them (never the key) */
  settings: StudioSettings | null;
  settingsLoaded: boolean;
  settingsAvailable: boolean;
  settingsOpen: boolean;
  settingsBusy: boolean;
  settingsTest: SettingsTestResult;
  /** the uploaded audio bed for music-video mode */
  audio: AudioTrack | null;
  bpm: number;
  /** shot_id -> duration in beats */
  beats: Record<string, number>;
  music: MusicState;
  /** bumped when the rig panel should be pulled into view */
  rigFlash: number;
  error: string | null;
  busy: boolean;
  booted: boolean;
}

export interface StudioActions {
  bootstrap(): Promise<void>;
  refreshVocab(): Promise<void>;
  refreshHealth(): Promise<void>;
  refreshShots(): Promise<void>;
  setRig(patch: Partial<CameraRig>): void;
  setMode(mode: ShotMode): void;
  setEngine(engine: EngineId): void;
  setPrompt(prompt: string): void;
  setQuality(quality: Quality): void;
  setGradeLook(look: GradeLook): void;
  setGradeStrength(strength: number): void;
  setSelected(shotId: string | null): void;
  setMedia(slot: 'start' | 'end', mediaId: string | null): void;
  setTimeline(ids: string[]): void;
  reorderTimeline(from: number, to: number): void;
  setWebmcp(patch: Partial<{ surface: string; tools: number }>): void;
  pushFeed(entry: {
    tool: string;
    args?: unknown;
    result?: unknown;
    status?: 'ok' | 'error';
    actor?: Actor;
  }): void;
  ingestShot(shot: Shot): void;
  uploadReference(file: File, slot?: 'start' | 'end'): Promise<{ media_id: string; kind: string; url?: string }>;
  renderShot(overrides?: RenderOverrides): Promise<{ shot_id: string; job_id: string }>;
  gradeShot(shotId: string, look: GradeLook, strength?: number): Promise<unknown>;
  cutSequence(shotIds?: string[]): Promise<unknown>;

  /* settings drawer */
  openSettings(): void;
  closeSettings(): void;
  refreshSettings(): Promise<void>;
  saveSettings(patch: { surface?: Surface; base_url?: string; api_key?: string }): Promise<StudioSettings | null>;
  testSettings(patch?: { surface?: Surface; base_url?: string; api_key?: string }): Promise<SettingsTestResult>;
  /** the agent-safe switch: surface/base_url only, never a key */
  configureBackend(patch: { surface?: Surface; base_url?: string }): Promise<StudioSettings | null>;
  /**
   * After the visitor changes where the studio lives (connection.ts), drop every
   * backend-derived value and bootstrap again against the new one. The WebMCP
   * tool count the registry reported is kept; the tools read this same store.
   */
  reconnect(): Promise<void>;

  /* music-video mode */
  uploadAudio(file: File): Promise<AudioTrack>;
  setAudioAnalysis(patch: { duration?: number; peaks?: number[]; error?: string }): void;
  clearAudio(): void;
  setBpm(bpm: number): void;
  setShotBeats(shotId: string, beats: number): void;
  cutMusicVideo(opts?: {
    shotIds?: string[];
    audioMediaId?: string;
    bpm?: number;
    beatsPerShot?: number[];
  }): Promise<MusicCutResult>;

  reshootFrom(shotId: string): void;
  openRig(): void;
  pollShot(shotId: string, attempt?: number): void;
  pollSequence(jobId: string, attempt?: number): void;
  reset(): void;
}

export type Studio = StudioState & StudioActions;

export const INITIAL_STATE: StudioState = {
  vocab: FALLBACK_VOCAB,
  vocabSource: 'bundled',
  shots: [],
  selectedId: null,
  rig: { move: 'dolly_in', lens: '35mm', speed: 'slow', framing: 'medium' },
  mode: 't2v',
  engine: DEFAULT_ENGINE,
  prompt: '',
  quality: 'draft',
  startMediaId: null,
  endMediaId: null,
  sourceShotId: null,
  gradeLook: 'none',
  gradeStrength: 0.8,
  grades: {},
  timeline: [],
  feed: [],
  health: null,
  healthAt: null,
  webmcp: { surface: 'polyfill', tools: 0 },
  sequence: { status: 'idle' },
  settings: null,
  settingsLoaded: false,
  settingsAvailable: false,
  settingsOpen: false,
  settingsBusy: false,
  settingsTest: { status: 'idle' },
  audio: null,
  bpm: 120,
  beats: {},
  music: { status: 'idle', musicApplied: null },
  rigFlash: 0,
  error: null,
  busy: false,
  booted: false,
};

/** The first-run explainer: a cloud surface with no key cannot render. */
export const FIRST_RUN_EXPLAINER =
  'Comfy Cloud needs an API key before it can render: paste yours below, or switch to Local ComfyUI.';

/** Mode inputs the backend requires before a render can be submitted. */
export function missingInput(mode: ShotMode, start: string | null, end: string | null, source: string | null): string | null {
  if (mode === 'i2v' && !start) return 'I2V needs a reference still — drop one on the rig panel';
  if (mode === 'flf2v' && !start) return 'FLF2V needs a first frame — drop a still on the rig panel';
  if (mode === 'flf2v' && !end) return 'FLF2V needs a last frame — drop a second still on the rig panel';
  if (mode === 'reshoot' && !source) return 'Re-shoot needs a source shot — pick one on the board';
  return null;
}

export const useStudio = create<Studio>((set, get) => ({
  ...INITIAL_STATE,

  /* -------------------------------------------------------------- load ---- */
  async bootstrap() {
    if (!bootPromise) {
      set({ booted: true });
      bootPromise = Promise.all([
        get()
          .refreshVocab()
          .catch(() => set({ vocab: FALLBACK_VOCAB, vocabSource: 'bundled' })),
        get()
          .refreshHealth()
          .catch(() => undefined),
        get()
          .refreshShots()
          .catch(() => undefined),
        get()
          .refreshSettings()
          .catch(() => undefined),
      ]).then(() => undefined);
    }
    await bootPromise;
  },

  async refreshVocab() {
    const raw = await api.getVocab();
    set({ vocab: normalizeVocab(raw), vocabSource: 'remote' });
  },

  async refreshHealth() {
    const health = await api.getHealth();
    set({ health, healthAt: Date.now() });
  },

  async refreshShots() {
    const raw = await api.listShots();
    const shots = normalizeShots(raw);
    set((state) => {
      const known = new Set(shots.map((s) => s.shot_id));
      const keep = state.timeline.filter((id) => known.has(id));
      const added = shots.map((s) => s.shot_id).filter((id) => !keep.includes(id)).reverse();
      const timeline = [...added, ...keep];
      const selectedId =
        state.selectedId && shots.some((s) => s.shot_id === state.selectedId)
          ? state.selectedId
          : (shots[0]?.shot_id ?? null);
      return { shots, timeline, selectedId, error: null };
    });
  },

  /* ----------------------------------------------------------- settings --- */
  async refreshSettings() {
    try {
      const raw = await api.getSettings();
      const settings = normalizeSettings(raw);
      set({ settings, settingsLoaded: true, settingsAvailable: true });
      // First run: a cloud surface with no key is a dead end — say so once.
      if (!autoOpenedSettings && settings.surface === 'cloud' && !settings.key_set) {
        autoOpenedSettings = true;
        set({ settingsOpen: true });
      }
    } catch {
      // A backend without the settings route is honest about it; we never
      // invent a key state, and the drawer explains what Save will do.
      set({ settingsLoaded: true, settingsAvailable: false });
    }
  },

  openSettings() {
    set({ settingsOpen: true });
  },
  closeSettings() {
    set({ settingsOpen: false, settingsTest: { status: 'idle' } });
  },

  async saveSettings(patch) {
    const before = get().settings;
    const body: api.PutSettingsBody = {};
    if (patch.surface) body.surface = patch.surface;
    if (patch.base_url) body.base_url = patch.base_url.trim();
    if (patch.api_key) body.api_key = patch.api_key; // only when the human typed one
    if (!Object.keys(body).length) return before;

    set({ settingsBusy: true });
    try {
      const res = await api.putSettings(body);
      const echoed = res && typeof res === 'object' ? (res as Record<string, unknown>) : {};
      const merged = normalizeSettings({
        ...(before ?? {}),
        ...body,
        ...echoed,
        ...(body.api_key ? { key_set: true, key_last4: body.api_key.slice(-4) } : {}),
      });
      set({
        settings: merged,
        settingsBusy: false,
        settingsAvailable: true,
        settingsLoaded: true,
        settingsTest: { status: 'idle' },
      });
      void get().refreshHealth().catch(() => undefined);
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'configure-backend',
          // the key never reaches the feed — only the fact that one was set
          args: {
            surface: body.surface,
            base_url: body.base_url,
            api_key: body.api_key ? '(new key, not logged)' : undefined,
          },
          result: { surface: merged.surface, base_url: merged.base_url, key_set: merged.key_set },
          status: 'ok',
        });
      }
      return merged;
    } catch (e) {
      set({ settingsBusy: false });
      throw new Error(redact(errText(e)));
    }
  },

  async testSettings(patch = {}) {
    const current = get().settings;
    const body: api.SettingsTestBody = {
      surface: patch.surface ?? current?.surface,
      base_url: patch.base_url?.trim() || current?.base_url,
    };
    if (patch.api_key) body.api_key = patch.api_key;
    set({ settingsTest: { status: 'testing', message: 'probing the backend…' }, settingsBusy: true });
    try {
      const raw = await api.testSettings(body);
      const result = normalizeTestResult(raw);
      set({ settingsTest: result, settingsBusy: false });
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'settings.test',
          args: { surface: body.surface, base_url: body.base_url },
          result: { ok: result.status === 'ok', message: result.message },
          status: result.status === 'ok' ? 'ok' : 'error',
        });
      }
      return result;
    } catch (e) {
      // a red result in the drawer, not a thrown error: the human is testing
      const result: SettingsTestResult = { status: 'error', message: redact(errText(e)) };
      set({ settingsTest: result, settingsBusy: false });
      return result;
    }
  },

  async configureBackend(patch) {
    // The tool surface has no key parameter on purpose: a key comes from the
    // human in the drawer, never from an agent.
    return get().saveSettings({ surface: patch.surface, base_url: patch.base_url });
  },

  async reconnect() {
    bootPromise = null;
    autoOpenedSettings = false;
    const { webmcp, settingsOpen } = get();
    // everything the previous backend told us is stale; the drawer stays open
    set({ ...INITIAL_STATE, webmcp, settingsOpen });
    await get().bootstrap();
  },

  /* -------------------------------------------------------------- edits --- */
  setRig(patch) {
    const clean = Object.fromEntries(Object.entries(patch).filter(([, v]) => typeof v === 'string' && v));
    if (!Object.keys(clean).length) return;
    set((state) => ({ rig: { ...state.rig, ...clean } as CameraRig }));
  },
  setMode(mode) {
    // The still engine and the keyframe mode are the same idea; keep them paired
    // so a plan is always renderable by the builder it names.
    set((state) => ({
      mode,
      engine: mode === 'keyframe' ? 'keyframe' : state.engine === 'keyframe' ? DEFAULT_ENGINE : state.engine,
    }));
  },
  setEngine(engine) {
    // Keep the plan coherent: the still engine shoots a keyframe, and the
    // Fun-Camera builder needs a start image, so it cannot sit on a T2V plan.
    set((state) => {
      let mode = state.mode;
      if (engine === 'keyframe') mode = 'keyframe';
      else if (mode === 'keyframe') mode = engine === 'camera' ? 'i2v' : 't2v';
      else if (engine === 'camera' && (mode === 't2v' || mode === 'flf2v')) mode = 'i2v';
      return { engine, mode };
    });
  },
  setPrompt(prompt) {
    set({ prompt });
  },
  setQuality(quality) {
    set({ quality });
  },
  setGradeLook(look) {
    set({ gradeLook: look });
  },
  setGradeStrength(strength) {
    set({ gradeStrength: Math.min(1, Math.max(0, strength)) });
  },
  setSelected(shotId) {
    set({ selectedId: shotId });
  },
  setMedia(slot, mediaId) {
    set(slot === 'start' ? { startMediaId: mediaId } : { endMediaId: mediaId });
  },
  setWebmcp(patch) {
    set((state) => ({ webmcp: { ...state.webmcp, ...patch } }));
  },

  setTimeline(ids) {
    set({ timeline: ids });
  },

  reorderTimeline(from, to) {
    set((state) => {
      const ids = [...state.timeline];
      if (from < 0 || from >= ids.length || to < 0 || to >= ids.length || from === to) return {};
      const [moved] = ids.splice(from, 1);
      ids.splice(to, 0, moved);
      return { timeline: ids };
    });
    if (feedMode === 'auto') {
      get().pushFeed({ tool: 'timeline', args: { from, to }, result: get().timeline });
    }
  },

  /* --------------------------------------------------------------- feed --- */
  pushFeed(entry) {
    const record: FeedEntry = {
      id: uid('feed'),
      tool: entry.tool,
      args: entry.args ?? {},
      result: entry.result ?? null,
      time: Date.now(),
      actor: entry.actor ?? getActor(),
      status: entry.status ?? 'ok',
    };
    set((state) => ({ feed: [record, ...state.feed].slice(0, 250) }));
  },

  ingestShot(shot) {
    set((state) => {
      const exists = state.shots.some((s) => s.shot_id === shot.shot_id);
      const shots = exists
        ? state.shots.map((s) => (s.shot_id === shot.shot_id ? { ...s, ...shot } : s))
        : [shot, ...state.shots];
      const timeline = state.timeline.includes(shot.shot_id) ? state.timeline : [...state.timeline, shot.shot_id];
      return { shots, timeline };
    });
  },

  /* -------------------------------------------------------------- media --- */
  async uploadReference(file, slot = 'start') {
    set({ busy: true, error: null });
    try {
      const res = await api.upload(file);
      set(
        slot === 'start'
          ? { startMediaId: res.media_id, busy: false }
          : { endMediaId: res.media_id, busy: false },
      );
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'upload-reference',
          args: { name: file.name, size: file.size, slot },
          result: res,
          status: 'ok',
          actor: 'you',
        });
      }
      return res;
    } catch (e) {
      set({ busy: false, error: errText(e) });
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'upload-reference',
          args: { name: file.name, slot },
          result: errText(e),
          status: 'error',
          actor: 'you',
        });
      }
      throw e;
    }
  },

  /* ------------------------------------------------------------- render --- */
  async renderShot(overrides = {}) {
    const state = get();

    const rig: CameraRig = {
      move: overrides.move ?? state.rig.move,
      lens: overrides.lens ?? state.rig.lens,
      speed: overrides.speed ?? state.rig.speed,
      framing: overrides.framing ?? state.rig.framing,
    };
    const mode = overrides.mode ?? state.mode;
    const quality = overrides.quality ?? state.quality;
    const family = overrides.engine ?? state.engine;
    // the picker names a family; the backend needs the family folded into the mode
    const engine = resolveEngine(family, mode);
    const prompt = overrides.prompt ?? state.prompt;

    let sourceMediaId = state.startMediaId;
    let sourceShotId = state.sourceShotId;
    if (overrides.sourceShotId) {
      const src = state.shots.find((s) => s.shot_id === overrides.sourceShotId);
      if (!src) throw new Error(`Unknown shot "${overrides.sourceShotId}"`);
      if (!src.media_id) throw new Error(`Shot "${overrides.sourceShotId}" has no rendered media to re-shoot from`);
      sourceMediaId = src.media_id;
      sourceShotId = src.shot_id;
    }

    const startMediaId = overrides.startMediaId ?? state.startMediaId;
    const endMediaId = overrides.endMediaId ?? state.endMediaId;

    const missing = missingInput(mode, startMediaId, endMediaId, sourceMediaId);
    if (missing) {
      set({ error: missing, mode, engine: family, rig, prompt, quality });
      throw new Error(missing);
    }

    set({ mode, engine: family, rig, prompt, quality, busy: true, error: null, sourceShotId });

    const body: api.CreateShotBody = {
      mode,
      prompt,
      camera: rig,
      quality,
      engine,
    };
    if (startMediaId && (mode === 'i2v' || mode === 'flf2v')) body.start_media_id = startMediaId;
    if (endMediaId && mode === 'flf2v') body.end_media_id = endMediaId;
    if (mode === 'reshoot' && sourceMediaId) body.source_media_id = sourceMediaId;
    if (typeof overrides.seed === 'number') body.seed = overrides.seed;

    try {
      const res = await api.createShot(body);
      const shot: Shot = {
        shot_id: res.shot_id,
        mode,
        prompt,
        camera: rig,
        engine,
        quality,
        status: 'queued',
        progress: 0,
        created_at: Date.now(),
      };
      get().ingestShot(shot);
      set({ busy: false, selectedId: res.shot_id });
      get().pollShot(res.shot_id, 0);
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: mode === 'reshoot' ? 'reshoot-shot' : 'render-shot',
          args: { mode, prompt, camera: rig, quality, engine },
          result: res,
          status: 'ok',
          actor: 'you',
        });
      }
      return res;
    } catch (e) {
      set({ busy: false, error: errText(e) });
      throw e;
    }
  },

  pollShot(shotId, attempt = 0) {
    if (!pollingEnabled || attempt > POLL_LIMIT) return;
    setTimeout(async () => {
      try {
        const shot = await api.getShot(shotId);
        if (shot && shot.shot_id) get().ingestShot(shot);
        if (shot?.status === 'done' || shot?.status === 'error') return;
      } catch {
        /* transient — keep polling until the limit */
      }
      get().pollShot(shotId, attempt + 1);
    }, POLL_MS);
  },

  /* -------------------------------------------------------------- grade --- */
  async gradeShot(shotId, look, strength) {
    const shot = get().shots.find((s) => s.shot_id === shotId);
    if (!shot) throw new Error(`Unknown shot "${shotId}"`);
    const res = await api.gradeShot(shotId, { look, strength: strength ?? get().gradeStrength });
    let mediaId = res?.media_id;
    // The backend answers with a job; the graded media only exists when it
    // finishes. Wait for it so the Monitor can actually show the new grade.
    if (!mediaId && res?.job_id) {
      for (let attempt = 0; attempt < 600 && !mediaId; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const job = await api.getJob(res.job_id).catch(() => null);
        const status = String(job?.status ?? '');
        if (status === 'error' || status === 'failed') {
          throw new Error(job?.error || `grade job ${res.job_id} failed`);
        }
        if (status === 'done' || status === 'completed' || status === 'success') {
          mediaId = typeof job?.result?.media_id === 'string' ? job.result.media_id : undefined;
          break;
        }
      }
    }
    if (mediaId) set((state) => ({ grades: { ...state.grades, [shotId]: mediaId } }));
    set({ gradeLook: look });
    if (feedMode === 'auto') {
      get().pushFeed({
        tool: 'grade-shot',
        args: { shot_id: shotId, look, strength: strength ?? get().gradeStrength },
        result: res,
        status: 'ok',
        actor: 'you',
      });
    }
    return res;
  },

  /* ----------------------------------------------------------- sequence --- */
  async cutSequence(shotIds) {
    const ids = (shotIds && shotIds.length ? shotIds : get().timeline).filter(Boolean);
    if (ids.length < 2) throw new Error('A sequence needs at least two shots on the timeline');
    set({ sequence: { status: 'running' } });
    try {
      const res = await api.cutSequence(ids);
      const jobId = typeof res?.job_id === 'string' ? res.job_id : undefined;
      const mediaId = typeof res?.media_id === 'string' ? res.media_id : undefined;
      if (mediaId) {
        set({ sequence: { status: 'done', jobId, mediaId } });
      } else {
        set({ sequence: { status: 'submitted', jobId } });
        if (jobId) get().pollSequence(jobId, 0);
      }
      if (feedMode === 'auto') {
        get().pushFeed({ tool: 'cut-sequence', args: { shot_ids: ids }, result: res, status: 'ok', actor: 'you' });
      }
      return res;
    } catch (e) {
      set({ sequence: { status: 'error', error: errText(e) } });
      throw e;
    }
  },

  /* ------------------------------------------------------- music video --- */
  async uploadAudio(file) {
    set({ busy: true, error: null });
    try {
      const res = await api.upload(file);
      const track: AudioTrack = {
        mediaId: res.media_id,
        name: file.name,
        url: res.url || api.mediaUrl(res.media_id),
      };
      set({ audio: track, busy: false, music: { status: 'idle', musicApplied: null } });
      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'upload-audio',
          args: { name: file.name, size: file.size },
          result: { media_id: track.mediaId },
          status: 'ok',
          actor: 'you',
        });
      }
      return track;
    } catch (e) {
      set({ busy: false, error: redact(errText(e)) });
      throw e;
    }
  },

  setAudioAnalysis(patch) {
    set((state) => (state.audio ? { audio: { ...state.audio, ...patch } } : {}));
  },

  clearAudio() {
    set({ audio: null, music: { status: 'idle', musicApplied: null } });
  },

  setBpm(bpm) {
    if (!Number.isFinite(bpm) || bpm <= 0) return;
    set({ bpm: Math.min(400, Math.max(20, Math.round(bpm))) });
  },

  setShotBeats(shotId, beats) {
    if (!Number.isFinite(beats)) return;
    set((state) => ({
      beats: { ...state.beats, [shotId]: Math.min(64, Math.max(1, Math.round(beats))) },
    }));
  },

  async cutMusicVideo(opts = {}) {
    const state = get();
    const shotIds = (opts.shotIds?.length ? opts.shotIds : state.timeline).filter(Boolean);
    const audioMediaId = opts.audioMediaId ?? state.audio?.mediaId ?? '';
    const bpm = opts.bpm ?? state.bpm;
    const beatsPerShot = opts.beatsPerShot?.length
      ? opts.beatsPerShot.map((b) => Math.max(1, Math.round(b)))
      : shotIds.map((id) => Math.max(1, Math.round(state.beats[id] ?? DEFAULT_BEATS)));

    if (!shotIds.length) throw new Error('A music video needs at least one shot on the timeline');
    if (!audioMediaId) throw new Error('A music video needs an uploaded audio track');
    if (!(bpm > 0)) throw new Error('A music video needs a BPM above zero');
    if (beatsPerShot.length !== shotIds.length) {
      throw new Error(`beats_per_shot needs one entry per shot (${shotIds.length} shots, ${beatsPerShot.length} beats)`);
    }

    const request: api.SequenceBody = {
      shot_ids: shotIds,
      audio_media_id: audioMediaId,
      bpm,
      beats_per_shot: beatsPerShot,
    };
    set({ music: { status: 'running', musicApplied: null, audioMediaId, note: 'cutting to the beat…' } });

    try {
      const raw = await api.cutSequence(request);
      const res: Record<string, unknown> = raw && typeof raw === 'object' ? raw : {};
      const jobId = typeof res.job_id === 'string' ? res.job_id : undefined;
      const mediaId = typeof res.media_id === 'string' ? res.media_id : undefined;
      const accepted = musicAccepted(res, request);
      const settled = accepted === true || mediaId !== undefined;
      const note = accepted
        ? MUSIC_ACCEPTED_NOTE
        : jobId
          ? 'Cut queued. The response did not confirm the music track yet — the job result will tell us.'
          : MUSIC_IGNORED_NOTE;

      set({
        music: {
          status: settled ? 'done' : jobId ? 'submitted' : 'running',
          jobId,
          mediaId,
          audioMediaId,
          musicApplied: accepted,
          note,
        },
      });
      if (mediaId || jobId) set({ sequence: { status: mediaId ? 'done' : 'submitted', jobId, mediaId } });
      if (jobId) get().pollSequence(jobId, 0);

      if (feedMode === 'auto') {
        get().pushFeed({
          tool: 'cut-music-video',
          args: request,
          result: { ...res, music_applied: accepted, note },
          status: 'ok',
          actor: 'you',
        });
      }
      return { request, response: res, jobId, mediaId, musicApplied: accepted, note };
    } catch (e) {
      const message = redact(errText(e));
      set({ music: { status: 'error', musicApplied: null, audioMediaId, error: message } });
      throw new Error(message);
    }
  },

  pollSequence(jobId, attempt = 0) {
    if (!pollingEnabled || attempt > 400) return;
    setTimeout(async () => {
      let job: api.JobRecord | null = null;
      try {
        job = await api.getJob(jobId);
      } catch {
        // Older backends may not expose the job route; the job is still
        // running server-side, so leave it 'submitted' rather than lie.
        return;
      }
      const status = String(job?.status ?? '');
      const mediaId = typeof job?.result?.media_id === 'string' ? job.result.media_id : undefined;
      const music = get().music;

      if (status === 'done' || status === 'completed' || status === 'success') {
        set({ sequence: { status: 'done', jobId, mediaId } });
        if (music.jobId === jobId) {
          const applied = musicAccepted(job, {
            audio_media_id: music.audioMediaId,
            beats_per_shot: Array.isArray(job?.params?.beats_per_shot)
              ? (job.params.beats_per_shot as number[])
              : undefined,
          });
          set({
            music: {
              ...music,
              status: 'done',
              mediaId: mediaId ?? music.mediaId,
              musicApplied: applied,
              note: applied ? MUSIC_ACCEPTED_NOTE : MUSIC_IGNORED_NOTE,
            },
          });
        }
        return;
      }
      if (status === 'error' || status === 'failed' || status === 'cancelled') {
        const error = job?.error ?? 'sequence failed';
        set({ sequence: { status: 'error', jobId, error } });
        if (music.jobId === jobId) set({ music: { ...music, status: 'error', error } });
        return;
      }

      set({ sequence: { status: 'running', jobId, mediaId } });
      if (music.jobId === jobId) set({ music: { ...music, status: 'running' } });
      get().pollSequence(jobId, attempt + 1);
    }, 3000);
  },

  /* ------------------------------------------------------------- rig UI --- */
  reshootFrom(shotId) {
    const shot = get().shots.find((s) => s.shot_id === shotId);
    if (!shot) throw new Error(`Unknown shot "${shotId}"`);
    set((state) => ({
      mode: 'reshoot',
      sourceShotId: shot.shot_id,
      startMediaId: shot.media_id ?? state.startMediaId,
      selectedId: shot.shot_id,
      rigFlash: state.rigFlash + 1,
    }));
  },

  openRig() {
    set((state) => ({ rigFlash: state.rigFlash + 1 }));
  },

  reset() {
    bootPromise = null;
    autoOpenedSettings = false;
    set({ ...INITIAL_STATE });
  },
}));

/** The bound store type — what the tools and the registry take. */
export type StudioApi = typeof useStudio;
