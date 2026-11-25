import { useState } from 'react';
import type { AgentInfo } from '../api';
import { Dialog } from './Dialog';
import { t } from '../i18n';
import './fork-subchat.css';

interface Props {
  worktreeAvailable: boolean;
  allowLightweight?: boolean;
  allowFullFork?: boolean;
  agents?: AgentInfo[];
  currentAgent: string;
  onConfirm: (opts: { forkTag: string; firstMessage: string; worktree: boolean; lightweight: boolean; agent?: string }) => void;
  onCancel: () => void;
}

/** Light one-step card to fork a grouped sub-conversation in the current directory. */
export function ForkSubchatDialog({ worktreeAvailable, allowLightweight = true, allowFullFork = true, agents = [], currentAgent, onConfirm, onCancel }: Props) {
  const [lightweight, setLightweight] = useState(allowLightweight || !allowFullFork);
  const [agent, setAgent] = useState(currentAgent);
  const [forkTag, setForkTag] = useState('');
  const [firstMessage, setFirstMessage] = useState('');
  const hasMessage = firstMessage.trim().length > 0;
  const availableAgents = agents.filter((a) => a.available !== false || a.id === currentAgent);
  const switchedAgent = agent !== currentAgent;
  const effectiveLightweight = allowLightweight && (lightweight || !allowFullFork || switchedAgent);

  return (
    <Dialog
      className="fork-subchat-dialog"
      title={t('actions.forkSubchatTitle')}
      message={t('actions.forkSubchatMessage')}
      options={worktreeAvailable ? [{ label: t('actions.forkSubchatWorktree'), hint: t('actions.forkSubchatWorktreeHint') }] : []}
      confirmLabel={hasMessage ? t('actions.forkSubchatBackground') : t('actions.forkSubchatEnter')}
      onConfirm={(_, [worktree]) =>
        onConfirm({
          forkTag: forkTag.trim(),
          firstMessage: firstMessage.trim(),
          worktree: Boolean(worktree),
          lightweight: effectiveLightweight,
          ...(switchedAgent ? { agent } : {}),
        })
      }
      onCancel={onCancel}
    >
      <div className="fork-subchat-fields">
        {allowLightweight && (
          <div className="fork-subchat-field">
            <span>{t('actions.forkModeLabel')}</span>
            <div className="fork-subchat-mode" role="radiogroup" aria-label={t('actions.forkModeLabel')}>
              <button
                type="button"
                role="radio"
                aria-checked={effectiveLightweight}
                className={effectiveLightweight ? 'active' : undefined}
                onClick={() => setLightweight(true)}
              >
                {t('actions.forkModeLightweight')}
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={!effectiveLightweight}
                disabled={!allowFullFork || switchedAgent}
                className={!effectiveLightweight ? 'active' : undefined}
                onClick={() => setLightweight(false)}
              >
                {t('actions.forkModeFull')}
              </button>
            </div>
            <p className="fork-subchat-mode-hint">
              {switchedAgent
                ? t('actions.forkModeCrossAgentHint')
                : effectiveLightweight
                  ? t('actions.forkModeLightweightHint')
                  : t('actions.forkModeFullHint')}
            </p>
          </div>
        )}
        {allowLightweight && availableAgents.length > 1 && (
          <label className="fork-subchat-field">
            <span>{t('actions.forkAgentLabel')}</span>
            <select
              value={agent}
              onChange={(e) => {
                const next = e.target.value;
                setAgent(next);
                if (next !== currentAgent) setLightweight(true);
              }}
            >
              {availableAgents.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </label>
        )}
        <label className="fork-subchat-field">
          <span>{t('actions.forkTagLabel')}</span>
          <input
            type="text"
            maxLength={40}
            autoFocus
            value={forkTag}
            placeholder={t('actions.forkTagPlaceholder')}
            onChange={(e) => setForkTag(e.target.value)}
          />
        </label>
        <label className="fork-subchat-field">
          <span>{t('actions.forkFirstMessageLabel')}</span>
          <textarea
            rows={2}
            value={firstMessage}
            placeholder={t('actions.forkFirstMessagePlaceholder')}
            onChange={(e) => setFirstMessage(e.target.value)}
          />
        </label>
      </div>
    </Dialog>
  );
}
