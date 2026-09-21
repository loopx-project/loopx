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

console.log("delegation authority-unavailable preflight smoke passed");
