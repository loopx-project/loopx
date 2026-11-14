import { useMemo, useState } from 'react';
import { isFastSpeed, type AgentInfo, type Api, type ModelOption } from '../api';
import { usageSummary, type Usage } from '../timeline';
import { EFFORT_LABELS, PERMISSION_LABELS, PERMISSION_ORDER, permissionHint } from '../permissions';
import { AgentMark, IconBolt, IconEffortGauge, IconCompress, IconRefresh, IconCheck, IconChevron, IconFolder, IconHand, IconPlus, IconShield, IconShieldAlert, IconSparkle, IconWorktree } from './icons';
import { Popover } from './Popover';
import { EffortSlider } from './EffortSlider';
import { useIsNarrow } from '../hooks';
import { t } from '../i18n';

const PERMISSION_ICON: Record<string, (p: { size?: number }) => JSX.Element> = {
  ask: IconHand,
  auto_edit: IconShield,
  full_access: IconShieldAlert,
};

interface PermissionProps {
  modes: string[];
  value: string;
  onChange: (mode: string) => void;
  /** Whether the agent can stop and ask. False changes what the middle level means. */
  canAsk: boolean;
  /** Opens the panel when this number changes. */
  openSignal?: number;
  /** Whose level this is, where it is not the thread's own: the trigger is then a quiet mark with a short word. */
  quietFor?: string;
}

/** How much the agent may do without asking. Hidden for agents that have no permission modes. */
export function PermissionPicker({ modes, value, onChange, canAsk, openSignal, quietFor }: PermissionProps) {
  const ordered = PERMISSION_ORDER.filter((m) => modes.includes(m));
  if (ordered.length === 0) return null;
  const Icon = PERMISSION_ICON[value] ?? IconShield;
  return (
    <Popover
      label={t('statusPanel.permission')}
      openSignal={openSignal}
      align={quietFor === undefined ? undefined : 'end'}
      triggerClassName={quietFor === undefined ? `tool-btn keep${value === 'full_access' ? ' warn' : ''}` : 'member-model set'}
      triggerLabel={quietFor === undefined ? undefined : t('pickers.permissionFor', quietFor)}
      triggerTitle={permissionHint(value, canAsk)}
      trigger={
        <>
          <Icon size={16} />
          <span>{PERMISSION_LABELS[value]?.short ?? value}</span>
        </>
      }
    >
      {(close) => (
        <>
          <div className="pop-title">{canAsk ? t('pickers.howApprove') : t('pickers.whatAllow')}</div>
          {ordered.map((m) => {
            const ItemIcon = PERMISSION_ICON[m] ?? IconShield;
            return (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={m === value}
                className={`pop-item${m === 'full_access' ? ' warn' : ''}`}
                onClick={() => {
                  onChange(m);
                  close();
                }}
              >
                <span className="pop-item-icon">
                  <ItemIcon size={18} />
                </span>
                <span className="pop-item-text">
                  <span className="pop-item-label">{PERMISSION_LABELS[m]?.label ?? m}</span>
                  <span className="pop-item-hint">{permissionHint(m, canAsk)}</span>
                </span>
                {m === value && (
                  <span className="pop-item-check">
                    <IconCheck size={16} />
                  </span>
                )}
              </button>
            );
          })}
        </>
      )}
    </Popover>
  );
}

interface ModelProps {
  onDiagnose?: () => void;
  models: ModelOption[];
  model: string | null;
  efforts: string[];
  effort: string | null;
  speed?: string | null;
  onSpeed?: (speed: string | null) => void;
  onModel: (model: string | null) => void;
  onEffort: (effort: string | null) => void;
  /** Refreshes live model metadata when the menu is actually opened. */
  onOpen?: () => void;
  /** Opens the panel when this number changes. */
  openSignal?: number;
  /**
   * Whose setting this is, where it is not the thread's own. The trigger is then a quiet mark that
   * only spells the choice out once one has been made, and it is there before the models are known.
   */
  quietFor?: string;
}

/** The id some agents use for "whatever you would pick yourself". */
const AGENT_DEFAULT = 'default';

/**
 * What the agent reports about a model. For null (no override) that is the agent's default: an
 * entry with the id "default" if there is one (Claude), else the entry flagged as default (pi).
 */
