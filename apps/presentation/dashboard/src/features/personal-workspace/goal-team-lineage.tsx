import type {DelegationReadback} from "../../data/chat";

/** Request intent, accepted output and requester decision retain distinct strength. */
export function GoalTeamLineage({result, zh, onInspect}: {
  result: DelegationReadback; zh: boolean; onInspect: (operationId: string) => void;
}) {
  const relations = zh ? {responds_to: "回应此版本", revises: "修订此版本", uses: "使用此版本"}
    : {responds_to: "Respond to this version", revises: "Revise this version", uses: "Use this version"};
  return <section className="goal-team-lineage" aria-label={zh ? "版本与采用关系" : "Versions and adoption"}>
    {result.current_use?.state === "unavailable" ? <div role="alert">
      <p>{zh ? "历史执行已完成，当前来源链无法核验，暂不能作为后续任务的依据。" : "The execution completed historically; its source chain cannot currently be verified for downstream use."}</p>
      <p>{({input_unavailable: zh ? "接收方输入不可用" : "Receiver input unavailable",
        source_unavailable: zh ? "来源执行不可核验" : "Source execution unavailable",
        source_version_changed: zh ? "来源版本已变化" : "Source version changed",
        dependency_cycle: zh ? "来源关系存在循环" : "Dependency cycle",
        verification_budget_exhausted: zh ? "本次核验达到上限" : "Verification limit reached"})[result.current_use.reason ?? "source_unavailable"]}</p>
      {result.current_use.blocking_input_ref ? <p>{zh ? "需核对的输入" : "Input to reconcile"}: <code>{result.current_use.blocking_input_ref}</code></p> : null}
      {result.current_use.path?.length ? <p>{result.current_use.path.join(" → ")}</p> : null}
      {result.current_use.blocking_operation_id ? <button type="button" onClick={() => onInspect(result.current_use!.blocking_operation_id!)}>{zh ? "查看阻断来源" : "Inspect blocking source"}</button> : null}
      <p>{zh ? "修复来源或接收方输入后，重新读取证据；不会自动重跑已完成任务。" : "Repair the source or receiver input, then recheck evidence. Completed work is not automatically rerun."}</p>
    </div> : null}
    {result.dependencies?.length ? <>
      <h4>{zh ? "请求中的版本依据" : "Versions in the request"}</h4>
      <ul>{result.dependencies.map((link, index) => <li key={`${link.operation_id}:${link.input_ref}:${index}`}>
        <strong>{relations[link.relation] ?? (zh ? "未知关系" : "Unknown relationship")}</strong>
        <button type="button" onClick={() => onInspect(link.operation_id)}>{link.operation_id}</button>
        <span>{link.state === "current" ? (zh ? "源产物与接收方输入一致" : "Source and receiver input match")
          : (zh ? "此版本无法核验" : "This version cannot be verified")}</span>
        <details><summary>{zh ? "指定版本" : "Referenced version"}</summary>
          <code>{link.ref} · sha256:{link.sha256}</code><code>{link.input_ref}</code>
        </details>
      </li>)}</ul>
      <p>{zh ? "这是请求的关系；完成修订或采用仍需结果和回执。" : "These are requested relationships; revision or adoption still needs results and receipts."}</p>
    </> : null}
    <h4>{zh ? "请求方采用" : "Requester adoption"}</h4>
    {result.adoptions?.length ? <ul>{result.adoptions.map(row => <li key={row.consumer_operation_id}>
      <strong>{row.state === "current" ? (zh ? "已记录采用 · 后续结果验收有效" : "Adoption recorded · downstream result currently accepted")
        : (zh ? "采用证据已失效或无法核验" : "Adoption evidence stale or unavailable")}</strong>
      <span>{row.requester_agent_id} → {row.consumer_agent_id}</span>
      <button type="button" onClick={() => onInspect(row.consumer_operation_id)}>{zh ? "查看后续结果" : "Inspect downstream result"}</button>
      <details><summary>{zh ? "采用版本与结果版本" : "Source and result versions"}</summary>
        {row.source_artifacts.map(artifact => <code key={`source:${artifact.ref}`}>{zh ? "来源" : "Source"}: {artifact.ref} · sha256:{artifact.sha256}</code>)}
        {row.consumer_artifacts.map(artifact => <code key={`result:${artifact.ref}`}>{zh ? "结果" : "Result"}: {artifact.ref} · sha256:{artifact.sha256}</code>)}
      </details>
    </li>)}</ul> : <p>{zh ? "尚无请求方采用记录。" : "No requester adoption is recorded."}</p>}
  </section>;
}
