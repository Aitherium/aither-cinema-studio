import { useConnectGate } from '../connection';
import { useStudio } from '../store';
import { ConnectionPanel } from './ConnectionPanel';

/**
 * The first-visit screen: where should the studio render?
 *
 * It is an overlay on top of the studio rather than a replacement for it, so the
 * page, its WebMCP tools and its state are already live behind the card and the
 * visitor enters a working studio the moment they choose.
 */
export function ConnectGate() {
  const open = useConnectGate();
  const reconnect = useStudio((s) => s.reconnect);

  if (!open) return null;

  return (
    <div className="connect-gate" role="dialog" aria-modal="true" aria-labelledby="connect-title">
      <div className="connect-card">
        <div className="connect-brand">
          <span className="brand-mark" aria-hidden="true">
            ◉
          </span>
          <span>
            Aither <em>Cinema</em> Studio
          </span>
        </div>
        <h2 id="connect-title">Where should the studio render?</h2>
        <p className="connect-lede">
          Connect your own backend — on this computer or one you host — or explore demo mode.
          Nothing leaves this browser until you choose a backend.
        </p>
        <ConnectionPanel
          variant="gate"
          onApplied={() => {
            void reconnect().catch(() => undefined);
          }}
        />
      </div>
    </div>
  );
}
