import {useEffect, useState, type ReactNode} from "react";
import {goalAcceptanceDisplayStatus, hasConfiguredGoalChecks, projectGoalAcceptanceCriteria, type GoalAcceptanceContract, type GoalAcceptanceObservation} from "../../data/goal-acceptance-observation";
import {acceptanceRequest} from "../../data/goal-acceptance-actions";
import type {GoalArtifactLifecycle} from "../../data/status";
import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.js";
import {projectVerificationWorkbench} from "../../../../../../loopx/control_plane/presentation/verification_workbench.js";
import {verifiedHostDepth, type VerificationEvidenceRef} from "../../../../../../loopx/control_plane/capabilities/explore_verification_evidence.js";
import {goalAcceptanceContractCopy} from "./delivery-review-copy";
import {GoalOracleEvidence} from "./goal-oracle-evidence";
import "./goal-verification-workbench.css";

export type GoalVerificationSources = {
  acceptance?: GoalAcceptanceObservation | null;
  lifecycle?: GoalArtifactLifecycle | null;
  liveAcceptance?: boolean;
};

function EvidenceRef({source, unknown}: {source?: VerificationEvidenceRef; unknown: string}) {
  return source ? <>{source.ref}<code>{source.sha256}</code></> : unknown;
}

function EvidenceRow({label, children}: {label: string; children: ReactNode}) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

