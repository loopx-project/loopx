import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Clipboard,
  Code2,
  Download,
  ExternalLink,
  FileCheck2,
  GitBranch,
  Menu,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Target,
  TerminalSquare,
  UserRoundCheck,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { usePublicPageNavigation } from "./usePublicPageNavigation";

import issueEvidenceUrl from "../../../../docs/assets/long-running-loop-openviking-trajectory.png?url";
import mlEvidenceUrl from "../../../../docs/assets/long-running-loop-ml-experiment-trajectory.png?url";

type Language = "en" | "zh";

const setupPrompts: Record<Language, string> = {
  en: `Connect the current project to LoopX: https://github.com/loopx-project/loopx

Do not clone LoopX. Follow the README Quick Start to use the no-clone installer and \`loopx doctor\`, then reuse or \`loopx connect/bootstrap\` the current project state.

After setup, first report the project connection status, current user gate, top agent todo, next safe action, and available commands.

Then I will use \`/loopx <complex task>\` to begin real work.
When you receive \`/loopx <goal text>\`, first provide a concise ordered plan, then write it as P0/P1/P2 todos so I can see what needs to be done, what is blocked by a gate, and what the next safe action is.`,
  zh: `把当前项目接入 LoopX: https://github.com/loopx-project/loopx

不要 clone LoopX；按 README Quick Start 使用 no-clone installer、
\`loopx doctor\`，复用或 \`loopx connect/bootstrap\` 当前项目状态。

接入后先汇报：项目连接状态、当前 user gate、top agent todo、
next safe action，以及可用命令。

然后我会直接用 \`/loopx <复杂任务>\` 开始真实工作。
收到 \`/loopx <goal text>\` 后，先给出简洁有序计划，再把它写成
P0/P1/P2 todos，让我能看到当前要做什么、什么被 gate 卡住、
下一步安全动作是什么。`,
};

const shellSetupCommand = `curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor
cd /path/to/your-project
loopx connect
loopx status`;

const macAppDownloadUrl = "https://github.com/loopx-project/loopx/releases/latest/download/LoopX.app.zip";
const macAppGuideUrl = "https://github.com/loopx-project/loopx/blob/main/apps/desktop/loopx-control-plane/README.md";

