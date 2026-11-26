import {useEffect, useState} from "react";
import {z} from "zod";
import {requestJson} from "../../data/chat";
import {GoalStrategyDocument} from "./goal-source-strategies";
import {useWorkspaceI18n} from "./i18n";
import type {WorkspaceGoal} from "./personal-workspace-model";

type Run = NonNullable<WorkspaceGoal["sourceRunHistory"]>["recent"][number];
const digest = z.string().regex(/^[a-f0-9]{64}$/);
const executionResponse = z.object({
  ok: z.literal(true),
  goal_id: z.string(),
  source: z.object({namespace: digest}),
  execution_ref: digest,
  sha256: digest,
  record: z.object({
    goal_id: z.string(),
    generated_at: z.string(),
    agent_id: z.string().nullish(),
    classification: z.string().nullish(),
    turn_instance_id: z.string().nullish(),
    agent_vision: z.record(z.string(), z.unknown()).nullish(),
    vision_checkpoint: z.object({decision: z.string().optional()}).passthrough().nullish(),
    delivery_outcome: z.string().nullish(),
  }).passthrough(),
});
type Execution = z.infer<typeof executionResponse>;

function SourceRun({goal, run}: {goal: WorkspaceGoal; run: Run}) {
  const {t, locale} = useWorkspaceI18n();
  const [expanded, setExpanded] = useState(false);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<Execution | "unavailable" | null>(null);
  const {goalId} = goal;
  const namespace = goal.sourceAccess?.namespace;
  const originalGoal = goal.sourceAccess?.original_goal_id ?? goalId;
  const ref = run.execution_ref;
  const generatedAt = run.generated_at;

  useEffect(() => {
    if (!expanded || !ref || !namespace) return;
    const controller = new AbortController();
    setResult(null);
    const query = new URLSearchParams({
      goal_id: goalId, source_namespace: namespace, execution_ref: ref, generated_at: generatedAt,
    });
    void requestJson<unknown>(`/api/chat/goal-source-execution?${query}`, {signal: controller.signal})
      .then(payload => {
        const detail = executionResponse.parse(payload);
        if (detail.goal_id !== originalGoal || detail.record.goal_id !== originalGoal
            || detail.source.namespace !== namespace || detail.execution_ref !== ref
            || detail.record.generated_at !== generatedAt) {
          throw new Error("Inconsistent execution source");
        }
        if (!controller.signal.aborted) setResult(detail);
      })
      .catch(() => {if (!controller.signal.aborted) setResult("unavailable");});
    return () => controller.abort();
  }, [expanded, ref, generatedAt, namespace, originalGoal, goalId, retry]);

  const execution = result && result !== "unavailable" ? result : null;
  const record = execution?.record ?? run;
  const loading = Boolean(expanded && ref && namespace && result === null);
  return <details onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary><time dateTime={run.generated_at}>{run.generated_at}</time></summary>
    {loading ? <p role="status">{t("drawer.sourceRunLoading")}</p> : null}
    {result === "unavailable" ? <div role="alert">
      <p>{t("drawer.sourceRunReadFailed")}</p>
      <button type="button" onClick={() => setRetry(value => value + 1)}>{t("drawer.sourceRunRetry")}</button>
    </div> : null}
    <dl>
      {record.classification ? <div><dt>{t("drawer.sourceRunRecordType")}</dt><dd>{record.classification}</dd></div> : null}
      <div><dt>{t("drawer.sourceRunOriginalTurn")}</dt><dd>
        {record.turn_instance_id ? <code>{record.turn_instance_id}</code> : t("drawer.sourceRunUnrecorded")}
      </dd></div>
      <div><dt>{t("drawer.sourceRunCheckpoint")}</dt><dd>
        {record.vision_checkpoint?.decision ? <code>{record.vision_checkpoint.decision}</code> : t("drawer.sourceRunUnrecorded")}
      </dd></div>
      {execution?.record.delivery_outcome ? <div><dt>{t("drawer.sourceRunOutcome")}</dt>
        <dd>{execution.record.delivery_outcome}</dd></div> : null}
    </dl>
    {record.agent_vision || execution ? <GoalStrategyDocument executionRecord value={record.agent_vision ?? {}}
      originalRecord={execution?.record} recordedAt={run.generated_at} agentId={record.agent_id ?? undefined}
      version={execution?.sha256 ?? `${run.generated_at} · ${run.turn_instance_id ?? ""}`}
      originalGoalId={originalGoal} namespace={namespace ?? ""} zh={locale === "zh-CN"}/>
      : !loading ? <p>{t("drawer.sourceRunDecisionUnavailable")}</p> : null}
  </details>;
}

export function SourceRunHistory({goal, heading = false}: {goal: WorkspaceGoal; heading?: boolean}) {
  const {t} = useWorkspaceI18n();
  const recent = goal.sourceRunHistory?.recent ?? [];
  const total = goal.sourceRunHistory?.total ?? null;
  return <section className="personal-detail-card personal-source-run-history" aria-label={t("drawer.sourceRunHistory")}>
    {heading ? <h3>{t("drawer.sourceRunHistory")}</h3> : null}
    <small>{goal.sourceAccess?.label} · {t("common.readOnly")}</small>
    <dl><div><dt>{t("drawer.sourceRunOriginalGoal")}</dt><dd><code>{goal.sourceAccess?.original_goal_id ?? goal.goalId}</code></dd></div></dl>
    {recent.length ? <>
      <p>{total === null ? t("drawer.sourceRunRecent", {shown: recent.length})
        : t("drawer.sourceRunCount", {shown: recent.length, count: total})}</p>
      <ol>{recent.map((run, index) => <li key={`${goal.sourceAccess?.namespace}:${run.execution_ref ?? run.generated_at}:${index}`}>
        <SourceRun goal={goal} run={run}/>
      </li>)}</ol>
    </> : <p>{t(total === 0 ? "drawer.noExecutionHistory" : "drawer.sourceRunUnavailable")}</p>}
  </section>;
}
