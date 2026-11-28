import { useState, type ReactNode } from 'react';
import { ApiError, type AgentInfo, type Api, type HandoverTarget, type Section, type Thread, type Todo } from '../api';
import { conversationPointer, copyText } from '../clipboard';
import { referenceMachine, useReferenceMachine } from '../reference-input';
import { folderName } from '../paths';
import { Dialog } from './Dialog';
import { ForkSubchatDialog } from './ForkSubchatDialog';
import { HandoverChainDialog } from './HandoverTrail';
import { Menu, type MenuEntry } from './Menu';
import { ChatQuotaMenu } from './ChatQuotaMenu';
import { IconArchive, IconBoard, IconCheck, IconClock, IconCopy, IconDot, IconEdit, IconExternal, IconFiles, IconFork, IconHandover, IconList, IconPin, IconPlus, IconSparkle, IconTerminal, IconTrace, IconTrash, IconWorktree } from './icons';
import { formatList, t } from '../i18n';
import { boardStage, snoozed, threadAttention, type BoardStage } from '../sidebar';

interface Deps {
  api: Api | null;
  agents: AgentInfo[];
  sections: Section[];
  /** The plan page's list, for the "link to plan" submenu. */
  todos: Todo[];
  /** A plan was linked or unlinked here: reload the list. */
  onTodosChange: () => void;
  /** A thread changed. One that now has `archivedAt` should leave the sidebar. */
  onThreadChange: (thread: Thread) => void;
  onThreadRemove: (id: string) => void;
  /** A thread was made here and should be shown. */
  onThreadOpen: (thread: Thread, draft?: string) => void;
  /** The user marked a thread unread by hand. If it is the one on screen, it has to be left to stay unread. */
  onMarkedUnread: (thread: Thread) => void;
  /** Sections were added, renamed or removed: reload them (and the threads filed under them). */
  onSectionsChange: () => void;
  /** Opens the web terminal, from a menu item tied to the thread's directory. */
  onOpenTerminal: () => void;
  /** Opens the open thread's files, from its own page menu. */
  onOpenFiles: (thread: Thread) => void;
  onOpenGit?: () => void;
  /** Opens the thread's trace, where the top bar no longer keeps a button of its own (a phone
   *  reaches it from the overview instead). */
  onOpenTrace?: () => void;
  onError: (e: unknown) => void;
  /** Says what the thread is busy with, as a line by its title until it is done; null clears it. */
  onBusy: (busy: { threadId: string; message: string } | null) => void;
  /** A thread was archived: its row is gone, so an undo is offered where it was noticed. */
  onArchived: (thread: Thread) => void;
  /** The whole conversation as Markdown, for the copy menu. */
  markdownOf: (thread: Thread) => Promise<string>;
  /** The auto-titles extension is installed and on: threads get "name it again". */
  autotitle?: boolean;
  /** The board extension is installed and on: threads can be placed in its columns. */
  board?: boolean;
  /** The handover chain record, offered on threads that belong to one. */
  chain?: {
    threads: Thread[];
    isMerged: (thread: Thread) => boolean;
    onToggle: (thread: Thread) => void;
    onSelect: (id: string) => void;
    onRefresh: () => void;
  };
}

type MenuState = { x: number; y: number; where: 'list' | 'page' } & ({ thread: Thread } | { section: Section });

type DialogState =
  | { kind: 'rename'; thread: Thread }
  | { kind: 'delete'; thread: Thread }
  /** The worktree to be deleted holds work that exists nowhere else: asked once more, with the count. */
  | { kind: 'delete-worktree'; thread: Thread; agentSession: boolean; changes: number; commits: number }
  /** Codex-style composer sheet for either new-session handoff (`handoff`) or native full-history fork (`fork`). */
  | { kind: 'fork-subchat'; thread: Thread; mode: 'fork' | 'handoff'; worktreeAvailable: boolean; worktreeBranch: string | null }
  /** Edit the short purpose tag of a grouped forked sub-conversation. */
  | { kind: 'fork-tag'; thread: Thread }
  | { kind: 'new-section'; thread: Thread | null }
  | { kind: 'rename-section'; section: Section }
  | { kind: 'delete-section'; section: Section }
  /** The audit trail of a handover chain this thread belongs to. */
  | { kind: 'chain'; thread: Thread };

