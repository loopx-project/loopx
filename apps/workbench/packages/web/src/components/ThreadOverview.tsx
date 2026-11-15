import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { type AgentInfo, type Api, type ChangedFile, type Coordination, type OutputFile, type ThreadStats } from '../api';
import { deliveredPreview, progressState } from '../timeline';
import { relativePath } from '../paths';
import { t } from '../i18n';
import { memberStatus } from './AgentPanel';
import { PlanList, PlanRing, planProgress, type PlanItem } from './PlanPanel';
import { Lightbox } from './Lightbox';
import { compactTokens, speedLabel } from './SessionStats';
import { useOverviewReading } from '../overview-reading';
import { AgentMark, IconAgents, IconBack, IconChevron, IconChevronRight, IconEdit, IconFile, IconImage, IconPreview, IconRefresh, IconSearch } from './icons';

/** The parts of the overview, in the order they are shown. */
export type Section = 'agents' | 'plan' | 'changes' | 'images' | 'files';

/** Which sections the user folded (true) or unfolded (false) by hand, kept on this device. */
export type Choices = Partial<Record<Section, boolean>>;

/** The fold choices a device remembers: today's map, and the plain list older pages wrote. */
export function choicesFrom(raw: string | null): Choices {
  try {
    const value: unknown = JSON.parse(raw ?? '{}');
    if (Array.isArray(value)) return Object.fromEntries(value.filter((v): v is Section => typeof v === 'string').map((v) => [v, true])) as Choices;
    if (value && typeof value === 'object') {
      const out: Choices = {};
      for (const [key, folded] of Object.entries(value)) if (typeof folded === 'boolean') out[key as Section] = folded;
      return out;
    }
  } catch { /* a page that cannot read it starts with everything open */ }
  return {};
}

/**
 * What a member's last report ended asking for, read the way the conversation's own lines read it:
 * the `[进度]` word at the end of what they said — or, when the delivery itself failed, that.
 */
export const memberProgress = (m: Coordination['members'][number]): 'need' | 'failed' | 'review' | 'progress' | null =>
  m.latest?.state === 'failed' ? 'failed' : progressState(m.latest?.result ?? '');

/** Who needs the user first, then what went wrong, then who is busy; the rest keep their order. */
const memberRank = (m: Coordination['members'][number]) => {
  const p = memberProgress(m);
  if (p === 'need') return 0;
  if (p === 'failed') return 1;
  if (m.thread?.status === 'running') return 2;
  if (m.latest?.state === 'queued') return 3;
  if (p === 'review') return 4;
  return 5;
};

export function sortMembers(members: Coordination['members']): Coordination['members'] {
  return members.map((m, i) => ({ m, i })).sort((a, b) => memberRank(a.m) - memberRank(b.m) || a.i - b.i).map(({ m }) => m);
}

/** Missing pictures remain identifiable without trying to fetch them. */
export const missingImages = (images: OutputFile[]) => images.filter((image) => image.missing);

/** A page or a drawing can be shown in the preview, as long as it is still on disk. */
export const previewable = (file: { path: string; missing?: true }): boolean => !file.missing && /\.(html?|svg)$/i.test(file.path);

const baseName = (path: string) => path.replace(/\/+$/, '').split('/').pop() || path;
const dirName = (path: string) => { const at = path.lastIndexOf('/'); return at > 0 ? path.slice(0, at) : ''; };

type PreviewLimit = 0 | 1 | 3;
export type OverviewSection = Section | 'all';
type OverviewProps = {
  api: Api; threadId: string; updatedAt: string; active: boolean;
  team: Coordination | null; agents: AgentInfo[]; plan: PlanItem | null; roots: string[];
  loadFile: (path: string) => Promise<Blob>;
  onOpenMember: (memberId?: string) => void;
  onOpenDiff: (file: ChangedFile) => void;
  onOpenFile: (path: string) => Promise<void>;
  onPreview: (path: string) => void;
  onOpenTrace: () => void;
  onClose: () => void;
  onViewAll: (section: OverviewSection, returnToPreview?: boolean) => void;
  onLoadingChange?: (loading: boolean) => void;
  persistent?: boolean;
  detail?: OverviewSection;
};
type Layout = { height: number; sections: Section[]; counts: Partial<Record<Section, number>> };

