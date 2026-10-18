import { useState } from "react";
import { ArrowRight, CheckCircle2, RotateCcw } from "lucide-react";
import { usePublicPageNavigation } from "../usePublicPageNavigation";
import { budget, canReturn, fixturePlans, inspectPlan, orders, policies, regionCapacity, reviewIsCurrent, reviewPlan, stock, vectorCount, type Request, type Review } from "./allocation";

const source = "https://github.com/loopx-project/loopx/tree/105c4bc15091afac832495167cb98a1389951a04/examples/collaboration-delivery";
const base = import.meta.env.BASE_URL;

export default function DelegationLab() {
  const [language, setLanguage] = usePublicPageNavigation("delegationLab");
  const t = (en: string, zh: string) => language === "zh" ? zh : en;
  const [preset, setPreset] = useState(0);
  const [request, setRequest] = useState<Request>({ revision: 1, policy: policies[0] });
  const [receiptRevision, setReceiptRevision] = useState<number | null>(null);
  const [planRevision, setPlanRevision] = useState(1);
  const [draft, setDraft] = useState(fixturePlans[0]!.map(String));
  const [review, setReview] = useState<Review | null>(null);
  const [returned, setReturned] = useState<{ revision: number; draft: string[]; value: number; cost: number } | null>(null);
  const inspection = inspectPlan(draft, request.policy);
  const currentReview = reviewIsCurrent(request, draft, review);
  const returnReady = canReturn(request, planRevision, receiptRevision, draft, review);
  const returnedCurrent = returned?.revision === request.revision && returned.draft.every((n, i) => n === draft[i]);
  const names = [t("Original request", "原始要求"), t("Reserve 1 + East ≥ 4", "每组留 1 + 东区至少 4"), t("Impossible: East ≥ 99", "无解要求：东区至少 99")];

  function changeRequest(next: number) {
    setPreset(next);
    setRequest({ revision: request.revision + 1, policy: policies[next] });
    // Keep the old plan, receipt, review and return visible as historical facts.
  }
  function reset() {
    setPreset(0); setRequest({ revision: 1, policy: policies[0] });
    setReceiptRevision(null); setPlanRevision(1); setDraft(fixturePlans[0]!.map(String));
    setReview(null); setReturned(null);
  }

  return <div className="dl-page">
    <a className="skip-link" href="#experiment">{t("Skip to the exercise", "跳到交互练习")}</a>
    <header className="dl-header">
      <a className="brand" href={base}><span className="product-mark" aria-hidden="true"><span /><span /><span /></span><span>LoopX</span></a>
      <a className="dl-source-link" href={source}>{t("Reproduce with real Agents", "查看真实 Agent 复现方法")} <ArrowRight size={14} /></a>
      <div className="dl-languages" aria-label={t("Language", "语言")}>
        <button aria-pressed={language === "en"} onClick={() => setLanguage("en")}>EN</button>
        <button aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>中文</button>
      </div>
    </header>
    <main className="dl-main">
      <section className="dl-hero">
        <p className="dl-kicker">{t("Interactive case · browser-local simulation", "交互案例 · 浏览器本地模拟")}</p>
        <h1>{t("The request changed.", "要求改了，")}<br />{t("Did the result?", "结果呢？")}</h1>
        <p>{t("A received message can leave yesterday’s plan intact. Change one constraint, find the broken allocation, and carry a checked result back to the owner.", "收到消息之后，旧方案可能原封不动。改一条要求，找出失效的分配，再把核验后的结果交回发起人。")}</p>
        <span className="dl-boundary">{t("You operate the steps. No Agents run and no LoopX state is changed.", "每一步由你操作；本页不运行 Agent，也不写入 LoopX 状态。")}</span>
      </section>

      <section className="dl-experiment" id="experiment" aria-labelledby="exercise-title">
        <noscript><p>{t("Enable JavaScript to use the local exercise. The source links and explanation below work without it.", "启用 JavaScript 后可操作本地练习；下方原始资料和说明无需 JavaScript。")}</p></noscript>
        <div className="dl-section-top"><h2 id="exercise-title">{t("Follow one correction", "跟进一次纠正")}</h2><button className="dl-reset" onClick={reset}><RotateCcw size={14} />{t("Reset exercise", "重置练习")}</button></div>
        <div className="dl-workbench">
          <section className="dl-request" aria-labelledby="request-title">
            <p className="dl-kicker">01 · {t("Owner → builder", "发起人 → builder")}</p>
            <h3 id="request-title">{t("Change the brief", "修订要求")}</h3>
            <p>{t("Allocate six orders. Maximize value within shared stock, regional capacity and a 1,000-cent budget.", "为六笔订单分配数量，在共享库存、地区容量和 1,000 分预算内最大化价值。")}</p>
            <div className="dl-rule"><span>{t("Keep in each stock group", "每组库存保留")}</span><strong>{request.policy.reserve}</strong></div>
            <div className="dl-rule"><span>{t("Minimum East allocation", "东区最少分配")}</span><strong>{request.policy.minimumEast}</strong></div>
            <p className="dl-revision">{t("Current request", "当前要求")} <b>v{request.revision}</b></p>
            <button className="dl-primary" aria-disabled={preset === 1} onClick={() => { if (preset !== 1) changeRequest(1); }}>{preset === 1 ? t("Correction added", "已加入纠正") : t("Add the owner’s correction", "加入发起人的纠正")} <ArrowRight size={16} /></button>
            {preset !== 0 && <p className="dl-prompt">{planRevision !== request.revision ? t("The brief changed. The plan on the right has not.", "要求已修改。右侧方案没有随之自动更新。") : currentReview && review?.accepted ? t("This allocation has been checked against the current request.", "这份分配已按当前要求核验。") : t("This plan now names the current request. Its result still needs a check.", "方案已明确依据当前要求，结果仍需核验。")}</p>}
            <details><summary>{t("Try another request", "试试其他要求")}</summary>
              <label htmlFor="request-preset">{t("Constraint set", "约束条件")}</label>
              <select id="request-preset" value={preset} onChange={(event) => changeRequest(Number(event.target.value))}>{names.map((name, i) => <option key={i} value={i}>{name}</option>)}</select>
              <p>{t("Each change creates a new version. Prior checks stay visible but cannot accept the new input.", "每次修改产生新版本。旧核验保留可见，但不能验收新输入。")}</p>
            </details>
          </section>

          <div className="dl-delivery">
            <section className="dl-receipt" aria-labelledby="receipt-title">
              <div><p className="dl-kicker">02 · {t("Receipt", "接收回执")}</p><h3 id="receipt-title">{receiptRevision === request.revision ? `${t("Received request", "已接收要求")} v${receiptRevision}` : t("Receipt is one fact", "回执只说明一件事")}</h3><span className="dl-hint">{t("Receiving a request does not update an artifact.", "接收要求不会自动更新产物。")}</span></div>
              <button className="dl-secondary" aria-disabled={receiptRevision === request.revision} onClick={() => { if (receiptRevision !== request.revision) setReceiptRevision(request.revision); }}>{receiptRevision === request.revision ? t("Received", "已接收") : t("Mark received", "标记已接收")}</button>
              {receiptRevision !== null && receiptRevision !== request.revision && <span className="dl-stale">{t("Previous receipt", "历史回执")} v{receiptRevision}</span>}
            </section>
            <section className="dl-plan" aria-labelledby="plan-title">
              <div className="dl-section-top"><div><p className="dl-kicker">03 · {t("Builder’s artifact", "builder 的产物")}</p><h3 id="plan-title">{t("Inspect the actual allocation", "检查实际分配")}</h3></div><span className={planRevision === request.revision ? "dl-revision" : "dl-stale"}>{planRevision === request.revision ? t("Plan input", "方案依据") : t("Stale plan input", "失效方案依据")} v{planRevision}</span></div>
              <div className="dl-table-wrap"><table className="dl-orders"><caption className="sr-only">{t("Editable quantities in order-id order; costs are cents per unit", "按订单编号排列的可编辑数量；成本单位为分/件")}</caption><thead><tr><th>{t("Order", "订单")}</th><th>{t("Group / region", "组 / 地区")}</th><th>{t("Demand", "需求")}</th><th>{t("Cost / value", "成本 / 价值")}</th><th>{t("Allocate", "分配")}</th></tr></thead><tbody>{orders.map((row, i) => <tr key={row.id}><th scope="row">{row.id}</th><td>{row.stock_group} / {row.region === "east" ? t("East", "东区") : t("West", "西区")}</td><td>{row.demand}</td><td>{row.cost} / {row.value}</td><td><input aria-label={`${t("Allocate order", "分配订单")} ${row.id}`} type="number" min="0" max={row.demand} step="1" value={draft[i]} onChange={(event) => setDraft(draft.map((n, j) => i === j ? event.target.value : n))} /></td></tr>)}</tbody></table></div>
              <div className="dl-measures"><span>{t("Value", "价值")} <b>{inspection.invalid.length ? "—" : inspection.value}</b></span><span>{t("Cost", "成本")} <b>{inspection.invalid.length ? "—" : inspection.cost}</b> / {budget}</span><span>{t("Stock", "库存")} x {stock.x} · y {stock.y} · z {stock.z}</span><span>{t("Region caps", "地区容量")} {t("East", "东")} {regionCapacity.east} · {t("West", "西")} {regionCapacity.west}</span></div>
              <div className="dl-problems" aria-live="polite">
                {inspection.invalid.length > 0 && <p>{t("Invalid quantity", "数量不合法")}: {inspection.invalid.join(", ")}. {t("Use whole numbers from 0 to demand.", "请输入 0 到需求量之间的整数。")}</p>}
                {inspection.violations.map((issue, i) => <p key={i}>{issue.subject === "budget" ? t("Budget", "预算") : issue.subject === "east" ? t("East", "东区") : issue.subject === "west" ? t("West", "西区") : `${t("Stock group", "库存组")} ${issue.subject}`}: {issue.used} {issue.minimum ? "<" : ">"} {issue.limit}. {t("Current request violated.", "不符合当前要求。")}</p>)}
              </div>
              <button className="dl-secondary" disabled={!fixturePlans[preset]} onClick={() => { setDraft(fixturePlans[preset]!.map(String)); setPlanRevision(request.revision); }}>{t("Adopt request + load fixture plan", "采纳当前要求，并载入样例方案")}</button>
              <p className="dl-hint">{fixturePlans[preset] ? t("Loads a published example, not a model-generated answer. You can edit any quantity.", "载入公开样例，不是现场生成的答案。你也可以修改任意数量。") : t("No feasible fixture plan: East capacity is 5, but this request requires 99. Change the request to recover.", "不存在可行样例：东区容量只有 5，要求却是 99。修改要求后可继续。")}</p>
            </section>
          </div>
        </div>

        <section className="dl-review" aria-labelledby="review-title">
          <div><p className="dl-kicker">04 · {t("Independent check → original conversation", "独立核验 → 原对话")}</p><h3 id="review-title">{t("A check belongs to exact inputs", "核验必须对应确切的输入")}</h3><p>{t(`Enumerate all ${vectorCount.toLocaleString("en-US")} integer vectors; enforce every constraint, then the value and lexicographic tie-break.`, `枚举全部 ${vectorCount.toLocaleString("en-US")} 个整数向量；检查全部约束，再核对价值与字典序平局规则。`)}</p></div>
          <div className="dl-review-actions"><button className="dl-primary" onClick={() => setReview(reviewPlan(request, draft))}>{t("Check current plan", "核验当前方案")}</button><button className="dl-secondary" disabled={!returnReady} aria-disabled={returnedCurrent || undefined} onClick={() => { if (returnReady && review && !returnedCurrent) setReturned({ revision: request.revision, draft: [...draft], value: review.inspection.value, cost: review.inspection.cost }); }}>{returnedCurrent ? t("Returned in this exercise", "已在练习中回传") : t("Return reviewed result", "回传已核验结果")}</button></div>
          <div className="dl-review-result" role="status">
            {!review ? <p>{t("Not checked yet. A delivery receipt does not verify this plan.", "尚未核验。消息回执不能证明方案正确。")}</p> : !currentReview ? <p className="dl-stale">{t("Stale check retained", "已保留失效的核验")}: v{review.requestRevision} · [{review.draft.join(", ")}]. {t("The request or artifact changed. Check again.", "要求或产物已改变，需要重新核验。")}</p> : !review.best ? <p className="dl-stale">{t("No feasible allocation for this request. Do not return a success result.", "当前要求无可行解，不能回传成功结果。")}</p> : review.accepted ? <p><CheckCircle2 size={16} />{t("Allocation verified", "分配已核验")} · v{review.requestRevision} · {t("value", "价值")} {review.inspection.value} · {t("cost", "成本")} {review.inspection.cost}.</p> : <p className="dl-stale">{t("Rejected", "核验未通过")}. {review.inspection.invalid.length || review.inspection.violations.length ? t("Fix the quantities or the violated constraints.", "先修复数量或违反的约束。") : t("Feasible is not enough: this is not the maximum-value, lexicographically first plan.", "可行还不够：这不是价值最高且字典序最小的方案。")}</p>}
            {currentReview && review?.accepted && !returnReady && <p>{t("Return still needs receipt and adoption of this request version.", "回传还需要接收、采纳当前版本的要求。")}</p>}
          </div>
          {returned && <div className="dl-return"><span className="dl-kicker">{t("Local return · retained record", "本地回传 · 保留记录")}</span><p>{t("Owner, reviewed allocation", "发起人，已核验分配")} [{returned.draft.join(", ")}] · v{returned.revision} · {t("value", "价值")} {returned.value} · {t("cost", "成本")} {returned.cost}.</p>{!returnedCurrent && <p className="dl-stale">{t("Historical return. It does not cover the current request and artifact.", "这是历史回传，不能代表当前要求与产物。")}</p>}</div>}
        </section>
      </section>

      <section className="dl-evidence" aria-labelledby="evidence-title"><h2 id="evidence-title">{t("What this teaches—and what the source proves", "练习说明了什么，原始证据覆盖到哪")}</h2>
        <div><p>{t("The old plan uses 5 units of group y. Keeping 1 from stock 5 leaves only 4. A receipt cannot repair that inequality.", "旧方案使用库存组 y 的 5 件。总库存 5 件、保留 1 件后，只能分配 4 件。回执不会自动修复这个不等式。")}</p><p>{t("The original optimum is 84; the corrected optimum is 83 under different constraints. That decrease is not a performance comparison.", "原始最优价值是 84，新约束下是 83。两者可行域不同，数值变化不能用来比较性能。")}</p></div>
        <div><p>{t("The reproducible source uses three managed Agents across fresh sessions, versioned requests, explicit file transfer, independent review and a real original-chat return pump. This page illustrates a smaller chain; it does not reenact those runs.", "可复现源码包含三个受管 Agent 跨全新会话接力、版本化请求、明确的文件传输、独立复核与真实原对话回传。本页只演示其中一条简化链路，不重演那些运行。")}</p><p>{t("Same host, same Goal, controller-selected phases. It does not establish autonomous scheduling, cross-host support or long-horizon effectiveness/efficiency gains.", "源案例是同机、同 Goal、控制器选择阶段；它不能证明自主调度、跨机能力，或长程效果与效率收益。")}</p></div>
        <nav aria-label={t("Primary evidence", "原始证据")}><a href={`${source.replace("/tree/", "/blob/")}/README.md`}>{t("Run the real example", "运行真实案例")} <ArrowRight size={14} /></a><a href={`${source.replace("/tree/", "/blob/")}/verify.py`}>{t("Inspect the separate oracle", "阅读独立 oracle")} <ArrowRight size={14} /></a><a href={`${base}lab/time-machine/${language === "zh" ? "?lang=zh" : ""}`}>{t("Explore a recorded research episode", "探索真实研究记录")} <ArrowRight size={14} /></a></nav>
      </section>
    </main>
    <footer className="dl-footer">LoopX · {t("Evidence before a success sentence.", "先看证据，再说完成。")}</footer>
  </div>;
}
