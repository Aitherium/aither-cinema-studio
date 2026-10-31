import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LOCAL_BASE,
  PAGES_ORIGIN,
  SETUP_NOTE,
  SETUP_STEPS,
  STORAGE_KEY,
  apiBase,
  connectionLabel,
  defaultConnection,
  getConnection,
  hasSavedConnection,
  healthUrlFor,
  isConnectGateOpen,
  isDemoMode,
  normalizeBaseUrl,
  normalizeConnection,
  probeConnection,
  readStoredConnection,
  resetConnectionForTests,
  setConnection,
  subscribeConnection,
} from './connection';

beforeEach(() => {
  localStorage.clear();
  resetConnectionForTests();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('normalizeBaseUrl', () => {
  it('adds https:// when the scheme is missing and strips the trailing slash', () => {
    expect(normalizeBaseUrl('cine.example.com/')).toBe('https://cine.example.com');
  });

  it('keeps an explicit http:// scheme and a path prefix', () => {
    expect(normalizeBaseUrl('http://127.0.0.1:8765/studio/')).toBe('http://127.0.0.1:8765/studio');
  });

  it('refuses empty input and non-http schemes', () => {
    expect(() => normalizeBaseUrl('   ')).toThrow(/Enter the address/);
    expect(() => normalizeBaseUrl('ftp://cine.example.com')).toThrow(/http/i);
  });
});

describe('defaultConnection', () => {
  it('opens a GitHub Pages visit in demo mode — the static page has no backend', () => {
    expect(defaultConnection('aitherium.github.io', 'https:')).toEqual({ mode: 'demo', baseUrl: '' });
    expect(defaultConnection('someone.github.io', 'https:')).toEqual({ mode: 'demo', baseUrl: '' });
  });

  it('keeps same-origin where a backend serves the page (dev and self-host)', () => {
    expect(defaultConnection('localhost', 'http:')).toEqual({ mode: 'same-origin', baseUrl: '' });
    expect(defaultConnection('cine.example.com', 'https:')).toEqual({ mode: 'same-origin', baseUrl: '' });
  });

  it('falls back to demo on a file: page, which can never have a same-origin backend', () => {
    expect(defaultConnection('', 'file:')).toEqual({ mode: 'demo', baseUrl: '' });
  });
});

describe('storage', () => {
  it('persists a choice and reads it back', () => {
    setConnection({ mode: 'remote', baseUrl: 'https://cine.example.com' });
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual({
      mode: 'remote',
      baseUrl: 'https://cine.example.com',
    });
    expect(readStoredConnection()).toEqual({ mode: 'remote', baseUrl: 'https://cine.example.com' });
  });

  it('treats unreadable or corrupt storage as no choice at all', () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(readStoredConnection()).toBeNull();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ mode: 'sneaky' }));
    expect(readStoredConnection()).toBeNull();
  });

  it('does not throw when the storage accessor is blocked', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readStoredConnection()).toBeNull();
  });

  it('defaults a local choice to the loopback backend', () => {
    expect(normalizeConnection({ mode: 'local' })).toEqual({ mode: 'local', baseUrl: LOCAL_BASE });
  });
});

describe('live value', () => {
  it('notifies subscribers when the connection changes', () => {
    const listener = vi.fn();
    const off = subscribeConnection(listener);
    setConnection({ mode: 'demo' });
    expect(listener).toHaveBeenCalledTimes(1);
    off();
    setConnection({ mode: 'same-origin' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('reports demo mode and the request base', () => {
    setConnection({ mode: 'demo' });
    expect(isDemoMode()).toBe(true);
    expect(apiBase()).toBe('');
    expect(connectionLabel(getConnection())).toMatch(/Demo mode/);

    setConnection({ mode: 'local', baseUrl: LOCAL_BASE });
    expect(isDemoMode()).toBe(false);
    expect(apiBase()).toBe(LOCAL_BASE);
  });
});

describe('connect gate', () => {
  it('stays shut where the page is served by a backend', () => {
    // jsdom's page is http://localhost — a same-origin backend needs no question.
    expect(hasSavedConnection()).toBe(false);
    expect(isConnectGateOpen()).toBe(false);
  });

  it('stays shut once a choice is saved', () => {
    setConnection({ mode: 'demo' });
    expect(hasSavedConnection()).toBe(true);
    expect(isConnectGateOpen()).toBe(false);
  });
});

describe('healthUrlFor', () => {
  it('points at the chosen base, or at this origin for same-origin', () => {
    expect(healthUrlFor({ mode: 'local', baseUrl: LOCAL_BASE })).toBe(`${LOCAL_BASE}/api/health`);
    expect(healthUrlFor({ mode: 'same-origin', baseUrl: '' }, 'https://cine.example.com')).toBe(
      'https://cine.example.com/api/health',
    );
  });
});

describe('probeConnection', () => {
  it('never touches the network in demo mode', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const result = await probeConnection({ mode: 'demo', baseUrl: '' });
    expect(result.status).toBe('ok');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('reports a healthy backend, including a warning when ComfyUI is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ ok: true, version: '0.3.40', comfy: { reachable: false } }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    );
    const result = await probeConnection({ mode: 'local', baseUrl: LOCAL_BASE });
    expect(result.status).toBe('ok');
    expect(result.version).toBe('0.3.40');
    expect(result.message).toMatch(/ComfyUI/);
  });

  it('turns a non-2xx answer into an honest failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 503 })));
    const result = await probeConnection({ mode: 'remote', baseUrl: 'https://cine.example.com' });
    expect(result.status).toBe('error');
    expect(result.message).toMatch(/503/);
  });

  it('turns a refused socket into a CORS/private-network explanation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );
    const result = await probeConnection({ mode: 'local', baseUrl: LOCAL_BASE });
    expect(result.status).toBe('error');
    expect(result.message).toMatch(/private-network/);
  });

  it('gives up at the timeout instead of hanging', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new DOMException('Aborted', 'AbortError');
      }),
    );
    const result = await probeConnection({ mode: 'local', baseUrl: LOCAL_BASE }, { timeoutMs: 250 });
    expect(result.status).toBe('error');
    expect(result.message).toMatch(/no answer within/);
  });
});

describe('the setup steps a visitor copies', () => {
  it('name the public repository, never an internal path or host', () => {
    const steps = SETUP_STEPS.join('\n');
    expect(steps).toContain('https://github.com/Aitherium/aither-cinema-studio.git');
    expect(steps).toContain('uvicorn cinema.app:app');
    // the start scripts allow this page's origin themselves; a manual install must be told to
    expect(steps).toContain(PAGES_ORIGIN);
    expect(`${steps}\n${SETUP_NOTE}`).not.toMatch(/AitherOS|media-forge|C:\\Users/i);
  });
});
