import { useState, type ReactNode } from 'react';
import type { AgentMention } from '../api';
import { AgentMark, IconBack, IconCheck, IconSettings } from './icons';
import { t } from '../i18n';

interface Props {
  options: AgentMention[];
  selected: AgentMention[];
  icon: (mention: AgentMention) => string | undefined;
  onSelect: (mention: AgentMention) => void;
  settings: ReactNode;
  unavailable: string;
}

/** Choosing a collaborator and configuring defaults are separate actions. */
export function CollaborationMenu({ options, selected, icon, onSelect, settings, unavailable }: Props) {
  const [page, setPage] = useState<'choose' | 'settings'>('choose');
  return <div className={`coord-picker${page === 'choose' ? ' choosing' : ''}`}>
    <div className="coord-picker-head">
      {page === 'settings' && <button type="button" className="icon-btn" aria-label={t('collab.back')} onClick={() => setPage('choose')}><IconBack size={18} /></button>}
      <strong>{page === 'settings' ? t('collab.settings') : t('collab.choose')}</strong>
      {page === 'choose' && <button type="button" className="tool-btn" onClick={() => setPage('settings')}><IconSettings size={16} /><span>{t('collab.settings')}</span></button>}
    </div>
    {page === 'settings' ? settings : <>
      <p className="coord-picker-hint">{t('collab.hint')}</p>
      <div className="coord-picker-list">
        {(['member', 'agent'] as const).map(kind => <div key={kind}>
          {options.some(m => m.kind === kind) && <div className="coord-caption">{kind === 'member' ? t('collab.members') : t('collab.add')}</div>}
          {options.filter(m => m.kind === kind).map(m => {
            const chosen = selected.some(v => v.id === m.id && v.kind === m.kind);
            const mark = icon(m);
            return <button type="button" role="menuitem" className={`pop-item coord-pick${chosen ? ' chosen' : ''}`} key={`${m.kind}:${m.id}`} aria-label={t('collab.assign', m.label)} onClick={() => onSelect(m)}>
              <span className="pop-item-icon">{mark && <AgentMark path={mark} size={19} />}</span>
              <span className="pop-item-text"><span className="pop-item-label">{m.label}</span><small className="pop-item-hint">{chosen ? t('collab.chosen') : kind === 'member' ? t('collab.appendTask') : t('collab.pickAndWrite')}</small></span>
              {chosen && <span className="pop-item-check"><IconCheck size={16} /></span>}
            </button>;
          })}
        </div>)}
        {!options.length && <p className="pop-note">{unavailable}</p>}
      </div>
    </>}
  </div>;
}
