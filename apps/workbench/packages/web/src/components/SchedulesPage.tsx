import { useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from 'react';
import type { AgentInfo, Api, Schedule, ScheduleRun, Thread } from '../api';
import { PERMISSION_LABELS, PERMISSION_ORDER, startingPermission } from '../permissions';
import { folderName } from '../paths';
import { Dialog } from './Dialog';
import { SettingsSection, usePageDraft } from './PageNavigation';
import { Menu, type MenuEntry } from './Menu';
import { Popover } from './Popover';
import { AgentMark, IconBack, IconChevron, IconClock, IconClose, IconFolder, IconMore, IconPlay, IconPlus } from './icons';
import { formatDate, formatList, t } from '../i18n';

interface Props {
  api: Api;
  path: string;
  onNavigate: (path: string) => void;
  /** The whole list, owned by App so it survives page switches. */
  schedules: Schedule[];
  agents: AgentInfo[];
  /** Roots for picking a directory: offered alongside the directories threads already use. */
  workdirs: string[];
  /** Bumped when the daemon says something changed, so the page reloads. */
  refreshKey: number;
  onReload: () => void;
  /** Opens the thread a run happened in, taking the page's place. */
  onOpenThread: (id: string) => void;
  onError: (e: unknown) => void;
}

/** How a schedule's frequency is picked: presets write the cron, custom writes it by hand. */
type Preset = 'minutes' | 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';

const PRESET_LABELS: Record<Exclude<Preset, 'custom'>, string> = {
  minutes: t('sched.preset.minutes'),
  hourly: t('sched.preset.hourly'),
  daily: t('sched.preset.daily'),
  weekdays: t('sched.preset.weekdays'),
  weekly: t('sched.preset.weekly'),
  monthly: t('sched.preset.monthly'),
};

/** Sunday first, as cron counts them. 1 January 2023 was a Sunday. */
const WEEKDAYS = Array.from({ length: 7 }, (_, d) => formatDate(new Date(2023, 0, 1 + d), { weekday: 'short' }));

const pad = (n: number) => String(n).padStart(2, '0');

const isCommit = (e: ReactKeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => e.key === 'Enter' && !e.nativeEvent.isComposing;

/** Reads "HH:MM"; returns null when it is not that. */
function parseTime(value: string): [number, number] | null {
  const m = value.trim().match(/^(\d{1,2}):(\d{1,2})$/);
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return [h, min];
}

function cronFromPreset(preset: Preset, opts: { time: string; weekday: number; day: string; interval: string }): string | null {
  const at = parseTime(opts.time);
  if (!at) return null;
  const [h, m] = at;
  switch (preset) {
    case 'minutes': {
      const step = Number(opts.interval);
      return step >= 1 && step <= 59 ? `*/${step} * * * *` : null;
    }
    case 'hourly':
      return `${m} * * * *`;
    case 'daily':
      return `${m} ${h} * * *`;
    case 'weekdays':
      return `${m} ${h} * * 1-5`;
    case 'weekly':
      return `${m} ${h} * * ${opts.weekday}`;
    case 'monthly': {
      const day = Number(opts.day);
      return day >= 1 && day <= 31 ? `${m} ${h} ${day} * *` : null;
    }
    default:
      return null;
  }
}

/** Which preset a stored expression is, so the form opens the way it was written. */
function presetFromCron(cron: string): { preset: Preset; time: string; weekday: number; day: string; interval: string } {
  const parts = cron.trim().split(/\s+/);
  const base = { preset: 'custom' as Preset, time: '09:00', weekday: 1, day: '1', interval: '30' };
  if (parts.length !== 5) return base;
  const [m, h, dom, mon, dow] = parts;
  if (mon !== '*' || dom !== '*' || m === '*') return base;
  const time = `${pad(Number(h))}:${pad(Number(m))}`;
  if (/^\*\/\d+$/.test(m) && h === '*' && dow === '*') return { ...base, preset: 'minutes', interval: m.slice(2) };
  const shaped = { ...base, time: parseTime(`${h}:${m}`) ? time : '09:00' };
  if (h !== '*' && dow === '*') return { ...shaped, preset: 'daily' };
  if (dow === '1-5') return { ...shaped, preset: 'weekdays' };
  if (/^\d$/.test(dow)) return { ...shaped, preset: 'weekly', weekday: Number(dow) };
  return base;
}

const RUN_LABELS: Record<ScheduleRun['status'], string> = {
  running: t('status.running'),
  done: t('sched.run.done'),
  quiet: t('sched.run.quiet'),
  error: t('status.error'),
  skipped: t('sched.run.skipped'),
  missed: t('sched.run.missed'),
};

/** "Today 09:02" or the date and time, in this device's words. */
function whenLabel(iso: string): string {
  const at = new Date(iso);
  const now = new Date();
  const sameDay = at.getFullYear() === now.getFullYear() && at.getMonth() === now.getMonth() && at.getDate() === now.getDate();
  const hm = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  return sameDay ? t('sched.today', hm) : `${formatDate(at, { month: 'short', day: 'numeric' })} ${hm}`;
}

/** The next run, said shortly: "14:30" today, else with its day. */
function nextLabel(iso: string): string {
  return whenLabel(iso);
}

const STATUS_DOT: Record<ScheduleRun['status'], string> = {
  running: 'running',
  done: 'ok',
  quiet: 'idle',
  error: 'bad',
  skipped: 'idle',
  missed: 'idle',
};

/** The frequency as the card says it: the daemon's words, or the raw expression. */
function frequencyLabel(s: Schedule): string {
  return s.description || `cron: ${s.cron}`;
}

export function SchedulesPage({ api, path, onNavigate, schedules, agents, workdirs, refreshKey, onReload, onOpenThread, onError }: Props) {
  const editing = path === 'new' ? 'new' : schedules.find(schedule => schedule.id === path) ?? null;
  const setEditing = (schedule: Schedule | 'new' | null) => onNavigate(schedule === 'new' ? 'new' : schedule?.id ?? '');
  const [filter, setFilter] = useState<'enabled' | 'paused' | 'all'>('enabled');
  const visible = schedules.filter(schedule => filter === 'all' || schedule.enabled === (filter === 'enabled'));
  const [menu, setMenu] = useState<{ schedule: Schedule; x: number; y: number } | null>(null);
  const [deleting, setDeleting] = useState<Schedule | null>(null);

  useEffect(() => {
    if (refreshKey > 0) onReload();
    // onReload only refetches; it is not a reason to run again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const agentOf = (id: string) => agents.find((a) => a.id === id);
  const act = async (run: () => Promise<unknown>) => {
    try {
      await run();
      onReload();
    } catch (e) {
      onError(e);
    }
  };

  const runNow = (s: Schedule) =>
    act(async () => {
      const out = await api.runSchedule(s.id);
      // The list comes back on its own; a skip says why it did not go.
      if (out.status === 'skipped' || out.status === 'error') onError(new Error(out.message ?? (out.status === 'skipped' ? t('sched.stillRunning') : t('sched.notRun'))));
    });

  const empty = schedules.length === 0;

  return (
    <div className={`sched-page sched-layout${editing ? ' has-detail' : ''}`}>
      <div className="sched-index">
      <div className="sched-head">
        <div>
          <h2 className="sched-title">
            <IconClock size={18} /> {t('sidebar.schedules')}
          </h2>
          <p className="sched-sub">{t('sched.hint')}</p>
        </div>
        <button className="btn-primary" onClick={() => setEditing('new')}>
          <IconPlus size={15} /> {t('sched.new')}
        </button>
      </div>

      <div className="sched-filters" role="group" aria-label={t('sidebar.schedules')}>{(['enabled', 'paused', 'all'] as const).map(value => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(`settings.${value}`)}</button>)}</div>
      {(empty || visible.length === 0) && (
        <div className="sched-empty">
          <IconClock size={28} />
          <p>{t(empty ? 'sched.empty' : 'sched.noMatches')}</p>
        </div>
      )}

      <ul className="sched-list">
        {visible.map((s) => {
          const agent = agentOf(s.agent);
          const last = s.lastRun;
          return (
            <li key={s.id} className={`sched-card${s.enabled ? '' : ' off'}${path === s.id ? ' selected' : ''}`}>
              <div className="sched-main">
                <button className="sched-open" onClick={() => setEditing(s)} title={t('sched.viewEdit')}>
                  <span className="sched-name">
                    <span className={`sched-dot ${last ? STATUS_DOT[last.status] : 'none'}`} aria-hidden="true" />
                    {s.name}
                  </span>
                  <span className="sched-freq">
                    {agent?.icon ? (
                      <AgentMark path={agent.icon} size={13} />
                    ) : (
                      <span className="sched-agent-name">{agent?.label ?? s.agent}</span>
                    )}
                    {frequencyLabel(s)}
                    {s.mode === 'same' ? ` · ${t('sched.fixedThread')}` : ''}
                    {s.timezone && s.timezone !== Intl.DateTimeFormat().resolvedOptions().timeZone ? ` · ${s.timezone}` : ''}
                  </span>
                  <span className="sched-meta">
                    {s.enabled ? (s.nextRunAt ? t('sched.next', nextLabel(s.nextRunAt)) : t('sched.noNext')) : t('sched.disabled')}
                    {last && ` · ${t('sched.last', RUN_LABELS[last.status])}`}
                  </span>
                  {s.problem && <span className="sched-problem">{s.problem}</span>}
                </button>
              </div>
              <div className="sched-side">
                <button
                  className={`switch${s.enabled ? ' on' : ''}`}
                  role="switch"
                  aria-checked={s.enabled}
                  aria-label={s.enabled ? t('sched.disable') : t('sched.enable')}
                  onClick={() => act(() => api.updateSchedule(s.id, { enabled: !s.enabled }))}
                >
                  <span className="switch-knob" />
                </button>
                <button
                  className="icon-btn"
                  aria-label={t('sidebar.more', s.name)}
                  onClick={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    setMenu({ schedule: s, x: rect.right - 208, y: rect.bottom + 4 });
                  }}
                >
                  <IconMore />
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      </div>
      {menu && (
        <Menu
          label={t('skills.actions', menu.schedule.name)}
          x={menu.x}
          y={menu.y}
          items={[
            { id: 'run', label: t('sched.runNow'), icon: IconPlay, onSelect: () => runNow(menu.schedule) },
            { id: 'edit', label: t('composer.edit'), icon: IconClock, onSelect: () => setEditing(menu.schedule) },
            'separator' as const,
            { id: 'delete', label: t('common.delete'), icon: IconClose, danger: true, onSelect: () => setDeleting(menu.schedule) },
          ] satisfies MenuEntry[]}
          onClose={() => setMenu(null)}
        />
      )}

      {deleting && (
        <Dialog
          title={t('sched.deleteTitle')}
          message={t('sched.deleteMessage', deleting.name)}
          confirmLabel={t('common.delete')}
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={(_, __) => {
            const gone = deleting;
            setDeleting(null);
            void act(() => api.deleteSchedule(gone.id));
          }}
        />
      )}

      {editing && (
        <ScheduleEditor
          key={path}
          api={api}
          schedule={editing === 'new' ? null : editing}
          agents={agents}
          workdirs={workdirs}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            onReload();
          }}
          onOpenThread={onOpenThread}
          onError={onError}
        />
      )}
      {!editing && <div className="sched-placeholder"><IconClock size={28} /><p>{t('sched.pick')}</p></div>}
    </div>
  );
}

/** A thread the form can bind, distilled to what choosing one needs. */
interface PickThread {
  id: string;
  title: string;
  agent: string;
  cwd: string;
}

interface EditorProps {
  api: Api;
  schedule: Schedule | null;
  agents: AgentInfo[];
  workdirs: string[];
  onCancel: () => void;
  onSaved: () => void;
  onOpenThread: (id: string) => void;
  onError: (e: unknown) => void;
}

function ScheduleEditor({ api, schedule, agents, workdirs, onCancel, onSaved, onOpenThread, onError }: EditorProps) {
  // A hidden instance is not offered to new schedules; editing an existing one keeps its agent.
  const usable = useMemo(() => agents.filter((a) => (a.available && !a.hidden) || a.id === schedule?.agent), [agents, schedule?.agent]);
  const [name, setName] = useState(schedule?.name ?? '');
  const [agent, setAgent] = useState(schedule?.agent ?? usable[0]?.id ?? '');
  const [cwd, setCwd] = useState(schedule?.cwd ?? workdirs[0] ?? '');
  const [prompt, setPrompt] = useState(schedule?.prompt ?? '');
  const shape = useMemo(() => presetFromCron(schedule?.cron ?? '0 9 * * *'), [schedule?.cron]);
  const [preset, setPreset] = useState<Preset>(shape.preset);
  const [time, setTime] = useState(shape.time);
  const [weekday, setWeekday] = useState(shape.weekday);
  const [day, setDay] = useState(shape.day);
  const [interval, setIntervalValue] = useState(shape.interval);
  const [customCron, setCustomCron] = useState(preset === 'custom' ? (schedule?.cron ?? '') : '');
  const [timezone, setTimezone] = useState(schedule?.timezone ?? '');
  const [tzError, setTzError] = useState(false);
  const [catchUp, setCatchUp] = useState(schedule?.catchUp ?? false);
  const [mode, setMode] = useState<'new' | 'same'>(schedule?.mode ?? 'new');
  const [boundThread, setBoundThread] = useState<PickThread | null>(null);
  const [permission, setPermission] = useState(schedule?.permissionMode ?? '');
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const [runsError, setRunsError] = useState('');
  const [threadError, setThreadError] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<{ cron: string; description: string; fires: string[] } | null>(null);

  const agentInfo = agents.find((a) => a.id === agent);
  const modes = agentInfo?.capabilities.permissionModes ?? [];
  const permissionValue = modes.includes(permission) ? permission : modes.length ? startingPermission(agentInfo!.capabilities) : '';

  // The cron the form stands for right now.
  const cron = useMemo(
    () => (preset === 'custom' ? customCron.trim() : (cronFromPreset(preset, { time, weekday, day, interval }) ?? '')),
    [preset, customCron, time, weekday, day, interval],
  );

  // A custom expression is explained while it is typed: what it means, when it fires next.
  useEffect(() => {
    if (preset !== 'custom' || !cron) {
      setPreview(null);
      return;
    }
    let stale = false;
    const timer = setTimeout(() => {
      api
        .previewCron(cron, timezone.trim() || undefined)
        .then((p) => {
          if (!stale) setPreview({ cron, ...p });
        })
        .catch(() => {
          if (!stale) setPreview({ cron, description: '', fires: [] });
        });
    }, 250);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [api, preset, cron, timezone]);

  const loadRuns = () => {
    if (!schedule) return;
    setRunsError(''); setRuns(null);
    void api.scheduleRuns(schedule.id).then(setRuns).catch(e => setRunsError(String(e)));
  };
  const loadThreads = () => {
    setThreadError('');
    void api.threads().then(value => setThreads(value.filter(thread => !thread.archivedAt))).catch(e => setThreadError(String(e)));
  };
  useEffect(() => { if (mode === 'same') loadThreads(); }, [api, mode]);

  // A bound thread carries its own agent and directory along.
  useEffect(() => {
    if (!schedule?.threadId || !threads) return;
    const found = threads.find((t) => t.id === schedule.threadId);
    if (found) setBoundThread({ id: found.id, title: found.title, agent: found.agent, cwd: found.cwd });
    // Bound threads that are gone or archived are not offered again; the problem line says so.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threads, schedule?.threadId]);

  const bound = boundThread && mode === 'same' ? boundThread : null;
  const effectiveCwd = bound ? bound.cwd : cwd;
  const dirOptions = useMemo(() => {
    const dirs = new Set<string>(workdirs);
    for (const t of threads ?? []) dirs.add(t.cwd);
    return [...dirs].slice(0, 12);
  }, [workdirs, threads]);

  const draft = JSON.stringify([name, agent, cwd, prompt, preset, time, weekday, day, interval, customCron, timezone, catchUp, mode, permission, boundThread?.id ?? schedule?.threadId ?? null]);
  const [initialDraft] = useState(draft);
  const release = usePageDraft(draft !== initialDraft, saving);
  const save = async () => {
    setError(''); setSaving(true);
    try {
      const body: Record<string, unknown> = {
        name,
        agent: bound ? bound.agent : agent,
        cwd: effectiveCwd,
        prompt,
        cron,
        mode,
        threadId: bound ? bound.id : null,
        timezone: timezone.trim() || null,
        catchUp,
        ...(permissionValue ? { permissionMode: permissionValue } : {}),
      };
      if (schedule) await api.updateSchedule(schedule.id, body);
      else await api.createSchedule(body as Parameters<Api['createSchedule']>[0]);
      release(); onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const canSave = cron && prompt.trim() && (bound || (cwd.trim() && agent)) && !tzError && !saving;

  return (
    <div className="sched-detail">
      <header className="sched-detail-top"><button className="icon-btn glass-control" aria-label={t('common.back')} onClick={onCancel}><IconBack size={20} /></button><h2>{schedule?.name || t('sched.new')}</h2><span /></header>
      <SettingsSection busy={saving} title={schedule?.name || t('sched.new')} className="sched-editor" actions={<>
        <button type="button" className="ghost" disabled={saving} onClick={onCancel}>{t('common.cancel')}</button>
        <button type="button" className="primary" disabled={!canSave} onClick={() => void save()}>{schedule ? t('sched.saveChanges') : t('sched.create')}</button>
      </>}>
      <div className="sched-form">
        {error && <p className="panel-error" role="alert">{error}</p>}
        <label className="field">
          <span className="field-label">{t('mcp.name')}</span>
          <input value={name} placeholder={t('sched.namePlaceholder')} onChange={(e) => setName(e.target.value)} />
        </label>

        <label className="field">
          <span className="field-label">{t('sched.prompt')}</span>
          <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && isCommit(e) && e.preventDefault()} placeholder={t('sched.promptPlaceholder')} />
        </label>

        <div className="personal-setting-line"><span>{t('personal.agent')}</span>
          <Popover label={t('sched.pickAgent')} placement="below" align="end" triggerClassName="context-btn" trigger={<>{usable.find(item => item.id === agent)?.label ?? agent}<IconChevron size={12} open /></>}>
            {close => usable.map(item => <button key={item.id} className="pop-item compact" role="menuitemradio" aria-checked={agent === item.id} onClick={() => { setAgent(item.id); close(); }}>
              {item.icon && <AgentMark path={item.icon} size={16} />}<span className="pop-item-label">{item.label}</span>{item.account && <span className="pop-item-hint">{item.account}</span>}
            </button>)}
          </Popover>
        </div>

        <div className="field">
          <span className="field-label">{bound ? t('sched.boundDir') : t('actions.cwd')}</span>
          {bound ? (
            <p className="field-static" title={bound.cwd}>
              {folderName(bound.cwd)}
            </p>
          ) : (
            <div className="field-inline">
              <input value={cwd} spellCheck={false} onChange={(e) => setCwd(e.target.value)} aria-label={t('actions.cwd')} />
              <Popover label={t('sched.commonDirs')} triggerLabel={t('sched.commonDirs')} trigger={<IconFolder size={14} />} triggerClassName="btn-secondary" triggerTitle={t('sched.commonDirs')}>
                {(close) => (
                  <ul className="pick-list">
                    {dirOptions.map((d) => (
                      <li key={d}>
                        <button
                          onClick={() => {
                            setCwd(d);
                            close();
                          }}
                          title={d}
                        >
                          {folderName(d)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Popover>
            </div>
          )}
        </div>

        <div className="field">
          <span className="field-label">{t('sched.frequency')}</span>
          <Popover label={t('sched.frequency')} placement="below" triggerClassName="context-btn" trigger={<>{preset === 'custom' ? t('sched.custom') : PRESET_LABELS[preset]}<IconChevron size={12} open /></>}>
            {close => [...Object.keys(PRESET_LABELS), 'custom'].map(value => <button key={value} className="pop-item compact" role="menuitemradio" aria-checked={preset === value} onClick={() => { setPreset(value as Preset); close(); }}>{value === 'custom' ? t('sched.custom') : PRESET_LABELS[value as Exclude<Preset, 'custom'>]}</button>)}
          </Popover>
          {preset === 'minutes' && (
            <div className="field-inline">
              <span>{t('sched.every')}</span>
              <input className="num" value={interval} inputMode="numeric" onChange={(e) => setIntervalValue(e.target.value)} aria-label={t('sched.intervalLabel')} />
              <span>{t('sched.minutes')}</span>
            </div>
          )}
          {preset !== 'minutes' && preset !== 'custom' && preset !== 'hourly' && (
            <div className="field-inline">
              <input className="num time" value={time} onChange={(e) => setTime(e.target.value)} placeholder="HH:MM" aria-label={t('sched.runTime')} />
              <span>{t('sched.run')}</span>
            </div>
          )}
          {preset === 'hourly' && (
            <div className="field-inline">
              <span>{t('sched.everyHourAt')}</span>
              <input className="num time" value={time} onChange={(e) => setTime(e.target.value)} placeholder="HH:MM" aria-label={t('sched.runTime')} />
              <span>{t('sched.run')}</span>
            </div>
          )}
          {preset === 'weekly' && (
            <div className="chip-row">
              {WEEKDAYS.map((label, d) => (
                <button key={label} type="button" className={`chip${weekday === d ? ' on' : ''}`} onClick={() => setWeekday(d)}>
                  {label}
                </button>
              ))}
            </div>
          )}
          {preset === 'monthly' && (
            <div className="field-inline">
              <span>{t('sched.everyMonth')}</span>
              <input className="num" value={day} inputMode="numeric" onChange={(e) => setDay(e.target.value)} aria-label={t('sched.dayOfMonth')} />
              <span>{t('sched.daySuffix')}</span>
            </div>
          )}
          {preset === 'custom' && (
            <>
              <input value={customCron} spellCheck={false} placeholder={t('sched.cronPlaceholder')} onChange={(e) => setCustomCron(e.target.value)} aria-label={t('sched.cron')} />
              {preview && preview.cron === cron && (
                <p className="field-hint">
                  {preview.description || t('sched.unusual')}
                  {preview.fires.length > 0 && ` · ${t('sched.then', formatList(preview.fires.slice(0, 3).map(whenLabel)))}`}
                </p>
              )}
            </>
          )}
          {preset === 'hourly' && parseTime(time) === null && <p className="field-hint bad">{t('sched.timeFormat')}</p>}
          {preset !== 'custom' && preset !== 'hourly' && preset !== 'minutes' && parseTime(time) === null && <p className="field-hint bad">{t('sched.timeFormat')}</p>}
          <label className="dialog-option" style={{ marginTop: '12px', marginBottom: 0 }}>
            <input
              type="checkbox"
              checked={catchUp}
              onChange={(e) => setCatchUp(e.target.checked)}
            />
            <span className="dialog-option-text">
              <span>{t('sched.catchUp')}</span>
            </span>
          </label>
        </div>

        <label className="field">
          <span className="field-label">{t('sched.timezone')}</span>
          <input
            value={timezone}
            placeholder={t('sched.timezonePlaceholder', Intl.DateTimeFormat().resolvedOptions().timeZone)}
            spellCheck={false}
            onChange={(e) => {
              setTimezone(e.target.value);
              if (tzError) setTzError(false);
            }}
            onBlur={() => {
              const val = timezone.trim();
              if (val) {
                try {
                  new Intl.DateTimeFormat('en-US', { timeZone: val });
                  setTzError(false);
                } catch {
                  setTzError(true);
                }
              } else {
                setTzError(false);
              }
            }}
            aria-label={t('sched.timezone')}
          />
          {tzError && <p className="field-hint bad">{t('sched.timezoneInvalid')}</p>}
        </label>

        <div className="field">
          <span className="field-label">{t('sched.session')}</span>
          <div className="chip-row" role="radiogroup" aria-label={t('sched.mode')}>
            <button type="button" className={`chip${mode === 'new' ? ' on' : ''}`} role="radio" aria-checked={mode === 'new'} onClick={() => setMode('new')}>
              {t('sched.newEachTime')}
            </button>
            <button type="button" className={`chip${mode === 'same' ? ' on' : ''}`} role="radio" aria-checked={mode === 'same'} onClick={() => setMode('same')}>
              {t('sched.continueSame')}
            </button>
          </div>
          {mode === 'same' && (
            <div className="field-inline">
              {bound ? (
                <>
                  <span className="field-static" title={bound.cwd}>
                    {bound.title}
                  </span>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => {
                      setBoundThread(null);
                    }}
                  >
                    {t('sched.unbind')}
                  </button>
                </>
              ) : (
                <>
                  <span className="field-hint">{t('sched.createdOnFirst')}</span>
                  <Popover label={t('sched.pickSession')} triggerLabel={t('sched.pickExisting')} trigger={<IconPlus size={14} />} triggerClassName="btn-secondary" triggerTitle={t('sched.pickExistingHint')}>
                    {(close) => (
                      <ul className="pick-list">
                        {threadError && <li role="alert">{threadError} <button onClick={loadThreads}>{t('common.retry')}</button></li>}
                        {!threads && !threadError && <li role="status">{t('common.loading')}</li>}
                        {(threads ?? []).slice(0, 30).map((t) => (
                          <li key={t.id}>
                            <button
                              title={t.cwd}
                              onClick={() => {
                                setBoundThread({ id: t.id, title: t.title, agent: t.agent, cwd: t.cwd });
                                close();
                              }}
                            >
                              {t.title}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Popover>
                </>
              )}
            </div>
          )}
          {mode === 'same' && <p className="field-hint">{t('sched.sameHint')}</p>}
        </div>

        {modes.length > 0 && (
          <div className="field">
            <span className="field-label">{t('statusPanel.permission')}</span>
            <div className="chip-row" role="radiogroup" aria-label={t('statusPanel.permission')}>
              {PERMISSION_ORDER.filter((m) => modes.includes(m)).map((m) => (
                <button key={m} type="button" className={`chip${permissionValue === m ? ' on' : ''}`} role="radio" aria-checked={permissionValue === m} onClick={() => setPermission(m)}>
                  {PERMISSION_LABELS[m].short}
                </button>
              ))}
            </div>
            <p className="field-hint">
              {permissionValue === 'full_access'
                ? t('sched.fullAccessHint')
                : PERMISSION_LABELS[permissionValue]?.hint ?? ''}
            </p>
          </div>
        )}

        {schedule && (
          <div className="field">
            <button className="settings-link-row" aria-expanded={showRuns} onClick={() => { if (!showRuns) loadRuns(); setShowRuns(value => !value); }}><span>{t('sched.runs')}</span><IconChevron size={14} open={showRuns} /></button>
            {showRuns && <>
            {runsError ? <p className="panel-error" role="alert">{runsError} <button className="ghost" onClick={loadRuns}>{t('common.retry')}</button></p> : runs === null || runs.length === 0 ? (
              <p className="field-hint">{runs === null ? t('activity.reading') : t('sched.noRuns')}</p>
            ) : (
              <ul className="sched-runs">
                {runs.slice(0, 8).map((r) => (
                  <li key={r.id}>
                    <span className={`sched-dot ${STATUS_DOT[r.status]}`} aria-hidden="true" />
                    <span className="sched-run-status">{RUN_LABELS[r.status]}</span>
                    <span className="sched-run-time">{whenLabel(r.startedAt)}</span>
                    {r.message && <span className="sched-run-message" title={r.message}>{r.message}</span>}
                    {r.threadId && (
                      <button type="button" className="link" onClick={() => onOpenThread(r.threadId!)}>
                        {t('sched.viewSession')}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            </>}
          </div>
        )}
      </div>

      </SettingsSection>
    </div>
  );
}
