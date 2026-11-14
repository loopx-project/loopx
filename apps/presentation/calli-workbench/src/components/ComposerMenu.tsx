import type { AgentInfo, SlashCommand } from '../api';
import { IconBolt, IconCheck, IconChevron, IconList, IconPaperclip, IconSparkle, IconTarget } from './icons';
import { t } from '../i18n';

export type GoalCapability = NonNullable<AgentInfo['capabilities']['goal']>;
export type GoalAction = NonNullable<GoalCapability['actions']>[number];

interface Props {
  commands: SlashCommand[];
  planMode?: { active: boolean; disabled: boolean; onChange: (active: boolean) => void };
  goal?: GoalCapability;
  running: boolean;
  attachTitle: string;
  attachDisabled?: boolean;
  onAttach: () => void;
  onBrowse: (section: 'actions' | 'skills') => void;
  onGoal: (action: GoalAction) => void;
  close: () => void;
}


/** A compact phone menu. Browsing opens the composer's full-width command palette. */
export function ComposerMenu({ commands, planMode, goal, running, attachTitle, attachDisabled, onAttach, onBrowse, onGoal, close }: Props) {
  const unknownKinds = commands.some(c => !c.kind);
  const skillTitle = unknownKinds ? t('composerMenu.skillsAndCommands') : t('composerMenu.skills');
  const pick = (action: () => void) => { close(); action(); };

  return <div className="composer-menu-list">
    <button type="button" role="menuitem" className="pop-item" title={attachTitle} disabled={attachDisabled} onClick={() => pick(onAttach)}>
      <span className="pop-item-icon"><IconPaperclip size={19} /></span><span className="pop-item-label">{t('composerMenu.attach')}</span>
    </button>
    {(planMode || goal) && <div className="pop-divider" />}
    {planMode && <button type="button" role="menuitemcheckbox" aria-checked={planMode.active} className="pop-item" disabled={planMode.disabled}
      title={planMode.disabled ? t('composerMenu.planAfterTurn') : planMode.active ? t('composerMenu.planOff') : t('composerMenu.planOn')}
      onClick={() => pick(() => planMode.onChange(!planMode.active))}>
      <span className="pop-item-icon"><IconList size={19} /></span>
      <span className="pop-item-text"><span className="pop-item-label">{t('composerMenu.planMode')}</span></span>
      {planMode.active && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>}
    {goal && (() => {
      // Every agent gets the same single tap, like plan mode: /goal goes into the box and the
      // objective is typed after it. What the card cannot show (an agent without live state)
      // is typed by hand when needed.
      const blocked = running ? t('composerMenu.afterTurn') : planMode?.active ? t('composerMenu.planFirst') : null;
      return <button type="button" role="menuitem" className="pop-item" disabled={!!blocked}
        title={blocked ?? t('composer.goalSetHint')}
        onClick={() => pick(() => onGoal('set'))}>
        <span className="pop-item-icon"><IconTarget size={19} /></span>
        <span className="pop-item-text"><span className="pop-item-label">{t('goal.set')}</span>{blocked && <small className="pop-item-hint">{blocked}</small>}</span>
      </button>;
    })()}
    <div className="pop-divider" />
    <button type="button" role="menuitem" className="pop-item" onClick={() => pick(() => onBrowse('actions'))}>
      <span className="pop-item-icon"><IconBolt size={19} /></span><span className="pop-item-text"><span className="pop-item-label">{t('composerMenu.actions')}</span></span><IconChevron size={14} />
    </button>
    <button type="button" role="menuitem" className="pop-item" onClick={() => pick(() => onBrowse('skills'))}>
      <span className="pop-item-icon"><IconSparkle size={19} /></span><span className="pop-item-text"><span className="pop-item-label">{skillTitle}</span></span><IconChevron size={14} />
    </button>
  </div>;
}
