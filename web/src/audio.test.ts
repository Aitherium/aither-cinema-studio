import { describe, expect, it } from 'vitest';
import { beatTimes, clock, computePeaks, isDownbeat, totalBeats } from './audio';

describe('computePeaks', () => {
  it('reduces every channel to normalised per-bucket peaks', () => {
    const left = new Float32Array([0, 0.5, 0, -1]);
    const right = new Float32Array([0, 0.25, 0, 0.5]);
    const peaks = computePeaks([left, right], 2);
    expect(peaks).toHaveLength(2);
    // bucket 0 holds indexes 0..1 (peak 0.5), bucket 1 holds 2..3 (peak 1.0)
    expect(peaks[0]).toBeCloseTo(0.5);
    expect(peaks[1]).toBeCloseTo(1);
  });

  it('returns silent buckets as zeros rather than dividing by zero', () => {
    const peaks = computePeaks([new Float32Array(4)], 2);
    expect(peaks).toEqual([0, 0]);
  });

  it('handles no channels and empty input', () => {
    expect(computePeaks([])).toEqual([]);
    expect(computePeaks([new Float32Array(0)])).toEqual([]);
  });

  it('never returns more buckets than samples', () => {
    expect(computePeaks([new Float32Array([0.5, 0.2])], 720)).toHaveLength(2);
  });
});

describe('beats', () => {
  it('counts whole beats in a track', () => {
    expect(totalBeats(120, 10)).toBe(20);
    expect(totalBeats(90, 60)).toBe(90);
    // 133 bpm for 7.5 s is 16.6 beats -> 16 whole beats
    expect(totalBeats(133, 7.5)).toBe(16);
  });

  it('is zero for nonsense input', () => {
    expect(totalBeats(0, 10)).toBe(0);
    expect(totalBeats(120, 0)).toBe(0);
    expect(totalBeats(NaN, 10)).toBe(0);
  });

  it('lays a grid that starts at zero and stays inside the track', () => {
    const times = beatTimes(120, 2);
    expect(times).toEqual([0, 0.5, 1, 1.5]);
    expect(times.every((t) => t >= 0 && t < 2)).toBe(true);
  });

  it('caps a long track instead of looping forever', () => {
    expect(beatTimes(400, 1e6).length).toBeLessThanOrEqual(8192);
    expect(beatTimes(0, 10)).toEqual([]);
  });

  it('marks every fourth beat as a downbeat', () => {
    expect([0, 1, 2, 3, 4, 7, 8].filter((i) => isDownbeat(i))).toEqual([0, 4, 8]);
  });
});

describe('clock', () => {
  it('prints m:ss and refuses nonsense', () => {
    expect(clock(0)).toBe('0:00');
    expect(clock(59.4)).toBe('0:59');
    expect(clock(61)).toBe('1:01');
    expect(clock(undefined)).toBe('0:00');
    expect(clock(-3)).toBe('0:00');
  });
});
