import type { CollaborationReadback } from "../../data/chat-model";
import { currentCollaborationConclusion } from "../../data/conversation-returns";
import { useEffect, useState } from "react";
import type { RequestControlView } from "../../data/request-control";
import { RequestControl } from "./request-control";
import { useWorkspaceI18n } from "./i18n";
import { needsOriginalDeliveryProof } from "./return-delivery-status";
import {
  WORKER_ROSTER_STATES,
  deriveCollaborationCardStatus,
  deriveCollaborationRosterSummary,
  workerRosterStateLabel,
} from "./collaboration-status";
import {
  WorkerNowLine,
  WorkerProgressBadge,
  WorkerScopeTag,
  WorkerStateBadge,
  WorkerStateGlyph,
} from "./worker-roster";
import "./collaboration-card.css";

const copy = {
  "zh-CN": {
    title: "交办说明",
    context: "背景与补充",
    constraints: "约束",
    inputs: "输入材料",
    acceptance: "验收要求",
    return: "需要回传",
    decision: "接收方判断",
    details: "查看交办内容",
    owner: "接收方",
    reason: "原因",
    rosterSummary: "协作接收方概览",
  },
  en: {
    title: "Delegation brief",
    context: "Context & corrections",
    constraints: "Constraints",
    inputs: "Inputs",
    acceptance: "Acceptance",
    return: "Expected return",
    decision: "Receiver decision",
    details: "View delegation details",
    owner: "Recipient",
    reason: "Reason",
    rosterSummary: "Recipient collaboration roster",
  },
} as const;

export function CollaborationCard({ request }: { request?: CollaborationReadback }) {
  const { locale, t } = useWorkspaceI18n();
  const [observed, setObserved] = useState<RequestControlView | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [editing, setEditing] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    setObserved(null);
    setUnavailable(false);
  }, [request?.request_id]);

  useEffect(() => {
    if (request?.request_control && observed && request.request_control.control_version >= observed.control_version) {
      setObserved(null);
      setUnavailable(false);
    }
  }, [request?.request_control, observed]);

  if (!request) return null;

  const c = copy[locale];
  const brief = observed?.brief ?? request.brief;
  const revision = observed?.revision ?? request.request_revision;
  const status = deriveCollaborationCardStatus({ request, observed, unavailable, locale });
  const conclusion = currentCollaborationConclusion({ ...request, request_revision: revision });
  const decisionReason = observed ? observed.assessment?.reason : request.decision_reason;
  const conclusionText = conclusion
    ? (needsOriginalDeliveryProof(conclusion) ? t("returnDelivery.originalHandoff") : status.conclusionLabel)
    : null;

  return (
    <section
      className="personal-collaboration"
      aria-label={c.title}
      data-roster-state={status.state}
    >
      <header className="pw-collab-header">
        <div className="pw-collab-title-group">
          <WorkerStateBadge state={status.state} locale={locale} />
          <strong>{brief.purpose}</strong>
        </div>
        <div className="pw-collab-meta-group">
          <span className="pw-collab-owner-label">{c.owner}:</span>
          <WorkerScopeTag scope={status.scope} />
          <WorkerProgressBadge progress={status.progress} />
        </div>
      </header>
      <WorkerNowLine now={status.now === conclusionText ? null : status.now} />
      <p className="personal-collaboration-status">
        {status.revisionLabel ? <span>{status.revisionLabel}</span> : null}
        <span>{status.readLabel}</span>
        <span>{c.decision}: {status.decisionLabel}</span>
        {conclusionText ? <span>{conclusionText}</span> : null}
      </p>
      {decisionReason && !unavailable && status.decisionReason !== status.now ? (
        <p className="pw-collab-reason">{c.reason}: {decisionReason}</p>
      ) : null}
      <details onToggle={(event) => setDetailsOpen(event.currentTarget.open)}>
        <summary>{c.details}</summary>
        {request.control_route ? (
          <RequestControl
            route={request.control_route}
            active={detailsOpen}
            onReadback={(value) => {
              setObserved(value);
              setUnavailable(value === null);
            }}
            onEditing={setEditing}
          />
        ) : null}
        {!editing && (
          <>
            <h4>{c.context}</h4>
            <p>{brief.context}</p>
            {brief.constraints.length ? (
              <>
                <h4>{c.constraints}</h4>
                <ul>{brief.constraints.map((item, i) => <li key={i}>{item}</li>)}</ul>
              </>
            ) : null}
            {brief.inputs.length ? (
              <>
                <h4>{c.inputs}</h4>
                <ul>
                  {brief.inputs.map((input, i) => (
                    <li key={i}>
                      <code>{input.ref}</code> — {input.description}
                      {input.sha256 ? <small>sha256:{input.sha256}</small> : null}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            <h4>{c.acceptance}</h4>
            <ul>{brief.acceptance.map((item, i) => <li key={i}>{item}</li>)}</ul>
            <h4>{c.return}</h4>
            <p>{brief.return_requirement}</p>
          </>
        )}
      </details>
    </section>
  );
}

/** Each recipient retains its own request controls, decision and return. */
export function CollaborationCards({
  request,
  requests,
}: {
  request?: CollaborationReadback;
  requests?: CollaborationReadback[];
}) {
  const { locale } = useWorkspaceI18n();
  const rows = requests ?? (request ? [request] : []);
  if (!rows.length) return null;
  const summary = rows.length > 1 ? deriveCollaborationRosterSummary(rows, locale) : null;

  return (
    <>
      {summary ? (
        <aside className="pw-collab-roster-summary" aria-label={copy[locale].rosterSummary}>
          <div className="pw-collab-roster-head">
            <div className="pw-roster-counts">
              {WORKER_ROSTER_STATES.filter((state) => summary.counts[state] > 0).map((state) => (
                <WorkerStateBadge
                  key={state}
                  state={state}
                  locale={locale}
                  count={summary.counts[state]}
                />
              ))}
            </div>
            <WorkerProgressBadge progress={summary.progress} />
          </div>
          <ul className="pw-collab-roster-list">
            {summary.workers.map((worker) => (
              <li key={worker.requestId} data-roster-state={worker.state}>
                <WorkerStateGlyph state={worker.state} />
                <span className="pw-collab-roster-agent">{worker.agentId}</span>
                <span className="pw-collab-roster-state-text">{worker.stateLabel}</span>
                <WorkerScopeTag scope={worker.scope} />
                <span className="pw-collab-roster-now" title={worker.now}>{worker.now}</span>
                <WorkerProgressBadge progress={worker.progress} />
              </li>
            ))}
          </ul>
        </aside>
      ) : null}
      {rows.map((row) => <CollaborationCard key={row.request_id} request={row} />)}
    </>
  );
}