export function resolveModel(models: ModelOption[], id: string | null): ModelOption | undefined {
  if (id !== null) return models.find((m) => m.id === id);
  return models.find((m) => m.id === AGENT_DEFAULT) ?? models.find((m) => m.isDefault);
}

/** What the default currently points at, when the agent says. */
function defaultTarget(models: ModelOption[]): string | undefined {
  const alias = models.find((m) => m.id === AGENT_DEFAULT);
  if (alias) return alias.description || undefined;
  return models.find((m) => m.isDefault)?.label;
}

/** The name shown for a model id. With no override, the model the agent's default points at. */
export function modelName(models: ModelOption[], id: string | null): string {
  if (id === null) return defaultTarget(models) ?? t('pickers.defaultModel');
  return models.find((m) => m.id === id)?.label ?? id;
}

/** Current load for the selected model, including an agent-owned default alias. */
export function modelLoadPercent(models: ModelOption[], id: string | null): number | undefined {
  return resolveModel(models, id)?.loadPercent;
}

export function modelLoadLabel(percent: number): string {
  return t('pickers.modelLoad', percent);
}

/** Why pictures cannot be sent with this model, or null when they can. */
export function imagesBlocked(models: ModelOption[], id: string | null): string | null {
  const option = resolveModel(models, id);
  return option?.images === false && !option.imagesVia ? t('pickers.noImages', option.label) : null;
}

/** How pictures reach a model that cannot read them itself, or null when it reads them (or cannot get them at all). */
export function imagesNote(models: ModelOption[], id: string | null): string | null {
  const option = resolveModel(models, id);
  return option?.images === false && option.imagesVia ? t('pickers.imagesVia', option.label, option.imagesVia) : null;
}

/**
 * Effort levels the given model accepts. `model` null is the agent default.
 * Falls back to the agent-wide list when the agent does not report per-model levels.
 */
export function effortsForModel(models: ModelOption[], model: string | null, fallback: string[]): string[] {
  return resolveModel(models, model)?.efforts ?? fallback;
}

/**
 * Rows for the model list. If the agent lists its own default, that row stands for "no override"
 * (stored as null), so there is never a second, duplicate default row.
 */
export function modelRows(models: ModelOption[]): Array<{ value: string | null; label: string; hint?: string }> {
  const rest = models.filter((m) => m.id !== AGENT_DEFAULT).map((m) => ({ value: m.id, label: m.label, hint: [m.description, m.speeds?.some(isFastSpeed) ? t('pickers.canAccelerate') : ''].filter(Boolean).join(' · ') || undefined }));
  const target = defaultTarget(models);
  const first = target
    ? { value: null, label: t('pickers.default'), hint: t('pickers.currently', target) }
    : { value: null, label: t('pickers.defaultModel'), hint: t('pickers.agentModel') };
  return [first, ...rest];
}

export function speedsForModel(models: ModelOption[], model: string | null): NonNullable<ModelOption['speeds']> {
  return resolveModel(models, model)?.speeds ?? [];
}

