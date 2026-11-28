import { useEffect, useMemo, useState } from 'react';
import type { AgentInfo, Api, ModelOption, Thread } from '../api';
import { DialogSurface } from './Dialog';
import { Popover } from './Popover';
import { AgentPicker, modelName } from './Pickers';
import { AgentMark, IconArrowRight, IconCheck, IconChevron } from './icons';
import { t } from '../i18n';
import './fork-subchat.css';

export interface ForkChoice {
  agent: string;
  model: string | null;
  firstMessage: string;
  worktree: boolean;
  lightweight: boolean;
  continueFromSource: boolean;
  forkKind: 'fork' | 'handoff';
}

interface Props {
  api: Api;
  agents: AgentInfo[];
  thread: Thread;
  /** `handoff`: fresh session with 0-token anchor + cross-Agent/Model switching; `fork`: native full-history copy. */
  mode: 'fork' | 'handoff';
  worktreeAvailable: boolean;
  worktreeBranch?: string | null;
  onConfirm: (opts: ForkChoice) => void;
  onCancel: () => void;
}

/**
 * Codex-style unified composer sheet serving both "Continue in new chat" (handoff) and "Fork" (native copy):
 * - Single rounded composer box holds the optional note above an embedded borderless Agent & Model toolbar.
 * - In `handoff` mode the user can switch Agent/Model freely and starts a fresh session with a silent
 *   0-token progress anchor; in `fork` mode the Agent stays locked to the source for native full-history copy.
 */
export function ForkSubchatDialog({
  api,
  agents,
  thread,
  mode,
  worktreeAvailable,
  worktreeBranch = null,
  onConfirm,
  onCancel,
}: Props) {
  const selectableAgents = useMemo(() => {
    const visible = agents.filter((a) => a.available && !a.hidden);
    if (visible.some((a) => a.id === thread.agent)) return visible;
    const current = agents.find((a) => a.id === thread.agent);
    return current ? [current, ...visible] : visible;
  }, [agents, thread.agent]);

  const isHandoff = mode === 'handoff';
  const [agent, setAgent] = useState(thread.agent);
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [modelError, setModelError] = useState('');
  const [model, setModel] = useState<string | null>(thread.model ?? null);
  const [firstMessage, setFirstMessage] = useState('');
  const [worktree, setWorktree] = useState(false);

  const trimmedMsg = firstMessage.trim();
  const hasMessage = trimmedMsg.length > 0;
  const activeAgent = selectableAgents.find((a) => a.id === agent);
  const hasModelCatalog = activeAgent?.capabilities.modelCatalog !== false;

  useEffect(() => {
    let alive = true;
    setModels(null);
    setModelError('');
    if (!agent || !hasModelCatalog) return () => { alive = false; };
    void api
      .models(agent)
      .then((list) => {
        if (!alive) return;
        setModels(list);
        setModel((cur) => {
          if (cur && list.some((m) => m.id === cur)) return cur;
          return null;
        });
      })
      .catch((e) => {
        if (alive) setModelError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [api, agent, hasModelCatalog]);

  const pickAgent = (next: string) => {
    setAgent(next);
    setModel(next === thread.agent ? (thread.model ?? null) : null);
  };

  const submit = () => {
    onConfirm({
      agent: isHandoff ? agent : thread.agent,
      model: hasModelCatalog ? model : null,
      firstMessage: trimmedMsg,
      worktree: worktreeAvailable && worktree,
      lightweight: isHandoff,
      continueFromSource: isHandoff && !hasMessage,
      forkKind: isHandoff ? 'handoff' : 'fork',
    });
  };

  const currentModelLabel = !hasModelCatalog
    ? t('auto.modelDefault')
    : models
      ? modelName(models, model)
      : model ?? t('auto.modelDefault');

  const titleLabel = isHandoff ? t('actions.handoffContinue') : t('sidebar.fork');
  const primaryLabel = hasMessage
    ? (isHandoff ? t('actions.handoffAndSend') : t('actions.forkAndSend'))
    : (isHandoff ? t('actions.handoffContinue') : t('actions.forkConfirm'));

  return (
    <DialogSurface className="fork-subchat-dialog" labelledBy="fork-card-title" onClose={onCancel}>
      <div className="fork-card-head">
        <h2 id="fork-card-title" className="dialog-title">
          {titleLabel}
        </h2>
      </div>

      <div className="fork-card-body">
        <div className="fork-composer">
          <label className="fork-composer-field">
            <span className="sr-only">{t('actions.forkFirstMessageLabel')}</span>
            <textarea
              rows={2}
              value={firstMessage}
              placeholder={isHandoff ? t('actions.forkPlaceholder') : t('actions.forkFullPlaceholder')}
              onChange={(e) => setFirstMessage(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
          </label>
          <div className="fork-composer-bar">
            {isHandoff ? (
              <AgentPicker api={api} agents={selectableAgents} value={agent} onChange={pickAgent} />
            ) : (
              <span className="fork-composer-agent-fixed">
                {activeAgent?.icon ? <AgentMark path={activeAgent.icon} size={14} /> : null}
                <span>{activeAgent?.label ?? thread.agent}</span>
              </span>
            )}
            {hasModelCatalog && (
              <>
                <span className="fork-composer-sep" aria-hidden="true">·</span>
                <Popover
                  triggerClassName="context-btn fork-composer-model"
                  label={t('actions.forkModelLabel')}
                  trigger={
                    <>
                      <span className="fork-card-model-name">{currentModelLabel}</span>
                      <IconChevron size={12} open />
                    </>
                  }
                >
                  {(close) => (
                    <div role="menu">
                      <button
                        type="button"
                        className="pop-item compact"
                        role="menuitemradio"
                        aria-checked={model === null}
                        onClick={() => {
                          setModel(null);
                          close();
                        }}
                      >
                        <span className="pop-item-text">
                          <span className="pop-item-label">{t('auto.modelDefault')}</span>
                        </span>
                        <span className="pop-item-check">{model === null && <IconCheck size={15} />}</span>
                      </button>
                      {models
                        ?.filter((m) => m.id !== 'default')
                        .map((m) => (
                          <button
                            key={m.id}
                            type="button"
                            className="pop-item compact"
                            role="menuitemradio"
                            aria-checked={model === m.id}
                            onClick={() => {
                              setModel(m.id);
                              close();
                            }}
                          >
                            <span className="pop-item-text">
                              <span className="pop-item-label">{m.label}</span>
                            </span>
                            <span className="pop-item-check">{model === m.id && <IconCheck size={15} />}</span>
                          </button>
                        ))}
                      {!models && !modelError && <p className="fork-card-pop-note">{t('common.loading')}</p>}
                      {modelError && (
                        <p className="panel-error fork-card-pop-note" role="alert">
                          {modelError}
                        </p>
                      )}
                    </div>
                  )}
                </Popover>
              </>
            )}
          </div>
        </div>

        {worktreeAvailable && (
          <label className="fork-check-row">
            <input type="checkbox" checked={worktree} onChange={(e) => setWorktree(e.target.checked)} />
            <span className="fork-check-label">{t('actions.forkWorktreeOption')}</span>
            {worktreeBranch && <span className="fork-check-note">{worktreeBranch}</span>}
          </label>
        )}
      </div>

      <div className="dialog-actions fork-card-actions">
        <button type="button" className="ghost" onClick={onCancel}>
          {t('common.cancel')}
        </button>
        <button type="button" className="primary" onClick={submit}>
          {primaryLabel}
          <IconArrowRight size={14} />
        </button>
      </div>
    </DialogSurface>
  );
}
