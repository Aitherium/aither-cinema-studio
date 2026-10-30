import { useStudio } from '../store';
import type { FeedEntry } from '../types';

function preview(value: unknown, max = 150): string {
  if (value === null || value === undefined) return '—';
  let text: string;
  if (typeof value === 'string') text = value;
  else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  text = text.replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max)}…` : text || '{}';
}

const clock = (t: number) => new Date(t).toLocaleTimeString([], { hour12: false });

function Row({ entry }: { entry: FeedEntry }) {
  return (
    <li className={`feed-row feed-${entry.status}`}>
      <span className="feed-time">{clock(entry.time)}</span>
      <span className={`actor actor-${entry.actor}`}>{entry.actor}</span>
      <span className="feed-tool">{entry.tool}</span>
      <span className="feed-args" title={preview(entry.args, 600)}>
        {preview(entry.args)}
      </span>
      <span className="feed-result" title={preview(entry.result, 600)}>
        {preview(entry.result)}
      </span>
    </li>
  );
}

/** Every WebMCP tool call, agent or human, in one strip. */
export function ActivityFeed() {
  const feed = useStudio((s) => s.feed);
  const agentCount = feed.filter((e) => e.actor === 'agent').length;

  return (
    <section className="panel panel-feed" aria-label="Agent activity">
      <div className="panel-head feed-head">
        <h2>Agent activity</h2>
        <span className="panel-meta">
          {feed.length} call{feed.length === 1 ? '' : 's'} · {agentCount} by agent · {feed.length - agentCount} by you
        </span>
      </div>

      <div className="feed-scroll">
        {feed.length === 0 ? (
          <p className="feed-empty">
            No calls yet. Every WebMCP tool — from this desk or an agent in the tab — lands here with its arguments and result.
          </p>
        ) : (
          <ol className="feed-list">
            {feed.map((entry) => (
              <Row key={entry.id} entry={entry} />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