/** Model and thinking effort in a compact card; the model list is a second view. */
export function ModelPicker(props: ModelProps) {
  const { models, model, efforts, effort, openSignal, quietFor } = props;
  const narrow = useIsNarrow();
  if (models.length === 0 && efforts.length === 0 && quietFor === undefined) {
    return (
      <Popover
        label={t('statusPanel.model')}
        openSignal={openSignal}
        onOpen={props.onOpen}
        align="end"
        triggerClassName="tool-btn model-control-trigger"
        triggerLabel={t('pickers.modelNoneLabel')}
        triggerTitle={t('pickers.noModels')}
        trigger={<><span className="model-trigger-name">{t('statusPanel.model')}</span><IconChevron size={13} open /></>}
      >
        {close => <><div className="pop-title">{t('pickers.noModels')}</div><p className="member-model-empty">{t('pickers.noModelsHint')}</p>{props.onDiagnose && <button type="button" role="menuitem" className="pop-item compact" onClick={() => { close(); props.onDiagnose?.(); }}><span className="pop-item-label">{t('pickers.diagnose')}</span></button>}</>}
      </Popover>
    );
  }
  const levels = effortsForModel(models, model, efforts);
  const shownEffort = effort && levels.includes(effort) ? effort : null;
  if (quietFor !== undefined) {
    const chosen = [model ? modelName(models, model) : '', shownEffort ? (EFFORT_LABELS[shownEffort] ?? shownEffort) : ''].filter(Boolean).join(' · ');
    // Named even when nothing was chosen: the default model's name, quieter than a choice.
    const shown = chosen || modelName(models, null);
    return (
      <Popover
        label={t('pickers.modelAndEffort')}
        onOpen={props.onOpen}
        align="end"
        panelClassName="model-control-panel"
        triggerClassName={`member-model${chosen ? ' set' : ''}`}
        triggerLabel={t('pickers.modelAndEffortFor', quietFor)}
        triggerTitle={chosen || t('pickers.modelAndEffortDefault')}
        // The main model button's dial, filled to the effort: the lightning bolt means fast mode.
        trigger={<><IconEffortGauge size={14} level={shownEffort ? (levels.indexOf(shownEffort) + 1) / levels.length : null} /><span>{shown}</span></>}
      >
        {() => (models.length === 0 && efforts.length === 0 ? <p className="member-model-empty">{t('pickers.nothingToChoose')}</p> : <ModelControl {...props} />)}
      </Popover>
    );
  }
  const summary = [modelName(models, model), shownEffort ? (EFFORT_LABELS[shownEffort] ?? shownEffort) : t('pickers.defaultEffort')].filter(Boolean).join(' · ');
  return (
    <Popover
      label={t('pickers.modelAndEffort')}
      openSignal={openSignal}
      onOpen={props.onOpen}
      align="end"
      panelClassName="model-control-panel"
      triggerClassName={`tool-btn model-control-trigger${narrow && levels.length > 0 ? ' dial-only' : ''}`}
      triggerTitle={summary}
      triggerLabel={t('pickers.modelAndEffortIs', summary)}
      trigger={<>
        {/* A phone has room for the dial alone; the card it opens names the model. */}
        {!(narrow && levels.length > 0) && <span className="model-trigger-name">{modelName(models, model)}</span>}
        {levels.length > 0 && <span className="model-trigger-gauge"><IconEffortGauge size={narrow ? 20 : 17} level={shownEffort ? (levels.indexOf(shownEffort) + 1) / levels.length : null} /></span>}
        {!narrow && <IconChevron size={13} open />}
      </>}
    >
      {() => <ModelControl {...props} />}
    </Popover>
  );
}

function ModelControl({ models, model, efforts, effort, onModel, onEffort, speed, onSpeed }: ModelProps) {
  const levels = effortsForModel(models, model, efforts);
  const loadPercent = modelLoadPercent(models, model);
  const speeds = onSpeed ? speedsForModel(models, model) : [];
  const fastSpeed = speeds.find(isFastSpeed);
  const standardSpeed = speeds.find(s => s.id === 'default');
  const canAccelerate = Boolean(fastSpeed && standardSpeed);
  const accelerated = speeds.some(s => s.id === speed && isFastSpeed(s));
  const [showModels, setShowModels] = useState(levels.length === 0 && !canAccelerate);
  const [preview, setPreview] = useState<string | null | undefined>(undefined);
  const selected = effort && levels.includes(effort) ? effort : null;
  const shown = preview === undefined ? selected : preview;
  return showModels ? (
    <>
      <div className="model-list-head">
        {(levels.length > 0 || canAccelerate) && <button type="button" className="icon-btn model-back" aria-label={t('pickers.backToEffort')} onClick={() => setShowModels(false)}><IconChevron size={16} /></button>}
        <span>{t('pickers.chooseModel')}</span>
      </div>
      <div className="pop-scroll">
        {modelRows(models).map((row) => (
          <button key={row.value ?? 'default'} type="button" role="menuitemradio" aria-checked={row.value === (model === AGENT_DEFAULT ? null : model)} className="pop-item compact"
            onClick={() => { onModel(row.value); setPreview(undefined); setShowModels(false); }}>
            <span className="pop-item-text"><span className="pop-item-label">{row.label}</span><span className="pop-item-hint">{row.hint}</span></span>
            {row.value === (model === AGENT_DEFAULT ? null : model) && <span className="pop-item-check"><IconCheck size={16} /></span>}
          </button>
        ))}
      </div>
    </>
  ) : (
    <div className="effort-card">
      <div className="effort-card-head">
        {fastSpeed && standardSpeed ? <button
          type="button"
          className="model-speed-toggle"
          aria-label={accelerated ? t('pickers.accelOff') : t('pickers.accelOn')}
          aria-pressed={accelerated}
          title={accelerated ? t('pickers.accelOffHint') : t('pickers.accelOnHint', speed == null)}
          onClick={() => onSpeed?.(accelerated ? standardSpeed.id : fastSpeed.id)}
        ><IconBolt size={19} /></button> : <span aria-hidden="true" />}
        <div className="effort-card-heading">
          <div className="effort-value" aria-live="polite">{levels.length ? (shown ? (EFFORT_LABELS[shown] ?? shown) : t('pickers.default')) : t('pickers.noEffort')}</div>
          <button type="button" className="effort-model" disabled={models.length === 0} onClick={() => setShowModels(true)} aria-label={t('pickers.switchModel', [modelName(models, model), loadPercent === undefined ? '' : modelLoadLabel(loadPercent)].filter(Boolean).join(' · '))}>
            <span className="effort-model-name">{modelName(models, model)}</span>{loadPercent !== undefined && <ModelLoad percent={loadPercent} />}<IconChevron size={13} />
          </button>
        </div>
        <button type="button" className="icon-btn effort-reset" aria-label={t('pickers.resetEffort')} title={t('pickers.resetEffort')} disabled={selected === null} onClick={() => onEffort(null)}><IconRefresh size={18} /></button>
      </div>
      {levels.length > 0 ? <EffortSlider key={`${model ?? 'default'}:${levels.join(',')}`} levels={levels} value={selected} onChange={onEffort} onPreview={setPreview} />
        : <div className="pop-note">{t('pickers.modelNoEffort')}</div>}
    </div>
  );
}

