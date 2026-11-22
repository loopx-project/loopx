import { useState, type ReactNode } from 'react';
import { ApiError, type AgentInfo, type Api, type HandoverTarget, type Section, type Thread, type Todo } from '../api';
import { copyText } from '../clipboard';
import { folderName } from '../paths';
import { ChoiceDialog, Dialog } from './Dialog';
import { HandoverChainDialog } from './HandoverTrail';
import { HandoverDialog } from './HandoverDialog';
import { Menu, type MenuEntry } from './Menu';
import { ChatQuotaMenu } from './ChatQuotaMenu';
import { IconArchive, IconBoard, IconCheck, IconCopy, IconDot, IconEdit, IconFiles, IconFork, IconHand, IconHandover, IconList, IconPin, IconPlus, IconSparkle, IconTerminal, IconTrace, IconTrash, IconWorktree } from './icons';
import { formatList, t } from '../i18n';
import { boardStage, threadAttention, type BoardStage } from '../sidebar';

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
  /** Where the copy should run: beside the original, or in a git worktree of its own. */
  | { kind: 'fork'; thread: Thread; turnId?: string; branch: string | null; changes: number }
  | { kind: 'new-section'; thread: Thread | null }
  | { kind: 'rename-section'; section: Section }
  | { kind: 'delete-section'; section: Section }
  /** Get source-session coordinates and progress for another agent to continue in the same directory. */
  | { kind: 'handover'; thread: Thread }
  /** The audit trail of a handover chain this thread belongs to. */
  | { kind: 'chain'; thread: Thread };

export type ThreadAction = 'rename' | 'pin' | 'fork' | 'archive' | 'restore' | 'delete';

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

  // The agent copies its side of the conversation, which can take a few seconds. With a turn, the
  // copy ends where that turn ended; the agent's own history is read to find that place.
  //
  // `where` is left out when the user has not been asked yet. They are asked only when there is
  // something to choose: the agent can move a copy, and the thread stands in a git repository.
  const fork = async (thread: Thread, turnId?: string, where?: 'here' | 'worktree') => {
    if (!api) return;
    const blocked = forkBlocked(thread, turnId);
    if (blocked) return onError(blocked);
    if (!where && agents.find((a) => a.id === thread.agent)?.capabilities.forkInto) {
      // An older daemon has no such question to answer; then the copy is made in place, as before.
      const options = await api.forkOptions(thread.id).catch(() => null);
      if (options?.worktree.available) {
        return setDialog({ kind: 'fork', thread, turnId, branch: options.worktree.branch ?? null, changes: options.worktree.changes ?? 0 });
      }
    }
    onBusy({ threadId: thread.id, message: where === 'worktree' ? t('actions.forkingWorktree') : t('actions.forking') });
    try {
      const copy = await api.forkThread(thread.id, { turnId, worktree: where === 'worktree' });
      onThreadOpen(copy);
    } catch (e) {
      // The agent keeps its own record of the conversation. A turn it has no record of (one that
      // never reached it, say) is not a place its copy can stop.
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
        return void fork(thread);
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
      { id: 'pin', label: thread.pinned ? t('actions.unpin') : t('actions.pin'), icon: IconPin, onSelect: () => run('pin', thread) },
      {
        id: 'unread',
        label: thread.unread ? t('actions.markRead') : t('actions.markUnread'),
        icon: IconDot,
        onSelect: () => void patch(thread, { unread: !thread.unread }, thread.unread ? undefined : () => onMarkedUnread(thread)),
      },
      { id: 'rename', label: t('actions.rename'), icon: IconEdit, onSelect: () => run('rename', thread) },
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
    { id: 'rename', label: t('actions.rename'), icon: IconEdit, onSelect: () => run('rename', thread) },
    ...(autotitle ? [{ id: 'autotitle', label: t('actions.autotitle'), icon: IconSparkle, onSelect: () => void autoName(thread) }] : []),
    {
      id: 'copy',
      label: t('common.copy'),
      icon: IconCopy,
      submenu: [
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
      ],
    },
    { id: 'continuation', label: t('actions.forkAndHandover'), icon: IconHand, submenu: [
      ...(agents.find((a) => a.id === thread.agent)?.capabilities.fork
        ? [{ id: 'fork', label: t('sidebar.fork'), icon: IconFork, disabled: forkBlocked(thread) !== null, onSelect: () => run('fork', thread) }]
        : []),
      { id: 'handover', label: t('handover.title'), icon: IconHand, onSelect: () => setDialog({ kind: 'handover', thread }) },
      ...(chainOf(thread) ? [{ id: 'chain', label: t('handover.trail.title'), icon: IconHandover, onSelect: () => setDialog({ kind: 'chain', thread }) }] : []),
    ] },
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
    } else if (dialog.kind === 'fork') {
      const { thread, turnId, branch, changes } = dialog;
      dialogNode = (
        <ChoiceDialog
          title={t('actions.forkTitle')}
          choices={[
            { id: 'here', icon: <IconFork size={18} />, label: t('actions.forkHere'), hint: t('actions.forkHereHint') },
            {
              id: 'worktree',
              icon: <IconWorktree size={18} />,
              label: t('actions.forkWorktree'),
              hint: t('actions.forkWorktreeHint', branch, changes),
            },
          ]}
          onPick={(id) => {
            close();
            void fork(thread, turnId, id === 'worktree' ? 'worktree' : 'here');
          }}
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
    } else if (dialog.kind === 'handover') {
      const { thread } = dialog;
      dialogNode = (
        <HandoverDialog api={api} thread={thread} agents={agents} onOpen={onThreadOpen} onClose={close} />
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
    openPageMenu: (thread: Thread, x: number, y: number) => setMenu({ thread, x, y, where: 'page' }),
    openSectionMenu: (section: Section, x: number, y: number) => setMenu({ section, x, y, where: 'list' }),
    copyConversation: (thread: Thread) => void copy(markdownOf(thread)),
    newSection: () => setDialog({ kind: 'new-section', thread: null }),
    /** Opens the handover, to pick who takes over or copy what it says. */
    handover: (thread: Thread) => setDialog({ kind: 'handover', thread }),
    /**
     * Hands the thread to `target` at once: the hub continues it in a new conversation in the same
     * place, with its queue and collaboration lead. For when the thread's own agent cannot go on, so
     * it is not asked. `note` is what the person adds for the successor.
     */
    takeOver: async (thread: Thread, target: string | HandoverTarget, note?: string) => {
      if (!api) return;
      try {
        onThreadOpen(await api.takeOver(thread.id, target, note));
      } catch (e) {
        onError(e);
        throw e;
      }
    },
    /** Forks so that the copy ends with the given turn. */
    forkAt: (thread: Thread, turnId: string) => void fork(thread, turnId),
    run,
    layer,
  };
}
