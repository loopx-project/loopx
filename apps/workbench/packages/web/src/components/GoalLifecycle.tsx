import { useState } from 'react';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { applyLifecycle, previewLifecycle, ActionError, type LifecycleOperation, type LifecycleProposal } from '../loopx/actions';
import type { LoopxGoal } from '../loopx/status';

/**
 * The Goal's own lifecycle: stop, resume, delete. Every operation is a typed action — the core
 * previews it, the person confirms what the preview said, and only the apply changes anything.
 * Delete is offered only on a stopped Goal because the core refuses it otherwise; the menu says
 * so instead of failing on confirm.
 */
export function GoalLifecycle({ token, goal, onChanged }: {
  token: string | undefined;
  goal: LoopxGoal;
  onChanged: () => void;
}) {
  const [previewing, setPreviewing] = useState<LifecycleOperation | null>(null);
  const [pending, setPending] = useState<{ operation: LifecycleOperation; proposal: LifecycleProposal } | null>(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stopped = goal.activationState !== 'active';

  const start = async (operation: LifecycleOperation) => {
    setError(null);
    setPreviewing(operation);
    try {
      const proposal = await previewLifecycle(token, goal.goalId, operation, {
        summary: t('loopx.lifecycle.summary', operationWord(operation), goal.title),
      });
      if (proposal.status !== 'preview_ready') {
        setError(t('loopx.lifecycle.notReady', proposal.error ?? proposal.status));
        return;
      }
      setPending({ operation, proposal });
    } catch (cause) {
      setError(actionErrorText(cause));
    } finally {
      setPreviewing(null);
    }
  };

  const confirm = async () => {
    if (!pending) return;
    setApplying(true);
    try {
      const applied = await applyLifecycle(token, pending.proposal.proposalId);
      if (applied.status !== 'applied') {
        setError(t('loopx.lifecycle.notReady', applied.error ?? applied.status));
        return;
      }
      setPending(null);
      onChanged();
    } catch (cause) {
      setError(actionErrorText(cause));
    } finally {
      setApplying(false);
    }
  };

  return <>
    <Popover label={t('loopx.lifecycle.menu')} triggerClassName="tool-btn goal-lifecycle" triggerLabel={t('loopx.lifecycle.menu')} triggerBusy={previewing !== null} align="end">
      {close => <>
        <button type="button" className="pop-item" disabled={stopped || previewing !== null} onClick={() => { close(); void start('stop'); }}>
          <span>{t('loopx.lifecycle.stop')}</span>
        </button>
        <button type="button" className="pop-item" disabled={!stopped || previewing !== null} onClick={() => { close(); void start('resume'); }}>
          <span>{t('loopx.lifecycle.resume')}</span>
        </button>
        <button type="button" className="pop-item danger" disabled={!stopped || previewing !== null} onClick={() => { close(); void start('delete'); }}>
          <span>{t('loopx.lifecycle.delete')}</span>
        </button>
        {!stopped ? <div className="pop-note">{t('loopx.lifecycle.deleteNeedsStopped')}</div> : null}
        {error ? <div className="pop-note" role="alert">{error}</div> : null}
      </>}
    </Popover>
    {pending ? <Dialog
      title={t('loopx.lifecycle.confirmTitle', operationWord(pending.operation))}
      confirmLabel={t('loopx.lifecycle.confirmLabel', operationWord(pending.operation))}
      danger={pending.operation === 'delete'}
      confirmDisabled={applying}
      onConfirm={() => void confirm()}
      onCancel={() => setPending(null)}
    >
      <div className="goal-lifecycle-confirm">
        <p className="goal-lifecycle-summary">{pending.proposal.summary}</p>
        {pending.proposal.evidence.length ? <ul className="goal-lifecycle-evidence">
          {pending.proposal.evidence.map((line, index) => <li key={index}>{line}</li>)}
        </ul> : null}
        {pending.operation === 'delete' ? <p className="goal-lifecycle-warning">{t('loopx.lifecycle.deleteWarning')}</p> : null}
      </div>
    </Dialog> : null}
  </>;
}

function operationWord(operation: LifecycleOperation): string {
  return operation === 'stop' ? t('loopx.lifecycle.stop') : operation === 'resume' ? t('loopx.lifecycle.resume') : t('loopx.lifecycle.delete');
}

function actionErrorText(cause: unknown): string {
  if (cause instanceof ActionError && cause.status === 502) return t('loopx.coreDown');
  if (cause instanceof Error) return t('loopx.lifecycle.failed', cause.message);
  return t('loopx.lifecycle.failed', String(cause));
}