const content = {
  en: {
    nav: ["Product", "Workflow", "Learn"],
    eyebrow: "The control plane for long-running AI agents",
    title: {
      first: "Long-running agents.",
      secondPrefix: "Lasting ",
      secondAccent: "progress",
    },
    body: "Turn complex work into durable goals, coordinated tasks, and reviewable results. LoopX keeps your agents moving across sessions. You keep the judgment.",
    copy: "Get started",
    mac: {
      download: "Download for Mac",
      compact: "Mac App",
      note: "Apple Silicon preview (.app.zip) · Python 3.11+ required.",
      guide: "Install and first launch",
    },
    setup: {
      eyebrow: "Set up LoopX",
      title: "Choose how you want to start.",
      body: "Use your current agent for the guided path, or copy the shell commands for a manual setup.",
      agentTitle: "Agent setup",
      agentBody: "Give the complete setup prompt to Codex, Claude Code, OpenCode, TraeX, Pi, or another shell-capable agent.",
      shellTitle: "Shell setup",
      shellBody: "Install LoopX, check the CLI, and connect the current project yourself.",
      copy: "Copy",
      copied: "Copied",
      close: "Close setup",
    },
    demo: "See in action",
    panel: {
      label: "ONE GOAL · ACROSS SESSIONS",
      title: "Ship the next release",
      status: "On track",
      running: "Agent work",
      researcher: "Research and implementation",
      gate: "Human gate",
      gateBody: "Merge needs your approval",
      waiting: "Waiting",
      evidence: "Evidence",
      evidenceBody: "Validation and handoff written",
      complete: "Complete",
      next: "Next safe action",
      nextBody: "Keep independent tasks moving while review waits",
    },
    proof: [
      ["Explicit authority", "Agents move fast without silently taking control."],
      ["Durable state", "The next session resumes from governed state, not memory."],
      ["Reviewable evidence", "Every transition leaves a result another person can verify."],
    ],
    sectionEyebrow: "One governed lifecycle",
    sectionTitle: "Long-running work stays legible.",
    sectionBody: "The runtime executes. LoopX preserves the control state that lets the work continue safely across sessions, hosts, and providers.",
    steps: [
      ["01", "Lifetime goal", "One durable direction"],
      ["02", "Authority gate", "A concrete decision"],
      ["03", "Agent lanes", "Scoped continuation"],
      ["04", "Evidence", "A validated outcome"],
      ["05", "Next run", "Recoverable progress"],
    ],
    capabilities: {
      eyebrow: "Across providers, runtimes, and hosts",
      title: "One governed loop across agent runtimes.",
      body: "Codex, Claude Code, OpenCode, TraeX, Pi, and custom runners can share one stateful goal lifecycle while each agent stays inside an explicit scope.",
      items: [
        ["01", "Durable state", "Goals, decisions, todos, claims, quota, run history, and handoffs survive session boundaries."],
        ["02", "Cross-runtime", "Change the executor or host without reconstructing the objective from chat history."],
        ["03", "Gate aware", "A blocked P0 stays visible while independent P1 and P2 lanes continue safely."],
        ["04", "Outcome driven", "Every run writes validation, ownership, review, and handoff evidence into the next loop."],
      ],
    },
    explore: {
      eyebrow: "Explore LoopX",
      title: "See the workspace. Explore the evidence.",
      body: "Try the product guide, read the research, and browse the full case collection.",
      cards: [
        ["Product demo", "Personal Workspace", "A shared view of Goals, Tasks, Chat, and outputs. Watch the demo and start your own local workspace.", "Watch demo & read guide"],
        ["Research & evaluation", "SWE-Marathon", "Compare three retained execution modes across 15 matched tasks, with results, costs, and study limitations.", "Read the study"],
        ["Research & evaluation", "LHTB × LoopX", "Compare five execution mechanisms across 46 long-horizon terminal tasks and inspect where recoverable state helps.", "Read the study"],
        ["Research · Chinese", "DeepSWE behavior discoveries", "Explore how domain hints affect implementation and validation in individual cases.", "Read the behavior analysis"],
        ["Research · Chinese", "DeepSWE × Sol", "Read the historical 113-task results, continuation mechanisms, and patch-delivery checks.", "Read the research brief"],
        ["Cases", "All LoopX showcases", "Browse public cases, interactive walkthroughs, and their evidence boundaries.", "Browse all cases"],
      ],
      developer: "Projection developer tools",
    },
    learn: {
      eyebrow: "Developer book",
      title: "Choose your path into LoopX.",
      body: "A bilingual, protocol-first guide for developers who want to understand the control plane, connect a real project, or contribute along the right ownership boundary.",
      open: "Open the Developer Book",
      cards: [
        ["01", "Understand the control plane", "Build the model from sessions and Goals through durable state, authority, governed turns, and recovery.", "Start with foundations"],
        ["02", "Connect an existing project", "Let your agent establish the Goal, identity, Host, and Git boundary, then verify the result.", "Follow project onboarding"],
        ["03", "Contribute to LoopX", "Trace protocols to source ownership, choose the right contribution surface, validate it, and prepare a focused PR.", "Open the contribution map"],
      ],
    },
    showcase: {
      eyebrow: "Evidence from real loops",
      title: "200+ hours, still legible.",
      body: "One public contribution sequence and one redacted owner-run showcase preserve delivery, decisions, evidence branches, and recovery across many bounded agent turns.",
      publicSafe: "Public-safe evidence",
      pause: "Pause",
      resume: "Resume",
      replay: "Replay",
      elapsed: "200+ hours elapsed lifetime",
      curated: "Curated replay from public evidence",
      view: "View full evidence",
      boundary: "Elapsed lifetime is wall-clock project time. The Auto ML material is a redacted owner-run showcase, not a production result, company or employer endorsement, or independently reproducible evidence.",
      tabs: ["Issue Fix", "Auto ML"],
    },
    quickstart: {
      eyebrow: "Manual fallback",
      title: "Prefer the shell? Install LoopX manually.",
      body: "Use this path when your current agent cannot run shell commands. The same official installer registers lightweight command entries for supported hosts.",
      guide: "Read the Quick Start",
      inspect: "Inspect installer",
      setup: "Setup options",
    },
    footer: "Keep the loop moving. Keep the judgment human.",
  },
  zh: {
    nav: ["产品", "工作方式", "学习"],
    eyebrow: "长程 AI Agent 的有状态控制面",
    title: {
      first: "让 Agent 持续推进。",
      secondPrefix: "让目标成为",
      secondAccent: "结果",
    },
    body: "把复杂工作变成持久的目标、协同的任务和可审查的成果。跨越会话，Agent 接着做；关键判断，始终由你掌握。",
    copy: "开始使用",
    mac: {
      download: "下载 Mac App",
      compact: "Mac App",
      note: "Apple Silicon 预览版（.app.zip）· 需要 Python 3.11+。",
      guide: "安装与首次启动",
    },
    setup: {
      eyebrow: "设置 LoopX",
      title: "选择你的开始方式。",
      body: "让当前 Agent 完成引导式接入，或复制 Shell 命令手动安装。",
      agentTitle: "Agent 接入",
      agentBody: "把完整设置指令交给 Codex、Claude Code、OpenCode、TraeX、Pi 或其他具备 Shell 能力的 Agent。",
      shellTitle: "Shell 接入",
      shellBody: "手动安装 LoopX、检查 CLI，并连接当前项目。",
      copy: "复制",
      copied: "已复制",
      close: "关闭设置",
    },
    demo: "查看实战",
    panel: {
      label: "一个目标 · 跨越多轮会话",
      title: "交付下一个版本",
      status: "进展正常",
      running: "Agent 执行",
      researcher: "研究与实现",
      gate: "Human Gate",
      gateBody: "合并前，等待你的批准",
      waiting: "等待中",
      evidence: "Evidence",
      evidenceBody: "验证与交接已写回",
      complete: "已完成",
      next: "下一步安全动作",
      nextBody: "审批等待中，继续推进不依赖审批的任务",
    },
    proof: [
      ["显式权限", "Agent 快速推进，但不会静默接管用户权力。"],
      ["持久状态", "下一轮从受治理状态恢复，而不是依赖聊天记忆。"],
      ["可审查证据", "每次状态变化都留下可供他人验证的结果。"],
    ],
    sectionEyebrow: "一个受治理的生命周期",
    sectionTitle: "让长程工作始终清晰可读。",
    sectionBody: "Runtime 负责执行；LoopX 保存跨会话、Host 与 Provider 安全推进所需的控制状态。",
    steps: [
      ["01", "长期目标", "一个持久方向"],
      ["02", "权限 Gate", "一个具体判断"],
      ["03", "Agent 线路", "有边界地继续"],
      ["04", "Evidence", "一个验证结果"],
      ["05", "下一轮运行", "可恢复地推进"],
    ],
    capabilities: {
      eyebrow: "跨 Provider、Runtime 与 Host",
      title: "一套受治理的闭环，连接不同 Agent Runtime。",
      body: "Codex、Claude Code、OpenCode、TraeX、Pi 与自定义 Runner 可以共享同一套有状态目标生命周期，同时让每个 Agent 保持明确边界。",
      items: [
        ["01", "持久状态", "目标、用户决策、Todo、Claim、Quota、运行历史与交接不会随会话结束而丢失。"],
        ["02", "跨运行时", "更换执行器或 Host 时，无需从聊天记录重建长期目标。"],
        ["03", "Gate-aware", "P0 等待决策时保持可见，独立的 P1、P2 线路可以安全继续。"],
        ["04", "结果驱动", "每轮运行都把验证、归属、审查和交接证据写入下一轮闭环。"],
      ],
    },
    explore: {
      eyebrow: "探索 LoopX",
      title: "体验工作区，查看研究与案例。",
      body: "从产品演示到评测结果，找到你想深入了解的内容。",
      cards: [
        ["产品演示", "Personal Workspace", "在一个工作区查看目标、任务、对话与产出。观看演示，开始使用本地工作区。", "观看演示与使用指南"],
        ["研究与评测", "SWE-Marathon", "查看三种保留模式在 15 个匹配任务上的结果、成本与研究局限。", "阅读研究简报"],
        ["研究与评测", "LHTB × LoopX", "对比五种执行机制在 46 个长程终端任务上的结果，观察可恢复状态在什么场景有效。", "阅读研究简报"],
        ["研究与评测", "DeepSWE 行为分析", "从具体案例观察领域提示如何影响实现选择与验证行为。", "阅读行为分析"],
        ["研究与评测", "DeepSWE × Sol", "解读 113 任务的历史结果、持续执行机制，以及补丁如何进入最终验收。", "阅读研究简报"],
        ["案例", "完整案例目录", "浏览公开案例、交互式讲解及其证据边界。", "浏览全部案例"],
      ],
      developer: "开发者投影工具",
    },
    learn: {
      eyebrow: "开发者手册",
      title: "选择你的 LoopX 学习路径。",
      body: "为想要理解控制面、接入真实项目或在正确 ownership 边界上贡献的开发者准备的系统性双语协议指南。",
      open: "打开开发者手册",
      cards: [
        ["01", "理解控制面", "从会话与目标出发，逐步建立对持久状态、权限、治理回合与恢复的完整认知。", "从基础开始"],
        ["02", "接入已有项目", "让 Agent 建立 Goal、身份、Host 与 Git 边界，然后验证结果。", "跟随项目接入指南"],
        ["03", "贡献 LoopX", "从协议追踪到源码归属，选择正确的贡献面，验证后提交聚焦的 PR。", "打开贡献地图"],
      ],
    },
    showcase: {
      eyebrow: "来自真实闭环的证据",
      title: "跨越 200+ 小时，依然清晰可读。",
      body: "一条公开贡献序列与一条经过脱敏的 owner-run showcase，保留了多轮 Agent 推进中的交付、决策、证据分支与恢复过程。",
      publicSafe: "公开安全证据",
      pause: "暂停",
      resume: "继续",
      replay: "重播",
      elapsed: "200+ 小时自然时长",
      curated: "基于公开证据的精选回放",
      view: "查看完整证据",
      boundary: "这里的时长是项目自然时长。Auto ML 材料是经过脱敏的 owner-run showcase，不代表生产结果、公司或雇主背书，也不是可独立复现的证据。",
      tabs: ["问题修复", "自动化 ML"],
    },
    quickstart: {
      eyebrow: "手动备用方案",
      title: "更习惯终端？可以手动安装 LoopX。",
      body: "当你使用的 Agent 无法执行 Shell 命令时使用这条路径。同一官方安装器会为受支持的 Host 注册轻量命令入口。",
      guide: "阅读快速开始",
      inspect: "查看安装脚本",
      setup: "设置方式",
    },
    footer: "让闭环持续运转，让判断始终属于人。",
  },
} as const;

