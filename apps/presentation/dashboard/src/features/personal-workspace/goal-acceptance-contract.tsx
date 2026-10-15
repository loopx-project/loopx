import {useEffect, useRef, useState} from "react";
import {useQuery, useQueryClient} from "@tanstack/react-query";
import {acceptanceRequest, preparedAcceptancePlan} from "../../data/goal-acceptance-actions";
import {applyTypedAction} from "../../data/chat";
import {useTypedActionReadback} from "../../data/use-typed-action-readback";
import {useWorkspaceI18n} from "./i18n";
import {GoalOracleEvidence} from "./goal-oracle-evidence";
import {goalAcceptanceDisplayStatus, projectGoalAcceptanceCriteria, type GoalAcceptanceContract} from "../../data/goal-acceptance-observation";
import type {GoalAcceptanceContractCopy} from "./delivery-review-copy";

/** The Agent prepares; the owner decides the described effect in context.
 * Proposal receipts are historical. Only native readback supplies check state. */
export function GoalAcceptanceContractSection({goalId, contract: retained, copy, current, canManage = current, management = false}: {
  goalId: string; contract?: GoalAcceptanceContract; copy: GoalAcceptanceContractCopy; current: boolean; canManage?: boolean; management?: boolean;
}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const client = useQueryClient();
  const queryKey = ["goal-acceptance-readback", goalId];
  const read = useQuery({queryKey, queryFn: ({signal}) => acceptanceRequest(goalId, undefined, signal),
    enabled: canManage, retry: false, refetchOnWindowFocus: "always",
    refetchInterval: 15_000, refetchIntervalInBackground: false});
  const actions = useTypedActionReadback(!canManage, goalId);
  const plan = actions.isError ? null : preparedAcceptancePlan(actions.data ?? [], goalId);
  const fresh = canManage && !read.isError && read.data?.goal_id === goalId;
  const contract = fresh ? read.data!.goal_acceptance_contract : retained;
  const enabled = contract?.enabled === true;
  const criteria = enabled ? projectGoalAcceptanceCriteria(contract) : [];
  const [document, setDocument] = useState<Record<string, unknown> | null>(null);
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0), flight = useRef(false);
  const source = useRef(goalId); source.current = goalId;
  useEffect(() => {
    generation.current++; flight.current = false;
    setDocument(null); setFilename(""); setError(""); setNotice(""); setBusy(false);
    return () => {generation.current++;};
  }, [goalId, canManage]);
  async function operate(action: "adopt" | "configure" | "verify" | "disable") {
    if (!canManage || flight.current || !fresh || action === "adopt" && !plan || action === "configure" && !document) return;
    const token = ++generation.current, target = goalId;
    flight.current = true; setBusy(true); setError(""); setNotice("");
    try {
      await client.cancelQueries({queryKey, exact: true});
      if (source.current !== target || token !== generation.current) return;
      if (action === "adopt") {
        const result = await applyTypedAction(plan!.proposal.proposal_id);
        await actions.acceptProposal(result.proposal);
        if (result.proposal.status !== "applied") throw new Error("The prepared plan changed");
      } else {
        await acceptanceRequest(target, {action, expected_provider_revision: read.data!.provider_revision,
          ...(action !== "verify" ? {operation_id: `acceptance-ui:${crypto.randomUUID()}`} : {}),
          ...(action === "configure" ? {document} : {})});
      }
      // Never turn a stored action receipt into current acceptance.
      const observed = await acceptanceRequest(target);
      if (source.current !== target || token !== generation.current) return;
      client.setQueryData(queryKey, observed);
      if (action === "configure") {setDocument(null); setFilename("");}
      setNotice(action === "disable" ? (zh ? "已停用检查，历史记录仍保留。" : "Checks disabled; history is retained.")
        : action === "configure" ? (zh ? "已保存，尚未运行检查。" : "Saved; checks have not run.")
        : "");
    } catch {
      if (source.current === target && token === generation.current) {
        setError(zh ? "操作结果尚未确认。先刷新结果；不会自动重跑本机命令。" : "The result is unconfirmed. Refresh the result first; local commands will not be retried automatically.");
        // A read-only reconciliation may recover a committed native result.
        await read.refetch();
        await actions.refetch();
      }
    } finally {if (token === generation.current) {flight.current = false; setBusy(false);}}
  }
  if (!management && !enabled && !plan) return null;
  const readOnly = !canManage;
  return <section className="delivery-acceptance-contract" aria-label={copy.title} aria-busy={busy}>
    <div className="delivery-acceptance-content">
      <header className="delivery-check-heading"><div><h3>{copy.title}</h3>
        <p>{plan ? plan.document.objective : enabled ? contract.objective : (zh ? "Agent 准备检查方案后，可在这里检查交付物。" : "Check the delivery here once the Agent prepares a plan.")}</p></div>
        {canManage && fresh && (plan || enabled) ? <button className="delivery-check-primary" type="button" disabled={busy || !!error}
          onClick={() => void operate(plan ? "adopt" : "verify")}>{busy ? (zh ? "正在检查…" : "Checking…") : plan ? (zh ? "采用并检查" : "Use plan and check") : (zh ? "检查交付" : "Check delivery")}</button> : null}
      </header>
      {plan ? <div className="delivery-check-plan">
        <p>{plan.document.scope.kind === "all_advancement" ? copy.allWork
          : (zh ? `仅覆盖方案选定的 ${plan.document.scope.todo_ids.length} 项任务` : `Covers only ${plan.document.scope.todo_ids.length} selected tasks`)}</p>
        <ul>{plan.document.criteria.map(item => <li key={item.id}>{item.description}</li>)}</ul>
        {plan.document.criteria.some(item => item.oracle?.output_refs.length) ? <p>{zh ? "交付物" : "Delivery"}: {Array.from(new Set(plan.document.criteria.flatMap(item => item.oracle?.output_refs ?? []))).join(" · ")}</p> : null}
        <p>{zh ? "将保存这份方案、更新相关任务的验收关联，并在本机运行以下检查。准备方案本身不会执行命令。" : "This saves the plan, updates its task associations and runs the following checks locally. Preparation itself executes no commands."}</p>
        <details><summary>{zh ? "检查内容与命令" : "Checks and commands"}</summary>
          {plan.document.criteria.map(item => <div key={item.id}><p>{item.description}</p><code>{item.validation_argv.map(arg => JSON.stringify(arg)).join(" ")}</code>
            {item.oracle?.output_refs.map(ref => <p key={ref}>{zh ? "交付物" : "Delivery"}: {ref}</p>)}</div>)}
        </details>
      </div> : null}
      {enabled ? <div className="delivery-check-result">
        <p role="status"><strong>{fresh ? copy.verificationState[goalAcceptanceDisplayStatus(contract)] : copy.retained}</strong></p>
        <ul>{criteria.map(item => <li key={item.id}><span>{item.description}</span>
          {item.oracle ? <span>{({current: zh ? "当前版本已核对" : "Current versions checked", recorded: zh ? "历史结果，当前版本未核对" : "Recorded; current versions not checked", stale: zh ? "来源或版本已变化，请重新检查" : "Source or versions changed; check again", failed: zh ? "检查失败；修复交付物后可再次检查" : "Checks failed; repair the delivery before checking again", unverified: zh ? "尚未检查" : "Not checked", unavailable: zh ? "证据不可用" : "Evidence unavailable"})[fresh ? item.oracle.state : "recorded"]}</span>
            : <span>{fresh ? copy.verificationState[item.state] : copy.retained}</span>}
        </li>)}</ul>
        <p className="delivery-check-boundary">{copy.boundary}</p>
        <details><summary>{zh ? "检查记录与版本" : "Check record and versions"}</summary>
          {!contract.verification ? <p>{zh ? "尚无检查记录。" : "No check record yet."}</p> : <>
            <p>{copy.receiptNote}</p>
            <dl className="delivery-acceptance-source">
              <div><dt>{copy.operation}</dt><dd><code>{contract.verification.operation_id}</code></dd></div>
              <div><dt>{copy.revision}</dt><dd>{contract.verification.contract_revision}</dd></div>
              <div><dt>{copy.digest}</dt><dd><code>{contract.verification.contract_digest}</code></dd></div>
              <div><dt>{copy.verificationScope}</dt><dd>{contract.verification.todo_id ?? copy.allCriteria}</dd></div>
            </dl>
            {contract.verification.results.map(result => <div key={result.criterion_id}>
              <p>{contract.criteria.find(item => item.id === result.criterion_id)?.description ?? result.criterion_id} · {result.passed ? copy.passed : copy.failed}</p>
              <p>{copy.exitCode}: {result.exit_code ?? copy.unknown}</p>
              {result.oracle_evidence ? <GoalOracleEvidence evidence={result.oracle_evidence} zh={zh}/> : null}
            </div>)}
          </>}
        </details>
      </div> : null}
      {readOnly ? <p role="status">{zh ? "当前来源只读；返回本机后可检查交付。" : "This source is read-only. Return to the local workspace to check."}</p> : null}
      {read.isError || actions.isError || error ? <div role="alert"><p>{error || (zh ? "检查方案或结果暂时不可用。刷新只读取信息。" : "The plan or result is unavailable. Refresh only reads information.")}</p>
        <button type="button" disabled={busy} onClick={() => {setError(""); void read.refetch(); void actions.refetch();}}>{zh ? "刷新结果" : "Refresh result"}</button></div> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {management || enabled ? <details className="delivery-check-advanced"><summary>{zh ? "高级配置与诊断" : "Advanced configuration and diagnostics"}</summary>
        <p>{copy.guidance}</p>
        <div className="delivery-acceptance-controls">
          <button type="button" disabled={busy || readOnly} onClick={() => {setError(""); void read.refetch(); void actions.refetch();}}>{zh ? "刷新结果" : "Refresh result"}</button>
          <label>{zh ? "导入现有 JSON 合同（可选）" : "Import an existing JSON contract (optional)"}<input type="file" accept="application/json,.json" disabled={busy || !fresh}
            onChange={event => {const file = event.target.files?.[0]; const token = ++generation.current; setDocument(null); setFilename("");
              if (!file) return; if (file.size > 262144) {setError(zh ? "文件超过 256 KB。" : "File exceeds 256 KB."); return;}
              void file.text().then(text => {if (token !== generation.current) return; const parsed: unknown = JSON.parse(text);
                if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Expected document");
                setDocument(parsed as Record<string, unknown>); setFilename(file.name); setError("");
              }).catch(() => {if (token === generation.current) setError(zh ? "请选择有效的 JSON 对象。" : "Choose a valid JSON object.");});
            }}/></label>
          {document ? <details open><summary>{zh ? "待保存配置" : "Configuration to save"} · {filename}</summary><pre>{JSON.stringify(document, null, 2)}</pre>
            <p>{zh ? "保存会更新所列任务的关联，不会运行检查。" : "Saving updates the listed task associations and does not run checks."}</p></details> : null}
          <button type="button" disabled={busy || !fresh || !document || !!error} onClick={() => void operate("configure")}>{zh ? "保存配置" : "Save configuration"}</button>
          <button type="button" disabled={busy || !fresh || !enabled} onClick={() => void operate("disable")}>{zh ? "停用检查" : "Disable checks"}</button>
        </div>
        <dl className="delivery-acceptance-source"><div><dt>{copy.source}</dt><dd><code>{goalId}</code></dd></div>
          {enabled ? <><div><dt>{copy.scope}</dt><dd>{contract.scope?.kind === "selected_work" ? copy.selectedWork : copy.allWork}</dd></div>
            <div><dt>{copy.revision}</dt><dd>{contract.revision}</dd></div><div><dt>{copy.digest}</dt><dd><code>{contract.digest}</code></dd></div></> : null}
        </dl>
        {enabled ? <><p>{contract.non_goals.join(" · ")}</p><h4>{copy.tasks}</h4>
          <ul>{contract.tasks.map(task => <li key={task.todo_id}><code>{task.todo_id}</code> · {copy.taskState[task.state]}
            <p>{copy.criteria}: {task.criterion_ids.join(", ") || copy.unknown}</p>
            {task.applicable === false ? <p>{copy.notApplicable}</p> : null}
            {task.reason ? <p>{task.reason}</p> : null}</li>)}</ul>
          {contract.held_todo_ids.length ? <p>{copy.heldTasks}: {contract.held_todo_ids.join(", ")}</p> : null}</> : null}
        <a href="https://github.com/loopx-project/loopx/blob/main/docs/reference/goal-acceptance-observations.md#owner-authorized-contract-v0" target="_blank" rel="noreferrer">{copy.guide}</a>
      </details> : null}
    </div>
  </section>;
}
