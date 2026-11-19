import {useRef, useState} from "react";
import {ContentMediaWorkbench} from "./content-media-workbench";
import {ContentReferenceWorkbench} from "./content-reference-workbench";
import {GoalAcceptanceContractSection} from "./goal-acceptance-contract";
import {deliveryReviewCopy} from "./delivery-review-copy";
import type {DeliveryReviewRead} from "./delivery-review-state";
import {useWorkspaceI18n} from "./i18n";

/** Production stays in the current work context; checks retain their existing owner. */
export function ContentProductionResults({goalId, review, onOpenConversation}: {
  goalId: string; review: DeliveryReviewRead; onOpenConversation: () => void;
}) {
  const {locale} = useWorkspaceI18n();
  const copy = (zh: string, en: string) => locale === "zh-CN" ? zh : en;
  const [referencesOpen, setReferencesOpen] = useState(false);
  const [checksOpen, setChecksOpen] = useState(false);
  const checks = useRef<HTMLDetailsElement>(null);
  return <section className="content-production-results" aria-label={copy("内容作品", "Content artifacts")}>
    <header className="content-media-heading"><h2>{copy("内容作品", "Content artifacts")}</h2>
      <div className="content-production-actions"><button type="button" onClick={onOpenConversation}>{copy("返回当前对话", "Return to conversation")}</button>
        <button type="button" onClick={() => {setChecksOpen(true); requestAnimationFrame(() => checks.current?.scrollIntoView({block: "nearest"}));}}>{copy("Goal 交付检查", "Goal delivery checks")}</button></div></header>
    <ContentMediaWorkbench key={goalId} goalId={goalId} />
    <details ref={checks} open={checksOpen} onToggle={event => setChecksOpen(event.currentTarget.open)}>
      <summary>{copy("Goal 交付要求与检查", "Goal delivery requirements and checks")}</summary>
      <p>{copy("检查范围由当前 Goal 的验收方案决定。", "The current Goal acceptance plan defines the scope of these checks.")}</p>
      {checksOpen && !review.usable ? <div role="status"><p>{copy("验收信息暂不可读，请刷新后重试。", "Acceptance information is unavailable. Refresh to retry.")}</p>
        <button type="button" disabled={review.kind === "loading"} onClick={review.refresh}>{copy("刷新验收信息", "Refresh acceptance information")}</button></div> : null}
      {checksOpen && review.usable ? <GoalAcceptanceContractSection goalId={goalId}
        contract={review.snapshot?.acceptance?.goal_acceptance_contract}
        copy={deliveryReviewCopy[locale].contract} current={review.usable} canManage={review.usable}>
        <p>{copy("可在当前对话中请 Agent 准备验收方案；准备不会运行检查。", "Ask the Agent in the current conversation to prepare an acceptance plan. Preparation does not run checks.")}</p>
      </GoalAcceptanceContractSection> : null}
    </details>
    <details open={referencesOpen} onToggle={event => setReferencesOpen(event.currentTarget.open)}>
      <summary>{copy("准备来源风格参考", "Prepare source and style references")}</summary>
      {referencesOpen ? <ContentReferenceWorkbench key={goalId} /> : null}
    </details>
  </section>;
}
