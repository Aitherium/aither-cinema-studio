import { useEffect, useRef, useState } from 'react';
import { analyseAudio, beatTimes, clock, isDownbeat, totalBeats } from '../audio';
import { labelFor } from '../cameraVocab';
import { mediaUrl } from '../api';
import { DEFAULT_BEATS, mediaKind, shotMediaSrc, useStudio } from '../store';
import { MoveGlyph } from './MoveGlyph';

/** Paint the waveform and its beat grid on a canvas that fills its box. */
function paintWaveform(
  canvas: HTMLCanvasElement,
  peaks: number[],
  bpm: number,
  duration: number,
): void {
  const width = canvas.clientWidth || 640;
  const height = canvas.clientHeight || 120;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext?.('2d');
  if (!ctx || !width || !height) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const styles = getComputedStyle(canvas);
  const amber = styles.getPropertyValue('--amber').trim() || '#f0a63c';
  const amberHot = styles.getPropertyValue('--amber-hot').trim() || '#ffc36b';
  const line = styles.getPropertyValue('--line').trim() || '#2a2621';

  // beat grid first, under the peaks
  if (bpm > 0 && duration > 0) {
    const beats = beatTimes(bpm, duration);
    ctx.lineWidth = 1;
    beats.forEach((t, i) => {
      if (i % 4 !== 0 && beats.length <= 220) return;
      const x = Math.round((t / duration) * width) + 0.5;
      ctx.strokeStyle = isDownbeat(i) && beats.length <= 900 ? 'rgba(240,166,60,0.34)' : 'rgba(42,38,33,0.9)';
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    });
  }

  const mid = height / 2;
  if (!peaks.length) {
    ctx.strokeStyle = line;
    ctx.beginPath();
    ctx.moveTo(0, mid + 0.5);
    ctx.lineTo(width, mid + 0.5);
    ctx.stroke();
    return;
  }

  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, amberHot);
  gradient.addColorStop(0.5, amber);
  gradient.addColorStop(1, amberHot);
  ctx.fillStyle = gradient;
  const step = width / peaks.length;
  const bar = Math.max(1, step * 0.72);
  peaks.forEach((p, i) => {
    const h = Math.max(1, p * (mid - 3));
    const x = i * step;
    ctx.fillRect(x, mid - h, bar, h * 2);
  });
}

