import { useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { applyLifecycle, previewLifecycle, ActionError, type LifecycleOperation, type LifecycleProposal } from '../loopx/actions';
import type { LoopxGoal } from '../loopx/status';
import { IconChevron } from './icons';

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
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!pending) return;
    dialog.current?.querySelector<HTMLButtonElement>('.dialog-actions button:last-child')?.focus();
    return () => { trigger.current?.focus({ preventScroll: true }); };
  }, [pending?.proposal.proposalId]);

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
    <Popover label={t('loopx.lifecycle.menu')} triggerClassName="tool-btn goal-lifecycle" triggerLabel={t('loopx.lifecycle.menu')} triggerRef={trigger} trigger={<><span>{t('loopx.lifecycle.menu')}</span><IconChevron size={12} open={false} /></>} triggerBusy={previewing !== null} align="end">
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
      </>}
    </Popover>
    {error && !pending ? <span className="goal-lifecycle-error" role="alert">{error}</span> : null}
    {pending ? <div ref={dialog} data-menu-disclosure="open"><Dialog
      title={t('loopx.lifecycle.confirmTitle', operationWord(pending.operation))}
      confirmLabel={t('loopx.lifecycle.confirmLabel', operationWord(pending.operation))}
      danger={pending.operation === 'delete'}
      confirmDisabled={applying}
      cancelDisabled={applying}
      onConfirm={() => void confirm()}
      onCancel={() => { setPending(null); setError(null); }}
    >
      <div className="goal-lifecycle-confirm">
        <p className="goal-lifecycle-summary">{pending.proposal.summary}</p>
        {pending.proposal.evidence.length ? <ul className="goal-lifecycle-evidence">
          {pending.proposal.evidence.map((line, index) => <li key={index}>{line}</li>)}
        </ul> : null}
        {pending.operation === 'delete' ? <p className="goal-lifecycle-warning">{t('loopx.lifecycle.deleteWarning')}</p> : null}
        {error ? <p className="goal-failure" role="alert">{error}</p> : null}
      </div>
    </Dialog></div> : null}
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
