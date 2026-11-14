import brandIcon from '../assets/calli.svg';
import { Fragment, useEffect, useState, type MouseEvent } from 'react';
import type { AgentInfo, HostInfo, SearchHit, Section, Thread } from '../api';
import { clockLabel } from '../timeline';
import { useInTitlebar, useLongPress, useStoredFlag, useStoredSet } from '../hooks';
import { attentionCount, threadMark, waitingLabel } from '../notices';
import { folderName } from '../paths';
import { buildActivity, buildMerged, buildSidebar, handoverGroups, markTerms, projectDir, SEARCH_MIN, taggedAgents, type HostSource } from '../sidebar';
import { timeAgo } from '../sessions';
import { HostSwitcher } from './Hosts';
import { AgentMark, IconBell, IconChevron, IconClose, IconEdit, IconFolder, IconMore, IconPin, IconPlus, IconSearch, IconSettings, IconSidebar, IconSparkle } from './icons';
import { t } from '../i18n';

export { folderName } from '../paths';

interface Props {
  /** Puts the list away on a wide screen; the top bar then has the button to bring it back. */
  onHide?: () => void;
  threads: Thread[];
  sections: Section[];
  /** For the mark each row shows in place of its agent's name. */
  agents: AgentInfo[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** Starts a new thread with a directory or section pre-filled, from a group's "+". */
  onNewIn: (opts: { cwd?: string; sectionId?: string; scratch?: boolean }) => void;
  onThreadMenu: (thread: Thread, x: number, y: number) => void;
  onSectionMenu: (section: Section, x: number, y: number) => void;
  onNewSection: () => void;
  onOpenDesk?: () => void;
  deskActive?: boolean;
  /** The phone layout: the list is a page of its own under the conversation, laid out for thumbs. */
  compact?: boolean;
  /** Opens the settings menu, where the look, the archive, importing and signing out are. It grows up and to the left from the point given. */
  onOpenSettings: (x: number, y: number) => void;
  /** Other machines the hub shows, and the one this list is of (null: the hub's own). */
  hosts?: HostInfo[];
  hostId?: string | null;
  onHost?: (id: string | null) => void;
  /** The machine this list is of cannot be reached: its threads are over there, so there is no list. */
  hostDown?: boolean;
  /** No list has been read from this machine yet, live or remembered: empty means "not read", not "none". */
  loading?: boolean;
  /** Every machine's list, the one on screen among them: the sidebar shows them merged. Absent: just this machine's. */
  merged?: HostSource[] | null;
  /** Shows every machine's threads in one list; offered as a tab before the machines. */
  onAll?: () => void;
  /** Opens a thread of another machine, which means switching to that machine. */
  onOpenElsewhere?: (hostId: string | null, threadId: string) => void;
  /** Looks through what was said in every thread; without it the search is of titles and folders only. */
  onSearch?: (query: string) => Promise<SearchHit[]>;
  /** Opens the thread a found turn is in, at that turn. */
  onOpenHit?: (hit: SearchHit) => void;
}

/** In the merged list, the machine a row's thread is on when that is not the hub's own, and how to open it if it is not the one on screen. */
interface Elsewhere { label?: string; open?: () => void }

/** Where a menu opened from a "more" button should appear: just under the button. */
function below(e: MouseEvent<HTMLElement>): [number, number] {
  const rect = e.currentTarget.getBoundingClientRect();
  return [rect.left, rect.bottom + 4];
}

/** A small "+" on a group header that starts a thread right in that group. */
function GroupAdd({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="group-add" aria-label={label} title={label} onClick={(e) => {
      e.stopPropagation();
      onClick();
    }}>
      <IconPlus size={14} />
    </button>
  );
}

/** The key that shows and hides the list, as Codex has it. */
export const SIDEBAR_SHORTCUT = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⇧⌘S' : 'Ctrl+Shift+S';

export function Sidebar({
  threads,
  sections,
  agents,
  selectedId,
  onSelect,
  onNew,
  onNewIn,
  onThreadMenu,
  onSectionMenu,
  onNewSection,
  onOpenDesk,
  deskActive = false,
  compact = false,
  onOpenSettings,
  onHide,
  hosts = [],
  hostId = null,
  onHost,
  hostDown = false,
  loading = false,
  merged = null,
  onAll,
  onOpenElsewhere,
  onSearch,
  onOpenHit,
}: Props) {
  const [search, setSearch] = useState('');
  // What was said, looked up a moment after the typing stops. `for` is the search the hits answer.
  const [found, setFound] = useState<{ for: string; hits: SearchHit[] } | null>(null);
  const query = search.trim();
  useEffect(() => {
    if (!onSearch || query.length < SEARCH_MIN) { setFound(null); return; }
    let stale = false;
    const timer = setTimeout(() => {
      onSearch(query).then((hits) => { if (!stale) setFound({ for: query, hits }); }).catch(() => { if (!stale) setFound({ for: query, hits: [] }); });
    }, 220);
    return () => { stale = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);
  const [expandedHandovers, setExpandedHandovers] = useState<Set<string>>(new Set());
  // A team is open on its own while a member works or waits, or is the one on screen; a click overrides that.
  // The search field is put away behind a button until it is asked for.
  const [searching, setSearching] = useState(false);
  // Where the nav's "more" pages menu opened from; null when closed.
  const closeSearch = () => {
    setSearch('');
    setSearching(false);
  };
  // A long press on a row is the phone's right click.
  const longPress = useLongPress<Thread>(onThreadMenu);
  const inTitlebar = useInTitlebar() && !compact;
  const [collapsed, toggle] = useStoredSet(`calli.sidebar.collapsed`);
  // The other way to read the list: by what is going on, not by where a thread is filed. Like Codex's activity view.
  const [byActivity, setByActivity] = useStoredFlag(`calli.sidebar.activity`);
  const model = buildSidebar(threads, sections, search);
  const mergedModel = merged ? buildMerged(merged, search, new Date(), selectedId) : null;
  const activity = byActivity && !mergedModel ? buildActivity(threads, search, new Date(), selectedId) : null;
  // Out of their project groups, rows say where they are and what was asked last.
  const flat = byActivity || Boolean(mergedModel);
  // The machine on screen being down hides its list, unless every machine is listed.
  const down = hostDown && !mergedModel;
  const wanted = attentionCount(threads, selectedId);
  // While searching, everything is expanded so matches are never hidden inside a closed group.
  const isOpen = (key: string) => Boolean(search.trim()) || !collapsed.has(key);

  const MARK_LABELS = { waiting: t('notices.unknown.label'), running: t('status.running'), error: t('status.error'), unread: t('sidebar.unread') } as const;
  const agentById = new Map(agents.map((a) => [a.id, a]));
  const tagged = taggedAgents(agents);

  /**
   * `chain`: the handover chain this row stands for, newest first, as `handoverGroups` gives it.
   * `on`: in the merged list, the other machine the thread is on (named on the row), and how to open
   * it there; such a row has no menu, as its actions would go to the machine on screen.
   */
  const row = (thread: Thread, title = thread.title, preview = thread.preview, chain?: Thread[], on?: Elsewhere) => {
    const open = on?.open;
    const mark = threadMark(thread, thread.id === selectedId);
    const agent = agentById.get(thread.agent);
    // The agents the task passed through, first to last, and the one it came from.
    const passed = chain ? [...chain].reverse().map((m) => agentById.get(m.agent)?.label ?? m.agent).join(' → ') : thread.handover ? `${agentById.get(thread.handover.agent)?.label ?? thread.handover.agent} → ${agent?.label ?? thread.agent}` : null;
    const hint = [agent && tagged.has(agent.id) ? agent.label : null, thread.worktree ? t('sidebar.inWorktree', thread.worktree.branch) : null, thread.forkedFrom ? t('sidebar.fork') : null, passed ? t('sidebar.handover', passed) : null, thread.schedule ? t('sidebar.scheduleRun', thread.schedule.name) : null].filter(Boolean).join(' · ');
    const from = chain ? agentById.get(chain.find((m) => m.id === thread.handover?.sourceId)?.agent ?? thread.handover?.agent ?? '') : undefined;
    // Filed by project, the list is for scanning: a title per row, as in Codex. The latest ask
    // shows where it helps finding something, by activity and in a search. A thread that needs a
    // hand says so in every view.
    const detail = flat || query ? preview : null;
    const note = mark === 'waiting' ? waitingLabel(thread) : mark === 'error' ? t('sidebar.failed') : null;
    return (
    <div
      key={thread.id}
      className={`thread-row${thread.id === selectedId ? ' active' : ''}${mark === 'unread' || mark === 'waiting' ? ' attention' : ''}`}
      onContextMenu={(e) => {
        e.preventDefault();
        if (!open) onThreadMenu(thread, e.clientX, e.clientY);
      }}
      {...(open ? {} : longPress(thread))}
    >
      {/* Two accounts of one agent share a mark; which one runs the thread shows on hover and in the
          open thread's top bar, not as text on every row. */}
      <button className="thread-item" onClick={() => (open ? open() : onSelect(thread.id))} title={[on?.label && open ? t('sidebar.merged.openOn', on.label) : null, hint].filter(Boolean).join(' · ') || undefined}>
        <span className="thread-text">
          <span className="thread-head">
            <span className="thread-title">{title}</span>
            {/* Beside the title, so the row stays one line like the rest. */}
            {note && <span className={`thread-note ${mark}`}>{note}</span>}
            {/* Out of its project group, a thread says where it is. */}
            {flat && <span className="thread-dir">{on?.label ? `${on.label} · ` : ''}{folderName(projectDir(thread).cwd)}</span>}
          </span>
          {detail && <span className="thread-preview">{detail}</span>}
        </span>
        <span className="thread-meta">
          {/* A handover chain: the agent now on it in front, the one before it tucked behind. */}
          {agent?.icon && from?.icon ? (
            <span className="agent-stack" role="img" aria-label={agent.label} title={passed ?? undefined}>
              <AgentMark path={from.icon} size={compact ? 12 : 11} />
              <AgentMark path={agent.icon} size={compact ? 14 : 12} />
            </span>
          ) : agent?.icon ? (
            <span className="agent-mark" role="img" aria-label={agent.label} title={agent.label}>
              <AgentMark path={agent.icon} size={compact ? 13 : 12} />
            </span>
          ) : (
            // An agent without a mark, or one this hub no longer has: its name, as before.
            <span className="agent-tag">{thread.agent}</span>
          )}
          {mark && <span className={`dot ${mark}`} role="img" aria-label={mark === 'waiting' ? waitingLabel(thread) : MARK_LABELS[mark]} title={mark === 'waiting' ? waitingLabel(thread) : MARK_LABELS[mark]} />}
        </span>
      </button>
      {!open && (
        <button className="row-more" aria-label={t('sidebar.more', thread.title)} onClick={(e) => onThreadMenu(thread, ...below(e))}>
          <IconMore size={16} />
        </button>
      )}
    </div>
    );
  };

  const now = Date.now();
  const entry = (thread: Thread, on?: Elsewhere) => row(thread, thread.title, thread.preview, undefined, on);

  const groupedRows = (list: Thread[]) => handoverGroups(list).map(({ key, latest, members }) => {
    if (members.length === 1) return entry(latest);
    const storageKey = `${hostId ?? 'local'}:${key}`;
    const expanded = Boolean(search.trim()) || expandedHandovers.has(storageKey);
    const marks = [...new Set(members.filter((thread) => thread.id !== latest.id).map((thread) => threadMark(thread, thread.id === selectedId)).filter(Boolean))];
    const current = members.some((thread) => thread.id === selectedId);
    // Like Orca's agents under a task: one card, the latest conversation on top, the chain folded below it.
    return <div key={key} className={`handover-group${expanded ? ' open' : ''}${current ? ' selected' : ''}`}>
      {row(latest, latest.title.replace(/(?:（接手）)+$/, ''), latest.preview, members)}
      <button className="handover-toggle" aria-label={expanded ? t('sidebar.collapseHandover', latest.title) : t('sidebar.expandHandover', latest.title)} aria-expanded={expanded} onClick={() => setExpandedHandovers((old) => {
        const next = new Set(old);
        if (next.has(storageKey)) next.delete(storageKey); else next.add(storageKey);
        return next;
      })}>
        <span>{t('sidebar.sessions', members.length)}</span>
        {!expanded && marks.length > 0 && <span className="handover-marks">{marks.map((mark) => <span key={mark} className={`dot ${mark}`} role="img" aria-label={t('sidebar.otherHandover', MARK_LABELS[mark!])} title={t('sidebar.otherHandover', MARK_LABELS[mark!])} />)}</span>}
        <IconChevron size={13} open={expanded} />
      </button>
      {expanded && <div className="handover-members handover-chain">{members.map((thread) => {
        const agent = agentById.get(thread.agent);
        const mark = threadMark(thread, thread.id === selectedId);
        return <button
          key={thread.id}
          className={`handover-member${thread.id === selectedId ? ' active' : ''}`}
          aria-current={thread.id === selectedId ? 'true' : undefined}
          onClick={() => onSelect(thread.id)}
          onContextMenu={(e) => { e.preventDefault(); onThreadMenu(thread, e.clientX, e.clientY); }}
          {...longPress(thread)}
        >
          <span className={`dot ${mark ?? 'idle'}`} role="img" aria-label={mark === 'waiting' ? waitingLabel(thread) : mark ? MARK_LABELS[mark] : t('status.idle')} />
          {agent?.icon ? <span className="agent-mark"><AgentMark path={agent.icon} size={compact ? 14 : 13} /></span> : null}
          <span className="handover-member-name">{agent?.label ?? thread.agent}</span>
          <span className="handover-member-role">{thread.handover ? t('sidebar.successor') : t('sidebar.original')}</span>
          <time className="handover-member-time" dateTime={thread.updatedAt}>{timeAgo(thread.updatedAt, now)}</time>
        </button>;
      })}</div>}
    </div>;
  });

  const group = (key: string, head: React.ReactNode, list: Thread[], extra?: React.ReactNode, emptyHint?: string) => (
    <section key={key} className="thread-group">
      <div className="group-head-row">
        <button className="group-head" aria-expanded={isOpen(key)} onClick={() => toggle(key)}>
          <span className="group-chevron">
            <IconChevron size={13} open={isOpen(key)} />
          </span>
          {head}
        </button>
        {extra}
      </div>
      {isOpen(key) && (list.length ? groupedRows(list) : emptyHint ? <p className="group-empty">{emptyHint}</p> : null)}
    </section>
  );

  const empty = (
    <p className="group-empty top" role={loading ? 'status' : undefined}>
      {loading ? t('sidebar.loading') : search.trim() ? t('sidebar.noMatch') : compact ? t('sidebar.emptyPhone') : t('sidebar.empty')}
    </p>
  );

  const clock = (at: string | null) => {
    if (!at) return null;
    const d = new Date(at);
    const sameDay = d.toDateString() === new Date().toDateString();
    const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  };
  const labelOf = new Map((merged ?? []).map((s) => [s.hostId, s.label]));
  const groups = mergedModel ? (
    <nav className="thread-groups activity merged" aria-label={t('sidebar.merged.label')}>
      {mergedModel.notes.map((n) => (
        <p key={n.hostId ?? ''} className={`merged-note ${n.state}`} role={n.state === 'loading' ? 'status' : undefined}>
          {n.state === 'loading' ? t('sidebar.merged.loading', n.label) : n.state === 'offline' ? t('sidebar.merged.offline', n.label, clock(n.at)) : t('sidebar.merged.failed', n.label, clock(n.at))}
        </p>
      ))}
      {mergedModel.count === 0 && !mergedModel.notes.some((n) => n.state === 'loading') && empty}
      {mergedModel.groups.map((g) => (
        <section key={g.key} className="thread-group" aria-label={g.label}>
          <div className="group-label">{g.label}</div>
          {g.items.map(({ thread, hostId: on }) => (
            <Fragment key={`${on ?? ''}:${thread.id}`}>
              {entry(thread, {
                // The hub's own machine is the usual one; only threads elsewhere are named.
                label: on === null ? undefined : labelOf.get(on),
                open: on === hostId ? undefined : () => onOpenElsewhere?.(on, thread.id),
              })}
            </Fragment>
          ))}
        </section>
      ))}
    </nav>
  ) : activity ? (
    <nav className="thread-groups activity" aria-label={t('sidebar.threadsByActivity')}>
      {activity.count === 0 && empty}
      {activity.groups.map((g) => (
        <section key={g.key} className="thread-group" aria-label={g.label}>
          <div className="group-label">{g.label}</div>
          {g.threads.map((t) => entry(t))}
        </section>
      ))}
    </nav>
  ) : (
    <nav className="thread-groups" aria-label={t('sidebar.threads')}>
      {model.count === 0 && empty}

      {model.pinned.length > 0 &&
        group(
          'pinned',
          <>
            <IconPin size={13} />
            <span className="group-name">{t('sidebar.pinned')}</span>
          </>,
          model.pinned,
        )}

      {(model.sections.length > 0 || !search.trim()) && (
        <div className="group-label-row">
          <span className="group-label">{t('sidebar.sections')}</span>
          <button className="group-add" aria-label={t('sidebar.newSection')} title={t('sidebar.newSection')} onClick={onNewSection}>
            <IconPlus size={14} />
          </button>
        </div>
      )}
      {model.sections.map(({ section, threads: list }) =>
        group(
          `section:${section.id}`,
          <span className="group-name">{section.name}</span>,
          list,
          <span className="group-head-actions">
            <GroupAdd label={t('sidebar.newInSection', section.name)} onClick={() => onNewIn({ sectionId: section.id })} />
            <button
              className="row-more"
              aria-label={t('sidebar.sectionMore', section.name)}
              onClick={(e) => onSectionMenu(section, ...below(e))}
            >
              <IconMore size={16} />
            </button>
          </span>,
          compact ? t('sidebar.sectionHintPhone') : t('sidebar.sectionHint'),
        ),
      )}
      {model.sections.length === 0 && !search.trim() && <p className="group-empty">{t('sidebar.noSections')}</p>}

      {model.projects.length > 0 && <div className="group-label">{t('sidebar.projects')}</div>}
      {model.projects.map((p) =>
        group(
          `project:${p.key}`,
          <>
            <IconFolder size={13} />
            <span className="group-name" title={p.cwd}>
              {p.name}
            </span>
          </>,
          p.threads,
          <GroupAdd label={t('sidebar.newInProject', p.name)} onClick={() => onNewIn({ cwd: p.cwd })} />,
        ),
      )}
      {model.scratch.length > 0 &&
        group(
          'scratch',
          <span className="group-name">{t('projects.none')}</span>,
          model.scratch,
          <GroupAdd label={t('sidebar.newScratch')} onClick={() => onNewIn({ scratch: true })} />,
        )}
    </nav>
  );

  const searchInput = (
    <input
      type="search"
      className="sidebar-search"
      placeholder={onSearch ? t('sidebar.searchAll') : t('sidebar.search')}
      value={search}
      onChange={(e) => setSearch(e.target.value)}
      aria-label={t('sidebar.search')}
      autoFocus
      onKeyDown={(e) => {
        if (e.key === 'Escape') closeSearch();
      }}
    />
  );

  const hideButton = onHide && (
    <button className={`${inTitlebar ? 'icon-btn' : 'round-btn'} sidebar-hide`} aria-label={t('sidebar.hide')} title={t('sidebar.hideKey', SIDEBAR_SHORTCUT)} onClick={onHide}>
      <IconSidebar size={16} />
    </button>
  );

  return (
    <div className={`sidebar-inner${compact ? ' compact' : ''}`}>
      {/* In the window's title bar, as in Codex: beside the system's buttons only the list's own
          toggle; the rest of the strip drags the window, and the name and its buttons sit below. */}
      {inTitlebar && <div className="sidebar-titlebar">{hideButton}</div>}
      <div className="sidebar-top">
        {searching ? (
          <label className="sidebar-search-box">
            <IconSearch size={compact ? 18 : 15} />
            {searchInput}
            <button type="button" className="search-close" aria-label={t('sidebar.closeSearch')} onClick={closeSearch}>
              <IconClose size={compact ? 16 : 14} />
            </button>
          </label>
        ) : (
          <>
            <span className="brand"><img className="brand-icon" src={brandIcon} alt="" width="32" height="32" /><span>{t('app.name')}</span></span>
            <span className="sidebar-top-actions">
              {!inTitlebar && hideButton}
              <button
                className={`round-btn activity-btn${byActivity ? ' on' : ''}`}
                aria-label={t('sidebar.byActivity')}
                aria-pressed={byActivity}
                title={byActivity ? t('sidebar.byProject') : wanted > 0 ? t('sidebar.byActivityCount', wanted) : t('sidebar.byActivity')}
                onClick={() => setByActivity(!byActivity)}
              >
                <IconBell size={compact ? 20 : 16} />
                {wanted > 0 && <span className="activity-badge" aria-hidden="true" />}
              </button>
              <button className="round-btn" aria-label={t('sidebar.search')} title={t('sidebar.search')} onClick={() => setSearching(true)}>
                <IconSearch size={compact ? 20 : 16} />
              </button>
            </span>
          </>
        )}
      </div>

      <div className="sidebar-scroll">
        {onHost && !search.trim() && <HostSwitcher hosts={hosts} current={hostId} onPick={onHost} all={Boolean(mergedModel)} onAll={onAll} />}
        {down && <p className="group-empty top">{t('sidebar.hostDown')}</p>}
        {!down && !search.trim() && (
          <div className="side-nav">
            {(() => {
              const links = [
                // On a wide screen a new thread is the first row, like Codex; a phone keeps the floating button by the thumb.
                !compact && (
                  <button key="new" className="side-link" onClick={onNew}>
                    <IconEdit size={17} /> {t('sidebar.newThread')}
                  </button>
                ),
                onOpenDesk && <button key="desk" className={`side-link${deskActive ? ' active' : ''}`} aria-current={deskActive ? 'page' : undefined} onClick={onOpenDesk}>
                  <IconSparkle size={compact ? 21 : 17} /> {t('desk.title')}
                </button>,
              ].filter(Boolean);
              return links;
            })()}
          </div>
        )}
        {!down && groups}
        {!down && onSearch && query.length >= SEARCH_MIN && (
          <section className="search-hits" aria-label={t('sidebar.inConversations')}>
            <div className="search-hits-head">{t('sidebar.inConversations')}</div>
            {found?.for !== query ? (
              <p className="group-empty">{t('sidebar.searching')}</p>
            ) : found.hits.length === 0 ? (
              <p className="group-empty">{t('sidebar.notFound')}</p>
            ) : (
              found.hits.map((hit) => (
                <button type="button" className={`search-hit${hit.threadId === selectedId ? ' active' : ''}`} key={`${hit.threadId}:${hit.turnId}:${hit.role}`} onClick={() => onOpenHit?.(hit)}>
                  <span className="search-hit-top">
                    <span className="thread-title">{hit.title}</span>
                    {hit.archived && <span className="search-hit-tag">{t('activity.archived')}</span>}
                    <time dateTime={hit.ts}>{clockLabel(hit.ts)}</time>
                  </span>
                  <span className="search-hit-text">
                    <span className="search-hit-who">{hit.role === 'user' ? t('sidebar.me') : t('sidebar.reply')}</span>
                    {markTerms(hit.snippet, query).map((part, i) => (part.hit ? <mark key={i}>{part.text}</mark> : part.text))}
                  </span>
                </button>
              ))
            )}
          </section>
        )}
      </div>

      <div className="sidebar-float">
        {compact && !down && (
          <button className="new-thread" onClick={onNew}>
            <IconEdit size={19} /> {t('sidebar.newThread')}
          </button>
        )}
        {/* The look, the archive, importing and signing out are not everyday things: they live behind this. */}
        <button
          className="round-btn"
          aria-label={t('sidebar.settings')}
          title={t('sidebar.settings')}
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            onOpenSettings(rect.right, rect.top - 8);
          }}
        >
          <IconSettings size={compact ? 20 : 17} />
        </button>
      </div>
    </div>
  );
}
