/**
 * The camera rig vocabulary.
 *
 * The backend's `camera.py` is the single source of truth and serves the table at
 * `GET /api/camera/vocab`. This module normalises that response and carries a
 * bundled copy of the same table so the studio still renders (and the WebMCP tool
 * schemas still build) when the backend is down.
 */

export interface VocabItem {
  id: string;
  label: string;
}

export interface CameraVocab {
  moves: VocabItem[];
  lenses: VocabItem[];
  speeds: VocabItem[];
  framings: VocabItem[];
}

/** Display labels; anything missing falls back to "title case from snake_case". */
const LABELS: Record<string, string> = {
  static: 'Static',
  dolly_in: 'Dolly in',
  dolly_out: 'Dolly out',
  truck_left: 'Truck left',
  truck_right: 'Truck right',
  pan_left: 'Pan left',
  pan_right: 'Pan right',
  tilt_up: 'Tilt up',
  tilt_down: 'Tilt down',
  crane_up: 'Crane up',
  crane_down: 'Crane down',
  orbit_left: 'Orbit left',
  orbit_right: 'Orbit right',
  handheld: 'Handheld',
  push_in_fast: 'Push in (fast)',
  whip_pan: 'Whip pan',
  arc: 'Arc',
  rack_focus: 'Rack focus',
  zoom_in: 'Zoom in',
  zoom_out: 'Zoom out',
  extreme_wide: 'Extreme wide',
  wide: 'Wide',
  medium: 'Medium',
  close_up: 'Close-up',
  extreme_close_up: 'Extreme close-up',
  over_the_shoulder: 'Over the shoulder',
  low_angle: 'Low angle',
  high_angle: 'High angle',
  birds_eye: "Bird's eye",
  dutch: 'Dutch',
  slow: 'Slow',
  fast: 'Fast',
  anamorphic: 'Anamorphic',
};

export function labelFor(id: string): string {
  const known = LABELS[id];
  if (known) return known;
  const spaced = id.replace(/[_-]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const items = (ids: string[]): VocabItem[] => ids.map((id) => ({ id, label: labelFor(id) }));

/** Bundled copy of the CONTRACT.md table — used when /api/camera/vocab is unreachable. */
export const FALLBACK_VOCAB: CameraVocab = {
  moves: items([
    'static',
    'dolly_in',
    'dolly_out',
    'truck_left',
    'truck_right',
    'pan_left',
    'pan_right',
    'tilt_up',
    'tilt_down',
    'crane_up',
    'crane_down',
    'orbit_left',
    'orbit_right',
    'handheld',
    'push_in_fast',
    'whip_pan',
    'arc',
    'rack_focus',
    'zoom_in',
    'zoom_out',
  ]),
  lenses: items(['14mm', '24mm', '35mm', '50mm', '85mm', '135mm', 'anamorphic']),
  speeds: items(['slow', 'medium', 'fast']),
  framings: items([
    'extreme_wide',
    'wide',
    'medium',
    'close_up',
    'extreme_close_up',
    'over_the_shoulder',
    'low_angle',
    'high_angle',
    'birds_eye',
    'dutch',
  ]),
};

function toItem(raw: unknown): VocabItem | null {
  if (typeof raw === 'string' && raw.trim()) return { id: raw.trim(), label: labelFor(raw.trim()) };
  if (raw && typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    const id =
      (typeof o.id === 'string' && o.id) ||
      (typeof o.value === 'string' && o.value) ||
      (typeof o.name === 'string' && o.name) ||
      null;
    if (!id) return null;
    const label =
      (typeof o.label === 'string' && o.label) ||
      (typeof o.title === 'string' && o.title) ||
      labelFor(id);
    return { id, label };
  }
  return null;
}

function normalizeGroup(raw: unknown, fallback: VocabItem[]): VocabItem[] {
  if (!Array.isArray(raw)) return fallback;
  const out: VocabItem[] = [];
  const seen = new Set<string>();
  for (const entry of raw) {
    const item = toItem(entry);
    if (item && !seen.has(item.id)) {
      seen.add(item.id);
      out.push(item);
    }
  }
  return out.length ? out : fallback;
}

/** Accepts `{moves,lenses,speeds,framings}` (or the same under a `vocab` key). */
export function normalizeVocab(raw: unknown): CameraVocab {
  const outer = (raw ?? {}) as Record<string, unknown>;
  const src = (outer.vocab && typeof outer.vocab === 'object' ? outer.vocab : outer) as Record<string, unknown>;
  return {
    moves: normalizeGroup(src.moves, FALLBACK_VOCAB.moves),
    lenses: normalizeGroup(src.lenses, FALLBACK_VOCAB.lenses),
    speeds: normalizeGroup(src.speeds, FALLBACK_VOCAB.speeds),
    framings: normalizeGroup(src.framings, FALLBACK_VOCAB.framings),
  };
}

export const ids = (list: VocabItem[]): string[] => list.map((i) => i.id);
