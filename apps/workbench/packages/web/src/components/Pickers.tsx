import { useEffect, useMemo, useState } from 'react';
import { isFastSpeed, type AgentInfo, type Api, type ModelOption } from '../api';
import { usageSummary, type Usage, type UsageSample } from '../timeline';
import { compactTokens, ContextTotals, percentLabel } from './SessionStats';
import { EFFORT_LABELS, PERMISSION_LABELS, PERMISSION_ORDER, permissionHint } from '../permissions';
import { AgentMark, ContextRing, IconBolt, IconEffortGauge, IconCompress, IconList, IconChevronRight, IconRefresh, IconCheck, IconChevron, IconFolder, IconHand, IconPlus, IconShield, IconShieldAlert, IconSparkle, IconWorktree } from './icons';
import { Popover } from './Popover';
import { EffortSlider } from './EffortSlider';
import { AgentQuota } from './AgentQuota';
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
      triggerClassName={quietFor === undefined ? `tool-btn icon-only composer-permission${value === 'full_access' ? ' warn' : ''}` : 'member-model set'}
      triggerLabel={quietFor === undefined ? t('pickers.permissionIs', PERMISSION_LABELS[value]?.label ?? value) : t('pickers.permissionFor', quietFor)}
      triggerTitle={permissionHint(value, canAsk)}
      trigger={
        <>
          <Icon size={16} />
          {quietFor !== undefined && <span>{PERMISSION_LABELS[value]?.short ?? value}</span>}
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
  catalogStatus?: 'loading' | 'ready' | 'error';
  onRetryCatalog?: () => void;
  preferenceStatus?: 'loading' | 'ready' | 'error';
  onRetryPreference?: () => void;
  onDiagnose?: () => void;
  /** False when the CLI does not expose a model catalog at all, rather than one that failed to load. */
  catalog?: boolean;
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

/** Metadata, not the agent's name, decides whether a model has a queue status row. */
function hasModelQueue(option: ModelOption | undefined): boolean {
  return option?.queuePosition !== undefined || option?.queueSize !== undefined;
}

/** A request position is a recent observation, never a model-wide queue count. */
export function modelQueueLabel(option: ModelOption | undefined, now = Date.now()): string {
  if (!option) return '';
  if (option.queuePosition === undefined) return option.queueSize !== undefined ? t('pickers.modelQueue', option.queueSize) : '';
  const at = option.queueObservedAt;
  if (at === undefined || !Number.isFinite(at) || at <= 0 || at > now) return t('pickers.modelQueueUnknown');
  if (now - at > 30 * 60_000) return t('pickers.modelQueueStale');
  if (option.queuePosition === null || !Number.isSafeInteger(option.queuePosition) || option.queuePosition < 0) return t('pickers.modelQueueUnknown');
  const minutes = Math.floor((now - at) / 60_000);
  const age = minutes < 1 ? t('time.justNow') : t('time.minutesAgo', minutes);
  return t('pickers.modelQueueObserved', option.queuePosition, age);
}

/** A missing queue is not an empty queue; older catalogs keep their existing load-only display. */
export function modelStatusLabel(option: ModelOption | undefined, now = Date.now()): string {
  if (!option) return '';
  return [
    option.loadPercent !== undefined ? modelLoadLabel(option.loadPercent) : option.loadError ? t('pickers.modelLoadFailed') : hasModelQueue(option) ? t('pickers.modelLoadUnknown') : '',
    modelQueueLabel(option, now),
  ].filter(Boolean).join(' · ');
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
export function modelRows(models: ModelOption[], now = Date.now()): Array<{ value: string | null; label: string; hint?: string }> {
  const rest = models.filter((m) => m.id !== AGENT_DEFAULT).map((m) => ({ value: m.id, label: m.label, hint: [hasModelQueue(m) ? modelStatusLabel(m, now) : '', m.description, m.speeds?.some(isFastSpeed) ? t('pickers.canAccelerate') : ''].filter(Boolean).join(' · ') || undefined }));
  const target = defaultTarget(models);
  const defaultModel = resolveModel(models, null);
  const status = hasModelQueue(defaultModel) ? modelStatusLabel(defaultModel, now) : '';
  const first = target
    ? { value: null, label: status ? `${t('pickers.default')} · ${target}` : t('pickers.default'), hint: status || t('pickers.currently', target) }
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
  if (props.catalog === false) return null;
  const levels = effortsForModel(models, model, efforts);
  const shownEffort = effort && levels.includes(effort) ? effort : null;
  if (props.catalogStatus && props.catalogStatus !== 'ready') {
    const failed = props.catalogStatus === 'error';
    const label = failed ? t('pickers.modelsLoadFailed') : t('common.loading');
    return <Popover label={t('pickers.modelAndEffort')} align="end" panelClassName="model-control-panel"
      triggerClassName={`tool-btn model-control-trigger${narrow && levels.length > 0 ? ' dial-only' : ''}`} triggerLabel={label} triggerTitle={label}
      trigger={<>
        {!(narrow && levels.length > 0) && <span className="model-trigger-name">{model ? modelName(models, model) : t('statusPanel.model')}</span>}
        {levels.length > 0 && <span className="model-trigger-gauge"><IconEffortGauge size={narrow ? 20 : 17} level={shownEffort ? (levels.indexOf(shownEffort) + 1) / levels.length : null} /></span>}
        {!narrow && <IconChevron size={13} open />}
      </>}>
      {() => <><div className="pop-title">{label}</div>{failed && <button type="button" className="pop-item compact" onClick={props.onRetryCatalog}>{t('common.retry')}</button>}</>}
    </Popover>;
  }

  if (props.preferenceStatus && props.preferenceStatus !== 'ready') {
    const failed = props.preferenceStatus === 'error';
    const label = failed ? t('pickers.preferenceFailed') : t('common.loading');
    return <Popover label={t('pickers.modelAndEffort')} align="end" panelClassName="model-control-panel"
      triggerClassName="tool-btn model-control-trigger" triggerLabel={label} triggerTitle={label}
      trigger={<><span className="model-trigger-name">{t('statusPanel.model')}</span><IconEffortGauge size={narrow ? 20 : 17} level={null} /></>}>
      {() => <><div className="pop-title">{label}</div>{failed && <button type="button" className="pop-item compact" onClick={props.onRetryPreference}>{t('common.retry')}</button>}</>}
    </Popover>;
  }

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
  const selectedModel = resolveModel(models, model);
  const loadPercent = selectedModel?.loadPercent;
  const queueStatus = hasModelQueue(selectedModel);
  const [clock, setClock] = useState(Date.now);
  const hasObservations = models.some(m => m.queueObservedAt !== undefined);
  useEffect(() => {
    if (!hasObservations) return;
    // Only advance observation age while this card is open; no provider polling or inference.
    const timer = setInterval(() => setClock(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, [hasObservations]);
  const now = Math.max(clock, Date.now());
  const queueLabel = modelQueueLabel(selectedModel, now);
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
        {modelRows(models, now).map((row) => (
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
          <button type="button" className="effort-model" disabled={models.length === 0} onClick={() => setShowModels(true)} aria-label={t('pickers.switchModel', [modelName(models, model), modelStatusLabel(selectedModel, now)].filter(Boolean).join(' · '))}>
            <span className="effort-model-name">{modelName(models, model)}</span>
            {!queueStatus && loadPercent !== undefined && <ModelLoad percent={loadPercent} />}
            <IconChevron size={13} />
          </button>
          {/* Keep the model's identity readable; queue metadata lives in one short secondary row. */}
          {queueStatus && <div className="model-status-line" aria-hidden="true">
            {loadPercent !== undefined ? <ModelLoad percent={loadPercent} /> : <span className="model-inline-load">{selectedModel?.loadError ? t('pickers.modelLoadFailed') : t('pickers.modelLoadUnknown')}</span>}
            <span className="model-inline-load model-inline-queue" title={queueLabel}>{queueLabel}</span>
          </div>}
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
 * One agent or account in the picker. The quota sits beside the select button, not inside it,
 * so opening the quota details never picks the account.
 */
function AgentRow({ api, agent, marks, checked, hint, onPick }: {
  api: Api; agent: AgentInfo; marks: boolean; checked: boolean; hint?: string; onPick: () => void;
}) {
  const quota = agent.available && agent.capabilities.quota;
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
      {quota && <AgentQuota api={api} agent={agent.id} />}
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

/**
 * The composition of the latest model request, as the agent itself reported it — no category is
 * estimated. Only fields the sample actually carries become cells; a reported zero is a real
 * zero, so zero-value parts stay off the row.
 */
function ContextSample({ sample }: { sample: UsageSample }) {
  const parts: Array<{ key: string; label: string; value: number; seg: string }> = [];
  if (sample.cacheRead) parts.push({ key: 'read', label: t('stats.cacheRead'), value: sample.cacheRead, seg: 's-read' });
  if (sample.cacheWrite) parts.push({ key: 'write', label: t('stats.cacheWrite'), value: sample.cacheWrite, seg: 's-write' });
  const uncached = sample.input != null && sample.cacheRead != null && sample.cacheWrite != null
    ? sample.input - sample.cacheRead - sample.cacheWrite : undefined;
  if (uncached != null && uncached > 0) parts.push({ key: 'uncached', label: t('stats.uncached'), value: uncached, seg: 's-uncached' });
  if (sample.output) parts.push({ key: 'output', label: t('stats.output'), value: sample.output, seg: 's-output' });
  if (parts.length === 0) return null;
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  // Three parts read as one row of tiles; four would squeeze the labels at that width, so they
  // pair into two rows instead.
  const columns = parts.length >= 4 ? 2 : parts.length;
  return <>
    <div className="usage-request-label">{sample.model ? t('pickers.contextLastRequest', sample.model) : t('pickers.contextLastRequestShort')}</div>
    <div className="usage-cells" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {parts.map((part) => (
        <div key={part.key} className="usage-cell" title={part.label}>
          <div className="usage-cell-value">
            <span className={`usage-swatch ${part.seg}`} />
            <span>{compactTokens(part.value)}</span>
            <span className="usage-cell-share">{percentLabel(part.value / total)}</span>
          </div>
          <div className="usage-cell-label">{part.label}</div>
        </div>
      ))}
    </div>
  </>;
}

/** How full the agent's context window is: a small ring that opens to the numbers. The panel keeps
 *  the summary first — percentage, window bar, the latest request's composition — and holds the
 *  session totals and the agent's own breakdown behind "Details", one deliberate step away. */
export function UsageMeter({ usage, sample, api, threadId, onCompact, onContext, running, compactDisabled, compactQueued }: {
  usage: Usage | null;
  /** The latest per-request token sample, when the agent reports any. */
  sample: UsageSample | null;
  /** Reads the session totals when the panel opens; null in the built-in demo. */
  api: Api | null;
  threadId: string;
  onCompact?: () => Promise<void>;
  /** Sends the agent's own context breakdown command, when its command list has one. */
  onContext?: () => Promise<void>;
  running?: boolean;
  compactDisabled?: boolean;
  compactQueued?: boolean;
}) {
  const [compacting, setCompacting] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const { percent, high } = usage ? usageSummary(usage) : { percent: null, high: false };
  const label = percent === null ? t('pickers.contextUsageUnknown') : t('pickers.contextUsagePercent', percent);
  const cost = usage?.cost && usage.cost.amount > 0
    ? ` · ${usage.cost.currency === 'USD' ? '$' : `${usage.cost.currency} `}${usage.cost.amount.toFixed(2)}` : '';
  const compactLabel = compactQueued ? t('pickers.compactQueued')
    : compactDisabled ? t('pickers.queueFull')
      : compacting ? t('pickers.submitting')
        : running ? t('pickers.compactNext') : t('pickers.compact');
  const details = (close: () => void) => <>
    {usage ? (
      <>
        <div className="usage-head">
          <span className="context-percent">{percent}%</span>
          <span className="usage-used">{compactTokens(usage.used)} / {compactTokens(usage.size)}{cost}</span>
        </div>
        <div className="usage-panel-bar">
          <div className="usage-bar" role="presentation"><div className="usage-bar-fill" style={{ width: `${percent}%` }} /></div>
        </div>
        {high && <div className="usage-high-note">{t('pickers.contextHighShort')}</div>}
      </>
    ) : (
      <div className="usage-head unknown">
        <span className="context-percent">?</span>
        <span className="usage-used">{t('pickers.contextUnknown')}</span>
      </div>
    )}
    {sample && <ContextSample sample={sample} />}
    {expanded && (
      <div className="usage-more">
        <ContextTotals api={api} threadId={threadId} active={running} label={t('pickers.contextTotals')} />
        {onContext && (
          <button type="button" className="usage-cmd" onClick={() => { void onContext(); close(); }}>
            <IconList size={15} />
            <span>{t('pickers.contextDetail')}</span>
            <IconChevronRight size={14} />
          </button>
        )}
      </div>
    )}
    {(onCompact || onContext) && (
      <div className="usage-foot">
        {onCompact && <button type="button" className="usage-act context-compact" disabled={compacting || compactDisabled || compactQueued}
          onClick={async () => {
            setCompacting(true);
            try { await onCompact(); close(); } catch { /* The caller displays the error. */ }
            finally { setCompacting(false); }
          }}>
          <IconCompress size={15} />{compactLabel}
        </button>}
        <span className="usage-rule" />
        <button type="button" className="usage-act" aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}>
          {t('pickers.contextDetails')}<IconChevron size={13} open={expanded} />
        </button>
      </div>
    )}
  </>;
  return (
    <Popover
      label={t('pickers.contextUsage')}
      align="end"
      panelClassName={`context-usage-panel${high ? ' high' : ''}`}
      triggerClassName={`usage${high ? ' high' : ''}${percent === null ? ' unknown' : ''}`}
      triggerLabel={label}
      triggerTitle={label + (high ? t('pickers.contextHigh') : '')}
      trigger={<ContextRing percent={percent} size={20} />}
    >
      {details}
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