export type ThreadAction = 'rename' | 'pin' | 'fork' | 'handoff' | 'fork-lightweight' | 'fork-subchat' | 'fork-tag' | 'archive' | 'restore' | 'delete';

/** Why a thread cannot be forked right now, or null when it can. */
export function forkBlocked(thread: Thread, turnId?: string): string | null {
  if (!thread.agentSessionId) return t('actions.forkEmpty');
  if (thread.status === 'running' && !turnId) return t('actions.forkRunning');
  return null;
}

/**
 * Everything that can be done to a thread or a section from a menu: the menus themselves, the
 * dialogs they lead to, and the requests behind them. One instance serves the sidebar, the thread
 * header, the archive and the slash menu, so an action behaves the same wherever it is started.
 * The two menus hold different work: the list's rows manage how a thread sits in the list, the
 * open thread's own menu serves the conversation being read.
 */
export function useThreadActions(deps: Deps) {
  const { api, agents, sections, todos, chain, onTodosChange, onThreadChange, onThreadRemove, onThreadOpen, onMarkedUnread, onSectionsChange, onOpenTerminal, onOpenFiles, onOpenGit, onOpenTrace, onError, onBusy, onArchived, markdownOf, autotitle, board } = deps;
  const machineId = useReferenceMachine(api?.demo ? null : api);
  const warmReferenceMachine = () => { if (api && !api.demo) void referenceMachine(api).catch(() => {}); };
  const copyReference = async (thread: Thread) => {
    if (!machineId) { warmReferenceMachine(); onError(t('references.copyUnavailable')); return; }
    if (!await copyText(conversationPointer(thread, undefined, machineId))) onError(t('actions.copyFailed'));
  };
  /** The chain record when this thread belongs to any handover chain, automatic or manual. */
  const chainOf = (thread: Thread) => chain && (thread.handover
    || chain.threads.some(x => x.handover?.sourceId === thread.id)) ? chain : undefined;

  // The agent chosen in the auto-titles settings names the thread now, by the person's rules. The
  // title itself says when it lands; until then the thread says it is busy naming it.
  const autoName = async (thread: Thread) => {
    if (!api) return;
    onBusy({ threadId: thread.id, message: t('actions.autotitling') });
    try {
      const { title } = await api.autotitle(thread.id);
      onThreadChange({ ...thread, title });
    } catch (e) {
      onError(e);
    } finally {
      onBusy(null);
    }
  };
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);

  const patch = async (thread: Thread, body: Parameters<Api['updateThread']>[1], then?: () => void) => {
    if (!api) return;
    try {
      onThreadChange(await api.updateThread(thread.id, body));
      then?.();
    } catch (e) {
      onError(e);
    }
  };

  const fullForkBlocked = (thread: Thread, turnId?: string): string | null => {
    const caps = agents.find((a) => a.id === thread.agent)?.capabilities;
    if (!caps?.fork) return t('actions.forkFullUnsupported');
    if (turnId && !caps.forkAt) return t('actions.forkFullUnsupported');
    if (!thread.agentSessionId) return t('actions.forkEmpty');
    if (thread.status === 'running' && (!turnId || !caps.forkAt)) return t('actions.forkRunning');
    return null;
  };

  /** One-click lightweight fork continuation onto a chosen agent/model (with 0-token Arm 2 anchor). */
  const forkContinue = async (thread: Thread, target: HandoverTarget, firstMessage?: string) => {
    if (!api) return;
    onBusy({ threadId: thread.id, message: t('actions.forking') });
    try {
      const copy = await api.forkThread(thread.id, {
        grouped: true,
        lightweight: true,
        forkKind: 'handoff',
        agent: target.agent,
        ...(target.model !== undefined ? { model: target.model } : {}),
        continueFromSource: true,
        ...(firstMessage ? { firstMessage } : {}),
      });
      onThreadOpen(copy);
    } catch (e) {
      onError(e);
      throw e;
    } finally {
      onBusy(null);
    }
  };

  const openForkSheet = async (thread: Thread, mode: 'fork' | 'handoff') => {
    if (!api) return;
    const isHandoff = mode === 'handoff';
    const [options, fresh] = await Promise.all([
      api.forkOptions(thread.id, { lightweight: isHandoff }).catch(() => null),
      thread.agentSessionId ? Promise.resolve(thread) : api.thread(thread.id).catch(() => thread),
    ]);
    const target = fresh.agentSessionId && !thread.agentSessionId
      ? { ...fresh, status: thread.status }
      : thread;
    const effectiveMode = !isHandoff && fullForkBlocked(target) ? 'handoff' : mode;
    setDialog({
      kind: 'fork-subchat',
      thread: target,
      mode: effectiveMode,
      worktreeAvailable: Boolean(options?.worktree.available),
      worktreeBranch: options?.worktree.branch ?? null,
    });
  };

  /**
   * Codex-style 1-click native fork from a reply's fork icon (grouped under the parent topic).
   * Falls back cleanly to the "Continue in new chat" sheet while running or when the agent lacks native fork.
   */
  const forkDirect = async (thread: Thread, turnId?: string) => {
    if (!api) return;
    const fresh = thread.agentSessionId ? thread : await api.thread(thread.id).catch(() => thread);
    const target = fresh.agentSessionId && !thread.agentSessionId
      ? { ...fresh, status: thread.status }
      : thread;
    if (fullForkBlocked(target, turnId)) {
      return void openForkSheet(target, 'handoff');
    }
    onBusy({ threadId: target.id, message: t('actions.forking') });
    try {
      const copy = await api.forkThread(target.id, {
        ...(turnId ? { turnId } : {}),
        grouped: true,
        lightweight: false,
        forkKind: 'fork',
      });
      onThreadOpen(copy);
    } catch (e) {
      if (turnId && e instanceof Error && /own history|cannot stop a copy|whole conversation/.test(e.message)) {
        return onError(t('actions.forkMismatch'));
      }
      onError(e);
    } finally {
      onBusy(null);
    }
  };

  const run = (action: ThreadAction, thread: Thread) => {
    switch (action) {
      case 'fork':
        return void openForkSheet(thread, 'fork');
      case 'handoff':
      case 'fork-lightweight':
      case 'fork-subchat':
        return void openForkSheet(thread, 'handoff');
      case 'fork-tag':
        return setDialog({ kind: 'fork-tag', thread });
      case 'rename':
        return setDialog({ kind: 'rename', thread });
      case 'pin':
        return void patch(thread, { pinned: !thread.pinned });
      case 'archive':
        // The row is about to vanish; the undo is offered where the thread was noticed.
        return void patch(thread, { archived: true }, () => onArchived(thread));
      case 'restore':
        return void patch(thread, { archived: false });
      case 'delete':
        return setDialog({ kind: 'delete', thread });
    }
  };

  const copy = async (value: string | null | Promise<string>) => {
    try {
      const text = await value;
      if (!text) return;
      // The clipboard holds the result; only a failure needs saying.
      if (!(await copyText(text))) onError(t('actions.copyFailed'));
    } catch (e) {
      onError(e);
    }
  };

  /** The snooze menu, the same in every menu that holds it: quiet this thread until a chosen time,
   *  as an inbox is snoozed. Presets only — an exact minute is rarely the point. */
  const snoozeMenu = (thread: Thread): MenuEntry => {
    const set = (at: number) => () => void patch(thread, { snoozedUntil: new Date(at).toISOString() });
    const morning = (days: number) => {
      const d = new Date();
      d.setDate(d.getDate() + days);
      d.setHours(9, 0, 0, 0);
      return d.getTime();
    };
    return {
      id: 'snooze',
      label: t('actions.snooze'),
      icon: IconClock,
      submenu: [
        { id: 'snooze-1h', label: t('actions.snooze1h'), onSelect: set(Date.now() + 3600_000) },
        { id: 'snooze-morning', label: t('actions.snoozeMorning'), onSelect: set(morning(1)) },
        { id: 'snooze-week', label: t('actions.snoozeWeek'), onSelect: set(morning(7)) },
        // Only a thread that is quiet now has something to call off.
        ...(snoozed(thread) ? [
          'separator',
          { id: 'snooze-off', label: t('actions.snoozeOff'), onSelect: () => void patch(thread, { snoozedUntil: null }) },
        ] as MenuEntry[] : []),
      ],
    };
  };

  /** What a row's menu offers: how the thread sits in the list. As in ChatGPT and the Claude app,
   *  a row menu holds only row management, never the conversation's own tools. */
  const listItems = (thread: Thread): MenuEntry[] => {
    const filed = sections.some((s) => s.id === thread.sectionId);
    const linkedTodoIds = new Set(todos.filter((todo) => todo.threads.some((it) => it.id === thread.id)).map((todo) => todo.id));
    const planChoices = todos.filter((todo) => !todo.parentId).slice(0, 30);
    const stage = boardStage(thread);
    // Running or waiting on the person decides the column for now; only done can be marked over it.
    const waiting = threadAttention(thread, null);
    const live = thread.status === 'running' || (waiting !== null && waiting.kind !== 'unread');
    const place = (id: BoardStage): MenuEntry => ({
      id: `board-${id}`,
      label: t(`actions.board.${id}`),
      checked: stage === id,
      disabled: id !== 'done' && live,
      onSelect: () => void (id === 'done' ? patch(thread, { done: stage !== 'done' }) : stage !== id && patch(thread, { stage: id })),
    });
    return [
      ...(!thread.archivedAt ? [
        { id: 'fork', label: t('sidebar.fork'), icon: IconFork, disabled: Boolean(fullForkBlocked(thread)), onSelect: () => run('fork', thread) },
        { id: 'handoff', label: t('actions.handoffContinue'), icon: IconHandover, onSelect: () => run('handoff', thread) },
      ] : []),
      { id: 'pin', label: thread.pinned ? t('actions.unpin') : t('actions.pin'), icon: IconPin, onSelect: () => run('pin', thread) },
      {
        id: 'unread',
        label: thread.unread ? t('actions.markRead') : t('actions.markUnread'),
        icon: IconDot,
        onSelect: () => void patch(thread, { unread: !thread.unread }, thread.unread ? undefined : () => onMarkedUnread(thread)),
      },
      snoozeMenu(thread),
      { id: 'rename', label: t('actions.rename'), icon: IconEdit, onSelect: () => run('rename', thread) },
      ...(thread.forkGroup ? [
        { id: 'fork-tag', label: t('actions.editForkTag'), icon: IconFork, onSelect: () => run('fork-tag', thread) },
        { id: 'fork-promote', label: t('actions.forkPromote'), icon: IconExternal, onSelect: () => void patch(thread, { forkGroup: null }) },
      ] : []),
      ...(autotitle ? [{ id: 'autotitle', label: t('actions.autotitle'), icon: IconSparkle, onSelect: () => void autoName(thread) }] : []),
      ...(board ? [{ id: 'board', label: t('actions.board'), icon: IconBoard, submenu: (['backlog', 'doing', 'blocked', 'review', 'done'] as const).map(place) }] : []),
      {
        id: 'section',
        label: t('actions.moveToSection'),
        icon: IconList,
        submenu: [
          ...sections.map((s) => ({
            id: `section-${s.id}`,
            label: s.name,
            checked: s.id === thread.sectionId,
            onSelect: () => void patch(thread, { sectionId: s.id === thread.sectionId ? null : s.id }),
          })),
          ...(filed ? [{ id: 'unfile', label: t('actions.unfile'), onSelect: () => void patch(thread, { sectionId: null }) }] : []),
          ...(sections.length ? (['separator'] as MenuEntry[]) : []),
          { id: 'new-section', label: t('actions.newSection'), icon: IconPlus, onSelect: () => setDialog({ kind: 'new-section', thread }) },
        ],
      },
      {
        id: 'todo',
        label: t('actions.linkPlan'),
        icon: IconCheck,
        disabled: planChoices.length === 0,
        submenu: [
          ...planChoices.map((todo) => ({
            id: `todo-${todo.id}`,
            label: todo.text,
            checked: linkedTodoIds.has(todo.id),
            onSelect: () =>
              void guard(async () => {
                if (!api) return;
                if (linkedTodoIds.has(todo.id)) await api.unlinkTodo(todo.id, thread.id);
                else await api.linkTodo(todo.id, thread.id);
                onTodosChange();
              }),
          })),
          ...(planChoices.length ? (['separator'] as MenuEntry[]) : []),
          {
            id: 'new-todo',
            label: t('actions.newPlan'),
            icon: IconPlus,
            onSelect: () =>
              void guard(async () => {
                if (!api) return;
                const todo = await api.createTodo(thread.title || folderName(thread.cwd));
                await api.linkTodo(todo.id, thread.id);
                onTodosChange();
              }),
          },
        ],
      },
      'separator',
      thread.archivedAt
        ? { id: 'restore', label: t('archive.restore'), icon: IconArchive, onSelect: () => run('restore', thread) }
        : { id: 'archive', label: t('actions.archive'), icon: IconArchive, onSelect: () => run('archive', thread) },
      { id: 'delete', label: t('archive.delete'), icon: IconTrash, danger: true, onSelect: () => run('delete', thread) },
    ];
  };

  /** What the open thread's "…" offers: the conversation being read, plus the things worth doing
   *  to the thread itself while reading it, kept at the first level rather than behind a submenu. */
  const pageItems = (thread: Thread): MenuEntry[] => [
    { id: 'files', label: t('files.files'), icon: IconFiles, onSelect: () => onOpenFiles(thread) },
    ...(onOpenGit && !api?.demo ? [{ id: 'git', label: t('git.label'), icon: IconWorktree, onSelect: onOpenGit }] : []),
    { id: 'terminal', label: t('actions.terminal'), icon: IconTerminal, onSelect: onOpenTerminal },
    { id: 'pin', label: thread.pinned ? t('actions.unpin') : t('actions.pin'), icon: IconPin, onSelect: () => run('pin', thread) },
    {
      id: 'unread',
      label: thread.unread ? t('actions.markRead') : t('actions.markUnread'),
      icon: IconDot,
      onSelect: () => void patch(thread, { unread: !thread.unread }, thread.unread ? undefined : () => onMarkedUnread(thread)),
    },
    snoozeMenu(thread),
    { id: 'rename', label: t('actions.rename'), icon: IconEdit, onSelect: () => run('rename', thread) },
    ...(autotitle ? [{ id: 'autotitle', label: t('actions.autotitle'), icon: IconSparkle, onSelect: () => void autoName(thread) }] : []),
    {
      id: 'copy',
      label: t('common.copy'),
      icon: IconCopy,
      submenu: [
        { id: 'copy-reference', label: t('actions.copyReference'), onSelect: () => void copyReference(thread) },
        { id: 'copy-md', label: t('actions.copyMarkdown'), onSelect: () => void copy(markdownOf(thread)) },
        {
          id: 'copy-resume',
          label: t('actions.resumeCommand'),
          disabled: !thread.resumeCommand,
          onSelect: () => void copy(thread.resumeCommand),
        },
        {
          id: 'copy-id',
          label: t('statusPanel.sessionId'),
          disabled: !thread.agentSessionId,
          onSelect: () => void copy(thread.agentSessionId),
        },
        { id: 'copy-cwd', label: t('actions.cwd'), onSelect: () => void copy(thread.cwd) },
        ...(api && !api.demo ? [{ id: 'copy-handover', label: t('handover.copy'), onSelect: () => void copy(api.handoverSummary(thread.id).then((r) => ('summary' in r ? r.summary : ''))) }] : []),
      ],
    },
    ...(!thread.archivedAt
      ? [
          { id: 'fork', label: t('sidebar.fork'), icon: IconFork, disabled: Boolean(fullForkBlocked(thread)), onSelect: () => run('fork', thread) },
          { id: 'handoff', label: t('actions.handoffContinue'), icon: IconHandover, onSelect: () => run('handoff', thread) },
        ]
      : []),
    ...(chainOf(thread)
      ? [{ id: 'chain', label: t('handover.trail.title'), icon: IconHandover, onSelect: () => setDialog({ kind: 'chain', thread }) }]
      : []),
    // A phone reaches the trace from the overview instead, so the bar keeps no button of its own.
    ...(onOpenTrace ? [{ id: 'trace', label: t('trace.openTrace'), icon: IconTrace, onSelect: onOpenTrace }] : []),
    'separator',
    thread.archivedAt
      ? { id: 'restore', label: t('archive.restore'), icon: IconArchive, onSelect: () => run('restore', thread) }
      : { id: 'archive', label: t('actions.archive'), icon: IconArchive, onSelect: () => run('archive', thread) },
    { id: 'delete', label: t('archive.delete'), icon: IconTrash, danger: true, onSelect: () => run('delete', thread) },
  ];

  const sectionItems = (section: Section): MenuEntry[] => [
    { id: 'rename', label: t('actions.renameSection'), icon: IconEdit, onSelect: () => setDialog({ kind: 'rename-section', section }) },
    { id: 'delete', label: t('actions.deleteSection'), icon: IconTrash, danger: true, onSelect: () => setDialog({ kind: 'delete-section', section }) },
  ];

  const close = () => setDialog(null);
  const guard = async (work: () => Promise<void>) => {
    try {
      await work();
    } catch (e) {
      onError(e);
    }
    close();
  };

  const removeThread = async (thread: Thread, opts: { agentSession: boolean; worktree: boolean; force?: boolean }) => {
    if (!api) return;
    try {
      await api.deleteThread(thread.id, opts);
      onThreadRemove(thread.id);
    } catch (e) {
      const details = e instanceof ApiError ? e.details : {};
      // Nothing has been deleted in either case; the thread is still there to decide about.
      if (details.code === 'worktree_dirty') {
        return setDialog({ kind: 'delete-worktree', thread, agentSession: opts.agentSession, changes: Number(details.changes) || 0, commits: Number(details.commits) || 0 });
      }
      // A shared worktree refuses the delete: the thread stays, so the refusal is said beside it.
      onError(details.code === 'worktree_shared' ? t('actions.worktreeShared') : e);
    }
    close();
  };

  let dialogNode: ReactNode = null;
  if (dialog && api) {
    if (dialog.kind === 'rename') {
      const { thread } = dialog;
      dialogNode = (
        <Dialog
          title={t('actions.renameThread')}
          input={{ initial: thread.title, label: t('actions.threadName') }}
          confirmLabel={t('actions.save')}
          onConfirm={(title) => void guard(async () => onThreadChange(await api.updateThread(thread.id, { title: title ?? thread.title })))}
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'delete') {
      const { thread } = dialog;
      const agent = agents.find((a) => a.id === thread.agent);
      const canDeleteSession = Boolean(agent?.capabilities.deleteSession && thread.agentSessionId);
      const tree = thread.worktree ?? null;
      // The checkboxes that apply, in the order they are shown.
      const extras = [
        ...(canDeleteSession
          ? [{ id: 'session', label: t('actions.deleteSession', agent?.label ?? thread.agent), hint: t('actions.deleteSessionHint') }]
          : []),
        ...(tree ? [{ id: 'worktree', label: t('actions.deleteWorktree'), hint: t('actions.deleteWorktreeHint', tree.branch, folderName(tree.of)) }] : []),
      ];
      dialogNode = (
        <Dialog
          title={t('actions.deleteTitle')}
          message={t('actions.deleteMessage', thread.title, agent?.label ?? thread.agent, Boolean(tree))}
          options={extras}
          confirmLabel={t('archive.delete')}
          danger
          onConfirm={(_, checked) => {
            const picked = (id: string) => checked[extras.findIndex((x) => x.id === id)] === true;
            void removeThread(thread, { agentSession: picked('session'), worktree: picked('worktree') });
          }}
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'delete-worktree') {
      const { thread, agentSession, changes, commits } = dialog;
      const left = formatList([changes > 0 && t('actions.uncommitted', changes), commits > 0 && t('actions.unpushed', commits)].filter((x): x is string => Boolean(x)));
      dialogNode = (
        <Dialog
          title={t('actions.worktreeDirty')}
          message={t('actions.worktreeDirtyMessage', left)}
          confirmLabel={t('actions.deleteAnyway')}
          danger
          onConfirm={() => void removeThread(thread, { agentSession, worktree: true, force: true })}
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'fork-subchat') {
      const { thread, mode, worktreeAvailable, worktreeBranch } = dialog;
      dialogNode = (
        <ForkSubchatDialog
          api={api}
          agents={agents}
          thread={thread}
          mode={mode}
          worktreeAvailable={worktreeAvailable}
          worktreeBranch={worktreeBranch}
          onConfirm={({ agent, model, firstMessage, worktree, lightweight, continueFromSource, forkKind }) => {
            close();
            onBusy({ threadId: thread.id, message: worktree ? t('actions.forkingWorktree') : t('actions.forking') });
            void api
              .forkThread(thread.id, {
                agent,
                model,
                worktree,
                grouped: true,
                lightweight,
                continueFromSource,
                forkKind,
                firstMessage: firstMessage || undefined,
              })
              .then((copy) => onThreadOpen(copy))
              .catch((e) => onError(e))
              .finally(() => onBusy(null));
          }}
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'fork-tag') {
      const { thread } = dialog;
      dialogNode = (
        <Dialog
          title={t('actions.editForkTagTitle')}
          input={{ initial: thread.forkTag ?? '', label: t('actions.forkTagLabel') }}
          confirmLabel={t('actions.save')}
          onConfirm={(value) => void guard(async () => onThreadChange(await api.updateThread(thread.id, { forkTag: (value ?? '').trim() || null })))}
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'new-section') {
      const { thread } = dialog;
      dialogNode = (
        <Dialog
          title={t('sidebar.newSection')}
          message={t('actions.sectionMessage')}
          input={{ initial: '', label: t('actions.sectionName') }}
          confirmLabel={t('actions.create')}
          onConfirm={(name) =>
            void guard(async () => {
              const section = await api.createSection(name ?? '');
              if (thread) onThreadChange(await api.updateThread(thread.id, { sectionId: section.id }));
              onSectionsChange();
            })
          }
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'rename-section') {
      const { section } = dialog;
      dialogNode = (
        <Dialog
          title={t('actions.renameSection')}
          input={{ initial: section.name, label: t('actions.sectionName') }}
          confirmLabel={t('actions.save')}
          onConfirm={(name) =>
            void guard(async () => {
              await api.renameSection(section.id, name ?? section.name);
              onSectionsChange();
            })
          }
          onCancel={close}
        />
      );
    } else if (dialog.kind === 'chain') {
      const record = chainOf(dialog.thread);
      const { thread } = dialog;
      dialogNode = record && (
        <HandoverChainDialog api={api} thread={thread} threads={record.threads} agentLabel={(id) => agents.find(a => a.id === id)?.label ?? id}
          merged={record.isMerged(thread)} onToggle={() => record.onToggle(thread)} onSelect={record.onSelect}
          onRefresh={record.onRefresh} onClose={close} />
      );
    } else {
      const { section } = dialog;
      dialogNode = (
        <Dialog
          title={t('actions.deleteSectionTitle', section.name)}
          message={t('actions.deleteSectionMessage')}
          confirmLabel={t('actions.deleteSection')}
          danger
          onConfirm={() =>
            void guard(async () => {
              await api.deleteSection(section.id);
              onSectionsChange();
            })
          }
          onCancel={close}
        />
      );
    }
  }

  const layer = (
    <>
      {menu && (
        <Menu
          label={'thread' in menu ? menu.thread.title : menu.section.name}
          x={menu.x}
          y={menu.y}
          corner={menu.where === 'page' ? 'top-right' : 'top-left'}
          items={'thread' in menu ? (menu.where === 'list' ? listItems(menu.thread) : pageItems(menu.thread)) : sectionItems(menu.section)}
          dense={menu.where === 'page'}
          // The quota belongs to the conversation being read, not to a row in the list.
          header={api && menu.where === 'page' && 'thread' in menu && agents.some(agent => agent.id === menu.thread.agent && !agent.hidden && agent.capabilities.quota)
            ? <ChatQuotaMenu api={api} agent={menu.thread.agent} label={agents.find(agent => agent.id === menu.thread.agent)!.label} close={() => setMenu(null)} /> : undefined}
          onClose={() => setMenu(null)}
        />
      )}
      {dialogNode}
    </>
  );

  return {
    openThreadMenu: (thread: Thread, x: number, y: number) => setMenu({ thread, x, y, where: 'list' }),
    /** Opens the open thread's own "…": the conversation's tools, without the list's management. */
    openPageMenu: (thread: Thread, x: number, y: number) => { warmReferenceMachine(); setMenu({ thread, x, y, where: 'page' }); },
    openSectionMenu: (section: Section, x: number, y: number) => setMenu({ section, x, y, where: 'list' }),
    copyConversation: (thread: Thread) => void copy(markdownOf(thread)),
    newSection: () => setDialog({ kind: 'new-section', thread: null }),
    /** Opens the unified Fork/Handoff sheet. */
    autoHandover: (thread: Thread) => void openForkSheet(thread, 'handoff'),
    /** One-click lightweight fork continuation onto a target agent/model. */
    forkContinue,
    /** Opens the handover chain, where an automatic takeover can be called off. */
    chain: (thread: Thread) => setDialog({ kind: 'chain', thread }),
    /** Whether the thread is part of a handover chain, so the chain dialog applies to it. */
    inChain: (thread: Thread) => Boolean(chainOf(thread)),
    /**
     * Hands the thread to `target` at once: the hub continues it in a new conversation in the same
     * place, with its queue and collaboration lead. For when the thread's own agent cannot go on, so
     * it is not asked. `note` is what the person adds for the successor.
     */
    takeOver: async (thread: Thread, target: string | HandoverTarget, note?: string, references: string[] = []) => {
      if (!api) return;
      try {
        onThreadOpen(await api.takeOver(thread.id, target, note, references));
      } catch (e) {
        onError(e);
        throw e;
      }
    },
    /** Codex-style 1-click native fork (or fallback handoff sheet while running / unsupported). */
    forkAt: (thread: Thread, turnId?: string) => void forkDirect(thread, turnId),
    /** Opens the "Continue in new chat" composer sheet (0-token anchor + cross-Agent/Model switching). */
    handoff: (thread: Thread) => void openForkSheet(thread, 'handoff'),
    /** Alias for opening the new-session continuation sheet from sidebar / recovery controls. */
    forkSubchat: (thread: Thread) => void openForkSheet(thread, 'handoff'),
    run,
    layer,
  };
}