function getBasePath() {
  const base = import.meta.env.BASE_URL;
  return base.endsWith("/") ? base : `${base}/`;
}

function ProductMark() {
  return (
    <span className="product-mark" aria-hidden="true">
      <span />
      <span />
    </span>
  );
}

function StatusPill({
  tone,
  children,
}: {
  tone: "ok" | "waiting" | "neutral";
  children: ReactNode;
}) {
  return (
    <span className={`status-pill status-${tone}`}>
      <i />
      {children}
    </span>
  );
}

function ControlPlane({ language }: { language: Language }) {
  const panel = content[language].panel;
  return (
    <div className="control-shell">
      <div className="control-topline">
        <span>{panel.label}</span>
        <span className="live-indicator"><i aria-hidden="true" /> LIVE</span>
      </div>
      <div className="control-goal">
        <div className="control-icon">
          <Target size={18} strokeWidth={1.8} />
        </div>
        <div>
          <small>{language === "en" ? "ACTIVE GOAL" : "当前目标"}</small>
          <strong>{panel.title}</strong>
        </div>
        <StatusPill tone="ok">{panel.status}</StatusPill>
      </div>

      <div className="control-timeline">
        <div className="timeline-rail" aria-hidden="true">
          <span />
        </div>
        <div className="control-row is-running">
          <div className="row-icon">
            <Code2 size={17} />
          </div>
          <div className="row-copy">
            <small>P0 · {panel.running}</small>
            <strong>{panel.researcher}</strong>
          </div>
          <div className="progress-track">
            <span />
          </div>
          <StatusPill tone="neutral">{language === "en" ? "Running" : "执行中"}</StatusPill>
        </div>
        <div className="control-row is-gate">
          <div className="row-icon">
            <UserRoundCheck size={17} />
          </div>
          <div className="row-copy">
            <small>GATE · {panel.gate}</small>
            <strong>{panel.gateBody}</strong>
          </div>
          <StatusPill tone="waiting">{panel.waiting}</StatusPill>
        </div>
        <div className="control-row is-evidence">
          <div className="row-icon">
            <FileCheck2 size={17} />
          </div>
          <div className="row-copy">
            <small>{panel.evidence}</small>
            <strong>{panel.evidenceBody}</strong>
          </div>
          <StatusPill tone="ok">{panel.complete}</StatusPill>
        </div>
      </div>

      <div className="next-action">
        <span className="next-action-icon">
          <Sparkles size={15} />
        </span>
        <div>
          <small>{panel.next}</small>
          <strong>{panel.nextBody}</strong>
        </div>
        <ArrowRight size={17} />
      </div>
    </div>
  );
}

