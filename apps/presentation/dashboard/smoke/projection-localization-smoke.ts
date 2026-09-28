import {
  agentStatusSentence,
  projectionSentence,
} from "../src/features/personal-workspace/projection-localization.js";

type Values = Record<string, string | number>;

const english = {
  "projection.agentWorkQueued": "Agent work is queued for this Goal",
  "projection.agentIdle": "Nothing needs your attention",
  "projection.agentNeedsDecision": "Agent is waiting for your decision",
  "projection.agentPreparingNextStep": "Agent is preparing the next step",
  "projection.agentStopped": "Stopped by you; history, Todos, and evidence are preserved",
  "projection.agentWaitingExternal": "Waiting for an external condition",
  "projection.confirmAgentDecision": "Confirm the permission or decision the Agent needs next",
  "projection.firstReadOnlyAdapterCheck": "Run the first read-only adapter check and save progress",
  "projection.nextUpdatePending": "Waiting for LoopX to update the next step",
  "projection.refreshState": "Refresh LoopX status and confirm the current progress is still valid",
  "projection.statusRefreshNeeded": "LoopX status needs to be refreshed",
  "projection.todoStatusUpdated": "Todo status updated; confirming the next step",
} as const;

const chinese = {
  "projection.agentWorkQueued": "这个 Goal 有待 Agent 推进的工作",
  "projection.agentIdle": "暂无需要你处理",
  "projection.agentNeedsDecision": "Agent 等待你的决定",
  "projection.agentPreparingNextStep": "Agent 正在整理下一步",
  "projection.agentStopped": "已由你停止；历史、Todo 和证据仍保留",
  "projection.agentWaitingExternal": "正在等待外部条件",
  "projection.confirmAgentDecision": "请确认 Agent 下一步需要的权限或决策",
  "projection.events24h": "24 小时内 {count} 个事件",
  "projection.firstReadOnlyAdapterCheck": "执行首次只读适配检查并保存进度",
  "projection.goalVerified": "Goal 状态、Todo 与注册信息已验证",
  "projection.latestRun": "最近运行",
  "projection.latestValidation": "最近验证",
  "projection.nextUpdatePending": "等待 LoopX 更新下一步",
  "projection.publicSafeProjection": "公开安全状态投影",
  "projection.refreshState": "刷新 LoopX 状态，确认当前进度仍然有效",
  "projection.runEvidenceAvailable": "存在可查看的运行证据",
  "projection.runRecorded": "最近一次 LoopX 运行已经记录",
  "projection.statusRefreshNeeded": "LoopX 状态需要刷新",
  "projection.todoStatusUpdated": "Todo 状态已经更新，正在确认下一步",
  "projection.validationRecorded": "最近验证已经记录",
} as const;

type Key = keyof typeof english;
type Translate = (key: Key, values?: Values) => string;

function translator(messages: Record<Key, string>): Translate {
  return (key, values) => Object.entries(values ?? {}).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    messages[key],
  );
}

function equal(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`);
  }
}

const en = translator(english);
const zhCN = translator(chinese);

equal(
  projectionSentence("loopx check --format json", en, "projection.agentWorkQueued"),
  "Agent work is queued for this Goal",
  "English command-like projection uses the localized queued-work fallback",
);
equal(
  projectionSentence("loopx check --format json", zhCN, "projection.agentWorkQueued"),
  "这个 Goal 有待 Agent 推进的工作",
  "Chinese command-like projection preserves the localized queued-work fallback",
);
equal(
  projectionSentence("Review the project-specific adapter signal", en, "projection.agentWorkQueued"),
  "Review the project-specific adapter signal",
  "Source-authored projection text remains unchanged",
);

equal(agentStatusSentence("needs_you", en), "Agent is waiting for your decision", "English needs-you status");
equal(agentStatusSentence("queued", en), "Agent work is queued for this Goal", "English queued-work status");
equal(agentStatusSentence("waiting_external", en), "Waiting for an external condition", "English external-wait status");
equal(agentStatusSentence("idle", en), "Nothing needs your attention", "English idle status");
equal(
  agentStatusSentence("stopped", en),
  "Stopped by you; history, Todos, and evidence are preserved",
  "English stopped status",
);

console.log("projection localization smoke passed");
