import {renderToStaticMarkup} from "react-dom/server";
import type {DelegationPreflight} from "../src/data/delegation-preflight.js";
import {DelegationPreflightStatus} from "../src/features/personal-workspace/delegation-preflight-status.js";

const unavailable: DelegationPreflight = {
  state: "authority_unavailable",
  turn_eligible: false,
  acceptance_ready: false,
  turn_route: null,
  authority_ready: false,
  authority_reason: "Goal acceptance requires an existing canonical authority",
  authority_state: "promotion_required",
  authority_next_action: "preview_reviewed_goal_authority_promotion",
  promotion_from_surface_allowed: false,
  executor: null,
  effects: {
    host_invoked: false,
    state_written: false,
    quota_spent: false,
    scheduler_acknowledged: false,
  },
};

for (const [zh, expectedLabel, expectedBoundary] of [
  [true, "缺少规范权限状态", "未检查或启动执行器"],
  [false, "Canonical authority unavailable", "No executor was inspected or launched"],
] as const) {
  const html = renderToStaticMarkup(<DelegationPreflightStatus check={unavailable} zh={zh}/>);
  if (!html.includes(expectedLabel)) throw new Error(`missing state label: ${expectedLabel}`);
  if (!html.includes("Goal acceptance requires an existing canonical authority")) throw new Error("missing authority reason");
  if (!html.includes(expectedBoundary)) throw new Error(`missing boundary copy: ${expectedBoundary}`);
  if (!html.includes(zh ? "下一步：预览整 Goal 协调 Authority 晋级" : "Next: preview whole-Goal coordination-authority promotion")) {
    throw new Error("missing reviewed promotion next action");
  }
  if (/Local launch prerequisites met|本机启动条件已满足/.test(html)) throw new Error("rendered launchable copy for unavailable authority");
}
if (Object.values(unavailable.effects).some(Boolean)) throw new Error("authority-unavailable response must report zero effects");

for (const workspace_state of ["missing", "not_directory", "unavailable"] as const) {
  const check: DelegationPreflight = {...unavailable, state: "workspace_unavailable",
    workspace_state, workspace_next_action: "review_operator_workspace_binding",
    authority_ready: null, authority_reason: null, authority_state: "uninspected",
    authority_next_action: "none"};
  for (const zh of [true, false]) {
    const html = renderToStaticMarkup(<DelegationPreflightStatus check={check} zh={zh}/>);
    if (!html.includes(zh ? "绑定工作目录不可用" : "Bound workspace unavailable")) throw new Error("missing workspace fault label");
    if (!html.includes(zh ? "核对原执行配置的工作目录" : "review the workspace in the original execution configuration")) throw new Error("missing workspace recovery action");
    if (!html.includes(zh ? "权限、验收与运行时尚未检查" : "Authority, acceptance and runtime uninspected")) throw new Error("workspace fault misreports inspection");
    if (/晋级|promotion|Canonical authority unavailable|缺少规范权限状态/.test(html)) throw new Error("workspace fault suggested authority repair/promotion");
  }
}

for (const [reason, action, en, zhText] of [
  ["independent_delegation_validation_required", "review_original_todo_validation", "Independent Todo validation is required", "原任务缺少独立验收声明"],
  ["completion_validation_declaration_unavailable", "review_original_todo_validation", "Declared Todo validation is unavailable", "原任务验收声明不可读取"],
  ["completion_validation_declaration_mismatch", "review_original_todo_validation", "Todo validation declaration does not match", "原任务验收声明与当前指纹不符"],
  ["validation_files_unavailable", "restore_original_validation_files", "Pinned validation files are unavailable", "固定验收文件不可用"],
  ["acceptance_binding_unavailable", "review_original_task_acceptance", "Current task acceptance could not be established", "当前任务验收依据不可确认"],
] as const) {
  const check = {...unavailable, state: "acceptance_unavailable",
    authority_ready: true, authority_reason: null, authority_state: "promoted", authority_next_action: "none",
    acceptance_reason_code: reason, acceptance_next_action: action,
    executor: {host: "dsh", available: true, reason: null, profile: null}} as DelegationPreflight;
  for (const zh of [true, false]) {
    const html = renderToStaticMarkup(<DelegationPreflightStatus check={check} zh={zh}/>);
    if (!html.includes(zh ? "任务验收不可用" : "Task validation unavailable")) throw new Error("misleading acceptance label");
    if (!html.includes(zh ? zhText : en)) throw new Error(`missing bounded validation diagnosis: ${reason}`);
    if (!html.includes(zh ? "原配置责任人" : "original configuration owner")) throw new Error("missing original-owner recovery hint");
    if (/晋级|promotion|Runtime unavailable|运行时不可用|缺少有效验收绑定/.test(html)) throw new Error("validation diagnosis suggested a different authority/runtime fault");
    if (!html.includes(zh ? "不代表正在执行" : "Does not mean executing")) throw new Error("lost inspection boundary");
  }
}

const malformed = {...unavailable, state: "acceptance_unavailable",
  authority_ready: true, authority_reason: null, authority_state: "promoted", authority_next_action: "none",
  acceptance_reason_code: "__proto__ /private/validator", acceptance_next_action: "PRIVATE_VALUE"} as unknown as DelegationPreflight;
for (const zh of [true, false]) {
  const html = renderToStaticMarkup(<DelegationPreflightStatus check={malformed} zh={zh}/>);
  if (/PRIVATE_VALUE|private\/validator|__proto__/.test(html)) throw new Error("unrecognized validation diagnostic leaked");
}

console.log("delegation authority/workspace/validation preflight smoke passed");

for (const valid of [true, false]) {
  const check = {...unavailable, state: "runtime_unverified", authority_ready: true,
    authority_reason: null, authority_state: "promoted", authority_next_action: "none",
    executor: {host: "codex-cli", available: valid, reason: valid ? null : "operation_transport_profile_required",
      profile: "test-model@xhigh", operation_transport: {schema_version: "loopx_operation_transport_v0",
        configuration_valid: valid, runtime_qualified: false}}} as DelegationPreflight;
  for (const zh of [false, true]) {
    const html = renderToStaticMarkup(<DelegationPreflightStatus check={check} zh={zh}/>);
    if (!html.includes(valid ? (zh ? "运行未核验" : "runtime unqualified")
      : (zh ? "操作传输配置未获准" : "Operation transport configuration not admitted"))) {
      throw new Error("Shared transport projection lost truthful configuration readback");
    }
    if (/Runtime qualified|运行已核验/.test(html)) throw new Error("Preflight invented transport qualification");
  }
}
console.log("managed operation transport remains configuration-only in delegation preflight");
