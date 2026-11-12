// Public URLs are independent of the local preview's deployment base.
export const siteUrl = "https://loopx-project.github.io/loopx/";
export const pageMetadata = {
  delegationLab: {
    path: "lab/delegation/",
    en: {
      title: "LoopX Delegation Lab — The request changed. Did the result?",
      description: "Change an allocation request, expose a stale plan, and check a corrected result in a browser-local exercise. Explore receipt, adoption, independent verification and return.",
    },
    zh: {
      title: "LoopX 委派实验室 — 要求改了，结果呢？",
      description: "通过浏览器本地分配练习，修订要求、发现旧方案失效并核验新结果。区分接收回执、实际采纳、独立验证与结果回传，追溯可复现的原始案例。",
    },
  },
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
      title: "LoopX — Start long-running agent work with one goal",
      description: "Tell Codex, Claude Code or another agent one natural-language goal. LoopX keeps the work, decisions, verification and recovery durable across sessions.",
    },
    zh: {
      title: "LoopX — 从一个目标开始长程 Agent 工作",
      description: "给 Codex、Claude Code 等 Agent 一个自然语言目标。LoopX 跨会话保留工作、判断、验证结果与恢复状态。",
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
