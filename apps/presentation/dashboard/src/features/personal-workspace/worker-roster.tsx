import {
  WORKER_ROSTER_STATES,
  workerRosterStateLabel,
  type CollaborationLocale,
  type GoalWorkerRoster,
  type ProgressCounter,
  type WorkerRosterState,
} from "./collaboration-status";
import "./worker-roster.css";

export function WorkerStateGlyph({ state }: { state: WorkerRosterState }) {
  return <span className={`pw-roster-ring is-${state}`} aria-hidden="true" />;
}

export function WorkerStateBadge({
  state,
  locale,
  count,
}: {
  state: WorkerRosterState;
  locale: CollaborationLocale;
  count?: number;
}) {
  const label = workerRosterStateLabel(state, locale);
  return (
    <span className={`pw-roster-state is-${state}`} data-roster-state={state}>
      <WorkerStateGlyph state={state} />
      <span>{count !== undefined ? `${count} ${label}` : label}</span>
    </span>
  );
}

export function WorkerScopeTag({ scope }: { scope?: string | null }) {
  if (!scope) return null;
  return (
    <span className="pw-roster-scope" title={scope}>
      {scope}
    </span>
  );
}

export function WorkerProgressBadge({ progress }: { progress: Pick<ProgressCounter, "label"> }) {
  if (!progress.label) return null;
  return <span className="pw-roster-progress">{progress.label}</span>;
}

export function WorkerNowLine({ now }: { now?: string | null }) {
  if (!now) return null;
  return (
    <p className="pw-roster-now" title={now}>
      <span className="pw-roster-now-key">Now</span>
      <span className="pw-roster-now-text">{now}</span>
    </p>
  );
}

export function GoalWorkerRosterStrip({
  roster,
  locale,
  onOpenAgentConversation,
  readOnly = false,
  compact = false,
  summaryOnly = false,
}: {
  roster: GoalWorkerRoster;
  locale: CollaborationLocale;
  onOpenAgentConversation?: (agentId: string) => void;
  readOnly?: boolean;
  compact?: boolean;
  summaryOnly?: boolean;
}) {
  if (!roster.workers.length) return null;
  const label = locale === "zh-CN" ? "Worker 协作队列" : "Worker roster";
  return (
    <section
      className={`pw-roster-panel${compact ? " is-compact" : ""}${summaryOnly ? " is-summary-only" : ""}`}
      aria-label={label}
      data-worker-count={roster.workers.length}
    >
      <header className="pw-roster-head">
        <div className="pw-roster-counts">
          {WORKER_ROSTER_STATES.filter((state) => roster.counts[state] > 0).map((state) => (
            <WorkerStateBadge
              key={state}
              state={state}
              locale={locale}
              count={roster.counts[state]}
            />
          ))}
        </div>
        <WorkerProgressBadge progress={roster.progress} />
      </header>
      {summaryOnly ? (
        <ul className="pw-roster-chips">
          {roster.workers.map((worker) => (
            <li key={worker.agentId} className="pw-roster-chip" data-roster-state={worker.state} title={worker.now ? `${worker.label} — ${worker.now}` : worker.label}>
              <WorkerStateGlyph state={worker.state} />
              <strong className="pw-roster-chip-name">{worker.label}</strong>
              <span className="pw-roster-state-text">{worker.stateLabel}</span>
              <WorkerScopeTag scope={worker.scope} />
              <WorkerProgressBadge progress={worker.progress} />
              {!readOnly && worker.hasConversation && onOpenAgentConversation ? (
                <button
                  type="button"
                  className="pw-roster-chip-jump"
                  aria-label={locale === "zh-CN" ? `打开 ${worker.label} 工作记录` : `Open ${worker.label} conversation`}
                  onClick={() => onOpenAgentConversation(worker.agentId)}
                >
                  {locale === "zh-CN" ? "对话" : "Chat"}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <ul className="pw-roster-list">
          {roster.workers.map((worker) => (
            <li key={worker.agentId} className="pw-roster-row" data-roster-state={worker.state}>
              <div className="pw-roster-row-top">
                <WorkerStateGlyph state={worker.state} />
                <strong className="pw-roster-name" title={worker.label}>{worker.label}</strong>
                <span className="pw-roster-state-text is-inline">{worker.stateLabel}</span>
                <WorkerScopeTag scope={worker.scope} />
                <WorkerProgressBadge progress={worker.progress} />
                {!readOnly && !compact && worker.hasConversation && onOpenAgentConversation ? (
                  <button
                    type="button"
                    className="pw-roster-jump"
                    onClick={() => onOpenAgentConversation(worker.agentId)}
                  >
                    {locale === "zh-CN" ? "工作记录" : "Conversation"}
                  </button>
                ) : null}
              </div>
              <WorkerNowLine now={worker.now} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
