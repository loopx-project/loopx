import {useEffect, useState} from "react";
import {ChatApiError, fetchChatSessions, fetchLoopXMode, fetchLoopXTeamWork, fetchManagedGoalResults, readLoopXTeamWork, readManagedGoalResult, type ManagedGoalResultRow} from "../../data/chat";
import {TeamArtifactReport, isMarkdownArtifact, managedReportArtifact, type TeamArtifact} from "./team-artifact-content";

type Readback = {kind: "waiting" | "unavailable" | "multiple"} | {
  kind: "adopted"; artifact: TeamArtifact; agentId: string;
};
type ManagedReadback = {kind: "waiting" | "unavailable" | "multiple"} | {
  kind: "accepted"; artifact: TeamArtifact; agentId: string; title: string;
};

/**
 * The Todo identities a Goal conversation itself reports work for, or the
 * coordinator bindings it was configured with. A conversation can only own
 * delegation work that its own mode names, so this is the relevance index for a
 * plan: one Goal holds many conversations and most of them are unrelated.
 */
type SessionWorkIndex = {sessionId: string; todoIds: Set<string>};
type GoalWorkIndex = {readAt: number; sessions: SessionWorkIndex[]; unclassified: boolean};

const WORK_INDEX_WINDOW_MS = 30_000;
/** Bounded read: more related conversations than a plan can have lanes means the
 *  readback cannot be attributed, so it withholds instead of guessing. */
const RELATED_SESSION_LIMIT = 8;
const workIndexes = new Map<string, GoalWorkIndex>();
const workIndexReads = new Map<string, Promise<GoalWorkIndex>>();
/** Goal-wide inventory plus the exact Todo ids the server could not verify, so a
 *  plan scopes its readback to its own ids instead of the whole Goal's health. */
type ManagedGoalIndex = {readAt: number; rows: ManagedGoalResultRow[]; unavailableTodoIds: Set<string>};
const managedIndexes = new Map<string, ManagedGoalIndex>();
const managedIndexReads = new Map<string, Promise<ManagedGoalIndex>>();

/** A 4xx is the server declining this conversation's team readback: without a
 *  coordinator identity it cannot own delegation work, so it is unrelated rather
 *  than unreadable. Anything else leaves the conversation unclassified. */
function declinedByServer(error: unknown): boolean {
  const status = (error as ChatApiError | undefined)?.payload?.http_status;
  return typeof status === "number" && status >= 400 && status < 500;
}

async function collectGoalWorkIndex(goalId: string): Promise<GoalWorkIndex> {
  const listed = await fetchChatSessions({goalId, channelId: `goal.${goalId}`});
  const sessions: SessionWorkIndex[] = [];
  let unclassified = false;
  for (let offset = 0; offset < listed.sessions.length; offset += 8) {
    const batch = listed.sessions.slice(offset, offset + 8);
    const modes = await Promise.allSettled(batch.map(session => fetchLoopXMode(session.session_id)));
    modes.forEach((mode, index) => {
      if (mode.status === "rejected") {
        unclassified ||= !declinedByServer(mode.reason);
        return;
      }
      if (!mode.value.settings.agent_id) return;
      const todoIds = new Set<string>();
      for (const row of [...mode.value.deliveries, ...mode.value.members]) {
        if (row.todo_id) todoIds.add(row.todo_id);
      }
      if (todoIds.size) sessions.push({sessionId: batch[index].session_id, todoIds});
    });
  }
  return {readAt: Date.now(), sessions, unclassified};
}

/**
 * Read every Goal conversation's work index at most once per window, and share
 * that one read with every applied card instead of rescanning the Goal per card.
 * An explicit refresh re-reads: the owner asked for the current state.
 */
async function readGoalWorkIndex(goalId: string, force: boolean): Promise<GoalWorkIndex> {
  const running = workIndexReads.get(goalId);
  if (running) return running;
  const cached = workIndexes.get(goalId);
  if (!force && cached && Date.now() - cached.readAt < WORK_INDEX_WINDOW_MS) return cached;
  const read = collectGoalWorkIndex(goalId)
    .then(index => {workIndexes.set(goalId, index); return index;})
    .finally(() => {workIndexReads.delete(goalId);});
  workIndexReads.set(goalId, read);
  return read;
}

