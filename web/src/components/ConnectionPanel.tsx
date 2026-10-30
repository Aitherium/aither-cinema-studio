import { useState } from 'react';
import {
  CONNECTION_PRESETS,
  LOCAL_BASE,
  SETUP_NOTE,
  SETUP_STEPS,
  closeConnectGate,
  connectionLabel,
  normalizeBaseUrl,
  probeConnection,
  setConnection,
  useConnection,
  type Connection,
  type ProbeResult,
} from '../connection';

type PresetId = (typeof CONNECTION_PRESETS)[number]['id'];

function presetFor(connection: Connection): PresetId {
  if (connection.mode === 'demo') return 'demo';
  if (connection.mode === 'remote') return 'remote';
  return 'local';
}

export interface ConnectionPanelProps {
  /** 'gate' closes the first-visit overlay; 'settings' shows an inline confirmation. */
  variant?: 'gate' | 'settings';
  /** Called after the choice is stored, so the caller can re-bootstrap the store. */
  onApplied?: (connection: Connection) => void;
}

/**
 * Connect the studio to a backend — or to the in-page demo.
 *
 * Local and remote choices are *probed* before they are adopted: the visitor
 * sees whether `GET {base}/api/health` answered and, when it did not, the exact
 * copy-paste setup for a backend of their own.
 */
export function ConnectionPanel({ variant = 'settings', onApplied }: ConnectionPanelProps) {
  const connection = useConnection();
  const [preset, setPreset] = useState<PresetId>(presetFor(connection));
  const [remoteUrl, setRemoteUrl] = useState(
    connection.mode === 'remote' ? connection.baseUrl : '',
  );
  const [probe, setProbe] = useState<ProbeResult>({ status: 'idle' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const build = (): Connection => {
    if (preset === 'demo') return { mode: 'demo', baseUrl: '' };
    if (preset === 'local') return { mode: 'local', baseUrl: LOCAL_BASE };
    return { mode: 'remote', baseUrl: normalizeBaseUrl(remoteUrl) };
  };

  const test = async () => {
    setError(null);
    setSaved(null);
    let candidate: Connection;
    try {
      candidate = build();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setBusy(true);
    setProbe({ status: 'probing', message: 'asking the backend for /api/health…' });
    try {
      const result = await probeConnection(candidate);
      setProbe(result);
    } finally {
      setBusy(false);
    }
  };

  const apply = () => {
    setError(null);
    setSaved(null);
    let candidate: Connection;
    try {
      candidate = build();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    const stored = setConnection(candidate);
    setProbe({
      status: 'ok',
      message:
        stored.mode === 'demo'
          ? 'Demo mode — the studio is simulated in this page.'
          : `using ${connectionLabel(stored)}`,
    });
    setSaved(
      stored.mode === 'demo'
        ? 'Demo mode is on.'
        : `Using ${connectionLabel(stored)}.`,
    );
    if (variant === 'gate') closeConnectGate();
    onApplied?.(stored);
  };

  const tone = probe.status === 'ok' ? 'ok' : probe.status === 'error' ? 'error' : probe.status;

  return (
    <div className={`conn-panel conn-${variant}`}>
      <div className="conn-current">
        <span className="conn-label">Now</span>
        <span className="conn-current-value">{connectionLabel(connection)}</span>
      </div>

      <div className="conn-preset-grid" role="radiogroup" aria-label="Where the studio renders">
        {CONNECTION_PRESETS.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={preset === option.id}
            className={`conn-preset${preset === option.id ? ' is-active' : ''}`}
            onClick={() => {
              setPreset(option.id);
              setProbe({ status: 'idle' });
              setError(null);
              setSaved(null);
            }}
          >
            <span className="conn-preset-name">{option.label}</span>
            <span className="conn-preset-hint">{option.hint}</span>
          </button>
        ))}
      </div>

      {preset === 'remote' && (
        <label className="conn-field">
          <span className="conn-label">Remote base URL</span>
          <input
            className="conn-input"
            type="url"
            inputMode="url"
            spellCheck={false}
            autoComplete="off"
            placeholder="https://cine.example.com"
            value={remoteUrl}
            onChange={(e) => {
              setRemoteUrl(e.target.value);
              setProbe({ status: 'idle' });
              setError(null);
            }}
          />
          <span className="conn-hint">
            The studio server's origin. It must answer CORS for this page's origin; see the setup
            steps below.
          </span>
        </label>
      )}

      {preset === 'local' && (
        <p className="conn-hint">
          Starts the studio backend on your own machine at <code>{LOCAL_BASE}</code>. This page is
          https, so the browser will send a Private Network Access preflight first — the studio
          server answers it.
        </p>
      )}

      {preset === 'demo' && (
        <p className="conn-hint">
          Demo mode runs entirely in this page: the camera vocabulary, a simulated render job with
          progress, and pre-rendered proof clips. Every WebMCP tool still works, so an agent can
          direct a shot with no backend at all.
        </p>
      )}

      {probe.status !== 'idle' && (
        <div className={`conn-result conn-${tone}`} role="status" aria-live="polite">
          <span className="test-dot" aria-hidden="true" />
          <span>
            {probe.status === 'probing' ? 'testing…' : probe.status === 'ok' ? 'reachable' : 'not reachable'}
            {probe.message ? ` — ${probe.message}` : ''}
          </span>
        </div>
      )}

      {error && <p className="conn-error">{error}</p>}
      {saved && <p className="conn-saved">{saved}</p>}

      <details className="conn-steps" open={probe.status === 'error' || preset === 'local'}>
        <summary>Copy-paste setup for your own backend</summary>
        <pre>{SETUP_STEPS.join('\n')}</pre>
        <p className="conn-note">{SETUP_NOTE}</p>
      </details>

      <div className="conn-actions">
        <button type="button" className="ghost-button" onClick={() => void test()} disabled={busy}>
          {busy ? 'Testing…' : 'Test studio connection'}
        </button>
        <button type="button" className="action-button conn-apply" onClick={apply}>
          {variant === 'gate' ? 'Open the studio' : 'Use this connection'}
        </button>
      </div>
    </div>
  );
}
