/**
 * Settings shapes: tolerant readers for `GET/PUT /api/settings` and
 * `POST /api/settings/test`.
 *
 * The server lane owns the endpoint. These helpers accept the documented shape
 * (`{surface, base_url, key_set, key_last4}`) and the obvious aliases, so the
 * drawer is honest rather than broken when a field is spelled differently —
 * and they never invent a key: `key_set` is true only when the server says so.
 */
import {
  DEFAULT_BASE_URL,
  type SettingsTestResult,
  type StudioSettings,
  type Surface,
} from './types';

const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object';

const text = (v: unknown): string | undefined =>
  typeof v === 'string' && v.trim() ? v.trim() : undefined;

const truthy = (v: unknown): boolean => v === true || v === 'true' || v === 1;

export function inferSurface(baseUrl: string): Surface {
  return /comfy\.org/i.test(baseUrl) ? 'cloud' : 'local';
}

export function normalizeSettings(raw: unknown): StudioSettings {
  const o = isRecord(raw) ? raw : {};
  const baseUrl =
    text(o.base_url) ?? text(o.baseUrl) ?? text(o.base) ?? text(o.comfy_base_url) ?? '';
  const surfaceRaw = text(o.surface) ?? text(o.backend) ?? text(o.mode);
  const surface: Surface =
    surfaceRaw === 'cloud' || surfaceRaw === 'local'
      ? surfaceRaw
      : surfaceRaw === 'comfy-cloud' || surfaceRaw === 'comfy_cloud'
        ? 'cloud'
        : inferSurface(baseUrl || DEFAULT_BASE_URL.local);
  const keySet = truthy(o.key_set) || truthy(o.api_key_set) || truthy(o.has_key);
  const last4 =
    text(o.key_last4) ??
    text(o.api_key_last4) ??
    text(o.last4) ??
    text(o.key_hint)?.replace(/[^0-9a-zA-Z]/g, '').slice(-4);

  return {
    surface,
    base_url: baseUrl || DEFAULT_BASE_URL[surface],
    key_set: keySet,
    key_last4: keySet ? last4 : undefined,
  };
}

/** The line the drawer shows — never the key, only its tail. */
export function keyLabel(settings: Pick<StudioSettings, 'key_set' | 'key_last4'> | null): string {
  if (!settings?.key_set) return 'no key set';
  return settings.key_last4 ? `key set …${settings.key_last4}` : 'key set';
}

export function normalizeTestResult(raw: unknown, fallbackMessage = ''): SettingsTestResult {
  const o = isRecord(raw) ? raw : {};
  const ok = truthy(o.ok) || truthy(o.reachable) || truthy(o.success) || truthy(o.connected);
  const message = text(o.message) ?? text(o.detail) ?? text(o.error) ?? fallbackMessage;
  return {
    status: ok ? 'ok' : 'error',
    message: message || (ok ? 'connected' : 'connection failed'),
    version: text(o.version) ?? text(o.comfyui_version),
  };
}

/** Client-side guard: never put a key in a URL, a feed row or an error. */
export function redact(s: string): string {
  return s.replace(/(X-API-Key:\s*)\S+/gi, '$1***').replace(/\b(sk-[A-Za-z0-9_-]{6,})\b/g, '***');
}
