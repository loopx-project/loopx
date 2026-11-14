import { useState, type ReactNode } from 'react';
import { ApiError, type AgentInfo, type Api, type Section, type Thread, type Todo } from '../api';
import { copyText } from '../clipboard';
import { folderName } from '../paths';
import { ChoiceDialog, Dialog } from './Dialog';
import { Menu, type MenuEntry } from './Menu';
import { IconArchive, IconCheck, IconCopy, IconDot, IconEdit, IconFork, IconList, IconPin, IconPlus, IconTrash, IconWorktree } from './icons';
import type { ToastState } from './Toast';
import { formatList, t } from '../i18n';

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
  onError: (e: unknown) => void;
  toast: (t: ToastState | null) => void;
  /** The whole conversation as Markdown, for the copy menu. */
  markdownOf: (thread: Thread) => Promise<string>;
}

type MenuState = { x: number; y: number } & ({ thread: Thread } | { section: Section });

type DialogState =
  | { kind: 'rename'; thread: Thread }
  | { kind: 'delete'; thread: Thread }
  /** The worktree to be deleted holds work that exists nowhere else: asked once more, with the count. */
  | { kind: 'delete-worktree'; thread: Thread; agentSession: boolean; changes: number; commits: number }
  /** Where the copy should run: beside the original, or in a git worktree of its own. */
  | { kind: 'fork'; thread: Thread; turnId?: string; branch: string | null; changes: number }
  | { kind: 'new-section'; thread: Thread | null }
  | { kind: 'rename-section'; section: Section }
  | { kind: 'delete-section'; section: Section };

export type ThreadAction = 'rename' | 'pin' | 'fork' | 'archive' | 'restore' | 'delete';

/** Why a thread cannot be forked right now, or null when it can. */
export function forkBlocked(thread: Thread): string | null {
  if (!thread.agentSessionId) return t('actions.forkEmpty');
  if (thread.status === 'running') return t('actions.forkRunning');
  return null;
}

/**
 * Everything that can be done to a thread or a section from a menu: the menu itself, the dialogs
 * it leads to, and the requests behind them. One instance serves the sidebar, the thread header,
 * the archive and the slash menu, so an action behaves the same wherever it is started.
 */
