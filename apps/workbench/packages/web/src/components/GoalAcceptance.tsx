import { useCallback, useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import {
  adoptPlan, criterionStates, displayStatus, operateAcceptance, readAcceptance, readPreparedPlan,
  type AcceptanceOperation, type AcceptanceStatus, type Contract, type Observation, type PreparedPlan, type Readback,
} from '../loopx/acceptance';
import type { LoopxGoal } from '../loopx/status';
import { Dialog } from './Dialog';

const REFRESH_MS = 15000;

/** The write basis and the prepared plan, read together and refreshed while the page is open. */
function useAcceptance(token: string | undefined, goalId: string) {
  const [state, setState] = useState<{ readback: Readback | null; plan: PreparedPlan | null; failed: boolean }>({ readback: null, plan: null, failed: false });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setState({ readback: null, plan: null, failed: false });
    const once = async () => {
      try {
        const readback = await readAcceptance(token, goalId, controller.signal);
        // A plan that cannot be read only hides the plan; the contract above it stays true.
        const plan = await readPreparedPlan(token, goalId, controller.signal).catch(() => null);
        if (alive) setState({ readback, plan, failed: false });
      } catch (cause) {
        if (!alive || controller.signal.aborted) return;
        setState(prev => ({ ...prev, failed: true }));
      } finally {
        if (alive) timer = setTimeout(() => void once(), REFRESH_MS);
      }
    };
    void once();
    return () => { alive = false; clearTimeout(timer); controller.abort(); };
  }, [token, goalId, nonce]);
  return { ...state, reload: useCallback(() => setNonce(n => n + 1), []) };
}

const statusWord = (status: AcceptanceStatus) => t(`loopx.accept.status.${status}`);

type Pending = { kind: 'verify' | 'clear-checks' } | { kind: 'adopt' };

