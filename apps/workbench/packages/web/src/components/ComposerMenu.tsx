import type { AgentInfo, SlashCommand } from '../api';
import { IconAgents, IconBolt, IconCheck, IconChevron, IconList, IconPaperclip, IconQuote, IconPen, IconSparkle, IconTarget } from './icons';
import { t } from '../i18n';

export type GoalCapability = NonNullable<AgentInfo['capabilities']['goal']>;
export type GoalAction = NonNullable<GoalCapability['actions']>[number];

/** The menu only starts a goal; view, pause and clear live on the goal card. */
export function goalMenuActions(goal: GoalCapability, running: boolean, plan: boolean) {
  return (goal.actions ?? []).includes('set')
    ? [{ action: 'set' as const, blocked: running ? t('composerMenu.afterTurn') : plan ? t('composerMenu.planFirst') : '' }]
    : [];
}

interface Props {
  commands: SlashCommand[];
  planMode?: { active: boolean; disabled: boolean; onChange: (active: boolean) => void };
  writingMode?: WritingMode;
  goal?: GoalCapability;
  running: boolean;
  attachTitle: string;
  attachDisabled?: boolean;
  onAttach: () => void;
  onReference?: () => void;
  onBrowse: (section: 'actions' | 'skills') => void;
  onGoal: (action: GoalAction) => void;
  collaboration?: { active: boolean; open: () => void };
  close: () => void;
}

/** Writing mode belongs to the hub, so every agent has it. */
export interface WritingMode { active: boolean; disabled: boolean; onChange: (active: boolean) => void }

/** A compact phone menu. Browsing opens the composer's full-width command palette. */
export function ComposerMenu({ commands, planMode, writingMode, goal, running, attachTitle, attachDisabled, onAttach, onReference, onBrowse, onGoal, collaboration, close }: Props) {
  const unknownKinds = commands.some(c => !c.kind);
  const skillTitle = unknownKinds ? t('composerMenu.skillsAndCommands') : t('composerMenu.skills');
  const pick = (action: () => void) => { close(); action(); };

  return <div className="composer-menu-list">
    <button type="button" role="menuitem" className="pop-item" title={attachTitle} disabled={attachDisabled} onClick={() => pick(onAttach)}>
      <span className="pop-item-icon"><IconPaperclip size={19} /></span><span className="pop-item-label">{t('composerMenu.attach')}</span>
    </button>
    {onReference && <button type="button" role="menuitem" className="pop-item" onClick={() => pick(onReference)}><span className="pop-item-icon"><IconQuote size={19} /></span><span className="pop-item-label">{t('references.add')}</span></button>}
    {collaboration && <button type="button" role="menuitem" className="pop-item" title={collaboration.active ? t('app.collabOn') : t('app.collab')} onClick={() => pick(collaboration.open)}>
      <span className="pop-item-icon"><IconAgents size={19} /></span><span className="pop-item-label">{t('app.collab')}</span>
      {collaboration.active && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>}
    {(planMode || writingMode || goal) && <div className="pop-divider" />}
    {planMode && <button type="button" role="menuitemcheckbox" aria-checked={planMode.active} className="pop-item" disabled={planMode.disabled}
      title={planMode.disabled ? t('composerMenu.planAfterTurn') : planMode.active ? t('composerMenu.planOff') : t('composerMenu.planOn')}
      onClick={() => pick(() => planMode.onChange(!planMode.active))}>
      <span className="pop-item-icon"><IconList size={19} /></span>
      <span className="pop-item-text"><span className="pop-item-label">{t('composerMenu.planMode')}</span></span>
      {planMode.active && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>}
    {writingMode && <button type="button" role="menuitemcheckbox" aria-checked={writingMode.active} className="pop-item" disabled={writingMode.disabled}
      title={writingMode.active ? t('writing.off') : t('writing.on')}
      onClick={() => pick(() => writingMode.onChange(!writingMode.active))}>
      <span className="pop-item-icon"><IconPen size={19} /></span>
      <span className="pop-item-text"><span className="pop-item-label">{t('writing.mode')}</span><small className="pop-item-hint">{t('writing.modeHint')}</small></span>
      {writingMode.active && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>}
    {goal && goalMenuActions(goal, running, !!planMode?.active).map(({ action, blocked }) => (
      <button key={action} type="button" role="menuitem" className="pop-item" disabled={!!blocked}
        title={blocked || t('composer.goalSetHint')}
        onClick={() => pick(() => onGoal(action))}>
        <span className="pop-item-icon"><IconTarget size={19} /></span>
        <span className="pop-item-text"><span className="pop-item-label">{t(`goal.${action}`)}</span>{blocked && <small className="pop-item-hint">{blocked}</small>}</span>
      </button>
    ))}
    <div className="pop-divider" />
    <button type="button" role="menuitem" className="pop-item" onClick={() => pick(() => onBrowse('actions'))}>
      <span className="pop-item-icon"><IconBolt size={19} /></span><span className="pop-item-text"><span className="pop-item-label">{t('composerMenu.actions')}</span></span><IconChevron size={14} />
    </button>
    <button type="button" role="menuitem" className="pop-item" onClick={() => pick(() => onBrowse('skills'))}>
      <span className="pop-item-icon"><IconSparkle size={19} /></span><span className="pop-item-text"><span className="pop-item-label">{skillTitle}</span></span><IconChevron size={14} />
    </button>
  </div>;
}
