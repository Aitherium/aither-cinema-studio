import { useState } from 'react';
import { openConnectGate, useConnection } from '../connection';

interface CinemaStudioHandle {
  call?: (name: string, args?: Record<string, unknown>, actor?: 'agent' | 'you') => Promise<unknown>;
}

const AGENT_PROMPT = 'A lone cyclist crossing a rain-slick bridge at dusk';

/**
 * The honest strip a demo visitor cannot miss, plus the one-click agent run that
 * proves the tools are real: it calls the same `window.cinemaStudio.call` an
 * outside agent would, in sequence, and the activity feed records every row.
 */
export function DemoBanner() {
  const connection = useConnection();
  const [line, setLine] = useState('Press “Try an agent” to plan and render a shot.');
  const [busy, setBusy] = useState(false);

  if (connection.mode !== 'demo') return null;

  const runAgent = async () => {
    const host =
      typeof window !== 'undefined'
        ? (window as unknown as { cinemaStudio?: CinemaStudioHandle })
        : undefined;
    const studio = host?.cinemaStudio;
    if (!studio?.call) {
      setLine('The tool surface is still registering — try again in a second.');
      return;
    }
    setBusy(true);
    try {
      await studio.call('list-camera-moves', {});
      setLine('agent · list-camera-moves — the vocabulary is live');
      await studio.call('plan-shot', {
        prompt: AGENT_PROMPT,
        mode: 't2v',
        move: 'dolly_in',
        lens: '35mm',
        speed: 'slow',
        framing: 'medium',
      });
      setLine('agent · plan-shot — dolly in, 35mm, medium');
      const shot = (await studio.call('render-shot', {})) as { shot_id?: string } | null;
      setLine(
        `agent · render-shot — ${shot?.shot_id ?? 'the shot'} is rendering in the demo backend; watch the monitor`,
      );
    } catch (e) {
      setLine(`agent · ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="demo-banner" role="status" aria-live="polite">
      <span className="demo-tag">DEMO MODE</span>
      <span className="demo-text">
        Demo mode — connect a backend to render for real.
        <span className="demo-line">{line}</span>
      </span>
      <div className="demo-actions">
        <button type="button" className="ghost-button" onClick={() => openConnectGate()}>
          Connect a backend
        </button>
        <button
          type="button"
          className="action-button demo-agent"
          onClick={() => void runAgent()}
          disabled={busy}
        >
          {busy ? 'Agent working…' : 'Try an agent'}
        </button>
      </div>
    </div>
  );
}