/** A short live hint beside a model name; normal load stays quiet, overload is called out. */
function ModelLoad({ percent }: { percent: number }) {
  const label = modelLoadLabel(percent);
  return <span className={`model-inline-load${percent >= 100 ? ' high' : ''}`} title={label} aria-hidden="true">{label}</span>;
}

/** Which agent a new thread uses. Existing threads show the agent as plain text instead. */
/** A CLI on PATH that looks like another copy of a known agent, as the picker offers it. */
export interface DiscoveredAgent {
  id: string;
  adapter: string;
  command: string;
}

/** An account of a multi-account agent already signed in on this host, as the picker offers it. */
export interface DiscoveredAccount {
  adapter: string;
  email: string;
  env: Record<string, string>;
  id: string;
  label: string;
}

interface AgentProps {
  api: Api;
  teams?: import('../api').TeamSummary[];
  agents: AgentInfo[];
  value: string;
  onChange: (id: string) => void;
  /** Signed-in accounts found on this host; picking one links it as a new instance. */
  accounts?: DiscoveredAccount[];
  onAddAccount?: (account: DiscoveredAccount) => Promise<void>;
}

/**
 * Agents grouped the way the picker shows them: instances of one adapter belong together, and a
 * group with several of them folds into one row that opens to the accounts. An agent whose
 * adapter is not told stands as a group of its own, the way it always listed.
 */
export function agentGroups(agents: AgentInfo[]): AgentInfo[][] {
  const groups = new Map<string, AgentInfo[]>();
  for (const a of agents) {
    if (a.hidden) continue;
    const key = a.adapter ?? `_${a.id}`;
    groups.set(key, [...(groups.get(key) ?? []), a]);
  }
  return [...groups.values()];
}

/** One group's name: the shortest label in it, which for a pair like "Claude Code" and
 * "Claude Code 2" is the adapter's own name. */
function groupLabel(group: AgentInfo[]): string {
  return group.reduce((short, a) => (a.label.length < short.length ? a.label : short), group[0].label);
}

/**
 * One agent in the picker.
 */
function AgentRow({ api, agent, marks, checked, hint, onPick }: {
  api: Api; agent: AgentInfo; marks: boolean; checked: boolean; hint?: string; onPick: () => void;
}) {
  return (
    <div className={`agent-picker-row${marks ? ' has-mark' : ''}${agent.available ? '' : ' disabled'}`} role="none">
      <button
        type="button"
        role="menuitemradio"
        aria-checked={checked}
        className="pop-item compact agent-picker-item"
        disabled={!agent.available}
        onClick={onPick}
      >
        {marks && <span className="pop-item-icon">{agent.icon && <AgentMark path={agent.icon} size={16} />}</span>}
        <span className="pop-item-text">
          <span className="pop-item-label">{agent.label}</span>
          {hint ? (
            <span className="pop-item-hint">{hint}</span>
          ) : (
            !agent.available && <span className="pop-item-hint">{t('pickers.notFound')}</span>
          )}
        </span>
        <span className="pop-item-check">{checked && <IconCheck size={16} />}</span>
      </button>
    </div>
  );
}