/** Preview counts are chosen once per opening; refreshes keep the same slots and footer. */
export function ThreadOverview(props: OverviewProps) {
  const { api, threadId, updatedAt, active, team, agents, plan, roots, loadFile, onOpenMember, onOpenDiff, onOpenFile, onPreview, onOpenTrace, onClose, onViewAll, onLoadingChange, persistent = false, detail } = props;
  const reading = useOverviewReading(api, threadId, updatedAt, active);
  const { outputs, stats, loading, failed } = reading;
  const ready = Boolean(outputs || failed);
  const root = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [choices, setChoices] = useState<Choices>(() => choicesFrom(localStorage.getItem('calli.overview.folded')));
  const [limit, setLimit] = useState<PreviewLimit>(3);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [search, setSearch] = useState('');
  const rel = (path: string) => relativePath(path, roots);
  const members = useMemo(() => team ? sortMembers(team.members) : [], [team]);
  const needs = members.filter(member => memberProgress(member) === 'need').length;
  const failures = members.filter(member => memberProgress(member) === 'failed').length;
  const running = members.filter(member => member.thread?.status === 'running').length;
  const attention = [needs ? t('overview.needsYou', needs) : '', failures ? t('overview.failedCount', failures) : ''].filter(Boolean).join(' · ');
  const pictures = outputs?.images ?? [];
  const gonePictures = outputs ? missingImages(outputs.images) : [];
  const changes = outputs?.changes;
  const files = outputs?.files ?? [];
  const progress = plan ? planProgress(plan) : null;
  const counts: Record<Section, number> = { agents: members.length, plan: plan?.entries.length ?? 0, changes: changes?.files.length ?? 0, images: pictures.length, files: files.length };
  const available = (Object.keys(counts) as Section[]).filter(id => counts[id] > 0);
  const sections = detail ? detail === 'all' ? available : [detail] : layout?.sections ?? available;
  const slots = (id: Section) => detail ? counts[id] : Math.min(limit, layout?.counts[id] ?? counts[id]);
  const action = (run: () => void) => () => { if (!persistent && !detail) onClose(); run(); };
  const viewAll = (id: OverviewSection) => action(() => onViewAll(id));
  const toggle = (id: Section) => {
    setChoices(previous => { const next = { ...previous, [id]: !previous[id] }; rememberChoices(next); return next; });
    setLayout(null);
  };
  const matches = (value: string) => value.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
  const take = <T,>(id: Section, rows: T[]) => detail ? rows : rows.slice(0, slots(id));
  const pad = (id: Section, shown: number) => !detail && Array.from({ length: Math.max(0, slots(id) - shown) }, (_, index) => (
    <div key={`empty-${index}`} className="overview-slot-empty" aria-hidden={shown > 0 || index > 0 ? true : undefined}>{!shown && !index ? t('overview.sectionEmpty') : '\u00a0'}</div>
  ));
  useEffect(() => { onLoadingChange?.(loading && !outputs); return () => onLoadingChange?.(false); }, [loading, outputs, onLoadingChange]);

  // The page covers the main column without unmounting its conversation or composer.
  useLayoutEffect(() => {
    if (!detail || !root.current) return;
    const previous = document.activeElement as HTMLElement | null;
    const siblings = [...(root.current.parentElement?.children ?? [])].filter((el): el is HTMLElement => el instanceof HTMLElement && el !== root.current);
    const inert = siblings.map(el => el.inert);
    siblings.forEach(el => { el.inert = true; });
    heading.current?.focus({ preventScroll: true });
    return () => { siblings.forEach((el, index) => { el.inert = inert[index]; }); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [detail]);

  useLayoutEffect(() => {
    if (detail || !ready || !root.current) return;
    // The sections are pinned per opening so refreshes keep their slots — but the panel must
    // still grow when what it holds grows: a section that lands late (the team usually arrives
    // after the outputs) or a group that gains a row (the team goes from one member to two).
    // Both show up as a different natural height. The pin from the previous fit would hide that,
    // so it is cleared for the measurement and restored before paint.
    const el = root.current;
    const pinned = el.style.height;
    el.style.height = 'auto';
    const height = el.offsetHeight;
    el.style.height = pinned;
    const key = available.join(' ');
    if (layout && layout.sections.join(' ') === key && Math.abs(layout.height - height) <= 1) return;
    const panel = el.closest<HTMLElement>('.overview-panel');
    if (!panel) return;
    const viewport = window.visualViewport;
    const bottom = (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight);
    const trigger = document.querySelector('.topbar-overview');
    const top = panel.classList.contains('anchored-pop') && trigger ? trigger.getBoundingClientRect().bottom + 8 : panel.getBoundingClientRect().top;
    const hard = Math.max(0, bottom - top - 12);
    const composer = document.querySelector('.composer-slot, .composer');
    const preferred = composer ? composer.getBoundingClientRect().top - top - 12 : hard;
    const budget = Math.min(hard, Math.max(preferred, Math.min(360, hard)));
    if (height + 18 > budget && limit > 0) { setLimit(limit === 3 ? 1 : 0); return; }
    if (height + 18 > hard && limit === 0) { action(() => onViewAll('all', false))(); return; }
    setLayout({ height, sections: available, counts });
  });
  useEffect(() => {
    if (detail) return;
    const reset = () => { setLimit(3); setLayout(null); };
    window.addEventListener('resize', reset);
    window.visualViewport?.addEventListener('resize', reset);
    return () => { window.removeEventListener('resize', reset); window.visualViewport?.removeEventListener('resize', reset); };
  }, [detail]);

  const content = sections.map(id => {
    let rows: ReactNode;
    let matched = 0;
    let summary: ReactNode = String(counts[id]);
    if (id === 'agents') {
      const selected = take(id, detail ? members.filter(member => matches(`${member.name} ${member.latest?.text ?? ''} ${member.latest?.result ?? ''}`)) : members);
      matched = selected.length;
      summary = attention ? <span className="overview-attention">{attention}</span> : team?.paused ? t('team.paused') : running ? t('team.running', running) : t('team.count', members.length);
      rows = <>{selected.map(member => <MemberRow key={member.id} member={member} agents={agents} onOpen={action(() => onOpenMember(member.id))} />)}{pad(id, selected.length)}</>;
    } else if (id === 'plan') {
      const entries = plan?.entries ?? [];
      const current = entries.findIndex(entry => entry.status === 'in_progress');
      const selected = detail ? entries.filter(entry => matches(entry.content)) : entries.slice(current >= slots(id) ? current : 0, (current >= slots(id) ? current : 0) + slots(id));
      matched = selected.length;
      summary = progress ? `${progress.completed}/${progress.total}` : '0';
      rows = <>{plan && <div className="overview-plan"><PlanList item={{ ...plan, entries: selected }} /></div>}{pad(id, selected.length)}</>;
    } else if (id === 'changes') {
      const selected = take(id, detail ? (changes?.files ?? []).filter(file => matches(rel(file.path))) : changes?.files ?? []);
      matched = selected.length;
      summary = changes ? <><span>{changes.total}</span><span className="overview-dot" aria-hidden="true">·</span><Nums added={changes.added} removed={changes.removed} /></> : '0';
      rows = <>{selected.map(file => <FileLine key={file.path} path={rel(file.path)} full={file.path} missing={file.missing} tag={file.created && !file.missing ? t('overview.created') : undefined}
        trailing={<Nums added={file.added} removed={file.removed} />} onOpen={action(() => onOpenDiff(file))} preview={previewable(file) ? action(() => onPreview(file.path)) : undefined} />)}{pad(id, selected.length)}</>;
    } else if (id === 'images') {
      const selected = take(id, detail ? pictures.filter(file => matches(rel(file.path))) : pictures);
      matched = selected.length;
      summary = gonePictures.length ? t('overview.missingImages', gonePictures.length) : String(pictures.length);
      rows = <>{selected.map(file => file.missing ? <FileLine key={file.path} path={rel(file.path)} full={file.path} missing locked onOpen={() => {}} /> : <Picture key={file.path} path={file.path} name={rel(file.path)} load={loadFile} enabled={Boolean(detail || layout)} />)}{pad(id, selected.length)}</>;
    } else {
      const selected = take(id, detail ? files.filter(file => matches(rel(file.path))) : files);
      matched = selected.length;
      rows = <>{selected.map(file => <FileLine key={file.path} path={rel(file.path)} full={file.path} missing={file.missing} locked={file.missing} onOpen={action(() => { void onOpenFile(file.path); })}
        preview={previewable(file) ? action(() => onPreview(file.path)) : undefined} />)}{pad(id, selected.length)}</>;
    }
    const icons = { agents: <IconAgents size={14} />, plan: <PlanRing done={progress?.total ? progress.completed / progress.total : 0} size={14} />, changes: <IconEdit size={14} />, images: <IconImage size={14} />, files: <IconFile size={14} /> };
    if (detail) return <section key={id} className="overview-detail-group"><h2>{t(`overview.${id}`)}<span>{summary}</span></h2>{rows}{matched === 0 && <p className="overview-empty">{t(search.trim() ? 'common.noMatch' : 'overview.sectionEmpty')}</p>}</section>;
    return <Group key={id} id={id} open={limit > 0 && !choices[id]} onToggle={limit === 0 ? section => viewAll(section)() : toggle} icon={icons[id]} label={t(`overview.${id}`)} summary={summary}>
      {rows}<button type="button" className="overview-more" onClick={viewAll(id)}>{t('overview.viewAll')}<IconChevronRight size={12} /></button>
    </Group>;
  });

  const newSections = available.some(id => layout && !layout.sections.includes(id));
  const status = failed && outputs ? t('overview.cachedFailed') : loading && outputs ? t('overview.refreshing') : !sections.includes('agents') && attention ? attention : newSections && sections.length > 0 ? t('overview.newContent') : '';
  const footer = <div className="overview-foot">
    {/* The readings themselves are the way to the trace: one faint line, one tap. */}
    <div className="overview-readings"><button type="button" className="overview-readings-link" title={t('trace.openTrace')} onClick={detail ? onOpenTrace : action(() => onOpenTrace())}>{readings(stats)}</button><span className={`overview-status${failed || (!sections.includes('agents') && attention) ? ' failed' : ''}`} role="status">{status}</span></div>
    <button type="button" className="icon-btn overview-refresh" aria-label={t(failed ? 'common.retry' : 'overview.refresh')} aria-busy={loading} aria-disabled={loading} onClick={() => { if (!loading) { if (!outputs) { setLimit(3); setLayout(null); } void reading.refresh(); } }}><IconRefresh size={14} /></button>
    <button type="button" className="overview-trace" onClick={detail ? onOpenTrace : viewAll('all')}>{t(detail ? 'overview.trace' : 'overview.viewAll')}<IconChevronRight size={12} /></button>
  </div>;
  if (detail) return <div ref={root} className="overview-details-page" role="region" aria-label={t(detail === 'all' ? 'overview.label' : `overview.${detail}`)}
    onKeyDown={event => { if (event.key === 'Escape' && !(event.target instanceof Element && event.target.closest('[role="dialog"]'))) { event.stopPropagation(); onClose(); } }}>
    <header className="overview-details-head"><button className="icon-btn glass-control" aria-label={t('common.back')} onClick={onClose}><IconBack size={21} /></button><h1 ref={heading} tabIndex={-1}>{t(detail === 'all' ? 'overview.label' : `overview.${detail}`)}</h1></header>
    <div className="overview-details-scroll"><div className="overview-details-toolbar"><label className="overview-details-search"><IconSearch size={16} /><input value={search} aria-label={t('overview.search')} placeholder={t('overview.search')} onChange={event => setSearch(event.target.value)} /></label>{footer}</div>
      {!outputs && <p className="overview-empty" role={failed ? 'alert' : 'status'}>{t(failed ? 'overview.failed' : 'common.loading')}</p>}{content}{!available.length && outputs && !failed && <p className="overview-empty">{t('overview.empty')}</p>}
    </div>
  </div>;
  return <div ref={root} className="thread-overview" data-ready={ready && Boolean(layout)} data-limit={limit} aria-busy={loading} style={{ height: layout?.height }}>
    <div className="overview-content">{content}{failed && !outputs && <p className="overview-empty" role="status">{t('overview.failed')}</p>}{!sections.length && ready && !failed && <p className="overview-empty">{t(available.length ? 'overview.newContent' : 'overview.empty')}</p>}</div>{footer}
  </div>;
}

function rememberChoices(choices: Choices) {
  try { localStorage.setItem('calli.overview.folded', JSON.stringify(choices)); } catch { /* this page keeps it anyway */ }
}

/** The readings line, in the order a session is read: turns, speed, tokens, cache. Nothing is
 *  drawn before it has been measured, and a zero is left out so the line never reads as a fact
 *  that has not happened yet. */
function readings(stats: ThreadStats | null): string {
  if (!stats) return '';
  const parts: string[] = [];
  if (stats.turns > 0) parts.push(t('stats.turnsShort', stats.turns));
  if (stats.tokPerSec != null && stats.tokPerSec > 0) parts.push(speedLabel(stats.tokPerSec, stats.tokPerSecApprox));
  const total = stats.tokens?.input != null ? stats.tokens.input + (stats.tokens.output ?? 0) : null;
  if (total != null && total > 0) parts.push(t('overview.tokens', compactTokens(total)));
  if (stats.tokens?.input) {
    const hit = stats.tokens.cacheRead != null ? stats.tokens.cacheRead / stats.tokens.input : null;
    if (hit != null && hit > 0) parts.push(t('overview.cache', `${Math.round(hit * 100)}%`));
  }
  // No spaces around the dot: the four readings are one faint line inside a 300px panel, and the
  // dot alone separates them without pushing the cache share under the ellipsis.
  return parts.join('·');
}

function Group({ id, open, onToggle, icon, label, summary, children }: {
  id: Section; open: boolean; onToggle: (s: Section) => void;
  icon: ReactNode; label: string; summary: ReactNode; children: ReactNode;
}) {
  return <section className={`overview-group${open ? ' open' : ''}`}>
    <div className="overview-head">
      {/* The chevron leads, so the numbers on the right line up with the rows below them. */}
      <button type="button" className="overview-toggle" aria-expanded={open} onClick={() => onToggle(id)}>
        <span className="overview-chevron"><IconChevron size={12} open={false} /></span>
        <span className="overview-head-icon">{icon}</span>
        <strong>{label}</strong>
        <span className="overview-summary">{summary}</span>
      </button>
    </div>
    {open && <div className="overview-body">{children}</div>}
  </section>;
}

/** One member on one line: its mark, its name, and the word its last report ended with. What it
 *  was last asked or answered is one hover away. */
function MemberRow({ member, agents, onOpen }: { member: Coordination['members'][number]; agents: AgentInfo[]; onOpen: () => void }) {
  const icon = agents.find((a) => a.id === member.thread?.agent)?.icon;
  const state = member.thread?.waiting ? 'waiting' : member.thread?.status === 'running' ? 'running' : member.latest?.state === 'failed' ? 'failed' : 'idle';
  const prog = memberProgress(member);
  const word = prog === 'need' ? t('view.deliveredNeed') : prog === 'failed' ? t('team.failed')
    : prog === 'review' ? t('view.deliveredReview') : prog === 'progress' ? t('view.deliveredProgress')
    : memberStatus(member);
  const doing = deliveredPreview(state === 'running' || state === 'waiting' ? member.latest?.text ?? '' : member.latest?.result || member.latest?.text || '');
  return <button type="button" role="menuitem" className="overview-member" title={doing || member.name} onClick={onOpen}>
    <span className="overview-member-mark">{icon ? <AgentMark path={icon} size={14} /> : <IconAgents size={14} />}</span>
    <span className="overview-member-name">{member.name}</span>
    <span className={`overview-state ${prog ?? state}`}>{word}</span>
  </button>;
}

/**
 * A file the thread touched or named: one line, the name first, the folder faint after it, what
 * changed at the end. A page or a drawing carries a preview button of its own, apart from the row;
 * a file no longer on disk is quiet and cannot be opened.
 */
function FileLine({ path, full, missing, locked, tag, trailing, preview, onOpen }: {
  path: string; full: string; missing?: true; locked?: boolean; tag?: string; trailing?: ReactNode;
  preview?: () => void; onOpen: () => void;
}) {
  return <div className={`overview-rowwrap${missing ? ' missing' : ''}`}>
    <button type="button" role="menuitem" className="overview-row" title={missing ? `${full}\n${t('overview.missing')}` : full} disabled={locked} onClick={onOpen}>
      <PathName path={path} />
      {tag && <span className="overview-tag">{tag}</span>}
      {missing && <span className="overview-gone">{t('overview.missing')}</span>}
      {trailing}
    </button>
    {preview && <button type="button" className="overview-preview" aria-label={t('overview.preview')} title={t('overview.preview')} onClick={preview}>
      <IconPreview size={14} />
    </button>}
  </div>;
}

/**
 * What a file (or a whole change set) added and removed, as one pair on the right: a side that is
 * zero stays out of the way, unless both are — then the pair says so rather than going missing.
 */
function Nums({ added, removed }: { added: number; removed: number }) {
  const both = (added > 0 && removed > 0) || (added === 0 && removed === 0);
  return <span className="overview-nums">
    {(added > 0 || both) && <span className="overview-add">+{added}</span>}
    {(removed > 0 || both) && <span className="overview-del">−{removed}</span>}
  </span>;
}

/** A path as a name with the folder it is in after it, faint. The row does the cutting itself,
 *  against its own width: the stem of the name folds first, the extension never folds, and the
 *  folder gives way before the name does — down to its last few characters, never further. */
function PathName({ path }: { path: string }) {
  const dir = dirName(path);
  const base = baseName(path);
  const dot = base.lastIndexOf('.');
  const ext = dot > 0 && base.length - dot <= 10 ? base.slice(dot) : '';
  return <span className="overview-path">
    <span className="overview-name"><span className="overview-stem">{ext ? base.slice(0, dot) : base}</span>{ext && <span className="overview-ext">{ext}</span>}</span>
    {dir && <span className="overview-dir">{dir}</span>}
  </span>;
}

/** A thumbnail, fetched while it is on screen; the full picture opens over everything. A file that
 *  exists but cannot be read keeps its square, grey, with the reason on hover. */
function Picture({ path, name, load, enabled = true }: { path: string; name: string; load: (path: string) => Promise<Blob>; enabled?: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [large, setLarge] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    let made: string | null = null;
    load(path).then(
      (blob) => { if (!live) return; made = URL.createObjectURL(blob); setUrl(made); },
      (reason: unknown) => { if (live) setError(reason instanceof Error && reason.message ? reason.message : t('view.noPreview')); },
    );
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [path, load, enabled]);
  // A drawing has no background of its own and would be cropped by the square cell; it sits whole
  // on the picture background instead, with room around it.
  const vector = /\.svgz?$/i.test(path);
  return <>
    <button type="button" className={`overview-row overview-picture-row${vector ? ' vector' : ''}${error ? ' failed' : ''}`} title={error ? `${name}\n${error}` : name} disabled={!url} onClick={() => setLarge(true)}>
      <span className="overview-picture-mini">{url ? <img src={url} alt="" /> : <IconImage size={15} />}</span><PathName path={name} />{error && <span className="overview-gone">{t('view.noPreview')}</span>}
    </button>
    {large && url && <Lightbox url={url} name={name} onClose={() => setLarge(false)} />}
  </>;
}
