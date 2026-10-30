import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setPollingEnabled, useStudio } from '../store';
import type { StudioSettings } from '../types';
import { SettingsDrawer } from './SettingsDrawer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function setInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('SettingsDrawer', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchSpy: ReturnType<typeof vi.fn>;

  const cloudWithKey: StudioSettings = {
    surface: 'cloud',
    base_url: 'https://cloud.comfy.org',
    key_set: true,
    key_last4: '7f2a',
  };

  const renderDrawer = async () => {
    await act(async () => {
      root.render(<SettingsDrawer />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  };

  beforeEach(() => {
    setPollingEnabled(false);
    useStudio.getState().reset();
    fetchSpy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith('/api/settings/test') && init?.method === 'POST') {
        return json({ ok: true, version: '0.3.40', message: 'reachable' });
      }
      if (path.endsWith('/api/settings') && init?.method === 'PUT') {
        return json({ ...cloudWithKey, ...JSON.parse(String(init.body)) });
      }
      if (path.endsWith('/api/settings')) return json(cloudWithKey);
      if (path.endsWith('/api/health')) return json({ ok: true, comfy: { reachable: true } });
      return json({});
    });
    vi.stubGlobal('fetch', fetchSpy);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('is closed until asked, and then never re-displays the stored key', async () => {
    await renderDrawer();
    expect(container.querySelector('.drawer')).toBeNull();

    await act(async () => {
      useStudio.getState().openSettings();
    });
    await renderDrawer();

    const key = container.querySelector<HTMLInputElement>('input.key-input');
    expect(key).not.toBeNull();
    expect(key!.type).toBe('password');
    expect(key!.value).toBe('');
    expect(key!.getAttribute('autocomplete')).toBe('new-password');

    const text = container.textContent ?? '';
    expect(text).toContain('key set …7f2a');
    expect(text).toContain('Get a key: platform.comfy.org');
    expect(container.querySelector<HTMLInputElement>('input.text-input[type="url"]')?.value).toBe(
      'https://cloud.comfy.org',
    );
  });

  it('sends a typed key once, then clears the box', async () => {
    useStudio.setState({ settings: { ...cloudWithKey, key_set: false, key_last4: undefined }, settingsOpen: true });
    await renderDrawer();

    const key = container.querySelector<HTMLInputElement>('input.key-input')!;
    setInput(key, 'brand-new-key-0000');

    const save = [...container.querySelectorAll<HTMLButtonElement>('button')].find((b) =>
      (b.textContent ?? '').trim() === 'Save',
    );
    expect(save).toBeDefined();
    await act(async () => {
      save!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const put = fetchSpy.mock.calls.find(
      ([url, init]) => String(url).endsWith('/api/settings') && init?.method === 'PUT',
    );
    expect(put).toBeDefined();
    const body = JSON.parse(String(put![1]?.body));
    expect(body.api_key).toBe('brand-new-key-0000');
    expect(body.surface).toBe('cloud');

    // the key is not left sitting in the form, nor in any feed row
    expect(container.querySelector<HTMLInputElement>('input.key-input')?.value).toBe('');
    const feed = JSON.stringify(useStudio.getState().feed);
    expect(feed).not.toContain('brand-new-key-0000');
  });

  it('carries the access token on a settings write, masked and never on the feed', async () => {
    useStudio.setState({ settings: cloudWithKey, settingsOpen: true });
    await renderDrawer();

    const token = container.querySelector<HTMLInputElement>('input.token-input');
    expect(token).not.toBeNull();
    expect(token!.type).toBe('password');
    expect(token!.value).toBe('');

    setInput(token!, 'tok-secret-123');

    const save = [...container.querySelectorAll<HTMLButtonElement>('button')].find((b) =>
      (b.textContent ?? '').trim() === 'Save',
    );
    expect(save).toBeDefined();
    await act(async () => {
      save!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const put = fetchSpy.mock.calls.find(
      ([url, init]) => String(url).endsWith('/api/settings') && init?.method === 'PUT',
    );
    expect(put).toBeDefined();
    const headers = new Headers(put![1]?.headers);
    expect(headers.get('authorization')).toBe('Bearer tok-secret-123');
    expect(JSON.stringify(useStudio.getState().feed)).not.toContain('tok-secret-123');
  });

  it('shows a green result when the connection test passes', async () => {
    useStudio.setState({ settings: cloudWithKey, settingsOpen: true });
    await renderDrawer();

    const probe = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (b) => (b.textContent ?? '').trim() === 'Test connection',
    );
    expect(probe).toBeDefined();
    await act(async () => {
      probe!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const result = container.querySelector('.test-result');
    expect(result).not.toBeNull();
    expect(result!.className).toContain('test-ok');
    expect(result!.textContent).toContain('connected');
    expect(result!.textContent).toContain('0.3.40');
  });

  it('auto-opens once on a first run: cloud surface, no key', async () => {
    fetchSpy.mockImplementation(async (url: string | URL | Request) => {
      if (String(url).endsWith('/api/settings')) {
        return json({ surface: 'cloud', base_url: 'https://cloud.comfy.org', key_set: false });
      }
      return json({ ok: true });
    });

    await act(async () => {
      await useStudio.getState().refreshSettings();
    });
    expect(useStudio.getState().settingsOpen).toBe(true);

    await renderDrawer();
    expect(container.textContent).toContain('Comfy Cloud needs an API key');
  });

  it('says the truth when the backend has no settings endpoint', async () => {
    fetchSpy.mockImplementation(async () => json({ detail: 'not found' }, 404));
    await act(async () => {
      await useStudio.getState().refreshSettings();
    });
    expect(useStudio.getState().settingsAvailable).toBe(false);

    useStudio.setState({ settingsOpen: true });
    await renderDrawer();
    expect(container.textContent).toContain('no settings endpoint yet');
  });
});
