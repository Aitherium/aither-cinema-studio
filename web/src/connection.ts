/**
 * Where the studio sends its requests.
 *
 * The public demo is a static page: there is no backend at the hosting origin,
 * so the visitor picks one —
 *
 *   'same-origin'  the page was served by the studio backend itself (dev / self-host)
 *   'local'        a backend on this computer (http://127.0.0.1:8765)
 *   'remote'       a backend the visitor hosts somewhere else (https://their-host)
 *   'demo'         no backend: an in-page mock, pre-rendered proofs, live tools
 *
 * The choice is persisted in localStorage (best effort — a private window may
 * refuse), and every reader goes through `getConnection()` so the API client
 * and the UI can never disagree about which backend is in use.
 */
import { useSyncExternalStore } from 'react';

export type ConnectionMode = 'same-origin' | 'local' | 'remote' | 'demo';

export interface Connection {
  mode: ConnectionMode;
  /** '' for same-origin and demo; a full origin (plus optional path) otherwise. */
  baseUrl: string;
}

export interface ConnectionPreset {
  id: 'local' | 'remote' | 'demo';
  label: string;
  hint: string;
}

export const LOCAL_BASE = 'http://127.0.0.1:8765';
export const PAGES_ORIGIN = 'https://aitherium.github.io';
export const STORAGE_KEY = 'aither.cinema.connection.v1';

export const CONNECTION_PRESETS: ConnectionPreset[] = [
  {
    id: 'local',
    label: 'This computer',
    hint: 'a studio backend on http://127.0.0.1:8765',
  },
  {
    id: 'remote',
    label: 'Remote URL',
    hint: 'a studio backend you host — paste its https:// address',
  },
  {
    id: 'demo',
    label: 'Demo mode',
    hint: 'no backend — the studio simulates renders in this page',
  },
];

/* --------------------------------------------------------------- address --- */

/** Trim, add a scheme when it is missing, and reject anything but http(s). */
export function normalizeBaseUrl(raw: string): string {
  const trimmed = String(raw ?? '').trim();
  if (!trimmed) {
    throw new Error('Enter the address of your studio backend, e.g. https://cine.example.com');
  }
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(trimmed);
  if (scheme && !/^https?$/i.test(scheme[1])) {
    throw new Error('only http:// and https:// backends are supported');
  }
  const candidate = scheme ? trimmed : `https://${trimmed}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new Error(`"${trimmed}" is not a URL — it should look like https://their-host`);
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('only http:// and https:// backends are supported');
  }
  if (!url.hostname) {
    throw new Error(`"${trimmed}" has no host — it should look like https://their-host`);
  }
  const path = url.pathname.replace(/\/+$/, '');
  return `${url.origin}${path}`;
}

export function normalizeConnection(next: Partial<Connection> | null | undefined): Connection {
  const mode = next?.mode;
  if (mode === 'demo' || mode === 'same-origin') return { mode, baseUrl: '' };
  if (mode === 'local') {
    const raw = (next?.baseUrl ?? '').trim();
    return { mode, baseUrl: raw ? normalizeBaseUrl(raw) : LOCAL_BASE };
  }
  if (mode === 'remote') return { mode, baseUrl: normalizeBaseUrl(next?.baseUrl ?? '') };
  return defaultConnection();
}

/* --------------------------------------------------------------- storage --- */

function storage(): Storage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    // Some environments expose an inert object instead of a Storage (and a
    // blocked cookie jar can make the accessor itself throw).
    if (typeof localStorage.getItem !== 'function' || typeof localStorage.setItem !== 'function') {
      return null;
    }
    return localStorage;
  } catch {
    return null;
  }
}

export function readStoredConnection(): Connection | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Connection> | null;
    if (!parsed || typeof parsed !== 'object') return null;
    const mode = parsed.mode;
    if (mode !== 'same-origin' && mode !== 'local' && mode !== 'remote' && mode !== 'demo') return null;
    return normalizeConnection({ mode, baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '' });
  } catch {
    return null;
  }
}

function writeStoredConnection(connection: Connection): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(connection));
  } catch {
    /* quota, private mode, blocked storage — the session still works */
  }
}