/** A plan links to work through the Todo identities written by its apply receipt. */
async function readAdoptedResult(goalId: string, todoIds: Set<string>, force: boolean): Promise<Readback> {
  let index: GoalWorkIndex;
  try {
    index = await readGoalWorkIndex(goalId, force);
  } catch { /* The Goal's conversations could not be read at all. */
    return {kind: "unavailable"};
  }
  // Discovery is bound to the receipt's own Todo identities: only a conversation
  // that names one of them can hold this plan's work. Unrelated conversations —
  // ordinary or coordinator — never enter the budget and never withdraw a report.
  const related = index.sessions.filter(session =>
    [...session.todoIds].some(todoId => todoIds.has(todoId)));
  if (index.unclassified || related.length > RELATED_SESSION_LIMIT) {
    // A conversation that could not be classified may still name this plan's
    // Todos, and an unattributable readback must not claim a verified result.
    return {kind: "unavailable"};
  }
  let incomplete = false;
  let unavailableAdoption = false;
  let remainingPages = 8;
  const adopted = new Map<string, Extract<Readback, {kind: "adopted"}>>();
  for (const session of related) {
    let cursor: string | undefined;
    do {
      if (!remainingPages--) return {kind: "unavailable"};
      try {
        const page = await fetchLoopXTeamWork(session.sessionId, cursor);
        incomplete ||= !page.page_readback_complete;
        for (const row of page.items) {
          if (!row.operation_id || !row.todo_id || !todoIds.has(row.todo_id) || row.status !== "accepted") continue;
          const source = await readLoopXTeamWork(session.sessionId, row.operation_id);
          if (source.operation_id !== row.operation_id || source.todo_id !== row.todo_id
            || source.status !== "accepted" || source.current_use?.state === "unavailable" || source.recovery_required || source.error) {
            incomplete = true;
            continue;
          }
          for (const adoption of source.adoptions ?? []) {
            if (adoption.state !== "current") {
              unavailableAdoption = true;
              continue;
            }
            if (!adoption.source_artifacts.length || !adoption.source_artifacts.every(version =>
              source.artifacts?.some(item => item.ref === version.ref && item.sha256 === version.sha256))) {
              unavailableAdoption = true;
              continue;
            }
            let verified = false;
            try {
              const consumer = await readLoopXTeamWork(session.sessionId, adoption.consumer_operation_id);
              const artifact = consumer.artifacts?.find(item =>
                adoption.consumer_artifacts.some(version => version.ref === item.ref && version.sha256 === item.sha256)
                && isMarkdownArtifact(item.ref))
                ?? consumer.artifacts?.find(item =>
                  adoption.consumer_artifacts.some(version => version.ref === item.ref && version.sha256 === item.sha256));
              if (consumer.operation_id === adoption.consumer_operation_id
                && consumer.request_id === adoption.consumer_request_id
                && consumer.agent_id === adoption.consumer_agent_id
                && consumer.todo_id === adoption.consumer_todo_id
                && consumer.status === "accepted" && consumer.current_use?.state !== "unavailable" && !consumer.recovery_required && !consumer.error && artifact) {
                const key = `${session.sessionId}:${consumer.operation_id}`;
                const earlier = adopted.get(key);
                if (!earlier || (!isMarkdownArtifact(earlier.artifact.ref) && isMarkdownArtifact(artifact.ref))) {
                  adopted.set(key, {kind: "adopted", artifact, agentId: adoption.consumer_agent_id});
                }
                verified = true;
              }
            } catch { /* The recorded adoption is not a readable conclusion. */ }
            if (!verified) unavailableAdoption = true;
          }
        }
        cursor = page.has_more ? page.next_cursor ?? undefined : undefined;
        if (page.has_more && !cursor) incomplete = true;
      } catch {
        incomplete = true;
        break;
      }
    } while (cursor);
  }
  if (incomplete || unavailableAdoption) return {kind: "unavailable"};
  if (adopted.size > 1) return {kind: "multiple"};
  return adopted.values().next().value ?? {kind: "waiting"};
}

