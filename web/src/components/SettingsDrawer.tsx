import { useEffect, useRef, useState } from 'react';
import { keyLabel } from '../settings';
import { FIRST_RUN_EXPLAINER, useStudio } from '../store';
import { DEFAULT_BASE_URL, SURFACES, type Surface } from '../types';

const KEY_URL = 'https://platform.comfy.org';

/**
 * The settings drawer: which Comfy the studio renders on, and the key it uses.
 *
 * The stored key is never re-displayed — `GET /api/settings` only reports
 * `key_set` and the last four characters, and the password box is emptied after
 * every save. A key typed here goes to `PUT /api/settings` once and nowhere else.
 */
export function SettingsDrawer() {
  const open = useStudio((s) => s.settingsOpen);
  const settings = useStudio((s) => s.settings);
  const settingsAvailable = useStudio((s) => s.settingsAvailable);
  const settingsLoaded = useStudio((s) => s.settingsLoaded);
  const busy = useStudio((s) => s.settingsBusy);
  const test = useStudio((s) => s.settingsTest);
  const closeSettings = useStudio((s) => s.closeSettings);
  const saveSettings = useStudio((s) => s.saveSettings);
  const testSettings = useStudio((s) => s.testSettings);
  const refreshSettings = useStudio((s) => s.refreshSettings);

  const [surface, setSurface] = useState<Surface>(settings?.surface ?? 'local');
  const [baseUrl, setBaseUrl] = useState(settings?.base_url ?? DEFAULT_BASE_URL.local);
  const [apiKey, setApiKey] = useState('');
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const dirty = useRef(false);

  // Seed the form from the server whenever the drawer opens (and once the
  // settings arrive). Edits the human has made are never overwritten.
  useEffect(() => {
    if (!open) return;
    dirty.current = false;
    setApiKey('');
    setSaved(null);
    setSaveError(null);
    void refreshSettings().catch(() => undefined);
  }, [open, refreshSettings]);

  useEffect(() => {
    if (!open || dirty.current || !settings) return;
    setSurface(settings.surface);
    setBaseUrl(settings.base_url || DEFAULT_BASE_URL[settings.surface]);
  }, [open, settings]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeSettings();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, closeSettings]);

  if (!open) return null;

  const keyIsSet = !!settings?.key_set;
  const cloudWithoutKey = surface === 'cloud' && !keyIsSet && !apiKey;
  const hint = SURFACES.find((s) => s.id === surface)?.hint ?? '';

  const pickSurface = (next: Surface) => {
    dirty.current = true;
    // Move the base URL with the surface only when it still holds the other
    // surface's default (a custom URL the human typed is theirs to keep).
    const previousDefaults = Object.values(DEFAULT_BASE_URL);
    setSurface(next);
    setBaseUrl((current) => (!current || previousDefaults.includes(current) ? DEFAULT_BASE_URL[next] : current));
  };

  const save = async () => {
    setSaved(null);
    setSaveError(null);
    try {
      await saveSettings({
        surface,
        base_url: baseUrl,
        api_key: apiKey.trim() || undefined,
      });
      setApiKey('');
      dirty.current = false;
      setSaved(`Saved — the studio is on ${surface === 'cloud' ? 'Comfy Cloud' : 'Local ComfyUI'}.`);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  };

  const probe = async () => {
    const raw = await testSettings({
      surface,
      base_url: baseUrl,
      api_key: apiKey.trim() || undefined,
    });
    if (raw.status === 'ok' && apiKey.trim()) setSaved('Connection works — Save to use these settings.');
  };

  const tone = test.status === 'ok' ? 'ok' : test.status === 'error' ? 'error' : test.status;

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && closeSettings()}>
      <div
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <div className="drawer-head">
          <div>
            <h2 id="settings-title">Backend</h2>
            <p className="drawer-sub">where the shots actually render</p>
          </div>
          <button type="button" className="icon-button draw-close" aria-label="close settings" onClick={closeSettings}>
            ×
          </button>
        </div>

        <div className="drawer-body">
          {cloudWithoutKey && (
            <p className="drawer-explain" role="status">
              {FIRST_RUN_EXPLAINER}
            </p>
          )}
          {!settingsAvailable && settingsLoaded && (
            <p className="drawer-explain drawer-explain-warn" role="status">
              This backend has no settings endpoint yet — Save and Test will report what really happened.
            </p>
          )}

          <div className="field">
            <div className="field-label">Surface</div>
            <div className="segmented">
              {SURFACES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className={`segment${surface === s.id ? ' is-active' : ''}`}
                  onClick={() => pickSurface(s.id)}
                  title={s.hint}
                  aria-pressed={surface === s.id}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <span className="drawer-hint">{hint}</span>
          </div>

          <label className="field">
            <span className="field-label">Base URL</span>
            <input
              className="text-input"
              type="url"
              value={baseUrl}
              spellCheck={false}
              autoComplete="off"
              placeholder={DEFAULT_BASE_URL[surface]}
              onChange={(e) => {
                dirty.current = true;
                setBaseUrl(e.target.value);
              }}
            />
          </label>

          <div className="field">
            <div className="field-label">
              API key <span className="field-hint">{keyLabel(settings)}</span>
            </div>
            <input
              className="text-input key-input"
              type="password"
              name="comfy-api-key"
              autoComplete="new-password"
              spellCheck={false}
              value={apiKey}
              placeholder={keyIsSet ? 'paste a new key to replace the stored one' : 'paste your API key'}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <span className="drawer-hint">
              Stored server-side; it is never shown again, never sent to a model, and never logged.
              {keyIsSet && ' Leave this empty to keep the current key.'}
            </span>
          </div>

          <a className="key-link" href={KEY_URL} target="_blank" rel="noreferrer noopener">
            Get a key: platform.comfy.org → API Keys → + New
          </a>

          {test.status !== 'idle' && (
            <div className={`test-result test-${tone}`} role="status" aria-live="polite">
              <span className="test-dot" aria-hidden="true" />
              <span>
                {test.status === 'testing'
                  ? 'testing…'
                  : test.status === 'ok'
                    ? `connected${test.version ? ` · ComfyUI ${test.version}` : ''}`
                    : 'not reachable'}
                {test.message && test.status !== 'testing' ? ` — ${test.message}` : ''}
              </span>
            </div>
          )}
          {saved && <p className="drawer-saved">{saved}</p>}
          {saveError && <p className="drawer-error">{saveError}</p>}
        </div>

        <div className="drawer-foot">
          <button type="button" className="ghost-button" onClick={() => void probe()} disabled={busy}>
            {test.status === 'testing' ? 'Testing…' : 'Test connection'}
          </button>
          <button type="button" className="action-button drawer-save" onClick={() => void save()} disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
