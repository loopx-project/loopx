import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { WorkspaceGoalTab } from "./personal-workspace-model";

/** Keep visited views alive within one Goal; each view owns its scroll position. */
export function GoalWorkspacePanels({ activeTab, panels, scrollRef }: {
  activeTab: WorkspaceGoalTab;
  panels: Record<WorkspaceGoalTab, ReactNode>;
  scrollRef: RefObject<HTMLDivElement | null>;
}) {
  const [visited, setVisited] = useState(() => new Set([activeTab]));
  const positions = useRef<Partial<Record<WorkspaceGoalTab, number>>>({});
  const currentTab = useRef(activeTab);
  useEffect(() => setVisited(current => current.has(activeTab) ? current : new Set([...current, activeTab])), [activeTab]);
  useLayoutEffect(() => {
    currentTab.current = activeTab;
    const scroll = scrollRef.current;
    if (scroll) scroll.scrollTop = positions.current[activeTab] ?? 0;
  }, [activeTab, scrollRef]);
  useEffect(() => {
    // The ancestor's scroll ref is ready after layout effects finish mounting.
    const scroll = scrollRef.current;
    if (!scroll) return;
    const remember = () => { positions.current[currentTab.current] = scroll.scrollTop; };
    scroll.addEventListener("scroll", remember, { passive: true });
    return () => scroll.removeEventListener("scroll", remember);
  }, [scrollRef]);
  return (Object.keys(panels) as WorkspaceGoalTab[]).map(tab => visited.has(tab) || tab === activeTab
    ? <div className="personal-goal-view-panel" data-goal-panel={tab} hidden={tab !== activeTab} key={tab}>{panels[tab]}</div>
    : null);
}