async function collectManagedGoalIndex(goalId: string): Promise<ManagedGoalIndex> {
  let cursor: string | undefined;
  let total: number | undefined;
  const rows: ManagedGoalResultRow[] = [];
  const unavailableTodoIds = new Set<string>();
  // Page until the snapshot ends. The previous fixed eight-page budget hid a
  // matching report that happened to sort later in the Goal's history.
  for (;;) {
    const page = await fetchManagedGoalResults(goalId, cursor);
    if (!Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 ||
      (total !== undefined && page.total !== total) ||
      !Number.isInteger(page.unavailable_count) || page.unavailable_count < 0 ||
      !Array.isArray(page.unavailable_todo_ids) ||
      page.unavailable_count !== page.unavailable_todo_ids.length ||
      (page.next_cursor !== null && (!page.next_cursor || typeof page.next_cursor !== "string"))) {
      throw new Error("managed inventory incomplete");
    }
    total = page.total;
    rows.push(...page.items);
    for (const todoId of page.unavailable_todo_ids) {
      if (typeof todoId !== "string" || !todoId) throw new Error("managed inventory incomplete");
      unavailableTodoIds.add(todoId);
    }
    if (!page.next_cursor) return {readAt: Date.now(), rows, unavailableTodoIds};
    if (page.next_cursor === cursor) throw new Error("managed cursor repeated");
    if (page.items.length === 0 && page.unavailable_count === 0) {
      throw new Error("managed inventory made no progress");
    }
    cursor = page.next_cursor;
  }
}

/** Share the Goal inventory across plan cards; manual refresh always bypasses the short cache. */
async function readManagedGoalIndex(goalId: string, force: boolean): Promise<ManagedGoalIndex> {
  const running = managedIndexReads.get(goalId);
  if (running) return running;
  const cached = managedIndexes.get(goalId);
  if (!force && cached && Date.now() - cached.readAt < WORK_INDEX_WINDOW_MS) return cached;
  const read = collectManagedGoalIndex(goalId)
    .then(index => {managedIndexes.set(goalId, index); return index;})
    .finally(() => {managedIndexReads.delete(goalId);});
  managedIndexReads.set(goalId, read);
  return read;
}

/** A confirmed plan supplies the only Todo ids that may return to its source conversation. */
async function readManagedPlanResult(goalId: string, todoIds: Set<string>, force: boolean): Promise<ManagedReadback> {
  try {
    const index = await readManagedGoalIndex(goalId, force);
    // Only a plan-owned unreadable row can hide this plan's report; unrelated
    // historical rows stay the server's and the Files view's concern.
    for (const todoId of todoIds) {
      if (index.unavailableTodoIds.has(todoId)) return {kind: "unavailable"};
    }
    const matched = new Map<string, ManagedGoalResultRow>();
    for (const row of index.rows) {
      if (!todoIds.has(row.todo_id)) continue;
      if (!row.sha256 || !row.producer_agent_id || !row.title) return {kind: "unavailable"};
      const previous = matched.get(row.todo_id);
      if (previous && previous.sha256 !== row.sha256) return {kind: "unavailable"};
      matched.set(row.todo_id, row);
    }
    if (matched.size > 1) return {kind: "multiple"};
    const row = matched.values().next().value;
    if (!row) return {kind: "waiting"};
    const read = await readManagedGoalResult(goalId, row.todo_id);
    if (read.goal_id !== goalId || read.todo_id !== row.todo_id ||
      read.result.sha256 !== row.sha256 || read.result.producer_agent_id !== row.producer_agent_id ||
      read.result.content_type !== row.content_type || typeof read.text !== "string") return {kind: "unavailable"};
    return {kind: "accepted", artifact: managedReportArtifact(row.content_type, row.sha256, read.text),
      agentId: row.producer_agent_id, title: row.title};
  } catch { /* A failed inventory or exact read cannot keep an earlier report visible. */ }
  return {kind: "unavailable"};
}

