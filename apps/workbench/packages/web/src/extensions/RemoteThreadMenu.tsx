import { useEffect, useState } from 'react';
import type { Api, AgentInfo, Section, Thread, Todo } from '../api';
import { useThreadActions } from '../components/ThreadActions';
import { buildTimeline, toMarkdown } from '../timeline';

/** The sidebar and extension cards use the same actions, scoped to the thread's own machine. */
export function RemoteThreadMenu({ api, target, board, onChange, onOpen, onRemove, onError }: {
  api: Api; target: { thread: Thread; x: number; y: number }; board: boolean;
  onChange: (thread: Thread) => void; onOpen: (thread: Thread) => void;
  onRemove: (id: string) => void; onError: (error: unknown) => void;
}) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [sections, setSections] = useState<Section[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const reloadSections = () => { void api.sections().then(setSections).catch(onError); };
  const reloadTodos = () => { void api.todos().then(setTodos).catch(onError); };
  useEffect(() => {
    let alive = true;
    void Promise.all([api.agents(), api.sections(), api.todos()]).then(([a, s, t]) => {
      if (alive) { setAgents(a); setSections(s); setTodos(t); }
    }).catch(onError);
    return () => { alive = false; };
  }, [api]); // eslint-disable-line react-hooks/exhaustive-deps
  const actions = useThreadActions({
    api, agents, sections, todos, board,
    onThreadChange: onChange, onThreadRemove: onRemove, onThreadOpen: onOpen,
    onMarkedUnread: () => undefined, onTodosChange: reloadTodos, onSectionsChange: reloadSections,
    onOpenTerminal: () => onOpen(target.thread), onOpenFiles: onOpen,
    onBusy: () => undefined, onArchived: () => undefined, onError,
    markdownOf: async thread => toMarkdown(thread.title, buildTimeline(await api.events(thread.id)).items),
  });
  useEffect(() => { actions.openThreadMenu(target.thread, target.x, target.y); }, [target]); // eslint-disable-line react-hooks/exhaustive-deps
  return actions.layer;
}