function Reveal({ children, className = "" }: { children: ReactNode; className?: string }) {
  const elementRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const element = elementRef.current;
    if (!element || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(true);
      return;
    }
    setVisible(false);
    const safetyTimeout = window.setTimeout(() => setVisible(true), 1200);
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        setVisible(true);
        window.clearTimeout(safetyTimeout);
        observer.disconnect();
      },
      { threshold: 0.12 },
    );
    observer.observe(element);
    return () => {
      window.clearTimeout(safetyTimeout);
      observer.disconnect();
    };
  }, []);

  return (
    <div ref={elementRef} className={`reveal-block ${visible ? "is-visible" : ""} ${className}`}>
      {children}
    </div>
  );
}

const terminalCases = {
  issue: {
    session: {
      en: "Public trajectory 01 · Open-source issue fix",
      zh: "公开轨迹 01 · 开源问题修复",
    },
    command: "$ loopx status --goal-id issue-fix",
    image: issueEvidenceUrl,
    imageTitle: {
      en: "PR delivery and reusable fix knowledge evolve together.",
      zh: "PR 交付与可复用修复知识共同演进。",
    },
    lines: [
      ["GOAL", "Ship the focused fix and preserve reusable knowledge.", "交付聚焦修复，并保留可复用知识。", "ACTIVE", "active"],
      ["P0", "Focused PR delivery · bounded diff and validation", "聚焦式 PR 交付 · 有边界的改动与验证", "RUNNING", "running"],
      ["GATE", "Reviewer authority · approve the focused change", "审查权限 · 批准聚焦改动", "WAITING", "waiting"],
      ["P1", "Repository knowledge and terminal resume remain available", "仓库知识与终端恢复保持可用", "READY", "ready"],
      ["EVIDENCE", "Merge/open result linked to the next reusable discovery", "合并 / 开放结果连接到下一轮可复用发现", "WRITTEN", "written"],
      ["NEXT", "Review the focused diff, then continue the capability lane.", "先审查聚焦改动，再继续能力沉淀线路。", "SAFE", "safe"],
    ],
  },
  ml: {
    session: {
      en: "Redacted owner-run showcase 02 · Auto ML experiment",
      zh: "脱敏 owner-run showcase 02 · 自动化 ML 实验",
    },
    command: "$ loopx status --goal-id auto-ml",
    image: mlEvidenceUrl,
    imageTitle: {
      en: "Evidence branches remain visible until promotion or stop.",
      zh: "证据分支保持可见，直到晋级或停止。",
    },
    lines: [
      ["BASELINE", "Matched candidate and evaluation contract locked.", "匹配候选项与评估契约已锁定。", "MATCHED", "active"],
      ["BRANCHES", "Positive · Negative · Invalid lineage · Running replicate", "正向 · 负向 · 无效谱系 · 运行中复现", "VISIBLE", "ready"],
      ["P1", "Replicate capacity stays bounded while evidence accumulates", "证据持续积累，同时保持复现容量有界", "RUNNING", "running"],
      ["EVIDENCE", "Negative results and invalid lineages remain traceable", "负向结果和无效谱系持续可追溯", "WRITTEN", "written"],
      ["P0 GATE", "Promote or stop after the running replicate completes", "运行中复现完成后，决定晋级或停止", "WAITING", "waiting"],
      ["NEXT", "Compare replicate evidence, then record the decision.", "比较复现证据，再记录最终决策。", "SAFE", "safe"],
    ],
  },
} as const;

