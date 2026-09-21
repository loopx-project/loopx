import type {DelegationPreflight} from "../../data/delegation-preflight.js";

const labels: Record<DelegationPreflight["state"], {en: string; zh: string}> = {
  authority_unavailable: {en: "Canonical authority unavailable", zh: "缺少规范权限状态"},
  turn_blocked: {en: "Task admission blocked", zh: "当前任务未获准执行"},
  acceptance_unavailable: {en: "Acceptance binding unavailable", zh: "缺少有效验收绑定"},
  runtime_unavailable: {en: "Runtime unavailable", zh: "运行时不可用"},
  runtime_unverified: {en: "Runtime availability unverified", zh: "运行时可用性尚未验证"},
  launchable: {en: "Local launch prerequisites met", zh: "本机启动条件已满足"},
};

export function DelegationPreflightStatus({check, zh}: {check: DelegationPreflight; zh: boolean}) {
  const detail = check.executor
    ? [check.executor.host, check.executor.reason].filter(Boolean).join(" · ")
    : check.authority_reason;
  const disclaimer = check.state === "authority_unavailable"
    ? (zh ? "未检查或启动执行器" : "No executor was inspected or launched")
    : (zh ? "不代表正在执行" : "Does not mean executing");
  const authorityAction = check.authority_next_action === "preview_reviewed_goal_authority_promotion"
    ? (zh ? "下一步：预览整 Goal 协调 Authority 晋级" : "Next: preview whole-Goal coordination-authority promotion")
    : check.authority_next_action === "repair_canonical_authority"
      ? (zh ? "下一步：修复规范 Authority 读回" : "Next: repair canonical authority readback")
      : null;
  return <p role="status">
    {zh ? labels[check.state].zh : labels[check.state].en}
    {detail ? ` · ${detail}` : ""}
    {authorityAction ? ` · ${authorityAction}` : ""}
    {` · ${disclaimer}`}
  </p>;
}