/** First visit: a GitHub Pages page has no backend, so it opens in demo mode. */
export function defaultConnection(
  hostname?: string,
  protocol?: string,
): Connection {
  const host =
    hostname ?? (typeof location !== 'undefined' ? location.hostname : '');
  const proto = protocol ?? (typeof location !== 'undefined' ? location.protocol : 'http:');
  if (!host || proto === 'file:') return { mode: 'demo', baseUrl: '' };
  if (/(^|\.)github\.io$/i.test(host)) return { mode: 'demo', baseUrl: '' };
  return { mode: 'same-origin', baseUrl: '' };
}

/* ------------------------------------------------------------ live value --- */

let current: Connection | null = null;
let gateOpen: boolean | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of [...listeners]) listener();
}

export function getConnection(): Connection {
  if (!current) current = readStoredConnection() ?? defaultConnection();
  return current;
}

export function setConnection(next: Partial<Connection> | Connection): Connection {
  current = normalizeConnection(next);
  writeStoredConnection(current);
  emit();
  return current;
}

export function hasSavedConnection(): boolean {
  return readStoredConnection() !== null;
}

export function subscribeConnection(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Tests (and a "forget this backend" button) start from a clean slate. */
export function resetConnectionForTests(): void {
  current = null;
  gateOpen = null;
}

/* -------------------------------------------------------------- the gate --- */

export function isConnectGateOpen(): boolean {
  if (gateOpen === null) {
    // A backend that serves the page itself (dev :5173, self-host, the desktop
    // app) needs no question asked. A static page does — it has no backend.
    gateOpen = !hasSavedConnection() && defaultConnection().mode !== 'same-origin';
  }
  return gateOpen;
}

export function openConnectGate(): void {
  gateOpen = true;
  emit();
}

export function closeConnectGate(): void {
  gateOpen = false;
  emit();
}

/* ------------------------------------------------------------------ hooks --- */

export function useConnection(): Connection {
  return useSyncExternalStore(subscribeConnection, getConnection, getConnection);
}

export function useConnectGate(): boolean {
  return useSyncExternalStore(subscribeConnection, isConnectGateOpen, () => false);
}

/* ------------------------------------------------------------------- URLs --- */

/** The prefix every request carries; '' means the page's own origin. */
export function apiBaseFor(connection: Connection): string {
  return connection.mode === 'local' || connection.mode === 'remote' ? connection.baseUrl : '';
}

export function apiBase(): string {
  return apiBaseFor(getConnection());
}

export function isDemoMode(): boolean {
  return getConnection().mode === 'demo';
}

export function healthUrlFor(connection: Connection, origin?: string): string {
  const base = apiBaseFor(connection);
  if (base) return `${base}/api/health`;
  const here = origin ?? (typeof location !== 'undefined' ? location.origin : '');
  return here ? `${here}/api/health` : '/api/health';
}

/** A one-line description of the active connection, for the header and drawer. */
export function connectionLabel(connection: Connection): string {
  if (connection.mode === 'demo') return 'Demo mode (in this page)';
  if (connection.mode === 'same-origin') return 'This server';
  return connection.baseUrl;
}

/* ------------------------------------------------------------------ probe --- */

export interface ProbeResult {
  status: 'idle' | 'probing' | 'ok' | 'error';
  message?: string;
  version?: string;
  comfyReachable?: boolean;
}

interface HealthBody {
  ok?: boolean;
  version?: string;
  comfy?: { reachable?: boolean; base?: string } | null;
}

/**
 * GET {base}/api/health, with a timeout and an honest verdict. Demo mode never
 * touches the network. Chrome from an https page to http://127.0.0.1 is
 * allowed, but sends a Private Network Access preflight; the studio server
 * answers it (`CINEMA_CORS_ORIGINS` must list this page's origin).
 */
export async function probeConnection(
  connection: Connection,
  options: { timeoutMs?: number } = {},
): Promise<ProbeResult> {
  if (connection.mode === 'demo') {
    return { status: 'ok', message: 'Demo mode — everything runs in this page, no backend needed.' };
  }
  const url = healthUrlFor(connection);
  const timeoutMs = options.timeoutMs ?? 6000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!res.ok) {
      return {
        status: 'error',
        message: `the backend answered ${res.status} at ${url}. Is a studio server listening there?`,
      };
    }
    let body: HealthBody = {};
    try {
      body = (await res.json()) as HealthBody;
    } catch {
      /* a health route that is not JSON is still a live socket */
    }
    const comfyReachable = body?.comfy?.reachable === true;
    const version = typeof body?.version === 'string' ? body.version : undefined;
    const comfyNote = comfyReachable
      ? 'ComfyUI reachable'
      : 'backend up, but ComfyUI is not reachable yet — start ComfyUI or set COMFY_BASE_URL';
    return {
      status: 'ok',
      version,
      comfyReachable,
      message: `connected${version ? ` · studio ${version}` : ''} — ${comfyNote}.`,
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return {
        status: 'error',
        message: `no answer within ${Math.round(timeoutMs / 1000)} s — the server may still be starting.`,
      };
    }
    return {
      status: 'error',
      message: `could not reach ${url}. The server is not running, the port is closed, or the browser blocked the private-network request (see the setup steps).`,
    };
  } finally {
    clearTimeout(timer);
  }
}

