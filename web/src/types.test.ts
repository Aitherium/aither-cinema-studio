import { describe, expect, it } from 'vitest';
import { ENGINES, engineShort, resolveEngine, SERVER_ENGINES } from './types';

describe('resolveEngine', () => {
  it('folds the Wan 2.2 family into the builder the mode needs', () => {
    expect(resolveEngine('wan2.2', 't2v')).toBe('wan_t2v');
    expect(resolveEngine('wan2.2', 'i2v')).toBe('wan_i2v');
    expect(resolveEngine('wan2.2', 'flf2v')).toBe('wan_flf2v');
    expect(resolveEngine('wan2.2', 'reshoot')).toBe('wan_i2v');
  });

  it('keeps the dedicated builders', () => {
    expect(resolveEngine('camera', 'i2v')).toBe('wan_camera');
    expect(resolveEngine('ltx2', 't2v')).toBe('ltx');
    expect(resolveEngine('keyframe', 'keyframe')).toBe('sdxl');
    // a still engine in any mode is still a still
    expect(resolveEngine('keyframe', 't2v')).toBe('sdxl');
    // and a keyframe mode can only be the still builder
    expect(resolveEngine('wan2.2', 'keyframe')).toBe('sdxl');
  });

  it('only ever names ids the backend accepts', () => {
    const modes = ['keyframe', 't2v', 'i2v', 'flf2v', 'reshoot'] as const;
    for (const engine of ENGINES) {
      for (const mode of modes) {
        expect(SERVER_ENGINES).toContain(resolveEngine(engine.id, mode));
      }
    }
  });
});

describe('engineShort', () => {
  it('labels both picker families and server builder ids', () => {
    expect(ENGINES.map((e) => engineShort(e.id))).toEqual(['FunCam', 'Wan 2.2', 'LTX-2', 'Still']);
    expect(engineShort('wan_camera')).toBe('FunCam');
    expect(engineShort('wan_i2v')).toBe('Wan 2.2');
    expect(engineShort('ltx')).toBe('LTX-2');
    expect(engineShort('sdxl')).toBe('Still');
    expect(engineShort(undefined)).toBe('');
  });
});
