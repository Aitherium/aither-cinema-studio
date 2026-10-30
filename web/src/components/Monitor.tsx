import { useState } from 'react';
import { labelFor } from '../cameraVocab';
import { mediaKind, shotMediaSrc, useStudio } from '../store';
import { GRADE_LOOKS, type GradeLook } from '../types';
import { MoveGlyph } from './MoveGlyph';

/** The monitor: plays the selected shot, takes the grade, opens the re-shoot. */
export function Monitor() {
  const shots = useStudio((s) => s.shots);
  const selectedId = useStudio((s) => s.selectedId);
  const grades = useStudio((s) => s.grades);
  const gradeLook = useStudio((s) => s.gradeLook);
  const gradeStrength = useStudio((s) => s.gradeStrength);
  const setGradeLook = useStudio((s) => s.setGradeLook);
  const setGradeStrength = useStudio((s) => s.setGradeStrength);
  const gradeShot = useStudio((s) => s.gradeShot);
  const reshootFrom = useStudio((s) => s.reshootFrom);

  const [grading, setGrading] = useState<string | null>(null);

  const shot = shots.find((s) => s.shot_id === selectedId) ?? null;
  const gradedMedia = shot ? grades[shot.shot_id] : undefined;
  const src = gradedMedia ? shotMediaSrc({ media_id: gradedMedia }) : shotMediaSrc(shot ?? undefined);
  const kind = mediaKind(
    gradedMedia
      ? { media_id: gradedMedia, engine: shot?.engine, mode: shot?.mode }
      : { media_id: shot?.media_id, media_url: shot?.media_url, engine: shot?.engine, mode: shot?.mode },
  );

  const applyGrade = async (look: GradeLook) => {
    setGradeLook(look);
    if (!shot || look === 'none') return;
    setGrading(look);
    try {
      await gradeShot(shot.shot_id, look, gradeStrength);
    } catch {
      /* the feed carries the error */
    } finally {
      setGrading(null);
    }
  };

  return (
    <section className="panel panel-monitor" aria-label="Monitor">
      <div className="panel-head">
        <h2>Monitor</h2>
        {shot && (
          <span className="panel-meta">
            {shot.shot_id} · {labelFor(shot.camera.move)} · {shot.camera.lens}
          </span>
        )}
      </div>

      <div className="monitor-body">
        <div className="frame">
          {src ? (
            kind === 'video' ? (
              <video key={src} className="frame-media" src={src} controls autoPlay loop muted playsInline />
            ) : (
              <img key={src} className="frame-media" src={src} alt={`shot ${shot?.shot_id ?? ''}`} />
            )
          ) : (
            <div className="frame-empty">
              <MoveGlyph move={shot?.camera.move ?? 'static'} size={92} />
              <span>{shot ? 'rendering — no media yet' : 'select a shot on the board'}</span>
            </div>
          )}

          {shot && (
            <div className="frame-overlay">
              <span className="frame-tag">{shot.status.toUpperCase()}</span>
              {gradedMedia && <span className="frame-tag frame-tag-grade">{gradeLook}</span>}
              {typeof shot.progress === 'number' && shot.status === 'running' && (
                <span className="frame-progress">{Math.round(shot.progress)}%</span>
              )}
            </div>
          )}
        </div>

        <div className="monitor-controls">
          <div className="grade-row">
            <span className="control-label">Grade</span>
            <div className="chip-row">
              {GRADE_LOOKS.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  className={`chip grade-chip${gradeLook === g.id ? ' is-active' : ''}${grading === g.id ? ' is-busy' : ''}`}
                  onClick={() => void applyGrade(g.id)}
                  aria-pressed={gradeLook === g.id}
                >
                  {g.label}
                </button>
              ))}
            </div>
            <label className="strength" title="grade strength">
              <span>str</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={gradeStrength}
                onChange={(e) => setGradeStrength(Number(e.target.value))}
              />
              <span className="strength-value">{gradeStrength.toFixed(2)}</span>
            </label>
          </div>

          <div className="monitor-actions">
            <button
              type="button"
              className="action-button"
              disabled={!shot}
              onClick={() => shot && reshootFrom(shot.shot_id)}
            >
              Re-shoot ↺
            </button>
            {shot?.compiled_prompt && (
              <span className="compiled" title={shot.compiled_prompt}>
                {shot.compiled_prompt}
              </span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