/* -------------------------------------------------------------- the steps --- */

const RAW = 'https://raw.githubusercontent.com/Aitherium/aither-cinema-studio/main/scripts';

/** One command per OS: the script clones/updates, installs, allows this page's origin, and starts. */
export const START_COMMANDS = {
  windows: `irm ${RAW}/start.ps1 | iex`,
  unix: `curl -fsSL ${RAW}/start.sh | sh`,
} as const;

export type StartOs = keyof typeof START_COMMANDS;

/** Where the renders run: the visitor's own GPU (ComfyUI + models installed for them) or Comfy Cloud. */
export type RenderOn = 'gpu' | 'cloud';

export const RENDER_ON: Record<RenderOn, { label: string; line: string }> = {
  gpu: {
    label: 'My GPU',
    line:
      'NVIDIA GPU or Apple Silicon. First run installs ComfyUI and the models (about 74 GB; ' +
      'add CINEMA_MODELS=keyframe for a 7 GB stills-only start). Nothing leaves your machine.',
  },
  cloud: {
    label: 'Comfy Cloud',
    line:
      'No GPU needed. Installs only the small backend; paste your Comfy API key ' +
      '(platform.comfy.org) in Settings after you connect.',
  },
};

export function startCommand(os: StartOs, on: RenderOn): string {
  if (on === 'gpu') return START_COMMANDS[os];
  return os === 'windows'
    ? `$env:CINEMA_COMFY='cloud'; ${START_COMMANDS.windows}`
    : `curl -fsSL ${RAW}/start.sh | CINEMA_COMFY=cloud sh`;
}

export const START_WHERE: Record<StartOs, string> = {
  windows: 'Paste into PowerShell',
  unix: 'Paste into a terminal (macOS / Linux)',
};

export function detectStartOs(
  ua: string = typeof navigator !== 'undefined' ? navigator.userAgent : '',
): StartOs {
  return /windows/i.test(ua) ? 'windows' : 'unix';
}

/** The long form, for people who would rather not pipe a script. Generic: no internal hosts or paths. */
export const SETUP_STEPS: string[] = [
  'git clone https://github.com/Aitherium/aither-cinema-studio.git',
  'cd aither-cinema-studio/server',
  'python -m venv .venv',
  '.venv/bin/pip install -e .        # Windows: .venv\\Scripts\\pip install -e .',
  `export CINEMA_CORS_ORIGINS=${PAGES_ORIGIN}`,
  '.venv/bin/python -m uvicorn cinema.app:app --host 127.0.0.1 --port 8765',
];

export const SETUP_NOTE =
  'Needs git, Python 3.10+, and ComfyUI on http://127.0.0.1:8188 (or COMFY_API_KEY for Comfy Cloud). ' +
  'If the browser asks to reach devices on your local network, allow it.';