export function AgentPicker({ api, agents, value, onChange, accounts = [], onAddAccount, teams = [] }: AgentProps) {
  const current = agents.find((a) => a.id === value && !a.hidden);
  const team = teams.find(t => `team:${t.id}` === value);
  const [adding, setAdding] = useState<string | null>(null);
  /** The group whose accounts are on screen, drilled in from the list of agents. */
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const groups = useMemo(() => agentGroups(agents), [agents]);
  const drilled = groups.find((g) => g.some((a) => a.id === openGroup)) ?? null;
  // The marks the thread list shows in place of names are introduced here, next to the names.
  const marks = groups.some((group) => group.some((a) => a.icon));
  return (
    <Popover
      label="Agent"
      panelClassName="agent-picker-panel"
      triggerClassName={`context-btn agent-btn${current?.icon ? ' has-mark' : ''}`}
      trigger={
        <>
          {current?.icon && <AgentMark path={current.icon} size={14} />}
          <span>{team?.name ?? current?.label ?? t('pickers.whichAgent')}</span>
          <IconChevron size={12} open />
        </>
      }
    >
      {(close) =>
        drilled ? (
          <>
            <button type="button" className="pop-back" onClick={() => setOpenGroup(null)}>
              <IconChevron size={12} /> {groupLabel(drilled)}
            </button>
            {drilled.map((a) => (
              <AgentRow key={a.id} api={api} agent={a} marks={marks} checked={a.id === value} hint={a.account ?? undefined}
                onPick={() => {
                  onChange(a.id);
                  setOpenGroup(null);
                  close();
                }} />
            ))}
          </>
        ) : (
          <>
          {teams.length > 0 && <><div className="pop-title">{t('teams.title')}</div>{teams.map(team => <button key={team.id} type="button" className="pop-item compact" role="menuitemradio" aria-checked={value === `team:${team.id}`} onClick={() => { onChange(`team:${team.id}`); close(); }}>{team.name}{value === `team:${team.id}` && <IconCheck size={16} />}</button>)}</>}
          <div className="pop-title">{t('pickers.whichAgent')}</div>
          {groups.map((group) => {
            const solo = group.length === 1 ? group[0] : null;
            if (solo)
              return (
                <AgentRow key={solo.id} api={api} agent={solo} marks={marks} checked={solo.id === value}
                  onPick={() => {
                    onChange(solo.id);
                    close();
                  }} />
              );
            // Several accounts of one agent: the row stands for the agent, says which account is
            // in use, and opens to the choice.
            const active = group.find((a) => a.id === value) ?? group[0];
            return (
              <button
                key={active.id}
                type="button"
                className="pop-item compact"
                onClick={() => setOpenGroup(active.id)}
              >
                {marks && <span className="pop-item-icon">{active.icon && <AgentMark path={active.icon} size={16} />}</span>}
                <span className="pop-item-text">
                  <span className="pop-item-label">{groupLabel(group)}</span>
                  <span className="pop-item-hint">{active.account ?? active.label}</span>
                </span>
                <span className="pop-item-check">
                  <IconChevron size={13} />
                </span>
              </button>
            );
          })}
          {onAddAccount && accounts.length > 0 && (
            <>
              <div className="pop-sep">{t('pickers.accounts')}</div>
              {accounts.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className="pop-item compact"
                  disabled={adding !== null}
                  onClick={async () => {
                    setAdding(a.id);
                    try {
                      await onAddAccount(a);
                      onChange(a.id);
                      close();
                    } finally {
                      setAdding(null);
                    }
                  }}
                >
                  <span className="pop-item-text">
                    <span className="pop-item-label">{a.email}</span>
                    <span className="pop-item-hint">{t('pickers.addAccount', a.label)}</span>
                  </span>
                  <span className="pop-item-check">
                    {adding === a.id ? <IconSparkle size={13} /> : <IconPlus size={14} />}
                  </span>
                </button>
              ))}
            </>
          )}
        </>
      )}
    </Popover>
  );
}

