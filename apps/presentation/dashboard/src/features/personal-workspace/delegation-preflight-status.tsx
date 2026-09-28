import type {DelegationPreflight} from "../../data/delegation-preflight.js";

const labels: Record<DelegationPreflight["state"], {en: string; zh: string}> = {
  workspace_unavailable: {en: "Bound workspace unavailable", zh: "绑定工作目录不可用"},
  authority_unavailable: {en: "Canonical authority unavailable", zh: "缺少规范权限状态"},
  turn_blocked: {en: "Task admission blocked", zh: "当前任务未获准执行"},
  acceptance_unavailable: {en: "Task validation unavailable", zh: "任务验收不可用"},
  runtime_unavailable: {en: "Runtime unavailable", zh: "运行时不可用"},
  runtime_unverified: {en: "Runtime availability unverified", zh: "运行时可用性尚未验证"},
  launchable: {en: "Local launch prerequisites met", zh: "本机启动条件已满足"},
};

const workspaceDetails = {
  missing: {en: "Directory missing", zh: "目录不存在"},
  not_directory: {en: "Bound location is not a directory", zh: "绑定位置不是目录"},
  unavailable: {en: "Directory could not be read", zh: "目录无法读取"},
};

const acceptanceDetails: Record<NonNullable<DelegationPreflight["acceptance_reason_code"]>, {en: string; zh: string}> = {
  independent_delegation_validation_required: {en: "Independent Todo validation is required", zh: "原任务缺少独立验收声明"},
  completion_validation_declaration_unavailable: {en: "Declared Todo validation is unavailable", zh: "原任务验收声明不可读取"},
  completion_validation_declaration_mismatch: {en: "Todo validation declaration does not match", zh: "原任务验收声明与当前指纹不符"},
  validation_files_unavailable: {en: "Pinned validation files are unavailable", zh: "固定验收文件不可用"},
  acceptance_binding_unavailable: {en: "Current task acceptance could not be established", zh: "当前任务验收依据不可确认"},
};

const acceptanceActions: Record<Exclude<NonNullable<DelegationPreflight["acceptance_next_action"]>, "none">, {en: string; zh: string}> = {
  review_original_todo_validation: {
    en: "Next: ask the original configuration owner to review the Todo validation declaration, then recheck",
    zh: "下一步：请原配置责任人核对任务验收声明，再重新检查",
  },
  restore_original_validation_files: {
    en: "Next: ask the original configuration owner to restore the pinned validation files, then recheck",
    zh: "下一步：请原配置责任人恢复固定验收文件，再重新检查",
  },
  review_original_task_acceptance: {
    en: "Next: ask the original configuration owner to review the current task and applicable acceptance contract, then recheck",
    zh: "下一步：请原配置责任人核对当前任务与适用验收契约，再重新检查",
  },
};

export function DelegationPreflightStatus({check, zh}: {check: DelegationPreflight; zh: boolean}) {
  const workspaceDetail = check.workspace_state ? workspaceDetails[check.workspace_state] : null;
  const acceptanceDetail = check.state === "acceptance_unavailable" && check.acceptance_reason_code
    && Object.hasOwn(acceptanceDetails, check.acceptance_reason_code) ? acceptanceDetails[check.acceptance_reason_code] : null;
  const acceptanceAction = check.state === "acceptance_unavailable" && check.acceptance_next_action
    && check.acceptance_next_action !== "none" && Object.hasOwn(acceptanceActions, check.acceptance_next_action)
    ? acceptanceActions[check.acceptance_next_action] : null;
  const executorDetail = check.executor
    ? [check.executor.host, check.executor.reason].filter(Boolean).join(" · ") : check.authority_reason;
  const detail = check.state === "workspace_unavailable"
    ? (workspaceDetail ? (zh ? workspaceDetail.zh : workspaceDetail.en) : null)
    : [acceptanceDetail ? (zh ? acceptanceDetail.zh : acceptanceDetail.en) : null, executorDetail].filter(Boolean).join(" · ");
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
      : acceptanceAction ? (zh ? acceptanceAction.zh : acceptanceAction.en) : null;
  return <p role="status">
    {zh ? labels[check.state].zh : labels[check.state].en}
    {detail ? ` · ${detail}` : ""}
    {nextAction ? ` · ${nextAction}` : ""}
    {` · ${disclaimer}`}
  </p>;
}
