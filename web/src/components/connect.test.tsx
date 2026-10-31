import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LOCAL_BASE,
  STORAGE_KEY,
  closeConnectGate,
  detectStartOs,
  isConnectGateOpen,
  openConnectGate,
  readStoredConnection,
  resetConnectionForTests,
  startCommand,
} from '../connection';
import { ConnectionPanel } from './ConnectionPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const health = () =>
  new Response(JSON.stringify({ ok: true, version: '0.3.40', comfy: { reachable: true } }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

function setInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const click = (el: Element | null) => {
  act(() => {
    el?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

const button = (container: HTMLElement, text: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) ?? null;

const preset = (container: HTMLElement, name: string) =>
  Array.from(container.querySelectorAll('.conn-preset')).find((b) =>
    b.textContent?.includes(name),
  ) ?? null;

describe('ConnectionPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  const render = async (variant: 'gate' | 'settings' = 'settings') => {
    await act(async () => {
      root.render(<ConnectionPanel variant={variant} />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  };

  beforeEach(() => {
    localStorage.clear();
    resetConnectionForTests();
    closeConnectGate();
    vi.unstubAllGlobals();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it('offers the three ways to run the studio', async () => {
    await render();
    const names = Array.from(container.querySelectorAll('.conn-preset-name')).map((n) => n.textContent);
    expect(names).toEqual(['This computer', 'Remote URL', 'Demo mode']);
  });

  it('adopts the loopback backend and remembers it', async () => {
    await render();
    click(preset(container, 'This computer'));
    click(button(container, 'Use this connection'));
    expect(readStoredConnection()).toEqual({ mode: 'local', baseUrl: LOCAL_BASE });
    expect(container.textContent).toContain(`Using ${LOCAL_BASE}`);
  });

  it('adopts demo mode with no backend at all', async () => {
    await render();
    click(preset(container, 'Demo mode'));
    click(button(container, 'Use this connection'));
    expect(readStoredConnection()).toEqual({ mode: 'demo', baseUrl: '' });
    expect(container.textContent).toContain('Demo mode is on.');
  });

  it('probes a remote URL before adopting it, and shows the verdict', async () => {
    const fetchSpy = vi.fn(async () => health());
    vi.stubGlobal('fetch', fetchSpy);
    await render();

    click(preset(container, 'Remote URL'));
    const input = container.querySelector('input.conn-input') as HTMLInputElement;
    setInput(input, 'cine.example.com');

    await act(async () => {
      button(container, 'Test studio connection')?.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      'https://cine.example.com/api/health',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(container.querySelector('.conn-result')?.textContent).toContain('reachable');

    click(button(container, 'Use this connection'));
    expect(readStoredConnection()).toEqual({ mode: 'remote', baseUrl: 'https://cine.example.com' });
  });

  it('refuses a remote value that is not a URL, without storing it', async () => {
    await render();
    click(preset(container, 'Remote URL'));
    const input = container.querySelector('input.conn-input') as HTMLInputElement;
    setInput(input, 'ftp://cine.example.com');
    click(button(container, 'Use this connection'));

    expect(container.querySelector('.conn-error')?.textContent).toMatch(/http/i);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('closes the first-visit gate after a choice', async () => {
    openConnectGate();
    expect(isConnectGateOpen()).toBe(true);
    await render('gate');
    click(button(container, 'Open the studio'));
    expect(isConnectGateOpen()).toBe(false);
    expect(readStoredConnection()).not.toBeNull();
  });

  it('carries copy-paste setup steps with no internal paths', async () => {
    await render();
    const steps = container.querySelector('.conn-steps pre')?.textContent ?? '';
    expect(steps).toContain('uvicorn cinema.app:app');
    expect(steps).not.toMatch(/AitherOS|media-forge|C:\\Users/i);
  });

  it('leads with one start command for the visitor OS', async () => {
    expect(detectStartOs('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')).toBe('windows');
    expect(detectStartOs('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)')).toBe('unix');
    await render();
    const cmd = container.querySelector('.conn-start-cmd')?.textContent ?? '';
    expect(cmd).toMatch(/scripts\/start\.(sh|ps1)/);
    expect(cmd).not.toMatch(/AitherOS|media-forge|C:\\Users/i);
  });

  it('switches the command to Comfy Cloud when the visitor has no GPU', async () => {
    expect(startCommand('unix', 'cloud')).toContain('CINEMA_COMFY=cloud sh');
    expect(startCommand('windows', 'cloud')).toMatch(/^\$env:CINEMA_COMFY='cloud'; irm /);
    await render();
    const choices = Array.from(container.querySelectorAll('.conn-render-choice'));
    expect(choices.map((b) => b.textContent)).toEqual(['My GPU', 'Comfy Cloud']);
    expect(container.querySelector('.conn-start-cmd')?.textContent).not.toContain('cloud');
    click(choices[1] as HTMLButtonElement);
    expect(container.querySelector('.conn-start-cmd')?.textContent).toContain('cloud');
  });
});