/** How full the agent's context window is: a small ring that opens to the numbers. */
export function UsageMeter({ usage, onCompact, running, compactDisabled, compactQueued }: {
  usage: Usage | null;
  onCompact?: () => Promise<void>;
  running?: boolean;
  compactDisabled?: boolean;
  compactQueued?: boolean;
}) {
  const [compacting, setCompacting] = useState(false);
  const { percent, high, detail } = usage ? usageSummary(usage)
    : { percent: 0, high: false, detail: t('pickers.contextUnknown') };
  // A ring of radius 6 has a circumference of about 37.7; the dash shows the used share of it.
  const circumference = 2 * Math.PI * 6;
  return (
    <Popover
      label={t('pickers.contextUsage')}
      align="end"
      triggerClassName={`usage${high ? ' high' : ''}`}
      trigger={
        <>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <circle className="usage-track" cx="8" cy="8" r="6" fill="none" strokeWidth="2.2" />
            <circle
              className="usage-fill"
              cx="8"
              cy="8"
              r="6"
              fill="none"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeDasharray={`${(percent / 100) * circumference} ${circumference}`}
              transform="rotate(-90 8 8)"
            />
          </svg>
          <span>{usage ? `${percent}%` : t('statusPanel.context')}</span>
        </>
      }
    >
      {(close) => (
        <>
          <div className="pop-title">{t('statusPanel.context')}</div>
          <div className="pop-note">
            {detail}
            {high && t('pickers.contextHigh')}
          </div>
          {onCompact && <button type="button" className="pop-item compact context-compact" disabled={compacting || compactDisabled || compactQueued}
            onClick={async () => {
              setCompacting(true);
              try { await onCompact(); close(); } catch { /* The caller displays the error. */ }
              finally { setCompacting(false); }
            }}>
            <span className="pop-item-icon"><IconCompress size={17} /></span>
            <span className="pop-item-text"><span className="pop-item-label">{compacting ? t('pickers.submitting') : compactQueued ? t('pickers.compactQueued') : t('pickers.compact')}</span>
              <span className="pop-item-hint">{compactDisabled ? t('pickers.queueFull') : running ? t('pickers.compactAfter') : t('pickers.compactHint')}</span>
            </span>
          </button>}
        </>
      )}
    </Popover>
  );
}

interface WorkspaceProps {
  /** The branch the directory is on, or null when HEAD is detached. */
  branch: string | null;
  /** Files there that differ from the last commit; they come along into a worktree. */
  changes: number;
  worktree: boolean;
  onChange: (worktree: boolean) => void;
}

/**
 * Where a new thread works: in the directory itself, or in a new git worktree of it under
 * `.worktrees/`. Offered only when the directory is in a repository, so it is not there otherwise.
 */
export function WorkspacePicker({ branch, changes, worktree, onChange }: WorkspaceProps) {
  const rows = [
    { value: false, icon: <IconFolder size={16} />, label: t('pickers.currentDir'), hint: t('pickers.currentDirHint') },
    {
      value: true,
      icon: <IconWorktree size={16} />,
      label: t('pickers.newWorktree'),
      hint: t('pickers.newWorktreeHint', branch, changes),
    },
  ];
  return (
    <Popover
      label={t('pickers.where')}
      triggerClassName="context-btn workspace-btn"
      triggerTitle={worktree ? t('pickers.startWorktree') : t('pickers.startDir')}
      trigger={
        <>
          {worktree ? <IconWorktree size={14} /> : <IconFolder size={14} />}
          <span>{worktree ? t('pickers.newWorktree') : t('pickers.currentDir')}</span>
          <IconChevron size={12} open />
        </>
      }
    >
      {(close) =>
        rows.map((row) => (
          <button
            key={String(row.value)}
            type="button"
            role="menuitemradio"
            aria-checked={worktree === row.value}
            className="pop-item"
            onClick={() => {
              onChange(row.value);
              close();
            }}
          >
            <span className="pop-item-icon">{row.icon}</span>
            <span className="pop-item-text">
              <span className="pop-item-label">{row.label}</span>
              <span className="pop-item-hint">{row.hint}</span>
            </span>
            {worktree === row.value && (
              <span className="pop-item-check">
                <IconCheck size={16} />
              </span>
            )}
          </button>
        ))
      }
    </Popover>
  );
}
