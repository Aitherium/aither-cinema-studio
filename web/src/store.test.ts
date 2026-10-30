import { beforeEach, describe, expect, it } from 'vitest';
import { musicAccepted, useStudio } from './store';

beforeEach(() => {
  useStudio.getState().reset();
});

describe('engine and mode stay a coherent plan', () => {
  it('picking the Fun-Camera builder gives it a start image to move', () => {
    useStudio.getState().setMode('t2v');
    useStudio.getState().setEngine('camera');
    expect(useStudio.getState().mode).toBe('i2v');
  });

  it('picking the still engine switches the plan to a keyframe', () => {
    useStudio.getState().setMode('t2v');
    useStudio.getState().setEngine('keyframe');
    expect(useStudio.getState().mode).toBe('keyframe');
  });

  it('leaving keyframe mode leaves the still engine behind', () => {
    useStudio.getState().setEngine('keyframe');
    useStudio.getState().setMode('i2v');
    expect(useStudio.getState().engine).toBe('wan2.2');
    expect(useStudio.getState().mode).toBe('i2v');
  });

  it('keeps a moving mode when a moving engine is picked', () => {
    useStudio.getState().setMode('flf2v');
    useStudio.getState().setEngine('wan2.2');
    expect(useStudio.getState().mode).toBe('flf2v');
    expect(useStudio.getState().engine).toBe('wan2.2');
  });
});

describe('musicAccepted reads the cut the backend actually made', () => {
  it('accepts the echo a music-muxing backend sends', () => {
    expect(
      musicAccepted(
        { job_id: 'j1', audio_media_id: 'aud-1', music_video: true, bpm: 120 },
        { audio_media_id: 'aud-1' },
      ),
    ).toBe(true);
  });

  it('accepts the beat sheet echoed in job.params', () => {
    expect(
      musicAccepted(
        { job_id: 'j2', params: { beats_per_shot: [4, 8] } },
        { audio_media_id: 'aud-1', beats_per_shot: [4, 8] },
      ),
    ).toBe(true);
  });

  it('rejects a plain cut that carries no music', () => {
    expect(
      musicAccepted({ job_id: 'j3', music_video: false }, { audio_media_id: 'aud-1' }),
    ).toBe(false);
  });
});