type TerminalKey = keyof typeof terminalCases;
type PlaybackState = "running" | "paused" | "complete";

function TerminalReplay({
  language,
  activeKey,
  onOpenEvidence,
  replayToken,
}: {
  language: Language;
  activeKey: TerminalKey;
  onOpenEvidence: (source: string, title: string) => void;
  replayToken: number;
}) {
  const [playback, setPlayback] = useState<PlaybackState>("running");
  const [cycle, setCycle] = useState(0);
  const data = terminalCases[activeKey];
  const labels = content[language].showcase;

  useEffect(() => {
    setPlayback("running");
    setCycle((value) => value + 1);
  }, [activeKey, replayToken]);

  function controlPlayback() {
    if (playback === "complete") {
      setPlayback("running");
      setCycle((value) => value + 1);
      return;
    }
    setPlayback(playback === "paused" ? "running" : "paused");
  }

  const controlLabel =
    playback === "complete" ? labels.replay : playback === "paused" ? labels.resume : labels.pause;

  return (
    <section className={`terminal-panel terminal-${playback}`} key={`${activeKey}-${cycle}`}>
      <header className="terminal-session-header">
        <span className="terminal-agent-mark" aria-hidden="true">
          <ProductMark />
        </span>
        <div>
          <strong>LoopX Agent</strong>
          <small>{data.session[language]}</small>
        </div>
        <button type="button" className="terminal-playback" onClick={controlPlayback}>
          {playback === "complete" ? <RotateCcw size={14} /> : playback === "paused" ? <Play size={14} /> : <Pause size={14} />}
          {controlLabel}
        </button>
      </header>
      <div className="terminal-demo-body">
        <div className="terminal-command">
          <span aria-hidden="true">›</span>
          <code>{data.command}</code>
        </div>
        {data.lines.map(([label, english, chinese, state, tone], index) => (
          <div
            className={`terminal-demo-line terminal-tone-${tone}`}
            style={{ "--terminal-delay": `${700 + index * 980}ms` } as CSSProperties}
            onAnimationEnd={
              index === data.lines.length - 1
                ? (event) => {
                    if (event.animationName === "terminal-line-enter") setPlayback("complete");
                  }
                : undefined
            }
            key={`${activeKey}-${label}`}
          >
            <i aria-hidden="true" />
            <span>{label}</span>
            <p>{language === "zh" ? chinese : english}</p>
            <b>{state}</b>
          </div>
        ))}
      </div>
      <footer className="terminal-demo-footer">
        <span>
          <b>{labels.elapsed}</b>
          <small>{activeKey === "issue" ? labels.curated : language === "zh" ? "经过脱敏的 owner-run showcase" : "Redacted owner-run showcase"}</small>
        </span>
        <button
          type="button"
          className="evidence-open"
          onClick={() =>
            onOpenEvidence(
              data.image,
              data.imageTitle[language],
            )
          }
        >
          {labels.view}
          <ExternalLink size={14} />
        </button>
      </footer>
    </section>
  );
}

