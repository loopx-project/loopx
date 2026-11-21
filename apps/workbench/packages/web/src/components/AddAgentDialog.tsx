import { useEffect, useState } from 'react';
import type { AgentInfo, Api } from '../api';
import type { DiscoveredAgent } from './Pickers';
import { OnboardingDialog } from './OnboardingDialog';
import { IconClose, IconPlus, IconSparkle } from './icons';
import { t } from '../i18n';

/**
 * CLIs on this host that look like another copy of a known agent. Adding one is rare, so it lives
 * here behind the settings menu instead of the agent picker the principle keeps lean: what is not
 * used often must not be in the way of what is.
 */

interface Props {
  found?: DiscoveredAgent[];
  onAdd?: (found: DiscoveredAgent) => Promise<void>;
  onClose: () => void;
  api?: Api;
  agents?: AgentInfo[];
  onConnected?: (newAgents: AgentInfo[]) => void;
}

export function AddAgentDialog({ found = [], onAdd, onClose, api, agents, onConnected }: Props) {
  const [adding, setAdding] = useState<string | null>(null);
  const [added, setAdded] = useState<string[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (api && agents && onConnected) {
    return (
      <OnboardingDialog
        api={api}
        agents={agents}
        initialStep="results"
        onConnected={onConnected}
        onClose={onClose}
        onLater={onClose}
      />
    );
  }

  const rows = found.filter((d) => !added.includes(d.id));

  return (
    <div className="dialog-scrim" onClick={onClose}>
      <div className="dialog add-agent" role="dialog" aria-modal="true" aria-labelledby="add-agent-title" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-title-row">
          <h2 id="add-agent-title" className="dialog-title">
            {t('addAgent.title')}
          </h2>
          <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
            <IconClose size={18} />
          </button>
        </div>
        <p className="dialog-message">{t('addAgent.message')}</p>
        <div className="handover-targets">
          {rows.length === 0 && <p className="handover-note">{t('addAgent.none')}</p>}
          {rows.map((d) => (
            <button
              key={d.id}
              type="button"
              className="handover-target"
              disabled={adding !== null}
              onClick={async () => {
                if (!onAdd) return;
                setAdding(d.id);
                try {
                  await onAdd(d);
                  setAdded((ids) => [...ids, d.id]);
                } finally {
                  setAdding(null);
                }
              }}
            >
              <span>{d.id}</span>
              {adding === d.id ? <IconSparkle size={14} /> : <IconPlus size={14} />}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