/** The Goal overview's acceptance card: what the core says is accepted, the checks and their last receipt, and the two actions the owner may take. */
export function GoalAcceptance({ token, goal, readOnly = false }: { token: string | undefined; goal: LoopxGoal; readOnly?: boolean }) {
  const { readback, plan, failed, reload } = useAcceptance(token, goal.goalId);
  const observation: Observation | null = goal.acceptance?.goalId === goal.goalId ? goal.acceptance : null;
  // The fresh readback wins; the status snapshot is what the card shows while that read is on its way.
  const contract: Contract | null = readback?.contract ?? observation?.contract ?? null;
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const flight = useRef(false);
  useEffect(() => { setPending(null); setError(''); setNotice(''); }, [goal.goalId, readOnly]);
  const active = (contract?.lifecycleState ?? 'active') === 'active' && goal.activationState === 'active';
  const writable = !readOnly && readback !== null && !failed && active;
  const checks = contract?.checks ?? null;
  // A check bound to a retained outcome requirement is that requirement; list it once, under the outcome.
  const outcomeReqs = (contract?.outcomes ?? []).flatMap(o => o.requirements);
  const ownCriteria = checks ? criterionStates(checks).filter(c => !outcomeReqs.some(r => r.id === c.id && r.description === c.description)) : [];
  const outcomeStates = new Map(checks ? criterionStates(checks).map(c => [c.id, c.state]) : []);

  const run = async (action: Pending['kind']) => {
    if (flight.current || !writable) return;
    flight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      if (action === 'adopt') { if (!plan) return; await adoptPlan(token, plan.proposalId); }
      else await operateAcceptance(token, goal.goalId, action as AcceptanceOperation, readback!.providerRevision);
      setNotice(action === 'clear-checks' ? t('loopx.accept.cleared') : action === 'adopt' ? t('loopx.accept.adopted') : t('loopx.accept.verified'));
    } catch {
      // The run may or may not have happened; read the current result before anyone tries again. Nothing is replayed.
      setError(t('loopx.accept.unconfirmed'));
    } finally {
      flight.current = false; setBusy(false); setPending(null); reload();
    }
  };

  const gaps = observation?.gaps ?? [], guards = observation?.guards ?? [];
  const confirmText = pending?.kind === 'clear-checks' ? t('loopx.accept.clearConfirm') : t('loopx.accept.runConfirm');
  return <section className="goal-acceptance" aria-label={t('loopx.accept.title')} aria-busy={busy}>
    <div className="goal-acceptance-head">
      <h2>{t('loopx.accept.title')}</h2>
      {checks ? <span className={`goal-accept-chip is-${displayStatus(checks)}`}>{statusWord(displayStatus(checks))}</span> : null}
    </div>
    <p className="goal-acceptance-note">{t('loopx.accept.note')}</p>
    {failed ? <p className="goal-failure" role="alert">{t('loopx.accept.unavailable')} <button type="button" className="ghost" onClick={reload}>{t('common.retry')}</button></p> : null}

    {contract?.outcomes.map(outcome => <div className="goal-accept-outcome" key={outcome.id}>
      <p className="goal-accept-objective">{outcome.objective}</p>
      <ul>{outcome.requirements.map(req => <li key={req.id}>
        <span className={`goal-accept-chip is-${checks ? (outcomeStates.get(req.id) ?? req.status) : req.status}`}>{statusWord(checks ? (outcomeStates.get(req.id) ?? req.status) : req.status)}</span>
        <span>{req.description}</span>
        {req.review ? <small>{t(`loopx.accept.review.${req.review.conclusion}`)}{req.review.note ? ` · ${req.review.note}` : ''}</small> : null}
      </li>)}</ul>
    </div>)}

    {plan ? <div className="goal-accept-plan">
      <h3>{t('loopx.accept.plan.title')}</h3>
      <p>{plan.objective}</p>
      <p className="goal-acceptance-note">{plan.scope === 'goal' ? t('loopx.accept.scope.goal') : plan.scope === 'selected_work' ? t('loopx.accept.scope.selected', plan.selectedTasks) : t('loopx.accept.scope.all')}</p>
      <ul>{plan.criteria.map(c => <li key={c.id}><span>{c.description}</span></li>)}</ul>
      <details><summary>{t('loopx.accept.plan.commands')}</summary>
        {plan.criteria.map(c => <div key={c.id}><p>{c.description}</p><code>{c.command}</code>{c.outputs.map(ref => <p key={ref}>{t('loopx.accept.plan.output', ref)}</p>)}</div>)}
      </details>
      {!readOnly && <p className="goal-acceptance-note">{t('loopx.accept.plan.hint')}</p>}
      {!readOnly && <button type="button" className="primary" disabled={busy || !writable} onClick={() => setPending({ kind: 'adopt' })}>{t('loopx.accept.plan.adopt')}</button>}
    </div> : null}

    {checks ? <div className="goal-accept-checks">
      <h3>{t('loopx.accept.checks')}</h3>
      {ownCriteria.length ? <><p>{checks.objective}</p>
      <ul>{ownCriteria.map(c => <li key={c.id}>
        <span className={`goal-accept-chip is-${c.state}`}>{statusWord(c.state)}</span><span>{c.description}</span>
      </li>)}</ul></> : null}
      <details><summary>{t('loopx.accept.receipt')}</summary>
        {checks.receipt ? <>
          <p>{t('loopx.accept.receiptLine', checks.receipt.results.filter(r => r.passed).length, checks.receipt.results.length)}</p>
          {checks.receipt.results.map(r => <p key={r.criterionId}>
            {checks.criteria.find(c => c.id === r.criterionId)?.description ?? r.criterionId} · {r.passed ? t('loopx.accept.passed') : t('loopx.accept.failedResult')}
            {r.exitCode !== null ? ` · ${t('loopx.accept.exit', r.exitCode)}` : ''}
          </p>)}
          <p className="goal-acceptance-note"><code>{checks.receipt.operationId}</code> · r{checks.receipt.contractRevision}</p>
        </> : <p>{t('loopx.accept.noReceipt')}</p>}
      </details>
      {!readOnly && <div className="goal-accept-actions">
        <button type="button" className="primary" disabled={busy || !writable} onClick={() => setPending({ kind: 'verify' })}>{busy ? t('loopx.accept.running') : t('loopx.accept.verify')}</button>
        <button type="button" className="ghost" disabled={busy || !writable} onClick={() => setPending({ kind: 'clear-checks' })}>{t('loopx.accept.clear')}</button>
      </div>}
      {!active ? <p className="goal-acceptance-note">{t('loopx.accept.stopped')}</p> : null}
    </div> : !plan && !failed ? <p className="goal-empty" role={readback || observation ? undefined : 'status'}>{t(readback || observation ? 'loopx.accept.noChecks' : 'common.loading')}</p> : null}

    {notice ? <p role="status" className="goal-acceptance-note">{notice}</p> : null}
    {error ? <p role="alert" className="goal-failure">{error}</p> : null}

    {observation && observation.coverage !== 'unavailable' ? <div className="goal-accept-observation">
      {observation.nextAction ? <div><h3>{t('loopx.accept.next')}</h3><p>{observation.nextAction}</p></div> : null}
      <h3>{t('loopx.accept.gaps', gaps.length)}</h3>
      {gaps.map((gap, i) => <div className="goal-accept-gap" key={i}>
        <p>{gap.reason ?? t('loopx.accept.unknown')}</p>
        {gap.resolutionHint ? <p>{gap.resolutionHint}</p> : null}
        {gap.evidenceRequired ? <p className="goal-acceptance-note">{t('loopx.accept.required')}: {gap.evidenceRequired}</p> : null}
        {gap.failedChecks > 0 ? <p className="goal-acceptance-note">{t('loopx.accept.failedChecks', gap.failedChecks)}</p> : null}
      </div>)}
      <h3>{t('loopx.accept.guards', guards.length)}</h3>
      {guards.map((guard, i) => <div className="goal-accept-gap" key={i}>
        <p>{guard.reason ?? t('loopx.accept.unknown')}</p>
        {guard.evidenceRequired ? <p className="goal-acceptance-note">{t('loopx.accept.required')}: {guard.evidenceRequired}</p> : null}
      </div>)}
      {observation.missingSources.length || observation.truncated ? <p className="goal-acceptance-note">{t('loopx.accept.partial')}</p> : null}
    </div> : observation ? <p className="goal-acceptance-note">{t('loopx.accept.observationUnavailable')}</p> : null}

    {pending && !readOnly ? <Dialog
      title={pending.kind === 'clear-checks' ? t('loopx.accept.clear') : pending.kind === 'adopt' ? t('loopx.accept.plan.adopt') : t('loopx.accept.verify')}
      confirmLabel={pending.kind === 'clear-checks' ? t('loopx.accept.clear') : pending.kind === 'adopt' ? t('loopx.accept.plan.adopt') : t('loopx.accept.verify')}
      danger={pending.kind === 'clear-checks'}
      confirmDisabled={busy}
      onConfirm={() => void run(pending.kind)}
      onCancel={() => setPending(null)}
    >
      <p>{pending.kind === 'adopt' ? t('loopx.accept.plan.hint') : confirmText}</p>
      {pending.kind === 'adopt' && plan ? <ul>{plan.criteria.map(c => <li key={c.id}><code>{c.command}</code></li>)}</ul> : null}
    </Dialog> : null}
  </section>;
}