function EvidenceViewer({
  evidence,
  onClose,
  language,
}: {
  evidence: { source: string; title: string } | null;
  onClose: () => void;
  language: Language;
}) {
  const [zoom, setZoom] = useState(100);

  useEffect(() => {
    if (!evidence) return;
    setZoom(100);
    document.body.classList.add("dialog-open");
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.classList.remove("dialog-open");
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [evidence, onClose]);

  if (!evidence) return null;

  return (
    <div className="evidence-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="evidence-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="evidence-dialog-toolbar">
          <div>
            <p>{language === "zh" ? "原始公开安全轨迹" : "Original public-safe trajectory"}</p>
            <h2 id="evidence-dialog-title">{evidence.title}</h2>
          </div>
          <div className="evidence-dialog-actions">
            <button type="button" aria-label="Zoom out" onClick={() => setZoom(Math.max(50, zoom - 25))}>
              <ZoomOut size={16} />
            </button>
            <output>{zoom}%</output>
            <button type="button" aria-label="Zoom in" onClick={() => setZoom(Math.min(200, zoom + 25))}>
              <ZoomIn size={16} />
            </button>
            <button type="button" onClick={() => setZoom(100)}>
              {language === "zh" ? "重置" : "Reset"}
            </button>
            <button type="button" aria-label="Close evidence viewer" onClick={onClose}>
              <X size={17} />
            </button>
          </div>
        </header>
        <div className="evidence-viewport">
          <div className="evidence-canvas">
            <img
              src={evidence.source}
              alt={evidence.title}
              draggable={false}
              style={{ width: `${zoom}%` }}
            />
          </div>
        </div>
        <footer className="evidence-dialog-footer">
          <p>{content[language].showcase.boundary}</p>
          <a href={evidence.source} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">
            {language === "zh" ? "在新标签页打开原图" : "Open original image"} <ExternalLink size={13} />
          </a>
        </footer>
      </section>
    </div>
  );
}

function SetupDialog({
  language,
  open,
  copiedOption,
  onCopy,
  onClose,
}: {
  language: Language;
  open: boolean;
  copiedOption: "agent" | "shell" | null;
  onCopy: (option: "agent" | "shell") => void;
  onClose: () => void;
}) {
  const setup = content[language].setup;

  useEffect(() => {
    if (!open) return;
    document.body.classList.add("dialog-open");
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.classList.remove("dialog-open");
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="setup-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="setup-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <p>{setup.eyebrow}</p>
            <h2 id="setup-dialog-title">{setup.title}</h2>
            <span>{setup.body}</span>
          </div>
          <button type="button" aria-label={setup.close} onClick={onClose}>
            <X size={17} />
          </button>
        </header>
        <div className="setup-options">
          <article>
            <span className="setup-option-icon">
              <Sparkles size={18} />
            </span>
            <div>
              <small>RECOMMENDED</small>
              <h3>{setup.agentTitle}</h3>
              <p>{setup.agentBody}</p>
            </div>
            <button type="button" onClick={() => onCopy("agent")}>
              {copiedOption === "agent" ? <Check size={15} /> : <Clipboard size={15} />}
              {copiedOption === "agent" ? setup.copied : setup.copy}
            </button>
          </article>
          <article>
            <span className="setup-option-icon">
              <TerminalSquare size={18} />
            </span>
            <div>
              <small>MANUAL</small>
              <h3>{setup.shellTitle}</h3>
              <p>{setup.shellBody}</p>
            </div>
            <button type="button" onClick={() => onCopy("shell")}>
              {copiedOption === "shell" ? <Check size={15} /> : <Clipboard size={15} />}
              {copiedOption === "shell" ? setup.copied : setup.copy}
            </button>
          </article>
        </div>
      </section>
    </div>
  );
}

export function App() {
  const [language, setLanguage] = usePublicPageNavigation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [copiedOption, setCopiedOption] = useState<"agent" | "shell" | null>(null);
  const [activeTerminal, setActiveTerminal] = useState<TerminalKey>("issue");
  const [terminalReplayToken, setTerminalReplayToken] = useState(0);
  const [evidence, setEvidence] = useState<{ source: string; title: string } | null>(null);
  const copy = content[language];
  const basePath = useMemo(getBasePath, []);

  useEffect(() => {
    document.body.dataset.language = language;
  }, [language]);

  async function copySetup(option: "agent" | "shell") {
    const text = option === "agent" ? setupPrompts[language] : shellSetupCommand;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = text;
      fallback.setAttribute("readonly", "");
      fallback.style.position = "fixed";
      fallback.style.left = "-9999px";
      document.body.append(fallback);
      fallback.select();
      const copied = document.execCommand("copy");
      fallback.remove();
      if (!copied) throw new Error("clipboard unavailable");
    }
    window.dispatchEvent(new CustomEvent("loopx:setup-copy", { detail: option }));
    setCopiedOption(option);
    window.setTimeout(() => setCopiedOption(null), 2200);
  }

  function showTerminalReplay() {
    setActiveTerminal("issue");
    setTerminalReplayToken((value) => value + 1);
  }

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header" id="top">
        <a className="brand" href={`${basePath}${language === "zh" ? "?lang=zh" : ""}`} aria-label="LoopX home">
          <ProductMark />
          <span>LoopX</span>
        </a>
        <nav className="desktop-nav" aria-label="Primary navigation">
          <a href="#product">{copy.nav[0]}</a>
          <a href="#workflow">{copy.nav[1]}</a>
          <a href={`${basePath}docs/book/${language === "en" ? "en/" : ""}`}>{copy.nav[2]}</a>
          <a href="#explore">{language === "zh" ? "探索" : "Explore"}</a>
          <a href={`${basePath}blog/${language === "zh" ? "zh/" : ""}`}>Blog</a>
          <a href={`${basePath}docs/`}>Docs</a>
          <a href="https://github.com/loopx-project/loopx">GitHub</a>
        </nav>
        <div className="header-actions">
          <button
            className="language-toggle"
            type="button"
            onClick={() => setLanguage(language === "en" ? "zh" : "en")}
          >
            {language === "en" ? "中文" : "EN"}
          </button>
          <a className="header-download" data-analytics-event="desktop_download" href={macAppDownloadUrl} aria-label={copy.mac.download} referrerPolicy="strict-origin-when-cross-origin">
            <Download size={16} aria-hidden="true" />
            <span className="header-download-full">{copy.mac.download}</span>
            <span className="header-download-compact">{copy.mac.compact}</span>
          </a>
          <button
            className="menu-button"
            type="button"
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
        {menuOpen ? (
          <nav className="mobile-nav" aria-label="Mobile navigation">
            <a href="#product" onClick={() => setMenuOpen(false)}>
              {copy.nav[0]}
            </a>
            <a href="#workflow" onClick={() => setMenuOpen(false)}>
              {copy.nav[1]}
            </a>
            <a href="#explore" onClick={() => setMenuOpen(false)}>
              {language === "zh" ? "探索" : "Explore"}
            </a>
            <a href={`${basePath}blog/${language === "zh" ? "zh/" : ""}`}>Blog</a>
            <a href={`${basePath}docs/`}>Docs</a>
            <a href="https://github.com/loopx-project/loopx">GitHub</a>
          </nav>
        ) : null}
      </header>

      <main id="main">
        <section className="hero">
          <div className="hero-mesh" aria-hidden="true" />
          <div className="hero-grid" aria-hidden="true" />
          <div className="hero-copy">
            <p className="eyebrow">
              <span />
              {copy.eyebrow}
            </p>
            <h1>
              {copy.title.first ? (
                <span className="hero-title-line">
                  {copy.title.first}
                </span>
              ) : null}
              <span className="hero-title-line">
                {copy.title.secondPrefix}
                <span className="hero-accent">{copy.title.secondAccent}</span>
                {language === "zh" ? "。" : "."}
              </span>
            </h1>
            <p className="hero-body">{copy.body}</p>
            <div className="hero-actions">
              <a className="button button-primary hero-download" data-analytics-event="desktop_download" href={macAppDownloadUrl} referrerPolicy="strict-origin-when-cross-origin">
                <Download size={16} aria-hidden="true" />
                {copy.mac.download}
              </a>
              <button className="button button-secondary" data-analytics-event="setup_open" type="button" onClick={() => setSetupOpen(true)}>
                <span>{copy.copy}</span>
                <ArrowRight className="button-trailing-icon" size={15} />
              </button>
              <a className="hero-demo" data-analytics-event="showcase_open" href="#showcases" onClick={showTerminalReplay}>
                <Play size={15} fill="currentColor" />
                {copy.demo}
              </a>
            </div>
            <p className="hero-footnote">
              {copy.mac.note}{" "}
              <a href={macAppGuideUrl} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{copy.mac.guide} <ExternalLink size={12} aria-hidden="true" /></a>
            </p>
          </div>
          <div className="hero-system">
            <div className="runtime-rail" aria-label={language === "en" ? "Explore agent integrations" : "查看 Agent 集成"}>
              <a href={`${basePath}docs/guides/long-running-coding-agents/#codex-continue-a-task-across-sessions`}>Codex</a>
              <a href={`${basePath}docs/guides/long-running-coding-agents/#claude-code-track-work-without-losing-review-state`}>Claude Code</a>
              <a href="https://github.com/loopx-project/loopx/tree/main/packages/dsh-loopx-plugin">DeepSeek Harness</a>
              <a href={`${basePath}docs/integrations/runtime-connector-catalog/`}>+ {language === "en" ? "more" : "更多"}</a>
            </div>
            <div className="runtime-bridge" aria-hidden="true"><span /><span /><span /></div>
            <ControlPlane language={language} />
            <p className="hero-system-caption">{language === "en" ? "A workflow illustration. Your runtime executes; LoopX keeps the state." : "工作流示意：Runtime 负责执行，LoopX 保存状态。"}</p>
          </div>
        </section>

        <section className="proof-strip" aria-label="LoopX product principles">
          {copy.proof.map(([title, body], index) => {
            const Icon = [ShieldCheck, GitBranch, FileCheck2][index];
            return (
              <article key={title}>
                <span className="proof-icon">
                  <Icon size={18} />
                </span>
                <div>
                  <strong>{title}</strong>
                  <p>{body}</p>
                </div>
              </article>
            );
          })}
        </section>

        <Reveal className="workflow-section-wrap">
        <section className="workflow-section" id="product">
          <div className="section-heading">
            <p className="eyebrow">
              <span />
              {copy.sectionEyebrow}
            </p>
            <h2>{copy.sectionTitle}</h2>
            <p>{copy.sectionBody}</p>
          </div>
          <div className="workflow-grid" id="workflow">
            {copy.steps.map(([number, title, body]) => (
              <article key={number}>
                <span>{number}</span>
                <div className="step-icon">
                  <ChevronRight size={16} />
                </div>
                <h3>{title}</h3>
                <p>{body}</p>
              </article>
            ))}
          </div>
        </section>
        </Reveal>

        <section className="showcase-section" id="showcases" aria-labelledby="showcase-title">
          <Reveal>
            <div className="section-heading">
              <p className="eyebrow">
                <span />
                {copy.showcase.eyebrow}
              </p>
              <h2 id="showcase-title">{copy.showcase.title}</h2>
              <p>{copy.showcase.body}</p>
            </div>
          </Reveal>
          <div className="terminal-shell">
            <div className="terminal-window-bar">
              <div aria-hidden="true">
                <i />
                <i />
                <i />
              </div>
              <p>~/loopx/evidence — curated replay</p>
              <span>{copy.showcase.publicSafe}</span>
            </div>
            <div className="terminal-tabs" role="tablist">
              {(["issue", "ml"] as const).map((key, index) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeTerminal === key}
                  onClick={() => setActiveTerminal(key)}
                  key={key}
                >
                  {copy.showcase.tabs[index]}
                </button>
              ))}
            </div>
            <TerminalReplay
              language={language}
              activeKey={activeTerminal}
              replayToken={terminalReplayToken}
              onOpenEvidence={(source, title) => setEvidence({ source, title })}
            />
          </div>
          <p className="evidence-boundary">{copy.showcase.boundary}</p>
          <a className="resource-inline-link" href={`${basePath}docs/showcases/index${language === "en" ? ".en" : ""}.html`}>
            {language === "zh" ? "浏览全部案例" : "Browse all cases"} <ArrowRight size={15} />
          </a>
        </section>

        <Reveal>
          <section className="learn-section explore-section" id="explore" aria-labelledby="explore-title">
            <div className="section-heading">
              <p className="eyebrow"><span />{copy.explore.eyebrow}</p>
              <h2 id="explore-title">{copy.explore.title}</h2>
              <p>{copy.explore.body}</p>
            </div>
            <div className="resource-grid">
              {copy.explore.cards.map(([category, title, body, action], index) => {
                const paths = [
                  "docs/guides/personal-workspace-user-guide/",
                  `benchmarks/swe-marathon/${language === "zh" ? "?lang=zh" : ""}`,
                  `benchmarks/lhtb/${language === "zh" ? "?lang=zh" : ""}`,
                  "benchmarks/deepswe/behavior-discovery/",
                  "benchmarks/deepswe-sol/",
                  `docs/showcases/index${language === "en" ? ".en" : ""}.html`,
                ];
                return (
                  <a className="learn-card resource-card" key={title} href={`${basePath}${paths[index]}`}>
                    <span>{category}</span>
                    <h3>{title}</h3>
                    <p>{body}</p>
                    <b>{action} <ArrowRight size={15} /></b>
                  </a>
                );
              })}
            </div>
            <a className="resource-inline-link" href={`${basePath}developers/projections/`}>
              {copy.explore.developer} <ArrowRight size={15} />
            </a>
          </section>
        </Reveal>

        <Reveal>
          <section className="capability-section" aria-labelledby="capability-title">
            <div className="section-heading">
              <p className="eyebrow">
                <span />
                {copy.capabilities.eyebrow}
              </p>
              <h2 id="capability-title">{copy.capabilities.title}</h2>
              <p>{copy.capabilities.body}</p>
            </div>
            <div className="capability-grid">
              {copy.capabilities.items.map(([number, title, body]) => (
                <article key={number}>
                  <span>{number}</span>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </article>
              ))}
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="learn-section" id="learn" aria-labelledby="learn-title">
            <div className="section-heading">
              <p className="eyebrow">
                <span />
                {copy.learn.eyebrow}
              </p>
              <h2 id="learn-title">{copy.learn.title}</h2>
              <div className="section-copy-link">
                <p>{copy.learn.body}</p>
                <a href={`${basePath}docs/book/${language === "en" ? "en/" : ""}`}>
                  {copy.learn.open} <ArrowRight size={15} />
                </a>
              </div>
            </div>
            <div className="learn-grid">
              {copy.learn.cards.map(([number, title, body, action], index) => {
                const paths = [
                  "chapters/01-from-session-to-loop/",
                  "chapters/05-connect-existing-project/",
                  "chapters/source-protocol-map/",
                ];
                return (
                  <a
                    className="learn-card"
                    key={number}
                    href={`${basePath}docs/book/${language === "en" ? "en/" : ""}${paths[index]}`}
                  >
                    <span>{number}</span>
                    <BookOpen size={20} />
                    <h3>{title}</h3>
                    <p>{body}</p>
                    <b>
                      {action} <ExternalLink size={13} />
                    </b>
                  </a>
                );
              })}
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="quickstart-section" id="quickstart" aria-labelledby="quickstart-title">
            <div>
              <p className="eyebrow">
                <span />
                {copy.quickstart.eyebrow}
              </p>
              <h2 id="quickstart-title">{copy.quickstart.title}</h2>
              <p>{copy.quickstart.body}</p>
            </div>
            <div className="quickstart-terminal">
              <div className="quickstart-terminal-bar">
                <div aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </div>
                <div className="quickstart-terminal-actions">
                  <span>
                    <TerminalSquare size={13} /> terminal
                  </span>
                  <button type="button" onClick={() => setSetupOpen(true)}>
                    <Sparkles size={13} />
                    {copy.quickstart.setup}
                  </button>
                </div>
              </div>
              <pre>
                <code>
                  <b>$</b> curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash{"\n"}
                  <b>$</b> export PATH="$HOME/.local/bin:$PATH"{"\n"}
                  <b>$</b> loopx doctor{"\n"}
                  <b>$</b> cd /path/to/your-project{"\n"}
                  <b>$</b> loopx connect{"\n"}
                  <b>$</b> loopx status{"\n\n"}
                  <em>✓ CLI health checked</em>{"\n"}
                  <em>✓ project state readable</em>{"\n"}
                  <em>→ finish host activation from the onboarding packet</em>
                </code>
              </pre>
              <div className="quickstart-links">
                <a href="https://github.com/loopx-project/loopx#quick-start">
                  {copy.quickstart.guide} <ArrowRight size={14} />
                </a>
                <a href="https://github.com/loopx-project/loopx/blob/main/scripts/install-from-github.sh">
                  {copy.quickstart.inspect} <ExternalLink size={13} />
                </a>
              </div>
            </div>
          </section>
        </Reveal>
      </main>

      <footer className="site-footer">
        <a className="brand" href="#top">
          <ProductMark />
          <span>LoopX</span>
        </a>
        <p>{copy.footer}</p>
        <nav>
          <a href="https://github.com/loopx-project/loopx">GitHub</a>
          <a href="#explore">{language === "zh" ? "研究与案例" : "Research & cases"}</a>
          <a href={`${basePath}blog/${language === "zh" ? "zh/" : ""}`}>Blog</a>
          <a href={`${basePath}docs/guides/personal-workspace-user-guide/`}>Personal Workspace</a>
          <a href={`${basePath}docs/`}>Docs</a>
        </nav>
      </footer>

      <EvidenceViewer evidence={evidence} onClose={() => setEvidence(null)} language={language} />
      <SetupDialog
        language={language}
        open={setupOpen}
        copiedOption={copiedOption}
        onCopy={copySetup}
        onClose={() => setSetupOpen(false)}
      />
    </>
  );
}