export function useThreadActions(deps: Deps) {
  const { api, agents, sections, todos, onTodosChange, onThreadChange, onThreadRemove, onThreadOpen, onMarkedUnread, onSectionsChange, onError, toast, markdownOf } = deps;

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
    const blocked = forkBlocked(thread);
    if (blocked) return toast({ message: blocked });
    if (!where && agents.find((a) => a.id === thread.agent)?.capabilities.forkInto) {
      // An older daemon has no such question to answer; then the copy is made in place, as before.
      const options = await api.forkOptions(thread.id).catch(() => null);
      if (options?.worktree.available) {
        return setDialog({ kind: 'fork', thread, turnId, branch: options.worktree.branch ?? null, changes: options.worktree.changes ?? 0 });
      }
    }
    toast({ message: where === 'worktree' ? t('actions.forkingWorktree') : t('actions.forking'), busy: true });
    try {
      const copy = await api.forkThread(thread.id, { turnId, worktree: where === 'worktree' });
      onThreadOpen(copy);
      toast({ message: where === 'worktree' && copy.worktree ? t('actions.forkedWorktree', copy.worktree.branch) : t('actions.forked') });
    } catch (e) {
      toast(null);
      // The agent keeps its own record of the conversation. A turn it has no record of (one that
      // never reached it, say) is not a place its copy can stop.
      if (turnId && e instanceof Error && /own history|cannot stop a copy|whole conversation/.test(e.message)) {
        return toast({ message: t('actions.forkMismatch') });
      }
      onError(e);
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
        return void patch(thread, { archived: true }, () =>
          toast({ message: t('archive.title'), action: { label: t('actions.undo'), run: () => void patch(thread, { archived: false }) } }),
        );
      case 'restore':
        return void patch(thread, { archived: false }, () => toast({ message: t('actions.restored') }));
      case 'delete':
        return setDialog({ kind: 'delete', thread });
    }
  };

  const copy = async (value: string | null | Promise<string>, what: string) => {
    try {
      const text = await value;
      if (!text) return;
      toast({ message: (await copyText(text)) ? t('actions.copied', what) : t('actions.copyFailed') });
    } catch (e) {
      onError(e);
    }
  };

  const threadItems = (thread: Thread): MenuEntry[] => {
    const filed = sections.some((s) => s.id === thread.sectionId);
    const linkedTodoIds = new Set(todos.filter((todo) => todo.threads.some((it) => it.id === thread.id)).map((todo) => todo.id));
    const planChoices = todos.filter((todo) => !todo.parentId).slice(0, 30);
    return [
      { id: 'rename', label: t('actions.rename'), icon: IconEdit, onSelect: () => run('rename', thread) },
      { id: 'pin', label: thread.pinned ? t('actions.unpin') : t('actions.pin'), icon: IconPin, onSelect: () => run('pin', thread) },
      {
        id: 'unread',
        label: thread.unread ? t('actions.markRead') : t('actions.markUnread'),
        icon: IconDot,
        onSelect: () => void patch(thread, { unread: !thread.unread }, thread.unread ? undefined : () => onMarkedUnread(thread)),
      },
      'separator',
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
      {
        id: 'copy',
        label: t('common.copy'),
        icon: IconCopy,
        submenu: [
          { id: 'copy-md', label: t('actions.copyMarkdown'), onSelect: () => void copy(markdownOf(thread), t('actions.what.conversation')) },
          {
            id: 'copy-resume',
            label: t('actions.resumeCommand'),
            disabled: !thread.resumeCommand,
            onSelect: () => void copy(thread.resumeCommand, t('actions.what.resumeCommand')),
          },
          {
            id: 'copy-id',
            label: t('statusPanel.sessionId'),
            disabled: !thread.agentSessionId,
            onSelect: () => void copy(thread.agentSessionId, t('actions.what.sessionId')),
          },
          { id: 'copy-cwd', label: t('actions.cwd'), onSelect: () => void copy(thread.cwd, t('actions.what.cwd')) },
        ],
      },
      ...(agents.find((a) => a.id === thread.agent)?.capabilities.fork
        ? [{ id: 'fork', label: t('sidebar.fork'), icon: IconFork, disabled: forkBlocked(thread) !== null, onSelect: () => run('fork', thread) }]
        : []),
      'separator',
      thread.archivedAt
        ? { id: 'restore', label: t('archive.restore'), icon: IconArchive, onSelect: () => run('restore', thread) }
        : { id: 'archive', label: t('actions.archive'), icon: IconArchive, onSelect: () => run('archive', thread) },
      { id: 'delete', label: t('archive.delete'), icon: IconTrash, danger: true, onSelect: () => run('delete', thread) },
    ];
  };

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
      toast({ message: opts.worktree ? t('actions.deletedWorktree') : t('actions.deleted') });
    } catch (e) {
      const details = e instanceof ApiError ? e.details : {};
      // Nothing has been deleted in either case; the thread is still there to decide about.
      if (details.code === 'worktree_dirty') {
        return setDialog({ kind: 'delete-worktree', thread, agentSession: opts.agentSession, changes: Number(details.changes) || 0, commits: Number(details.commits) || 0 });
      }
      if (details.code === 'worktree_shared') toast({ message: t('actions.worktreeShared') });
      else onError(e);
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
          items={'thread' in menu ? threadItems(menu.thread) : sectionItems(menu.section)}
          onClose={() => setMenu(null)}
        />
      )}
      {dialogNode}
    </>
  );

  return {
    openThreadMenu: (thread: Thread, x: number, y: number) => setMenu({ thread, x, y }),
    openSectionMenu: (section: Section, x: number, y: number) => setMenu({ section, x, y }),
    copyConversation: (thread: Thread) => void copy(markdownOf(thread), t('actions.what.conversation')),
    newSection: () => setDialog({ kind: 'new-section', thread: null }),
    /** Forks so that the copy ends with the given turn. */
    forkAt: (thread: Thread, turnId: string) => void fork(thread, turnId),
    run,
    layer,
  };
}
