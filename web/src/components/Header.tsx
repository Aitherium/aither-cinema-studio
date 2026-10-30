import { useStudio } from '../store';

function Dot({ tone }: { tone: 'ok' | 'warn' | 'err' }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

/** Brand bar + the honest status strip: backend, Comfy, WebMCP surface, tool count. */
export function Header() {
  const health = useStudio((s) => s.health);
  const healthAt = useStudio((s) => s.healthAt);
  const webmcp = useStudio((s) => s.webmcp);
  const vocabSource = useStudio((s) => s.vocabSource);
  const settings = useStudio((s) => s.settings);
  const openSettings = useStudio((s) => s.openSettings);
  const refreshHealth = useStudio((s) => s.refreshHealth);
  const refreshShots = useStudio((s) => s.refreshShots);

  const backendUp = !!health && health.ok !== false;
  const comfyUp = !!health?.comfy?.reachable;
  const gradeUp = !!health?.grade?.available;
  const keyMissing = settings?.surface === 'cloud' && !settings.key_set;
  const surfaceName = settings?.surface === 'cloud' ? 'Comfy Cloud' : 'Local ComfyUI';

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          ◉
        </span>
        <div className="brand-text">
          <h1>
            Aither <em>Cinema</em> Studio
          </h1>
          <p>open weights · every control is a WebMCP tool</p>
        </div>
      </div>

      <div className="statusbar" aria-live="polite">
        <button
          type="button"
          className="status-item status-button"
          onClick={() => {
            void refreshHealth().catch(() => undefined);
            void refreshShots().catch(() => undefined);
          }}
          title={healthAt ? `checked ${new Date(healthAt).toLocaleTimeString()}` : 'check now'}
        >
          <Dot tone={backendUp ? 'ok' : 'err'} />
          <span className="k">backend</span>
          <span className="v">{backendUp ? 'up' : 'down'}</span>
        </button>

        <div className="status-item" title={health?.comfy?.base ?? 'COMFY_BASE_URL'}>
          <Dot tone={comfyUp ? 'ok' : health ? 'err' : 'warn'} />
          <span className="k">comfy</span>
          <span className="v">
            {comfyUp ? (health?.comfy?.version ? `ok ${health.comfy.version}` : 'reachable') : health ? 'unreachable' : 'unknown'}
          </span>
        </div>

        <div className="status-item">
          <Dot tone={gradeUp ? 'ok' : 'warn'} />
          <span className="k">grade</span>
          <span className="v">{gradeUp ? 'ready' : 'off'}</span>
        </div>

        <div className="status-item" title="where the tools are registered">
          <Dot tone="ok" />
          <span className="k">webmcp</span>
          <span className="v">{webmcp.surface}</span>
        </div>

        <div className="status-item">
          <span className="k">tools</span>
          <span className="v v-count">{webmcp.tools}</span>
        </div>

        <div className="status-item status-vocab" title="camera vocabulary source">
          <span className="k">vocab</span>
          <span className="v">{vocabSource === 'remote' ? 'live' : 'bundled'}</span>
        </div>

        <button
          type="button"
          className={`status-item status-button gear-button${keyMissing ? ' gear-warn' : ''}`}
          onClick={openSettings}
          title={`Backend settings — ${surfaceName}${keyMissing ? ' · no API key' : ''}`}
          aria-label="Backend settings"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3.1" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6h.09A1.7 1.7 0 0 0 10 3.05V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9v.09A1.7 1.7 0 0 0 20.95 10H21a2 2 0 1 1 0 4h-.09A1.7 1.7 0 0 0 19.4 15z" />
          </svg>
          <span className="v">{keyMissing ? 'key?' : surfaceName === 'Comfy Cloud' ? 'cloud' : 'local'}</span>
        </button>
      </div>
    </header>
  );
}
