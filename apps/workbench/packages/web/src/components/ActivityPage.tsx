import { useEffect, useMemo, useState } from 'react';
import type { ActivityDay, ActivityReport, AgentInfo, Api, SystemPrompt } from '../api';
import { Dialog } from './Dialog';
import { AgentMark } from './icons';
import { TracePanel } from './TracePanel';
import { t } from '../i18n';

interface Props {
  api: Api;
  agents: AgentInfo[];
  onOpenThread: (id: string) => void;
  onError: (e: unknown) => void;
}

const DAYS_KEY = 'calli.activity.days';
const RANGES = [7, 14, 30];

function readRange(): number {
  try {
    const value = Number(localStorage.getItem(DAYS_KEY));
    return RANGES.includes(value) ? value : 14;
  } catch {
    return 14;
  }
}

/** The local calendar day of right now, the way the daemon groups its days. */
function localDayKey(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

/** "Today", "Yesterday", then the date and weekday: the sidebar's activity view words days the same way. */
function dayTitle(date: string, now: Date): string {
  const day = new Date(`${date}T00:00:00`);
  if (Number.isNaN(day.getTime())) return date;
  const today = localDayKey(now);
  const yesterday = localDayKey(new Date(now.getTime() - 86_400_000));
  if (date === today) return t('sidebar.group.today');
  if (date === yesterday) return t('sidebar.group.yesterday');
  return t('activity.day', day);
}

const timeOf = (ts: string): string => {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? '' : `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
};

/** A tool's chip label: plain names as they are, MCP tools as `server · tool`. */
function toolLabel(name: string, server?: string): string {
  if (server) return name.startsWith('mcp__') ? `${server} · ${name.split('__').slice(2).join('__')}` : `${server} · ${name}`;
  return name;
}

/** The prompt of one conversation, when its agent persists one; fetched when asked for. */
interface PromptView {
  threadId: string;
  title: string;
  agent: string;
  prompt: Promise<SystemPrompt> | null;
}

/**
 * The daily digest of what the models were asked and what they called. A document like the plan
 * page: a heading, a range, then one section a day with the tools as chips and the asks as rows
 * under the conversation they belong to. The whole system prompt is a conversation's own, so it
 * is read per conversation, in a dialog, not summed into the day.
 */
export function ActivityPage({ api, agents, onOpenThread, onError }: Props) {
  const [range, setRange] = useState(readRange);
  const [report, setReport] = useState<ActivityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<PromptView | null>(null);
  const [trace, setTrace] = useState<{ threadId: string; turnId: string } | null>(null);

  useEffect(() => {
    setLoading(true);
    let stale = false;
    api
      .activity(range)
      .then((value) => {
        if (!stale) setReport(value);
      })
      .catch((e) => !stale && onError(e))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [api, range]);

  const canReadPrompt = useMemo(
    () => new Set(agents.filter((a) => a.capabilities.systemPrompt).map((a) => a.id)),
    [agents],
  );

  const openPrompt = (day: ActivityDay, threadId: string) => {
    const group = day.prompts.find((p) => p.threadId === threadId);
    if (!group) return;
    // Started here rather than kept in state, so reopening the same conversation reads again.
    const promise = api.systemPrompt(threadId);
    promise.catch((e) => onError(e));
    setView({ threadId, title: group.title, agent: group.agent, prompt: promise });
  };

  const days = report?.days ?? [];
  return (
    <div className="activity-page">
      <header className="activity-head">
        <h1 className="activity-title">{t('activity.title')}</h1>
        <div className="activity-range" role="group" aria-label={t('activity.range')}>
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              className={`activity-range-btn${r === range ? ' active' : ''}`}
              aria-pressed={r === range}
              onClick={() => {
                setRange(r);
                try {
                  localStorage.setItem(DAYS_KEY, String(r));
                } catch {
                  // A private window keeps its own range for as long as it is open.
                }
              }}
            >
              {t('activity.days', r)}
            </button>
          ))}
        </div>
        <p className="activity-hint">{t('activity.hint')}</p>
      </header>

      {loading && !report && <div className="activity-empty">{t('activity.reading')}</div>}
      {!loading && days.length === 0 && <div className="activity-empty">{t('activity.empty')}</div>}

      {days.map((day) => {
        // Newest asks first within the day; conversations follow their newest ask.
        const groups = new Map<string, ActivityDay['prompts']>();
        for (const p of day.prompts) {
          groups.set(p.threadId, [...(groups.get(p.threadId) ?? []), p]);
        }
        return (
          <section key={day.date} className="activity-day">
            <header className="activity-day-head">
              <h2>{dayTitle(day.date, new Date())}</h2>
              <span className="activity-day-stats">
                {t('activity.stats', day.turns, day.threads)}
              </span>
            </header>

            {day.tools.length > 0 && (
              <div className="activity-tools">
                {day.tools.map((tool) => (
                  <span key={tool.name} className={`activity-chip${tool.mcp ? ' mcp' : ''}`} title={tool.mcp ? t('activity.mcpTool', tool.name) : tool.name}>
                    {tool.mcp && <span className="activity-chip-tag">mcp</span>}
                    {toolLabel(tool.name, tool.server)}
                    <span className="activity-chip-count">{tool.count}</span>
                  </span>
                ))}
              </div>
            )}

            {[...groups.entries()].map(([threadId, asks]) => {
              const info = agents.find((a) => a.id === asks[0].agent);
              return (
                <div key={threadId} className="activity-thread">
                  <header className="activity-thread-head">
                    {info?.icon ? (
                      <span className="agent-mark" role="img" aria-label={info.label}>
                        <AgentMark path={info.icon} size={13} />
                      </span>
                    ) : null}
                    <button type="button" className="activity-thread-title" onClick={() => onOpenThread(threadId)} title={asks[0].cwd}>
                      {asks[0].title}
                    </button>
                    <span className="activity-thread-agent">
                      {info?.label ?? asks[0].agent}
                      {asks[0].archived ? ` · ${t('activity.archived')}` : ''}
                    </span>
                    {canReadPrompt.has(asks[0].agent) && (
                      <button type="button" className="activity-sys-btn" onClick={() => openPrompt(day, threadId)}>
                        {t('activity.systemPrompt')}
                      </button>
                    )}
                  </header>
                  <ul className="activity-asks">
                    {asks.map((p) => (
                      <li key={p.turnId}>
                        <button type="button" className="activity-ask" onClick={() => setTrace({ threadId, turnId: p.turnId })} title={p.text}>
                          <span className="activity-ask-time">{timeOf(p.ts)}</span>
                          <span className="activity-ask-text">{p.text}</span>
                          {p.tools ? <span className="activity-ask-tools">{t('activity.tools', p.tools)}</span> : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}

            {day.morePrompts > 0 && (
              <p className="activity-more">
                {t('activity.more', day.morePrompts)}
              </p>
            )}
          </section>
        );
      })}

      {view && <SystemPromptDialog view={view} onClose={() => setView(null)} />}
      {trace && (
        <TracePanel
          api={api}
          agents={agents}
          threadId={trace.threadId}
          turnId={trace.turnId}
          onOpenThread={(id) => {
            setTrace(null);
            onOpenThread(id);
          }}
          onClose={() => setTrace(null)}
        />
      )}
    </div>
  );
}

/** The whole prompt of one conversation, in a dialog: source, the text, a copy button. */
function SystemPromptDialog({ view, onClose }: { view: PromptView; onClose: () => void }) {
  const [prompt, setPrompt] = useState<SystemPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let stale = false;
    view.prompt?.then(
      (value) => !stale && setPrompt(value),
      (e) => !stale && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      stale = true;
    };
  }, [view.prompt]);

  const copy = async () => {
    if (!prompt) return;
    try {
      await navigator.clipboard.writeText(prompt.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Not a secure context: the text stays selectable in the dialog.
    }
  };

  return (
    <Dialog
      title={t('activity.systemPrompt')}
      message={prompt ? t('activity.from', view.title, prompt.source) : view.title}
      confirmLabel={t('common.close')}
      onConfirm={onClose}
      onCancel={onClose}
      className="activity-sys-dialog"
    >
      {error ? (
        <p className="activity-sys-error">{error}</p>
      ) : !prompt ? (
        <p className="activity-sys-loading">{t('activity.reading')}</p>
      ) : (
        <div className="activity-sys-body">
          <pre>{prompt.text}</pre>
        </div>
      )}
      {prompt && (
        <button type="button" className="activity-sys-copy" onClick={copy}>
          {copied ? t('common.copied') : t('activity.copyAll')}
        </button>
      )}
    </Dialog>
  );
}
