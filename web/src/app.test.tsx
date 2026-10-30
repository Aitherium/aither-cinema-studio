import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { FALLBACK_VOCAB } from './cameraVocab';
import { setPollingEnabled, useStudio } from './store';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe('App', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    setPollingEnabled(false);
    useStudio.getState().reset();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string | URL | Request) => {
        const path = String(url);
        if (path.endsWith('/api/health')) return json({ ok: true, comfy: { reachable: true, base: 'http://127.0.0.1:8188' } });
        if (path.endsWith('/api/camera/vocab')) return json(FALLBACK_VOCAB);
        if (path.endsWith('/api/settings')) {
          return json({ surface: 'local', base_url: 'http://127.0.0.1:8188', key_set: false });
        }
        if (path.endsWith('/api/shots')) return json([]);
        return json({});
      }),
    );
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('renders every screen in the one page', async () => {
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });

    const text = container.textContent ?? '';
    for (const label of ['Shot board', 'Camera rig', 'Monitor', 'Timeline', 'Agent activity', 'backend', 'webmcp']) {
      expect(text).toContain(label);
    }

    // the whole camera vocabulary is offered as movable rig buttons
    expect(container.querySelectorAll('.move').length).toBe(FALLBACK_VOCAB.moves.length);
    expect(container.querySelectorAll('.segment').length).toBe(5);
    expect(container.querySelectorAll('.grade-chip').length).toBe(9);
  });

  it('a rig click drives the store from the UI (as a human)', async () => {
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });

    const buttons = [...container.querySelectorAll<HTMLButtonElement>('.move')];
    const orbit = buttons.find((b) => b.title.startsWith('Orbit left'));
    expect(orbit).toBeDefined();

    await act(async () => {
      orbit!.click();
    });

    expect(useStudio.getState().rig.move).toBe('orbit_left');
    // a rig click is not a WebMCP tool call, so it stays out of the feed
    expect(useStudio.getState().feed).toHaveLength(0);
  });

  it('shows the empty-board instructions before any shot exists', async () => {
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });
    expect(container.textContent).toContain('No shots yet.');
  });
});