/** The timeline: order the cut, then cut it — or cut it to the beat. */
export function Timeline() {
  const timeline = useStudio((s) => s.timeline);
  const shots = useStudio((s) => s.shots);
  const sequence = useStudio((s) => s.sequence);
  const music = useStudio((s) => s.music);
  const audio = useStudio((s) => s.audio);
  const bpm = useStudio((s) => s.bpm);
  const beats = useStudio((s) => s.beats);
  const setSelected = useStudio((s) => s.setSelected);
  const selectedId = useStudio((s) => s.selectedId);
  const reorderTimeline = useStudio((s) => s.reorderTimeline);
  const cutSequence = useStudio((s) => s.cutSequence);
  const uploadAudio = useStudio((s) => s.uploadAudio);
  const setAudioAnalysis = useStudio((s) => s.setAudioAnalysis);
  const clearAudio = useStudio((s) => s.clearAudio);
  const setBpm = useStudio((s) => s.setBpm);
  const setShotBeats = useStudio((s) => s.setShotBeats);
  const cutMusicVideo = useStudio((s) => s.cutMusicVideo);

  const [dragging, setDragging] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [cutting, setCutting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [musicMode, setMusicMode] = useState(false);
  const [analysing, setAnalysing] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  const ordered = timeline
    .map((id) => shots.find((s) => s.shot_id === id))
    .filter((s): s is NonNullable<typeof s> => !!s);

  const beatsFor = (id: string) => beats[id] ?? DEFAULT_BEATS;
  const beatSum = ordered.reduce((sum, s) => sum + beatsFor(s.shot_id), 0);
  const trackBeats = totalBeats(bpm, audio?.duration ?? 0);
  const canCutMusic = !!audio?.mediaId && ordered.length > 0;

  // Repaint the waveform whenever the peaks, the tempo or the width change.
  useEffect(() => {
    if (!musicMode) return;
    const el = canvas.current;
    if (!el) return;
    paintWaveform(el, audio?.peaks ?? [], bpm, audio?.duration ?? 0);
    const onResize = () => paintWaveform(el, audio?.peaks ?? [], bpm, audio?.duration ?? 0);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [musicMode, audio?.peaks, audio?.duration, bpm]);

  const move = (index: number, delta: number) => {
    const to = index + delta;
    if (to < 0 || to >= ordered.length) return;
    reorderTimeline(index, to);
  };

  const cut = async () => {
    setCutting(true);
    setError(null);
    try {
      await cutSequence(ordered.map((s) => s.shot_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCutting(false);
    }
  };

  const takeAudio = async (file: File | undefined) => {
    if (!file) return;
    setAnalysing(true);
    setAudioError(null);
    try {
      const track = await uploadAudio(file);
      const src = track.url ?? mediaUrl(track.mediaId);
      try {
        const analysis = await analyseAudio(src);
        setAudioAnalysis(analysis);
      } catch (e) {
        // the upload stands; only the picture of it failed
        const message = e instanceof Error ? e.message : String(e);
        setAudioError(message);
        setAudioAnalysis({ error: message });
      }
    } catch (e) {
      setAudioError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalysing(false);
    }
  };

  const cutToBeat = async () => {
    setError(null);
    try {
      await cutMusicVideo();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const musicTone =
    music.musicApplied === true ? 'ok' : music.musicApplied === false ? 'warn' : 'neutral';

  return (
    <section className={`panel panel-timeline${musicMode ? ' is-music' : ''}`} aria-label="Timeline">
      <div className="panel-head">
        <h2>Timeline</h2>
        <div className="timeline-head-right">
          <button
            type="button"
            className={`music-toggle${musicMode ? ' is-active' : ''}`}
            onClick={() => setMusicMode((v) => !v)}
            aria-pressed={musicMode}
            title="cut the sequence to an uploaded track"
          >
            <span className="music-toggle-dot" aria-hidden="true" />
            Music video
          </button>
          <span className="panel-meta">{ordered.length} in the cut</span>
          <button
            type="button"
            className="action-button cut-button"
            disabled={ordered.length < 2 || cutting || sequence.status === 'running'}
            onClick={() => void cut()}
          >
            {cutting || sequence.status === 'running' ? 'Cutting…' : 'Cut sequence'}
          </button>
        </div>
      </div>

      {(sequence.status !== 'idle' || error) && !musicMode && (
        <div className={`sequence-line seq-${sequence.status}`}>
          {error ?? sequence.error ?? (
            <>
              sequence {sequence.status}
              {sequence.jobId ? ` · job ${sequence.jobId}` : ''}
              {sequence.mediaId ? ' · media ready' : ''}
            </>
          )}
        </div>
      )}

      {musicMode && (
        <div className="music-bed">
          <div className="music-track-row">
            <input
              ref={fileInput}
              type="file"
              accept="audio/*,.wav,.mp3,.ogg,.m4a,.flac"
              hidden
              onChange={(e) => {
                void takeAudio(e.target.files?.[0]);
                e.currentTarget.value = '';
              }}
            />
            {audio ? (
              <>
                <button type="button" className="ghost-button music-pick" onClick={() => fileInput.current?.click()}>
                  {analysing ? 'Reading…' : 'Replace track'}
                </button>
                <span className="music-name" title={audio.name}>
                  {audio.name}
                </span>
                <span className="music-dur">
                  {audio.duration ? clock(audio.duration) : audioError ? 'no picture' : 'analysing…'}
                </span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="remove the audio track"
                  onClick={clearAudio}
                >
                  ×
                </button>
              </>
            ) : (
              <button
                type="button"
                className="ghost-button music-pick"
                onClick={() => fileInput.current?.click()}
                disabled={analysing}
              >
                {analysing ? 'Uploading…' : '+ Upload audio track'}
              </button>
            )}

            <label className="bpm-field" title="beats per minute">
              <span>BPM</span>
              <input
                type="number"
                min={20}
                max={400}
                step={1}
                value={bpm}
                onChange={(e) => setBpm(Number(e.target.value))}
              />
            </label>
          </div>

          <canvas
            ref={canvas}
            className="waveform"
            aria-label="audio waveform with beat markers"
            role="img"
          />

          <div className="music-meta">
            <span>{audio?.duration ? `${trackBeats} beats in the track` : audio ? 'reading the track…' : 'no track yet'}</span>
            <span className="music-beat-sum">
              {beatSum} beats across {ordered.length} shot{ordered.length === 1 ? '' : 's'}
            </span>
            {(audioError || audio?.error) && <span className="music-warn">{audioError ?? audio?.error}</span>}
          </div>

          <div className="music-cut-row">
            <button
              type="button"
              className="action-button music-cut-button"
              disabled={!canCutMusic || music.status === 'running'}
              onClick={() => void cutToBeat()}
              title={
                canCutMusic
                  ? 'cut the shots on the beat grid'
                  : 'upload an audio track and put at least one shot on the timeline'
              }
            >
              {music.status === 'running' || music.status === 'submitted' ? 'Cutting…' : 'Cut music video'}
            </button>
            <span className="music-cut-hint">{bpm} BPM · {beatSum} beats</span>
          </div>

          {music.status !== 'idle' && (
            <div className={`music-result music-${music.status} tone-${musicTone}`} role="status" aria-live="polite">
              <span className="music-result-text">
                {music.error ??
                  music.note ??
                  (music.status === 'done' ? 'cut ready' : `sequence ${music.status}`)}
              </span>
              {music.mediaId && (
                <a
                  className="ghost-button music-open"
                  href={mediaUrl(music.mediaId)}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  open cut
                </a>
              )}
            </div>
          )}
          {error && <div className="music-error">{error}</div>}
        </div>
      )}

      <div
        className="timeline-track"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const from = Number(e.dataTransfer.getData('text/plain') || dragging);
          if (Number.isFinite(from) && overIndex !== null) reorderTimeline(from, overIndex);
          setDragging(null);
          setOverIndex(null);
        }}
      >
        {ordered.length === 0 && <span className="timeline-empty">Rendered shots land here in cut order.</span>}

        {ordered.map((shot, index) => {
          const src = shotMediaSrc(shot);
          const isVideo = mediaKind(shot) === 'video';
          const shotBeats = beatsFor(shot.shot_id);
          return (
            <div
              key={shot.shot_id}
              className={`cut${shot.shot_id === selectedId ? ' is-selected' : ''}${overIndex === index ? ' is-over' : ''}${
                dragging === index ? ' is-dragging' : ''
              }${musicMode ? ' is-beat' : ''}`}
              style={musicMode ? { flexGrow: shotBeats, flexBasis: 0 } : undefined}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', String(index));
                e.dataTransfer.effectAllowed = 'move';
                setDragging(index);
              }}
              onDragEnd={() => {
                setDragging(null);
                setOverIndex(null);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setOverIndex(index);
              }}
              onClick={() => setSelected(shot.shot_id)}
            >
              <span className="cut-index">{String(index + 1).padStart(2, '0')}</span>
              <div className="cut-thumb">
                {src ? (
                  isVideo ? (
                    <video src={src} muted playsInline preload="metadata" />
                  ) : (
                    <img src={src} alt="" loading="lazy" />
                  )
                ) : (
                  <MoveGlyph move={shot.camera.move} size={26} />
                )}
              </div>
              <div className="cut-meta">
                <span className="cut-cam">{labelFor(shot.camera.move)}</span>
                <span className={`cut-status status-${shot.status}`}>{shot.status}</span>
              </div>
              {musicMode && (
                <label
                  className="cut-beats"
                  title={`${shotBeats} beat${shotBeats === 1 ? '' : 's'} on the timeline`}
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    type="number"
                    min={1}
                    max={64}
                    step={1}
                    value={shotBeats}
                    aria-label={`beats for shot ${index + 1}`}
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      if (Number.isFinite(n) && n > 0) setShotBeats(shot.shot_id, n);
                    }}
                  />
                  <span>b</span>
                </label>
              )}
              <div className="cut-move">
                <button type="button" aria-label="move earlier" onClick={(e) => { e.stopPropagation(); move(index, -1); }}>
                  ‹
                </button>
                <button type="button" aria-label="move later" onClick={(e) => { e.stopPropagation(); move(index, 1); }}>
                  ›
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
