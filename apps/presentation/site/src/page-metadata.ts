// Public URLs are independent of the local preview's deployment base.
export const siteUrl = "https://loopx-project.github.io/loopx/";
export const pageMetadata = {
  timeMachine: {
    path: "lab/time-machine/",
    en: {
      title: "LoopX Time Machine — If you were there",
      description: "Make three choices for a research Agent, then compare them with real recorded outcomes. Inspect the original evidence, failed routes and remaining uncertainty.",
    },
    zh: {
      title: "LoopX 时光机 — 如果你在场",
      description: "先替研究 Agent 做选择，再看真实记录里的结果。逐条阅读原始证据、失败路线和仍未回答的问题，体验长程研究如何延续。",
    },
  },
  home: {
    path: "",
    en: {
      title: "LoopX — Stateful control plane for long-running AI agents",
      description: "Keep long-running AI agents on track across sessions with durable goals, tasks, human approval gates and recovery. Works with Codex, Claude Code and other agent runtimes.",
    },
    zh: {
      title: "LoopX — 长程 AI Agent 的有状态控制面",
      description: "让 Codex、Claude Code 等 Agent 跨会话持续推进任务，用持久目标、任务看板、人工审批与恢复机制管理长程工作。",
    },
  },
  sweMarathon: {
    path: "benchmarks/swe-marathon/",
    en: {
      title: "LoopX × SWE-Marathon: Continued self-verification",
      description: "Compare three retained agent execution modes across 15 matched SWE-Marathon tasks, with results, costs, self-verification mechanisms and study limitations.",
    },
    zh: {
      title: "LoopX × SWE-Marathon：持续自我验证",
      description: "对比 15 道匹配 SWE-Marathon 任务的三种保留执行模式，查看结果、成本、持续自我验证机制与研究局限。",
    },
  },
  lhtb: {
    path: "benchmarks/lhtb/",
    en: {
      title: "LoopX × LHTB: compared with Plain and native Goal",
      description: "Explore five execution mechanisms across 46 long-horizon terminal tasks, including Plain, native Goal and LoopX, with per-task results and study limitations.",
    },
    zh: {
      title: "LoopX × LHTB：与 Plain、原生 Goal 的对比",
      description: "查看 46 道长程终端任务中 Plain、原生 Goal 与 LoopX 等五种执行机制的逐任务结果、差异和研究局限。",
    },
  },
} as const;
export type PublicPage = keyof typeof pageMetadata;