/** Return only an accepted, currently adopted report to the manager conversation. */
export function ManagerTeamResult({goalId, todoIds, zh, onOpenGoalEvidence}: {
  goalId: string; todoIds: string[]; zh: boolean; onOpenGoalEvidence: (goalId: string) => void;
}) {
  const [result, setResult] = useState<{key: string; readback: Readback} | null>(null);
  const [managed, setManaged] = useState<{key: string; readback: ManagedReadback} | null>(null);
  const [request, setRequest] = useState({count: 0, force: false});
  const todoKey = [...todoIds].sort().join(",");
  // A new read must withdraw the previous accepted report immediately. The
  // request can be slow or fail after its source acceptance has changed.
  const key = `${goalId}:${todoKey}:${request.count}`;
  useEffect(() => {
    if (!goalId || !todoKey) return;
    let cancelled = false;
    void readAdoptedResult(goalId, new Set(todoKey.split(",")), request.force)
      .then(value => {if (!cancelled) setResult({key, readback: value});})
      .catch(() => {if (!cancelled) setResult({key, readback: {kind: "unavailable"}});});
    void readManagedPlanResult(goalId, new Set(todoKey.split(",")), request.force)
      .then(value => {if (!cancelled) setManaged({key, readback: value});})
      .catch(() => {if (!cancelled) setManaged({key, readback: {kind: "unavailable"}});});
    return () => {cancelled = true;};
  }, [goalId, todoKey, key, request.force]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) setRequest(previous => ({count: previous.count + 1, force: false}));
    }, 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const readback = result?.key === key ? result.readback : null;
  const managedReadback = managed?.key === key ? managed.readback : null;
  if (!goalId || !todoKey) return null;
  return <section className={`personal-manager-team-result is-${readback?.kind ?? "loading"}`} aria-label={zh ? "团队结果回到管家" : "Team result returned to manager"} aria-busy={!readback || !managedReadback}>
    {!readback ? <p role="status">{zh ? "正在核验团队结果…" : "Verifying team result…"}</p> : readback.kind === "adopted" ? <>
      <header><strong>{zh ? "团队验收结果" : "Team result"}</strong><small>{goalId} · {readback.agentId}</small></header>
      <TeamArtifactReport artifact={readback.artifact} zh={zh} heading={zh ? "依据已采用 · 结果已验收" : "Source adopted · Result accepted"}/>
    </> : readback.kind === "waiting" && managedReadback?.kind === "accepted" ? null : <p role="status">{readback.kind === "unavailable"
      ? (zh ? "团队结果或采用证据无法核验，请到 Goal 查看版本关系。" : "Team result or adoption evidence cannot be verified; inspect versions in the Goal.")
      : readback.kind === "multiple"
        ? (zh ? "有多个已验收的下游结果，请到 Goal 选择要采用的结论。" : "Multiple downstream results are accepted; choose the conclusion in the Goal.")
      : (zh ? "团队任务已分配，尚无可核验的已采用结果。" : "Team work is assigned; no verifiable adopted result yet.")}</p>}
    {managedReadback?.kind === "accepted" ? <div className="personal-manager-managed-report">
      <header><strong>{zh ? "托管团队报告 · 已验收，采用尚未核验" : "Managed report · Accepted, adoption not verified"}</strong>
        <small>{managedReadback.agentId}</small></header>
      <TeamArtifactReport artifact={managedReadback.artifact} zh={zh} heading={managedReadback.title}/>
    </div> : managedReadback?.kind === "multiple" ? <p role="status">{zh
      ? "这次分配已有多份托管报告；请到 Goal 选择结论。" : "This assignment has multiple managed reports; choose a conclusion in the Goal."}</p>
      : managedReadback?.kind === "unavailable" ? <p role="status">{zh
        ? "托管报告无法核验；旧内容已撤回。" : "Managed report cannot be verified; previous content was withdrawn."}</p> : null}
    <div className="personal-manager-team-result-actions">
      <button type="button" onClick={() => onOpenGoalEvidence(goalId)}>{zh ? "查看证据与任务" : "Inspect evidence and tasks"}</button>
      <button type="button" disabled={!readback} onClick={() => setRequest(previous => ({count: previous.count + 1, force: true}))}>{zh ? "刷新结果" : "Refresh result"}</button>
    </div>
  </section>;
}
