import { labelFor } from '../cameraVocab';
import { mediaKind, shotMediaSrc, useStudio } from '../store';
import { SHOT_MODES, engineShort, type Shot } from '../types';
import { MoveGlyph } from './MoveGlyph';

const modeLabel = (id: string) => SHOT_MODES.find((m) => m.id === id)?.label ?? id;

function Thumb({ shot }: { shot: Shot }) {
  const src = shotMediaSrc(shot);
  if (src) {
    return mediaKind(shot) === 'video' ? (
      <video className="thumb-media" src={src} muted loop playsInline preload="metadata" />
    ) : (
      <img className="thumb-media" src={src} alt={`shot ${shot.shot_id}`} loading="lazy" />
    );
  }
  return (
    <div className="thumb-empty">
      <MoveGlyph move={shot.camera.move} size={46} />
      {shot.status === 'running' && <span className="thumb-scan" />}
    </div>
  );
}

/** The shot board: every shot as a slate card. */
export function ShotBoard() {
  const shots = useStudio((s) => s.shots);
  const selectedId = useStudio((s) => s.selectedId);
  const setSelected = useStudio((s) => s.setSelected);
  const openRig = useStudio((s) => s.openRig);

  return (
    <section className="panel panel-board" aria-label="Shot board">
      <div className="panel-head">
        <h2>Shot board</h2>
        <span className="panel-meta">{shots.length} shot{shots.length === 1 ? '' : 's'}</span>
      </div>

      <div className="board-scroll">
        {shots.length === 0 && (
          <div className="empty-board">
            <p className="empty-title">No shots yet.</p>
            <p className="empty-body">Set the rig, write the action, hit render. An agent can do the same through WebMCP.</p>
            <button type="button" className="ghost-button" onClick={openRig}>
              Open the rig
            </button>
          </div>
        )}

        {shots.map((shot, index) => {
          const selected = shot.shot_id === selectedId;
          return (
            <article
              key={shot.shot_id}
              className={`slate${selected ? ' is-selected' : ''} slate-${shot.status}`}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              onClick={() => setSelected(shot.shot_id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setSelected(shot.shot_id);
                }
              }}
            >
              <div className="slate-top">
                <span className="slate-no">SH{String(shots.length - index).padStart(2, '0')}</span>
                {shot.engine && <span className="slate-engine">{engineShort(shot.engine)}</span>}
                <span className="slate-mode">{modeLabel(shot.mode)}</span>
                <span className={`status-word status-${shot.status}`}>{shot.status}</span>
              </div>

              <div className="slate-thumb">
                <Thumb shot={shot} />
                {typeof shot.progress === 'number' && shot.status === 'running' && (
                  <div className="progress">
                    <span style={{ width: `${Math.max(2, Math.min(100, shot.progress))}%` }} />
                  </div>
                )}
              </div>

              <div className="slate-foot">
                <span className="camline">
                  {labelFor(shot.camera.move)} · {shot.camera.lens} · {labelFor(shot.camera.framing)}
                </span>
                <span className="slate-prompt">{shot.prompt || <em>no prompt</em>}</span>
                {shot.error && <span className="slate-error">{shot.error}</span>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
