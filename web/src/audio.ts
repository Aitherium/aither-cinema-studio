/**
 * The music bed: decode an uploaded track in the browser, reduce it to peaks
 * the timeline can draw, and lay a beat grid over it.
 *
 * Pure helpers (`computePeaks`, `beatTimes`, `totalBeats`) carry every rule and
 * are unit-tested without WebAudio; `analyseAudio` is the only browser call and
 * it fails with a sentence, never a bare exception.
 */

/** Reduce decoded channels to at most `buckets` normalised peak values (0..1). */
export function computePeaks(channels: ArrayLike<number>[], buckets = 720): number[] {
  if (!channels.length) return [];
  const length = Math.min(...channels.map((c) => c.length));
  if (!Number.isFinite(length) || length <= 0) return [];
  const count = Math.max(1, Math.min(Math.floor(buckets), length));
  const peaks: number[] = [];
  for (let b = 0; b < count; b += 1) {
    const start = Math.floor((b * length) / count);
    const end = Math.max(start + 1, Math.floor(((b + 1) * length) / count));
    let peak = 0;
    for (const channel of channels) {
      for (let i = start; i < end && i < channel.length; i += 1) {
        const v = Math.abs(channel[i]);
        if (v > peak) peak = v;
      }
    }
    peaks.push(peak);
  }
  const loudest = peaks.reduce((m, v) => (v > m ? v : m), 0);
  return loudest > 0 ? peaks.map((v) => v / loudest) : peaks;
}

export function totalBeats(bpm: number, duration: number): number {
  if (!(bpm > 0) || !(duration > 0)) return 0;
  return Math.floor((duration * bpm) / 60);
}

/** Every beat onset in seconds; the last one strictly inside the track. */
export function beatTimes(bpm: number, duration: number): number[] {
  if (!(bpm > 0) || !(duration > 0)) return [];
  const step = 60 / bpm;
  const times: number[] = [];
  for (let t = 0; t < duration && times.length < 8192; t += step) times.push(t);
  return times;
}

/** Bar/beat grouping for labels: 4/4 unless the caller says otherwise. */
export function isDownbeat(index: number, beatsPerBar = 4): boolean {
  return index % Math.max(1, beatsPerBar) === 0;
}

interface Ctor {
  new (): AudioContext;
}

function contextCtor(): Ctor | null {
  const g = globalThis as unknown as { AudioContext?: Ctor; webkitAudioContext?: Ctor };
  return g.AudioContext ?? g.webkitAudioContext ?? null;
}

export interface AudioAnalysis {
  duration: number;
  peaks: number[];
}

/**
 * Fetch the media bytes and decode them. Throws an Error whose message is safe
 * to show (no URL credentials are involved; media is same-origin).
 */
export async function analyseAudio(url: string, buckets = 720): Promise<AudioAnalysis> {
  const Ctx = contextCtor();
  if (!Ctx) throw new Error('This browser has no WebAudio, so the waveform cannot be drawn. The track is still uploaded.');
  const res = await fetch(url);
  if (!res.ok) throw new Error(`could not read the track (${res.status})`);
  const bytes = await res.arrayBuffer();
  if (!bytes.byteLength) throw new Error('the uploaded track is empty');
  const ctx = new Ctx();
  try {
    const buffer = await ctx.decodeAudioData(bytes);
    const channels: Float32Array[] = [];
    for (let c = 0; c < buffer.numberOfChannels; c += 1) channels.push(buffer.getChannelData(c));
    return { duration: buffer.duration, peaks: computePeaks(channels, buckets) };
  } catch {
    throw new Error('could not decode this file as audio — try WAV, MP3 or OGG');
  } finally {
    void Promise.resolve(ctx.close?.()).catch(() => undefined);
  }
}

/** Seconds -> m:ss, for the drawer and the timeline footer. */
export function clock(seconds: number | undefined): string {
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