export function GoalVerificationWorkbench({zh, goalId, finding: defaultFinding, findings, complete, unavailable, acceptance, lifecycle, liveAcceptance}: GoalVerificationSources & {
  zh: boolean; goalId: string; finding?: ExploreHistoryEvent; findings: readonly ExploreHistoryEvent[];
  complete: boolean; unavailable: boolean;
}) {
  const [chosen, setChosen] = useState<string>();
  const candidates = findings.filter(item => item.status === "confirmed" && (item.work_yardstick || item.execution_observation));
  const finding = candidates.find(item => item.event_id === chosen) ?? defaultFinding;
  const p = projectVerificationWorkbench(finding, findings);
  const observation = acceptance?.goal_id === goalId ? acceptance : null;
  const savedContract = observation?.goal_acceptance_contract;
  const lifetime = savedContract?.goal_ref?.goal_instance_id;
  const basis = JSON.stringify([goalId, lifetime, hasConfiguredGoalChecks(savedContract) ? savedContract.digest : null,
    hasConfiguredGoalChecks(savedContract) ? savedContract.verification?.operation_id : null]);
  const [live, setLive] = useState<{basis: string; revision: number; contract?: GoalAcceptanceContract; failed?: true}>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!liveAcceptance) return;
    const controller = new AbortController();
    // This is the same GET used by Goal acceptance. It checks current
    // source versions and neither executes a verifier nor commits a receipt.
    void acceptanceRequest(goalId, undefined, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const contract = result.goal_acceptance_contract;
      setLive(lifetime && contract.goal_ref?.goal_instance_id !== lifetime
        ? {basis, revision, failed: true} : {basis, revision, contract});
    }, () => {
      if (!controller.signal.aborted) setLive({basis, revision, failed: true});
    });
    return () => controller.abort();
  }, [goalId, liveAcceptance, revision, basis, lifetime]);
  const current = liveAcceptance && live?.basis === basis && live.revision === revision ? live : undefined;
  const contract = liveAcceptance ? current?.contract : savedContract;
  const configured = hasConfiguredGoalChecks(contract) ? contract : null;
  const status = configured ? goalAcceptanceDisplayStatus(configured) : null;
  const criteria = configured ? projectGoalAcceptanceCriteria(configured) : [];
  const results = configured?.verification?.results ?? [];
  // Demand both an exact candidate output and the producer's exact attached
  // receipts before showing a verified depth for this run.
  const currentEvidence = results.flatMap(result => {
    const criterion = criteria.find(item => item.id === result.criterion_id);
    const evidence = result.oracle_evidence;
    return result.passed && evidence && (criterion?.state === "accepted" || criterion?.state === "partial")
      && criterion.oracle?.state === "current" && criterion.oracle.source_id === evidence.source_id
      && criterion.oracle.source_digest === evidence.source_digest ? [{evidence, criterionId: result.criterion_id}] : [];
  });
  const resultRef = finding?.execution_observation?.result_ref;
  const checked = p?.candidate && resultRef ? currentEvidence.filter(item =>
    item.evidence.output_versions.some(ref => ref.ref === resultRef && ref.sha256 === p.candidate)) : [];
  const depth = verifiedHostDepth(p?.evidence, checked.flatMap(({evidence, criterionId}) =>
    evidence.output_versions.map(ref => ({...ref, criterion_id: criterionId}))));
  const guards = lifecycle?.goal_id === goalId ? lifecycle.guards.filter(item => item.blocked) : null;
  const unknown = zh ? "未知" : "Unknown";
  const copy = goalAcceptanceContractCopy[zh ? "zh-CN" : "en"];
  const labels = zh ? {
    proxy: "局部快尺", final: liveAcceptance ? "权威终评" : "存档终评", fidelity: "快慢尺保真", guards: "证据缺口",
    baseline: "冻结基线与版本", negative: "已证伪路线", comparison: "快尺与终评对账", decisionRefs: "判定引用", costs: "成本",
    frozen: "冻结基线", environment: "运行环境", source: "源码版本", candidate: "候选产物", host: "实际宿主", depth: liveAcceptance ? "已验证档位" : "存档验证档位",
    warm: "温缓存 token", cold: "冷缓存 token", duration: "核验耗时（秒）", humans: "人工确认次数", hooks: "工具拦截次数",
    same: "在匹配样本中排序一致", different: "在匹配样本中排序不同", relation: "排序仅适用于此对照，因果增益仍未知。",
    included: "权威终评已核对该产物", unbound: "此候选的权威回执未知", sourceGaps: "缺少来源",
    pending: "正在读取研究来源", incomplete: "研究来源读取不完整", failed: "研究来源暂不可读",
    noFinding: "此 Goal 的研究候选未知", noOpenGuards: "来源未记录开放的门槛",
    selected: "研究记录", retry: "重试读取终评", finalUnavailable: "当前权威回执暂不可读",
  } : {
    proxy: "Local yardstick", final: liveAcceptance ? "Authoritative verifier" : "Archived verifier", fidelity: "Proxy fidelity", guards: "Evidence gaps",
    baseline: "Frozen baseline and versions", negative: "Refuted alternatives", comparison: "Proxy and verifier comparison", decisionRefs: "Decision references", costs: "Costs",
    frozen: "Frozen baseline", environment: "Run environment", source: "Source revision", candidate: "Candidate artifact", host: "Observed host", depth: liveAcceptance ? "Verified tier" : "Archived tier",
    warm: "Warm-cache tokens", cold: "Cold-cache tokens", duration: "Verifier time (seconds)", humans: "Human confirmations", hooks: "Tool interceptions",
    same: "Same ordering on the matched pair", different: "Different ordering on the matched pair", relation: "The ordering applies to this matched pair. The causal gain is unknown.",
    included: "Authoritative check includes this artifact", unbound: "Authoritative receipt for this candidate is unknown", sourceGaps: "Missing sources",
    pending: "Reading research evidence", incomplete: "Incomplete research coverage", failed: "Research source is unavailable",
    noFinding: "Research candidate is unknown", noOpenGuards: "No open guards recorded by this source",
    selected: "Research record", retry: "Retry verifier read", finalUnavailable: "Current authoritative receipt is unavailable",
  };
  const passing = status === "accepted" || status === "partial";
  const authority = status && (!passing || checked.length) ? copy.verificationState[status] : unknown;
  const fidelity = p?.fidelity === "same_order" ? labels.same : p?.fidelity === "different_order" ? labels.different : unknown;
  const gaps = observation?.missing_sources ?? [];
  const stage = unavailable ? labels.failed : !complete ? finding ? labels.incomplete : labels.pending : !finding ? labels.noFinding : null;
  const costRows = [
    ["warm_cache_tokens", labels.warm], ["cold_cache_tokens", labels.cold], ["verifier_seconds", labels.duration],
    ["human_confirmations", labels.humans], ["tool_interceptions", labels.hooks],
  ] as const;
  const format = new Intl.NumberFormat(zh ? "zh-CN" : "en");
  const alternativeRefs = new Set(p?.alternatives.flatMap(item => item.evidence_refs));
  const decisionRefs = p?.decision?.counterevidence_refs.filter(ref => !alternativeRefs.has(ref)) ?? [];
  return <div className="goal-verification-body" aria-busy={Boolean(liveAcceptance && !current)}>
    {stage ? <p role="status">{stage}</p> : null}
    {candidates.length > 1 ? <label className="goal-verification-select">{labels.selected}
      <select value={candidates.some(item => item.event_id === finding?.event_id) ? finding?.event_id : ""}
        onChange={event => setChosen(event.currentTarget.value)}>
        {candidates.some(item => item.event_id === finding?.event_id) ? null
          : <option value="">{finding?.title ?? unknown}</option>}
        {candidates.map(item => <option key={item.event_id} value={item.event_id}>{item.title}</option>)}
      </select>
    </label> : finding ? <p>{labels.selected} · <strong>{finding.title}</strong></p> : null}
    <dl className="goal-verification-profile">
      <EvidenceRow label={labels.proxy}>{p?.decision?.proxy ? <>{p.decision.proxy.metric} ({p.decision.proxy.unit})<code>{p.decision.proxy.evaluation_ref}</code></>
        : p?.decision ? zh ? "直接评估" : "Direct evaluation" : unknown}</EvidenceRow>
      <EvidenceRow label={labels.final}>{authority}
        {current?.failed ? <><small role="status">{labels.finalUnavailable}</small>
          <button type="button" onClick={() => setRevision(value => value + 1)}>{labels.retry}</button></> : null}{p?.decision ? <code>{p.decision.authoritative.evaluation_ref}</code> : null}
        {p?.candidate && configured ? <small>{checked.length ? labels.included : labels.unbound}</small> : null}</EvidenceRow>
      <EvidenceRow label={labels.fidelity}>{fidelity}</EvidenceRow>
      <EvidenceRow label={labels.guards}>{gaps.length ? <>{labels.sourceGaps}<ul>{gaps.map(item => <li key={item}>{item}</li>)}</ul></> : null}
        {guards?.length ? <ul>{guards.map((item, index) => <li key={`${item.id}:${index}`}>{item.id} · {item.owner}</li>)}</ul> : null}
        {!gaps.length && !guards?.length ? guards ? labels.noOpenGuards : unknown : null}</EvidenceRow>
    </dl>
    <div className="goal-verification-packet">
      <section><h3>{labels.baseline}</h3><dl>
        <EvidenceRow label={labels.host}>{p?.evidence?.host ?? unknown}</EvidenceRow>
        <EvidenceRow label={labels.depth}>{depth === null ? unknown : `L${depth}`}</EvidenceRow>
        <EvidenceRow label={labels.frozen}><EvidenceRef source={p?.evidence?.baseline} unknown={unknown}/></EvidenceRow>
        <EvidenceRow label={labels.environment}><EvidenceRef source={p?.evidence?.environment} unknown={unknown}/></EvidenceRow>
        <EvidenceRow label={labels.source}><EvidenceRef source={p?.evidence?.source} unknown={unknown}/></EvidenceRow>
        <EvidenceRow label={labels.candidate}>{p?.candidate ? <code>{p.candidate}</code> : unknown}</EvidenceRow>
      </dl></section>
      <section><h3>{labels.negative}</h3>
        {p?.alternatives.length ? <ul>{p.alternatives.map(row => <li key={row.event_id}><strong>{row.title}</strong>
          {row.summary ? <div>{row.summary}</div> : null}
          {row.evidence_refs.length ? row.evidence_refs.map(ref => <code key={ref}>{ref}</code>) : unknown}</li>)}</ul> : <p>{unknown}</p>}
      </section>
      <section><h3>{labels.comparison}</h3><dl>
        {([["proxy", labels.proxy], ["authoritative", labels.final]] as const).map(([key, label]) => {
          const pair = p?.[key];
          return <EvidenceRow key={key} label={label}>{pair ? <>
            {format.format(pair.before.value!)} → {format.format(pair.after.value!)} {pair.after.unit}
            <code>{pair.before.evidence_ref} → {pair.after.evidence_ref}</code>
          </> : unknown}</EvidenceRow>;
        })}
        {decisionRefs.length ? <EvidenceRow label={labels.decisionRefs}>
          {decisionRefs.map(ref => <code key={ref}>{ref}</code>)}
        </EvidenceRow> : null}
      </dl>{p?.authoritative && p.proxy ? <p>{labels.relation}</p> : null}
        {checked.map(({evidence, criterionId}) => <GoalOracleEvidence key={criterionId} evidence={evidence} zh={zh}/>)}
      </section>
      <section><h3>{labels.costs}</h3><dl>
        {costRows.map(([key, label]) => {
          const cost = p?.evidence?.costs?.[key];
          return <EvidenceRow key={key} label={label}>{cost ? <>{format.format(cost.value)}<code>{cost.evidence_ref}</code></> : unknown}</EvidenceRow>;
        })}
      </dl></section>
    </div>
  </div>;
}
