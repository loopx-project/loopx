import type { CollaborationReadback } from "../../data/chat-model";
import {useEffect, useState} from "react";
import type {RequestControlView} from "../../data/request-control";
import {RequestControl} from "./request-control";
import { useWorkspaceI18n } from "./i18n";
import "./collaboration-card.css";

const copy = {
  "zh-CN": {
    title: "交办说明", context: "背景与补充", constraints: "约束", inputs: "输入材料",
    acceptance: "验收要求", return: "需要回传", supplied: "已提供给接收方", pending: "等待接收方读取",
    decision: "接收方判断", unknown: "尚未记录", unavailable: "暂时无法读取",
    adopt: "已采纳", defer: "已暂缓", reject: "未采纳", no_change: "无需调整",
    result: "回复等待送达", delivered: "回复已送达", unverified: "回复送达尚未核验", details: "查看交办内容",
    owner: "接收方", reason: "原因",
  },
  en: {
    title: "Delegation brief", context: "Context & corrections", constraints: "Constraints", inputs: "Inputs",
    acceptance: "Acceptance", return: "Expected return", supplied: "Supplied to receiver", pending: "Awaiting receiver read",
    decision: "Receiver decision", unknown: "Not recorded", unavailable: "Readback unavailable",
    adopt: "Adopted", defer: "Deferred", reject: "Rejected", no_change: "No change needed",
    result: "Reply awaiting delivery", delivered: "Reply delivered", unverified: "Reply delivery unverified", details: "View delegation details",
    owner: "Recipient", reason: "Reason",
  },
};

export function CollaborationCard({ request }: { request?: CollaborationReadback }) {
  const { locale } = useWorkspaceI18n();
  const [observed, setObserved] = useState<RequestControlView | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [editing, setEditing] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => {setObserved(null); setUnavailable(false);}, [request?.request_id]);
  useEffect(() => {
    if (request?.request_control && observed && request.request_control.control_version >= observed.control_version) {
      setObserved(null); setUnavailable(false);
    }
  }, [request?.request_control]);
  if (!request) return null;
  const c = copy[locale];
  const brief = observed?.brief ?? request.brief;
  const revision = observed?.revision ?? request.request_revision;
  const readStatus = unavailable ? "unavailable" : observed ? observed.read_readback === "recorded" ? "supplied"
    : observed.read_readback === "unavailable" ? "unavailable" : "pending" : request.read_status;
  const recordedDecision = unavailable ? "unavailable" : observed ? observed.assessment?.decision
    ?? (observed.assessment_readback === "unavailable" ? "unavailable" : "pending") : request.decision;
  const decision = (() => {
    switch (recordedDecision) {
      case "adopt": return c.adopt;
      case "defer": return c.defer;
      case "reject": return c.reject;
      case "no_change": return c.no_change;
      case "unavailable": return c.unavailable;
      default: return c.unknown;
    }
  })();
  const conclusion = request.returns.find((reply) => reply.phase === "conclusion" &&
    (revision === undefined || (reply.request_revision === undefined ? 1 : reply.request_revision) === revision));
  const decisionReason = observed ? observed.assessment?.reason : request.decision_reason;
  return <section className="personal-collaboration" aria-label={c.title}>
    <header><strong>{brief.purpose}</strong><span>{c.owner}: {request.goal_id ? `${request.goal_id} / ` : ""}{request.agent_id}</span></header>
    <p className="personal-collaboration-status">
      {revision !== undefined ? <span>{locale === "zh-CN" ? "请求版本" : "Request revision"} {revision}</span> : null}
      <span>{readStatus === "supplied" ? c.supplied : readStatus === "unavailable" ? c.unavailable : c.pending}</span>
      <span>{c.decision}: {decision}</span>
      {conclusion ? <span>{conclusion.status === "delivered" ? c.delivered
        : conclusion.status === "queued" ? c.result : c.unverified}</span> : null}
    </p>
    {decisionReason && !unavailable ? <p>{c.reason}: {decisionReason}</p> : null}
    <details onToggle={event => setDetailsOpen(event.currentTarget.open)}>
      <summary>{c.details}</summary>
      {request.control_route ? <RequestControl route={request.control_route} active={detailsOpen}
        onReadback={value => {setObserved(value); setUnavailable(value === null);}} onEditing={setEditing} /> : null}
      {!editing && <>
      <h4>{c.context}</h4><p>{brief.context}</p>
      {brief.constraints.length ? <><h4>{c.constraints}</h4><ul>{brief.constraints.map((item, i) => <li key={i}>{item}</li>)}</ul></> : null}
      {brief.inputs.length ? <><h4>{c.inputs}</h4><ul>{brief.inputs.map((input, i) => <li key={i}><code>{input.ref}</code> — {input.description}{input.sha256 ? <small>sha256:{input.sha256}</small> : null}</li>)}</ul></> : null}
      <h4>{c.acceptance}</h4><ul>{brief.acceptance.map((item, i) => <li key={i}>{item}</li>)}</ul>
      <h4>{c.return}</h4><p>{brief.return_requirement}</p>
      </>}
    </details>
  </section>;
}
