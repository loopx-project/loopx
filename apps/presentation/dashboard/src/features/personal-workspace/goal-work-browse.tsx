import {createContext, useContext, useState, type ReactNode} from "react";

/** Presentation choices shared by the Goal's overview and full task browser. */
export type GoalWorkBrowseState = {
  lens: "owner" | "board" | "map";
  view: "board" | "list";
  owner: string;
  filter: "all" | "needsYou" | "open" | "blocked" | "done";
  query: string;
};

export const defaultGoalWorkBrowse: GoalWorkBrowseState = {
  lens: "owner", view: "board", owner: "all", filter: "all", query: "",
};

export function readGoalWorkBrowse(search: Record<string, unknown>, goalId: string, source: string): GoalWorkBrowseState {
  if (!goalId || search.workGoal !== goalId || search.workSource !== source) return {...defaultGoalWorkBrowse};
  return {
    lens: search.workLens === "board" || search.workLens === "map" ? search.workLens : "owner",
    view: search.taskView === "list" ? "list" : "board",
    owner: typeof search.taskOwner === "string" && search.taskOwner.length <= 200 ? search.taskOwner : "all",
    filter: search.taskFilter === "needsYou" || search.taskFilter === "open" || search.taskFilter === "blocked" || search.taskFilter === "done" ? search.taskFilter : "all",
    query: typeof search.taskQuery === "string" ? search.taskQuery.slice(0, 256) : "",
  };
}

export function writeGoalWorkBrowse(value: GoalWorkBrowseState, goalId: string, source: string) {
  return {
    workGoal: goalId, workSource: source,
    workLens: value.lens === "owner" ? undefined : value.lens,
    taskView: value.view === "board" ? undefined : value.view,
    taskOwner: value.owner === "all" ? undefined : value.owner,
    taskFilter: value.filter === "all" ? undefined : value.filter,
    taskQuery: value.query || undefined,
  };
}

type BrowseChange = (patch: Partial<GoalWorkBrowseState>, replace?: boolean) => void;
const GoalWorkBrowseContext = createContext<readonly [GoalWorkBrowseState, BrowseChange] | null>(null);

/** The existing router owns history; this provider never writes browser history. */
export function GoalWorkBrowseProvider({value, onChange, children}: {
  value: GoalWorkBrowseState; onChange: BrowseChange; children: ReactNode;
}) {
  return <GoalWorkBrowseContext.Provider value={[value, onChange]}>{children}</GoalWorkBrowseContext.Provider>;
}

export function useGoalWorkBrowse(): readonly [GoalWorkBrowseState, BrowseChange] {
  const shared = useContext(GoalWorkBrowseContext);
  // Standalone consumers retain their existing in-memory behavior.
  const [local, setLocal] = useState(defaultGoalWorkBrowse);
  return shared ?? [local, patch => setLocal(current => ({...current, ...patch}))];
}
