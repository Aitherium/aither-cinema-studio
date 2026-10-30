import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setPollingEnabled, useStudio } from '../store';
import type { Shot } from '../types';
import { Timeline } from './Timeline';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const shot = (id: string): Shot => ({
  shot_id: id,
  mode: 't2v',
  prompt: 'a platform at dusk',
  camera: { move: 'static', lens: '35mm', speed: 'normal', framing: 'medium' },
  status: 'done',
  engine: 'wan2.2',
});

describe('Timeline music-video mode', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    setPollingEnabled(false);
    useStudio.getState().reset();
    useStudio.setState({ shots: [shot('a'), shot('b')], timeline: ['a', 'b'] });
    // jsdom has no 2d context; the component must survive that
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  const render = async () => {
    await act(async () => {
      root.render(<Timeline />);
      await Promise.resolve();
    });
  };

  it('stays a plain cut until music video is switched on', async () => {
    await render();
    expect(container.querySelectorAll('.cut')).toHaveLength(2);
    expect(container.querySelector('.music-bed')).toBeNull();
    expect(container.querySelectorAll('.cut-beats')).toHaveLength(0);
  });

  it('opens the music bed with a BPM field and a beat count per shot', async () => {
    await render();

    const toggle = container.querySelector<HTMLButtonElement>('.music-toggle')!;
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    await act(async () => {
      toggle.click();
    });

    expect(container.querySelector('.music-bed')).not.toBeNull();
    const bpm = container.querySelector<HTMLInputElement>('.bpm-field input')!;
    expect(bpm.value).toBe('120');

    const beatInputs = [...container.querySelectorAll<HTMLInputElement>('.cut-beats input')];
    expect(beatInputs).toHaveLength(2);
    expect(beatInputs.map((i) => i.value)).toEqual(['4', '4']);
  });

  it('drives the BPM and the per-shot beats in the store', async () => {
    await render();
    await act(async () => {
      container.querySelector<HTMLButtonElement>('.music-toggle')!.click();
    });

    const bpm = container.querySelector<HTMLInputElement>('.bpm-field input')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(bpm, '96');
      bpm.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(useStudio.getState().bpm).toBe(96);

    const first = container.querySelectorAll<HTMLInputElement>('.cut-beats input')[0];
    await act(async () => {
      setter?.call(first, '8');
      first.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(useStudio.getState().beats.a).toBe(8);
  });

  it('cannot cut a music video without an uploaded track, and says why', async () => {
    await render();
    await act(async () => {
      container.querySelector<HTMLButtonElement>('.music-toggle')!.click();
    });

    const cut = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (b) => (b.textContent ?? '').trim() === 'Cut music video',
    );
    expect(cut).toBeDefined();
    expect(cut!.disabled).toBe(true);
    expect(cut!.title).toMatch(/upload an audio track/i);
    expect(container.textContent).toContain('no track yet');
  });
});
