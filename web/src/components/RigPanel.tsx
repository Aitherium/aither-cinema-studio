import { useEffect, useRef, useState, type ReactNode } from 'react';
import { labelFor } from '../cameraVocab';
import { mediaUrl } from '../api';
import { missingInput, useStudio } from '../store';
import { ENGINES, QUALITY_PRESETS, SHOT_MODES, engineShort, type CameraRig } from '../types';
import { MoveGlyph } from './MoveGlyph';

/* --------------------------------------------------------------- dropzone --- */

function Dropzone({
  label,
  mediaId,
  onFile,
  onClear,
}: {
  label: string;
  mediaId: string | null;
  onFile: (file: File) => void;
  onClear: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);

  const take = (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    onFile(file);
    window.setTimeout(() => setBusy(false), 600);
  };

  return (
    <div
      className={`dropzone${over ? ' is-over' : ''}${mediaId ? ' has-media' : ''}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files?.[0]);
      }}
      onClick={() => input.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          input.current?.click();
        }
      }}
    >
      <input
        ref={input}
        type="file"
        accept="image/*,video/*"
        hidden
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.currentTarget.value = '';
        }}
      />
      {mediaId ? (
        <>
          <img className="drop-thumb" src={mediaUrl(mediaId)} alt={`${label} reference`} />
          <div className="drop-clear">
            <span>{label}</span>
            <button
              type="button"
              className="icon-button"
              aria-label={`remove ${label}`}
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
            >
              ×
            </button>
          </div>
        </>
      ) : (
        <div className="drop-empty">
          <span className="drop-plus">{busy ? '…' : '+'}</span>
          <span className="drop-label">{label}</span>
          <span className="drop-hint">drag &amp; drop or click</span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ chip --- */

function Chip({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  title?: string;
}) {
  return (
    <button type="button" className={`chip${active ? ' is-active' : ''}`} onClick={onClick} title={title} aria-pressed={active}>
      {children}
    </button>
  );
}

/* ------------------------------------------------------------- rig panel --- */

export function RigPanel() {
  const vocab = useStudio((s) => s.vocab);
  const rig = useStudio((s) => s.rig);
  const mode = useStudio((s) => s.mode);
  const engine = useStudio((s) => s.engine);
  const prompt = useStudio((s) => s.prompt);
  const quality = useStudio((s) => s.quality);
  const startMediaId = useStudio((s) => s.startMediaId);
  const endMediaId = useStudio((s) => s.endMediaId);
  const sourceShotId = useStudio((s) => s.sourceShotId);
  const shots = useStudio((s) => s.shots);
  const busy = useStudio((s) => s.busy);
  const error = useStudio((s) => s.error);
  const rigFlash = useStudio((s) => s.rigFlash);

  const setRig = useStudio((s) => s.setRig);
  const setMode = useStudio((s) => s.setMode);
  const setEngine = useStudio((s) => s.setEngine);
  const setPrompt = useStudio((s) => s.setPrompt);
  const setQuality = useStudio((s) => s.setQuality);
  const setMedia = useStudio((s) => s.setMedia);
  const renderShot = useStudio((s) => s.renderShot);
  const uploadReference = useStudio((s) => s.uploadReference);
  const setSelected = useStudio((s) => s.setSelected);

  const panel = useRef<HTMLElement>(null);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (!rigFlash) return;
    panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setFlash(true);
    const timer = window.setTimeout(() => setFlash(false), 1400);
    return () => window.clearTimeout(timer);
  }, [rigFlash]);

  const sourceShot = shots.find((s) => s.shot_id === sourceShotId) ?? null;
  const blocked = missingInput(mode, startMediaId, endMediaId, sourceShot?.media_id ?? null);
  const needsStill = mode === 'i2v' || mode === 'flf2v';

  const pick = (key: keyof CameraRig, value: string) => setRig({ [key]: value });

  return (
    <section ref={panel} className={`panel panel-rig${flash ? ' is-flash' : ''}`} aria-label="Camera rig">
      <div className="panel-head">
        <h2>Camera rig</h2>
        <span className="panel-meta">
          {labelFor(rig.move)} · {rig.lens}
        </span>
      </div>

      <div className="rig-scroll">
        <div className="field">
          <div className="field-label">Mode</div>
          <div className="segmented">
            {SHOT_MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                className={`segment${mode === m.id ? ' is-active' : ''}`}
                onClick={() => setMode(m.id)}
                title={m.hint}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <div className="field-label">Engine</div>
          <div className="engine-grid">
            {ENGINES.map((e) => (
              <button
                key={e.id}
                type="button"
                className={`engine${engine === e.id ? ' is-active' : ''}`}
                onClick={() => setEngine(e.id)}
                title={e.hint}
                aria-pressed={engine === e.id}
              >
                <span className="engine-name">{e.label}</span>
                <span className="engine-hint">{e.hint}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <div className="field-label">
            Move <span className="field-hint">{vocab.moves.length}</span>
          </div>
          <div className="move-grid">
            {vocab.moves.map((m) => (
              <button
                key={m.id}
                type="button"
                className={`move${rig.move === m.id ? ' is-active' : ''}`}
                onClick={() => pick('move', m.id)}
                aria-pressed={rig.move === m.id}
                title={`${m.label} (${m.id})`}
              >
                <MoveGlyph move={m.id} />
                <span>{m.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <div className="field-label">Lens</div>
          <div className="chip-row">
            {vocab.lenses.map((l) => (
              <Chip key={l.id} active={rig.lens === l.id} onClick={() => pick('lens', l.id)}>
                {l.label}
              </Chip>
            ))}
          </div>
        </div>

        <div className="field two-up">
          <div>
            <div className="field-label">Speed</div>
            <div className="chip-row">
              {vocab.speeds.map((s) => (
                <Chip key={s.id} active={rig.speed === s.id} onClick={() => pick('speed', s.id)}>
                  {s.label}
                </Chip>
              ))}
            </div>
          </div>
          <div>
            <div className="field-label">Quality</div>
            <div className="chip-row">
              {QUALITY_PRESETS.map((q) => (
                <Chip key={q.id} active={quality === q.id} onClick={() => setQuality(q.id)} title={q.hint}>
                  {q.label}
                </Chip>
              ))}
            </div>
          </div>
        </div>

        <div className="field">
          <div className="field-label">Framing</div>
          <div className="chip-row">
            {vocab.framings.map((f) => (
              <Chip key={f.id} active={rig.framing === f.id} onClick={() => pick('framing', f.id)}>
                {f.label}
              </Chip>
            ))}
          </div>
        </div>

        <div className="field">
          <div className="field-label">
            Action <span className="field-hint">{prompt.length}</span>
          </div>
          <textarea
            className="prompt"
            value={prompt}
            rows={3}
            placeholder="A lone figure crosses the wet platform, neon bleeding into the rain…"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>

        {(needsStill || mode === 'reshoot') && (
          <div className="field">
            <div className="field-label">{mode === 'reshoot' ? 'Re-shoot source' : 'Reference'}</div>
            {mode === 'reshoot' ? (
              <div className="source-shot">
                {sourceShot ? (
                  <>
                    <span className="source-id">{sourceShot.shot_id}</span>
                    <span className="source-cam">
                      {labelFor(sourceShot.camera.move)} · {sourceShot.camera.lens}
                    </span>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={() => setSelected(sourceShot.shot_id)}
                    >
                      show
                    </button>
                  </>
                ) : (
                  <span className="source-empty">Pick a shot on the board, then press Re-shoot.</span>
                )}
              </div>
            ) : (
              <div className={`drop-row${mode === 'flf2v' ? '' : ' single'}`}>
                <Dropzone
                  label={mode === 'flf2v' ? 'First frame' : 'Reference still'}
                  mediaId={startMediaId}
                  onFile={(file) => void uploadReference(file, 'start').catch(() => undefined)}
                  onClear={() => setMedia('start', null)}
                />
                {mode === 'flf2v' && (
                  <Dropzone
                    label="Last frame"
                    mediaId={endMediaId}
                    onFile={(file) => void uploadReference(file, 'end').catch(() => undefined)}
                    onClear={() => setMedia('end', null)}
                  />
                )}
              </div>
            )}
          </div>
        )}

        {error && <div className="rig-error">{error}</div>}
        {!error && blocked && <div className="rig-hint">{blocked}</div>}

        <button
          type="button"
          className="render-button"
          disabled={busy || !!blocked}
          onClick={() => {
            void renderShot().catch(() => undefined);
          }}
        >
          <span>{busy ? 'Sending…' : mode === 'reshoot' ? 'Re-shoot' : 'Render shot'}</span>
          <span className="render-meta">
            {engineShort(engine)} · {SHOT_MODES.find((m) => m.id === mode)?.hint} · {quality}
          </span>
        </button>
      </div>
    </section>
  );
}
