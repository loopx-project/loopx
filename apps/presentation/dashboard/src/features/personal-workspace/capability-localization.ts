import type { CapabilityConfigurationCatalog } from "../../data/chat";
import type { WorkspaceLocale } from "./i18n";

type CapabilityDescriptor = CapabilityConfigurationCatalog["capabilities"][number];
type LocalizedCopy = Readonly<{
  description: string;
  displayName: string;
  readOnlyReason?: string;
}>;

type FieldCopy = Record<string, Readonly<{ description?: string; label: string }>>;

const capabilityCopy: Record<WorkspaceLocale, Record<string, LocalizedCopy>> = {
  en: {
    manager_runtime: {
      displayName: "Manager runtime",
      description: "Selects the persistent host-tool profile used by owner manager conversations.",
    },
    steward_executor: {
      displayName: "Steward executor",
      description: "Guides the steward executor, model, and selection boundary for this machine. A pinned route blocks substitution; a flexible pool permits only authorized fallback.",
    },
    todo_replan_cadence: { displayName: "Goal review cadence", description: "Configures the Goal review cadence." },
    change_quality_qualification: {
      displayName: "Change quality qualification",
      description: "Prepares a provider-neutral review packet, allows at most one policy-authorized safe-fix pass, and can require an exact-diff receipt.",
    },
    explore_graph: {
      displayName: "Explore Graph",
      description: "Organizes bounded exploration as a typed evidence graph so branches, findings, and synthesis remain inspectable.",
    },
    explore_harness: {
      displayName: "Explore Harness",
      description: "Selects a capability-owned planning and research harness profile for bounded multi-step exploration.",
    },
    lark_event_inbox: {
      displayName: "Lark event inbox",
      description: "Receives provider events through a local-private inbox binding before LoopX projects them into governed work.",
      readOnlyReason: "This capability requires a local-private inbox binding. Manage it in Lark settings or through the capability CLI.",
    },
    lark_kanban_heartbeat_sync: {
      displayName: "Lark Kanban heartbeat sync",
      description: "Synchronizes accepted LoopX work state to the configured Lark Kanban heartbeat surface.",
    },
    local_authority_shadow: {
      displayName: "Local authority shadow",
      description: "Observes post-commit Todo and task-lease state through the shared authority contract without taking write authority.",
    },
    coordination_runtime_shadow: {
      displayName: "Coordination runtime shadow",
      description: "Captures transaction-bound Todo and task-lease mutations for reviewed whole-Goal coordination-authority promotion.",
    },
    multi_subagent: {
      displayName: "Adaptive child capacity",
      description: "Sets bounded child-agent capacity and the public-safe responsibility domains in which parallel work may be delegated.",
    },
    peer_task_coordination: {
      displayName: "Registered-peer task coordination",
      description: "Routes explicitly scoped peer-owned work to one registered coordinator without granting cross-owner mutation authority.",
    },
    periodic_report: {
      displayName: "Periodic reports",
      description: "Turns validated Goal stage progress into a frozen report and automatically delivers it through the configured Goal Channel with exact readback.",
    },
    pull_request_review: {
      displayName: "Pull-request review",
      description: "Ranks the public GitHub PR review queue with a machine-level default; it never grants GitHub, Todo, push, or merge authority.",
    },
    reward_memory: {
      displayName: "Reward Memory experiment",
      description: "Configures a reviewed local-private provider binding for Goal-scoped Agent recall and evidence-backed outcome learning.",
    },
  },
  "zh-CN": {
    manager_runtime: {
      displayName: "管家 Runtime",
      description: "选择管家会话持续生效的宿主工具模式。",
    },
    steward_executor: {
      displayName: "管家执行器",
      description: "配置本机管家的执行器、模型与选择边界；锁定路径禁止替代，灵活池只允许在已授权范围内回退。",
    },
    todo_replan_cadence: { displayName: "Goal 复核周期", description: "配置 Goal 的复核周期。" },
    change_quality_qualification: {
      displayName: "变更质量验证",
      description: "生成与 Provider 无关的审阅包，最多允许一次策略授权的安全修复，并可要求精确 diff 回执。",
    },
    explore_graph: {
      displayName: "探索图谱",
      description: "把有界探索组织为 typed 证据图，让探索分支、发现与综合结论都可检查、可追溯。",
    },
    explore_harness: {
      displayName: "探索 Harness",
      description: "为有界的多步探索选择由能力负责的规划与研究 Harness profile。",
    },
    lark_event_inbox: {
      displayName: "飞书事件收件箱",
      description: "通过本机私有收件箱接收 Provider 事件，再由 LoopX 将其投影为受治理的工作。",
      readOnlyReason: "此能力依赖本机私有的收件箱绑定，请在飞书设置或 capability CLI 中管理。",
    },
    lark_kanban_heartbeat_sync: {
      displayName: "飞书看板心跳同步",
      description: "把 LoopX 已接受的工作状态同步到配置好的飞书看板心跳界面。",
    },
    local_authority_shadow: {
      displayName: "本地 Authority 影子观测",
      description: "通过共享 Authority contract 观测提交后的 Todo 与 task lease 状态，但不取得写入权。",
    },
    coordination_runtime_shadow: {
      displayName: "协调 Runtime 影子",
      description: "捕获事务绑定的 Todo 与 task lease 变更，为经评审的整 Goal 协调 Authority 晋级提供证据。",
    },
    multi_subagent: {
      displayName: "自适应子 Agent 容量",
      description: "限定子 Agent 容量与可公开的职责域，只有落在这些边界内的工作才能并行委派。",
    },
    peer_task_coordination: {
      displayName: "已注册 Peer 任务协调",
      description: "把明确限定的 Peer 工作路由给一个已注册协调者，不授予跨 Owner 修改权限。",
    },
    periodic_report: {
      displayName: "周期报告",
      description: "把经过验证的 Goal 阶段进展整理为冻结报告，并通过配置的 Goal Channel 自动发送和精确回读。",
    },
    pull_request_review: {
      displayName: "Pull-request Review",
      description: "配置公开 GitHub PR 审阅队列的本机默认排序；不会授予 GitHub、Todo、push 或 merge 权限。",
    },
    reward_memory: {
      displayName: "Reward Memory 实验",
      description: "为 Goal 内 Agent 的召回与证据化结果学习配置经过审阅的本机私有 Provider 绑定。",
    },
  },
};

