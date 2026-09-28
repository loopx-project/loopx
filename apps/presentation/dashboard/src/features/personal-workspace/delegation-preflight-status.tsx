import type {DelegationPreflight} from "../../data/delegation-preflight.js";

const labels: Record<DelegationPreflight["state"], {en: string; zh: string}> = {
  workspace_unavailable: {en: "Bound workspace unavailable", zh: "绑定工作目录不可用"},
  authority_unavailable: {en: "Canonical authority unavailable", zh: "缺少规范权限状态"},
  turn_blocked: {en: "Task admission blocked", zh: "当前任务未获准执行"},
  acceptance_unavailable: {en: "Acceptance binding unavailable", zh: "缺少有效验收绑定"},
  runtime_unavailable: {en: "Runtime unavailable", zh: "运行时不可用"},
  runtime_unverified: {en: "Runtime availability unverified", zh: "运行时可用性尚未验证"},
  launchable: {en: "Local launch prerequisites met", zh: "本机启动条件已满足"},
};

const workspaceDetails = {
  missing: {en: "Directory missing", zh: "目录不存在"},
  not_directory: {en: "Bound location is not a directory", zh: "绑定位置不是目录"},
  unavailable: {en: "Directory could not be read", zh: "目录无法读取"},
};

export function DelegationPreflightStatus({check, zh}: {check: DelegationPreflight; zh: boolean}) {
  const workspaceDetail = check.workspace_state ? workspaceDetails[check.workspace_state] : null;
  const detail = check.state === "workspace_unavailable"
    ? (workspaceDetail ? (zh ? workspaceDetail.zh : workspaceDetail.en) : null)
    : check.executor
      ? [check.executor.host, check.executor.reason].filter(Boolean).join(" · ")
      : check.authority_reason;
  const disclaimer = check.state === "workspace_unavailable"
    ? (zh ? "权限、验收与运行时尚未检查；未启动成员" : "Authority, acceptance and runtime uninspected; no member launched")
    : check.state === "authority_unavailable"
      ? (zh ? "未检查或启动执行器" : "No executor was inspected or launched")
      : (zh ? "不代表正在执行" : "Does not mean executing");
  const nextAction = check.workspace_next_action === "review_operator_workspace_binding"
    ? (zh ? "下一步：核对原执行配置的工作目录，再重新检查" : "Next: review the workspace in the original execution configuration, then recheck")
    : check.authority_next_action === "preview_reviewed_goal_authority_promotion"
    ? (zh ? "下一步：预览整 Goal 协调 Authority 晋级" : "Next: preview whole-Goal coordination-authority promotion")
    : check.authority_next_action === "repair_canonical_authority"
      ? (zh ? "下一步：修复规范 Authority 读回" : "Next: repair canonical authority readback")
      : null;
  return <p role="status">
    {zh ? labels[check.state].zh : labels[check.state].en}
    {detail ? ` · ${detail}` : ""}
    {nextAction ? ` · ${nextAction}` : ""}
    {` · ${disclaimer}`}
  </p>;
}
