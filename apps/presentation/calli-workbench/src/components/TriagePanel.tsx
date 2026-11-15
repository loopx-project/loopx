import { t } from '../i18n';
import type { TriageBucket, TriageEntry, TriageView } from '../loopx/types';

interface Props {
  triage: TriageView | null;
  onOpenThread: (id: string) => void;
}

const SHOWN: TriageBucket[] = ['needsYou', 'running', 'error'];

/** The first evidence line; for needs-you, the one that names the person, not an agent the goal also waits on. */
function reason(entry: TriageEntry): string | undefined {
  if (entry.bucket === 'needsYou') {
    const person = entry.evidence.find((line) => line === 'waiting_on=user' || line.startsWith('operator_question='));
    if (person) return person;
  }
  return entry.evidence[0];
}

/** The LoopX triage as it is projected: needs you, running, errors; waiting is folded away. Read-only. */
export function TriagePanel({ triage, onOpenThread }: Props) {
  if (!triage) return null;
  const row = (entry: TriageEntry) => {
    const body = <>
      <strong>{entry.title}</strong>
      <span className="triage-meta"><span>{t(`triage.kind.${entry.kind}`)}</span>{reason(entry) && <span>{reason(entry)}</span>}</span>
    </>;
    return <li key={`${entry.kind}:${entry.id}`}>
      {entry.kind === 'session'
        ? <button type="button" className="triage-row" onClick={() => onOpenThread(entry.id)}>{body}</button>
        : <div className="triage-row">{body}</div>}
    </li>;
  };
  return <section className="triage-panel" aria-label={t('triage.title')}>
    {SHOWN.map(bucket => <div className="triage-group" key={bucket} data-bucket={bucket}>
      <h2>{t(`triage.${bucket}`)}<span>{triage[bucket].length}</span></h2>
      {triage[bucket].length ? <ul>{triage[bucket].map(row)}</ul> : <p className="desk-empty">{t('triage.empty')}</p>}
    </div>)}
    {triage.quiet.length > 0 && <details className="triage-quiet">
      <summary>{t('triage.quiet')}</summary>
      <ul>{triage.quiet.map(row)}</ul>
    </details>}
  </section>;
}