const fieldCopy: Record<WorkspaceLocale, FieldCopy> = {
  en: {
    runtime_profile: { label: "Runtime profile", description: "Restricted keeps scoped LoopX reads only. Trusted owner enables normal host tools while protected operations retain separate checks." },
    selection_policy: { label: "Selection policy", description: "Preferred allows an explicit user choice; pinned rejects another executor; flexible permits fallback only inside the eligible pool." },
    executor_endpoint: { label: "Primary steward executor", description: "The preferred or pinned executor for this machine. In a flexible pool it is tried first when available." },
    eligible_endpoints: { label: "Flexible eligible executors", description: "One authorized executor per line. Use only with flexible selection and include the primary executor." },
    executor_model: { label: "Model", description: "Optional model for the selected executor. Leave blank to keep the executor's own default." },
    executor_reasoning_effort: { label: "Reasoning effort", description: "Optional reasoning effort for the selected executor. Leave blank to keep the executor's own default." },
    completed_todos: { label: "Completed Todos between Goal reviews", description: "Machine default or explicit Goal override, from 1 to 5." },
    allowed_domains: { label: "Allowed responsibility domains", description: "Enter one bounded, public-safe domain per line." },
    coordinator_agent_id: { label: "Coordinator Agent", description: "Use an already registered Agent id; leave blank to disable coordination." },
    enabled: { label: "Enabled" },
    model: { label: "Child model", description: "For example gpt-5.6-luna. Blank clears the child model preference." },
    reasoning_effort: { label: "Child reasoning effort", description: "For example max; the host must support this model and effort." },
    max_children: { label: "Maximum children", description: "Hard upper bound for concurrently delegated child work." },
    profile: { label: "Planner profile", description: "Select one registered Explore Harness profile." },
    profile_preset: { label: "Report profile", description: "Capability-owned report profile, such as weekly-progress." },
    wait_for_ci: { label: "Wait for CI", description: "Disable to use local validation without querying or waiting for CI. Merge authority is unchanged." },
    review_priority: { label: "Review priority", description: "Choose whether other developers' PRs or the authenticated reviewer's own PRs are ranked first." },
    route_ref: { label: "Goal Channel route", description: "Public route alias only; credentials and provider identifiers stay outside this form." },
    safe_fix: { label: "Allow one bounded safe-fix pass" },
    strict_receipt: { label: "Require an exact-diff receipt" },
    timezone: { label: "Timezone", description: "Use an IANA timezone, for example Asia/Shanghai." },
    schedule: { label: "Calendar reports", description: "Optional daily or weekly reports; no schedule preserves stage-only delivery." },
    config_path: { label: "Local-private configuration path", description: "Repo-relative ignored JSON under .loopx/config/. Leave blank to retain the current binding; the path is never returned." },
    enabled_agents: { label: "Enabled Goal Agents", description: "Enter one registered Goal-local Agent id per line. A private binding currently accepts exactly one Agent." },
  },
  "zh-CN": {
    runtime_profile: { label: "运行模式", description: "restricted 仅使用受限 LoopX 读取；trusted_owner 开放常规宿主工具，但受保护操作仍单独校验。" },
    selection_policy: { label: "选择策略", description: "preferred 允许用户显式改选；pinned 拒绝其他执行器；flexible 只在已授权资源池内回退。" },
    executor_endpoint: { label: "首选管家执行器", description: "本机首选或锁定的执行器；灵活池模式下优先尝试它。" },
    eligible_endpoints: { label: "灵活池可用执行器", description: "每行一个已授权执行器，仅用于 flexible；必须包含首选执行器。" },
    executor_model: { label: "模型", description: "所选执行器使用的模型，可留空；留空表示沿用执行器自身的默认模型。" },
    executor_reasoning_effort: { label: "推理档位", description: "所选执行器使用的推理档位，可留空；留空表示沿用执行器自身的默认档位。" },
    completed_todos: { label: "两次 Goal 复核间的已完成 Todo 数", description: "可设置 1–5；机器默认值可被 Goal 显式覆盖。" },
    allowed_domains: { label: "允许的职责域", description: "每行填写一个有边界、可公开的职责域。" },
    coordinator_agent_id: { label: "协调 Agent", description: "填写一个已经注册的 Agent ID；留空表示关闭协调。" },
    enabled: { label: "启用" },
    model: { label: "子 Agent 模型", description: "例如 gpt-5.6-luna；留空清除模型偏好。" },
    reasoning_effort: { label: "子 Agent 推理档位", description: "例如 max；宿主须支持所选模型与档位。" },
    max_children: { label: "最大子 Agent 数", description: "可同时委派的子任务硬上限。" },
    profile: { label: "规划 Profile", description: "选择一个已注册的 Explore Harness profile。" },
    profile_preset: { label: "报告 Profile", description: "由该能力管理的报告 profile，例如 weekly-progress。" },
    wait_for_ci: { label: "等待 CI", description: "关闭后使用本地验证，不查询或等待 CI；不改变合并权限。" },
    review_priority: { label: "审阅优先级", description: "选择先排其他开发者的 PR，还是先排当前已认证审阅者自己的 PR。" },
    route_ref: { label: "Goal Channel 路由", description: "只填写公开 route alias；凭据与 Provider 标识不会进入此表单。" },
    safe_fix: { label: "允许一次有界安全修复" },
    strict_receipt: { label: "要求精确 diff 回执" },
    timezone: { label: "时区", description: "使用 IANA 时区，例如 Asia/Shanghai。" },
    schedule: { label: "日历汇报", description: "可选每日或每周计划；未设置时保持阶段结束汇报。" },
    config_path: { label: "本机私有配置路径", description: "填写 .loopx/config/ 下、相对仓库且被忽略的 JSON；留空保留当前绑定，路径不会被回传。" },
    enabled_agents: { label: "已启用的 Goal Agent", description: "每行填写一个已注册的 Goal 内 Agent ID；私有绑定当前只接受一个 Agent。" },
  },
};

export function localizeCapability(
  capability: CapabilityDescriptor,
  locale: WorkspaceLocale,
): CapabilityDescriptor {
  const copy = capabilityCopy[locale][capability.capability_id];
  if (!copy) return capability;
  return {
    ...capability,
    display_name: copy.displayName,
    description: copy.description,
    configuration_editor: {
      ...capability.configuration_editor,
      ...(copy.readOnlyReason ? { read_only_reason: copy.readOnlyReason } : {}),
    },
  };
}

export function localizedCapabilityFieldCopy(locale: WorkspaceLocale): FieldCopy {
  return fieldCopy[locale];
}

export const localizedCapabilityIds = Object.freeze(Object.keys(capabilityCopy.en).sort());
