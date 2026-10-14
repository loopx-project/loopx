#!/usr/bin/env python3
"""Generate public-safe showcase HTML pages without rewriting the hardware artifact."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_DIR = REPO_ROOT / "docs" / "showcases"
CASES_DIR = SHOWCASE_DIR / "cases"
CATALOG = SHOWCASE_DIR / "showcase-catalog.json"
SHARED_STYLESHEET = SHOWCASE_DIR / "showcase-page.css"
HARDWARE_CASE_ID = "2026-06-19-dynamic-workflow-hardware-agent"
HARDWARE_CANONICAL_PAGE = CASES_DIR / "0619-dynamic-workflow-hardware-agent.html"

CASE_TYPE_LABELS = {
    "independent_user": {"zh": "外部独立用户", "en": "Independent user"},
    "contributor_case": {"zh": "Contributor case", "en": "Contributor case"},
    "creator_dogfooding": {"zh": "Creator dogfooding", "en": "Creator dogfooding"},
    "reproducible_demo": {"zh": "可复现 Demo", "en": "Reproducible demo"},
}

EVIDENCE_STRENGTH_LABELS = {
    "user_report_with_public_method_reference": {"zh": "用户报告 + 公开方法", "en": "User report + public method"},
    "user_report_only": {"zh": "用户报告", "en": "User report"},
    "public_repository_plus_user_report": {"zh": "公开仓库 + 用户报告", "en": "Public repo + user report"},
    "public_git_evidence": {"zh": "公开 Git 证据", "en": "Public Git evidence"},
    "public_safe_pattern": {"zh": "Public-safe pattern", "en": "Public-safe pattern"},
    "public_interactive_case": {"zh": "公开交互案例", "en": "Public interactive case"},
    "reproducible_synthetic_demo": {"zh": "可复现合成证据", "en": "Reproducible synthetic evidence"},
    "public_safe_case_spec": {"zh": "Public-safe case spec", "en": "Public-safe case spec"},
}

PRIMARY_CASE_ORDER = [
    "2026-07-cpp-accuracy-long-run",
    "2026-07-four-day-unattended-agent",
    "2026-07-public-engine-refactor",
    "2026-10-vllm-long-context-optimization",
    "2026-06-27-overnight-pr-batch",
    "2026-06-24-pr-issue-auto-fix",
    "2026-06-23-agent-to-agent-pr-comments",
    "2026-06-23-overnight-project-refactor",
    HARDWARE_CASE_ID,
    "2026-06-19-loopx-self-iteration",
    "2026-06-17-blocked-p0-safe-rotation",
]

SHOWCASE_TABLE = {
    "2026-07-cpp-accuracy-long-run": {
        "proof_point": "A >13h engineering run can remain aligned, change method through public research, and retain an understandable evidence trail.",
        "loopx_intervention": "vision alignment, replan, public research, evidence retention",
    },
    "2026-07-four-day-unattended-agent": {
        "proof_point": "A user observed four days of unattended work and judged the work useful, with a later reporting surface available for inspection.",
        "loopx_intervention": "durable task state, unattended continuation, periodic reporting",
    },
    "2026-07-public-engine-refactor": {
        "proof_point": "One public refactor goal landed as seven merged PRs instead of a monolithic change.",
        "loopx_intervention": "durable goal, staged todo sequence, PR-sized delivery, evidence-backed continuation",
    },
    "2026-06-27-overnight-pr-batch": {
        "proof_point": "High-throughput multi-lane work can remain PR-sized, reviewable, and merge-safe.",
        "loopx_intervention": "todo claim, review packet, self-merge boundary, focused smoke, public-boundary scan",
    },
    "2026-06-24-pr-issue-auto-fix": {
        "proof_point": "Issue and review feedback can enter an executable repair loop.",
        "loopx_intervention": "issue-fix workflow, command pack, repro smoke, PR review feedback",
    },
    "2026-06-23-agent-to-agent-pr-comments": {
        "proof_point": "Multiple agents can coordinate around PR review comments without losing owner review.",
        "loopx_intervention": "claimed_by, handoff gate, review packet, comment/fix loop",
    },
    "2026-06-23-overnight-project-refactor": {
        "proof_point": "Long unattended refactors can split into moderate PR slices instead of one unreviewable diff.",
        "loopx_intervention": "loop, todo follow-up, supersede, PR-sized slices",
    },
    HARDWARE_CASE_ID: {
        "proof_point": "Fuzzy goals, multiple workers, and long unattended runs can still converge.",
        "loopx_intervention": "goal state, worker handoff, dynamic workflow",
    },
    "2026-06-19-loopx-self-iteration": {
        "proof_point": "A high-churn multi-lane project can keep state, boundaries, and evidence coherent.",
        "loopx_intervention": "todo, quota, gate, evidence, review packet, frontstage",
    },
    "2026-06-17-blocked-p0-safe-rotation": {
        "proof_point": "A user decision should not block all safe work.",
        "loopx_intervention": "concrete user todo, safe fallback, quota control",
    },
}

ZH_COPY = {
    "2026-06-27-overnight-pr-batch": {
        "title": "一晚 30 个高价值 PR",
        "headline": "高吞吐、多车道推进仍保持 PR 级可 review、可合并。",
        "proof_point": "高吞吐多 lane 工作也可以保持 PR 粒度、可审阅、可合并。",
        "loopx_intervention": "todo claim、review packet、自合并边界、focused smoke、public-boundary scan",
        "beats": ["长时间窗口被拆成 PR-sized slice。", "每条 lane 写回验证和 review 边界。", "公开 Git 证据替代原始运行日志。"],
    },
    "2026-06-24-pr-issue-auto-fix": {
        "title": "PR Issue 自动 Fix",
        "headline": "Issue / Review comment 可以进入可执行修复闭环。",
        "proof_point": "Issue 和 review 反馈可以变成受控、可执行的修复循环。",
        "loopx_intervention": "issue-fix workflow、command pack、repro smoke、PR review feedback",
        "beats": ["反馈先变成可 claim 的修复目标。", "repro、fix、validation 被拆开记录。", "剩余风险回到 reviewer，而不是被 agent 默认吞掉。"],
    },
    "2026-06-23-agent-to-agent-pr-comments": {
        "title": "Agent to agent 回复 PR comment 和 PR Fix",
        "headline": "多 agent 可围绕 PR review 协同发现、评论、修复。",
        "proof_point": "多 agent lane 可以围绕 PR comment 协作，同时保留 owner review。",
        "loopx_intervention": "claimed_by、handoff gate、review packet、comment/fix loop",
        "beats": ["一个 agent 留下公开安全的 PR 反馈。", "另一条 lane claim 后续修复。", "owner 仍能看到 handoff、证据和剩余 gate。"],
    },
    "2026-06-23-overnight-project-refactor": {
        "title": "一晚上自主重构项目",
        "headline": "长时间无值守重构能拆成适中 PR，而不是一个不可 review 大改。",
        "proof_point": "无人值守 refactor 可以拆成 PR-sized slice。",
        "loopx_intervention": "loop、todo follow-up、supersede、PR-sized slices",
        "beats": ["开放 cleanup 被拆成有序 slice。", "发现项写成 follow-up todo。", "每个 slice 都留下验证和剩余风险。"],
    },
    HARDWARE_CASE_ID: {
        "title": "外部芯片 agent workflow",
        "headline": "模糊目标、多 worker、长时间无值守下仍可收敛。",
        "proof_point": "模糊目标、多 worker、长时间无值守下仍可收敛。",
        "loopx_intervention": "goal state、worker handoff、dynamic workflow",
        "beats": ["LoopX 持有 goal state、quota、todo、claim、evidence 和 history。", "Claude Code 编写任务级编排脚本。", "worker agent 执行有边界的硬件工程工作。"],
    },
    "2026-06-19-loopx-self-iteration": {
        "title": "LoopX Meta Agent 自迭代",
        "headline": "高变更、多车道项目可保持状态、边界和证据。",
        "proof_point": "高 churn 多 lane agent repo 可以保持状态、证据和边界一致。",
        "loopx_intervention": "todo、quota、gate、evidence、review packet、frontstage",
        "beats": ["benchmark、产品、文档和 peer lanes 并行推进。", "状态、gate、quota 和 evidence 收敛到同一控制面。", "公开 Git 窗口形成保守效率证据。"],
    },
    "2026-06-17-blocked-p0-safe-rotation": {
        "title": "P0 block 后推进 P1/P2",
        "headline": "用户决策不应阻塞全部安全工作。",
        "proof_point": "被阻塞的 P0 决策不应该阻止安全的 P1/P2 工作继续。",
        "loopx_intervention": "concrete user todo、safe fallback、quota control",
        "beats": ["P0 决策投影成具体 user todo。", "safe fallback lane 继续推进。", "quota 和 evidence 限制自动推进节奏。"],
    },
    "2026-06-20-creator-operator-case-spec": {
        "title": "创作者-运营者长跑 Agent 案例",
        "headline": "研究可以继续，发布决策仍然 gated。",
        "proof_point": "创作与运营工作可以共享一个 gate-aware 的长期 agent loop。",
        "loopx_intervention": "creator-operator workflow、user gate、feedback capture、material library",
        "beats": ["素材整理作为 safe side path 继续。", "发布和品牌判断停在人类 gate。", "反馈沉淀成可复用偏好和材料库。"],
    },
}

DEFAULT_FRONTEND = {
    "2026-06-27-overnight-pr-batch": ("parallel PR lanes converge into a reviewable merge rail", ["PR batch", "review packet", "public boundary"]),
    "2026-06-24-pr-issue-auto-fix": ("issue feedback closes through repro, fix, validation, and review handoff", ["issue fix", "repro smoke", "PR review"]),
    "2026-06-23-agent-to-agent-pr-comments": ("agent comments pass through explicit ownership instead of loose threads", ["handoff", "PR comment", "claimed_by"]),
    "2026-06-23-overnight-project-refactor": ("a large refactor moves as small reviewable packets", ["refactor", "PR slices", "todo evidence"]),
}

CASE_DETAILS = {
    "2026-06-27-overnight-pr-batch": {
        "zh": {
            "context": [
                "这个案例不是在展示“agent 很忙”，而是在展示高吞吐自动化如何仍然保持可审阅。仓库把证据窗口固定在 2026-06-27 01:29 到 11:29 +08:00，避免把私有队列、聊天截图或操作员笔记当成公开证明。",
                "窗口内公开 Git 历史显示 22 个 merged commits、60 个 touched files、6695 行新增和 223 行删除，其中 10 条 commit message 带 PR 编号。页面只声称这些公开 Git 能复现的事实。",
            ],
            "evidence": [
                ("公开窗口", "2026-06-27 01:29 到 11:29 +08:00，10 小时 Git 证据窗口。"),
                ("变更粒度", "22 个 merged commits 覆盖 docs、runtime、status/quota、benchmark contracts、smokes 和 release/runtime guardrails。"),
                ("审阅边界", "工作以 PR-sized slices 落地，而不是一个需要 maintainer 盲信的巨大 diff。"),
                ("复现方式", "case 文档给出 `git log --since ... --until ...` 和 `git log --numstat` 命令。"),
            ],
            "mechanism": [
                "LoopX 把每条 lane 绑定到 todo ownership、validation、review policy 和 public evidence。",
                "当 control-plane contract 改变时，runtime、docs 和 focused smoke 一起移动。",
                "self-merge 只用于窄而验证过的变更；更大或不清楚的工作仍保留 review gate。",
                "公开边界扫描把内部截图、本地状态、原始日志和私有计划排除在 showcase 之外。",
            ],
            "user_outcome": [
                "用户醒来看到的是一组可 review、可追踪、可解释的 public slices，而不是一堆原始 agent 输出。",
                "operator 可以继续追问每条 slice 的验证和剩余 gate；证据不依赖 chat memory。",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0627-overnight-pr-batch.md"),
                ("catalog workload signal", "docs/showcases/showcase-catalog.json"),
            ],
        },
        "en": {
            "context": [
                "This case is not about an agent being busy. It is about keeping high-throughput autonomous work reviewable. The repository anchors the public evidence window to 2026-06-27 01:29 through 11:29 +08:00 instead of relying on private queues, screenshots, or operator notes.",
                "Public Git history in that window shows 22 merged commits, 60 touched files, 6695 insertions, 223 deletions, and 10 commit messages with explicit PR numbers. The page only claims what those public facts support.",
            ],
            "evidence": [
                ("Public window", "2026-06-27 01:29 to 11:29 +08:00, a 10-hour Git evidence window."),
                ("Change shape", "22 merged commits across docs, runtime, status/quota, benchmark contracts, smokes, and release/runtime guardrails."),
                ("Review boundary", "Work landed as PR-sized slices instead of one broad diff that maintainers would have to trust blindly."),
                ("Reproduction", "The case document gives `git log --since ... --until ...` and `git log --numstat` commands."),
            ],
            "mechanism": [
                "LoopX ties each lane to todo ownership, validation, review policy, and public evidence.",
                "Runtime, docs, and focused smokes move together when a control-plane contract changes.",
                "Self-merge remains limited to narrow validated changes; larger or unclear work preserves review gates.",
                "Public-boundary scans keep internal screenshots, local state, raw logs, and private planning out of the showcase.",
            ],
            "user_outcome": [
                "The user wakes up to reviewable, traceable, explainable public slices rather than raw agent output.",
                "The operator can inspect validation and remaining gates for each slice without relying on chat memory.",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0627-overnight-pr-batch.md"),
                ("catalog workload signal", "docs/showcases/showcase-catalog.json"),
            ],
        },
    },
    "2026-06-24-pr-issue-auto-fix": {
        "zh": {
            "context": [
                "这个案例把 PR issue、review comment 或 issue text 转成可执行修复闭环。它不是“读一段评论就改代码”，而是先做 metadata/intake、repro、branch-local patch、validation 和 review packet。",
                "对用户有价值的是反馈不再停在评论区：它会进入一个有 owner、有复现、有分支修复、有验证、有 review handoff 的闭环。公开证据只证明这条闭环和边界，不把 raw issue body、私有 timeline 或本地路径带进页面。",
            ],
            "evidence": [
                ("产品入口", "`loopx/capabilities/issue_fix/README.md` 声明 `loopx issue-fix ...`、content-ops bridge、protocol docs 和 smoke；入口面向维护者的真实动作是把反馈转成可执行修复包。"),
                ("工作流协议", "`issue_fix_workflow_contract_v0` 明确 metadata preview、intake classification、workflow plan、todo writeback、caller repo branch、validation、PR review packet 和 gate handling。"),
                ("可执行闭环", "`issue_fix_acceptance_loop_v0` 包含 acceptance fixture、repo-branch fixture 和 caller-approved repo branch mode。"),
                ("验证面", "focused smokes 保护 metadata preview、content-ops intake、workflow plan、workflow contract、acceptance loop 和端到端 workflow 的关键边界。"),
            ],
            "mechanism": [
                "public metadata 可以进入 packet；raw issue body、comment body、timeline、provider payload 都是 gated source。",
                "accepted candidates 写成有序 LoopX todos：repro smoke、code-context route、branch-local patch、validation、review-packet readiness。",
                "caller-approved repo 模式只在 `--execute` 时检查本地 repo，并且输出 repo-relative changed files、validation pass/fail 和 PR-readiness。",
                "外部 comment、PR creation、merge、publish、destructive git 和 production action 都保留为显式 gate。",
            ],
            "user_outcome": [
                "维护者给出一个 public issue/PR signal 后，可以得到一个先复现、再修复、再验证、最后交给 review 的闭环。",
                "用户不需要把每个 review comment 手动改写成 agent prompt，也不会把原始 issue body 或本地路径泄漏进公开 artifact。",
            ],
            "source_refs": [
                ("capability README", "loopx/capabilities/issue_fix/README.md"),
                ("workflow contract", "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md"),
                ("acceptance loop", "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md"),
                ("workflow smoke", "examples/issue-fix-workflow-plan-smoke.py"),
                ("acceptance smoke", "examples/issue-fix-acceptance-loop-smoke.py"),
            ],
        },
        "en": {
            "context": [
                "This case turns a PR issue, review comment, or issue text into an executable repair loop. It is not simply reading a comment and editing code; the path goes through metadata/intake, repro, branch-local patch, validation, and review packet.",
                "The user-facing value is that feedback no longer sits in a comment thread: it enters a loop with an owner, repro path, branch-local fix, validation, and review handoff. The public evidence proves the loop and its boundaries without exposing raw issue bodies, private timelines, or local paths.",
            ],
            "evidence": [
                ("Product entry", "`loopx/capabilities/issue_fix/README.md` names `loopx issue-fix ...`, the content-ops bridge, protocol docs, and smokes; the maintainer-facing action is turning feedback into an executable fix packet."),
                ("Workflow contract", "`issue_fix_workflow_contract_v0` defines metadata preview, intake classification, workflow plan, todo writeback, caller repo branch, validation, PR review packet, and gate handling."),
                ("Executable loop", "`issue_fix_acceptance_loop_v0` includes the acceptance fixture, repo-branch fixture, and caller-approved repo branch mode."),
                ("Validation surface", "Focused smokes protect the key boundaries for metadata preview, content-ops intake, workflow plan, workflow contract, acceptance loop, and end-to-end workflow behavior."),
            ],
            "mechanism": [
                "Public metadata can enter packets; raw issue bodies, comment bodies, timelines, and provider payloads remain gated sources.",
                "Accepted candidates become ordered LoopX todos: repro smoke, code-context route, branch-local patch, validation, and review-packet readiness.",
                "Caller-approved repo mode inspects the local repo only under `--execute` and reports repo-relative changed files plus validation pass/fail.",
                "External comments, PR creation, merge, publish, destructive git, and production action remain explicit gates.",
            ],
            "user_outcome": [
                "A maintainer can provide a public issue or PR signal and get a loop that reproduces, fixes, validates, and prepares human review.",
                "The user does not need to rewrite every review comment as an agent prompt, and raw issue bodies or local paths do not leak into the public artifact.",
            ],
            "source_refs": [
                ("capability README", "loopx/capabilities/issue_fix/README.md"),
                ("workflow contract", "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md"),
                ("acceptance loop", "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md"),
                ("workflow smoke", "examples/issue-fix-workflow-plan-smoke.py"),
                ("acceptance smoke", "examples/issue-fix-acceptance-loop-smoke.py"),
            ],
        },
    },
    "2026-06-23-agent-to-agent-pr-comments": {
        "zh": {
            "context": [
                "这个案例描述的是 PR review feedback 在多 agent 之间流转时如何不丢 ownership。重点不是聊天记录，而是 comment、claim、handoff、fix、validation、review packet 这条链。",
                "对用户有价值的是每条反馈都能回答三个问题：谁负责、修复证据在哪里、还需要谁 review。公开仓库里的 event contract、review packet、heartbeat prompt 和 validation fixtures 共同证明这条 handoff 链。",
            ],
            "evidence": [
                ("todo ownership", "`event_sourced_state_contract_v0` 把 `todo_claimed` 定义为 canonical event，记录 ownership、lease 或 `claimed_by`。"),
                ("review packet", "`loopx/review_packet.py` 在 open-todo rendering 和 handoff ranking 路径中保留 `claimed_by`，让 review packet 能显示 owner。"),
                ("peer continuation contract", "`docs/heartbeat-automation-prompt.md` 规定 peer 小变更可带 evidence 自合并，其他后续使用 typed continuation。"),
                ("CLI smokes", "`examples/control_plane/todo-lifecycle-cli-smoke.py` 和 `examples/control_plane/todo-cli-smoke.py` 覆盖 claim、typed successor、peer self-merge、explicit review handoff 和 same-agent review rejection。"),
            ],
            "mechanism": [
                "PR feedback 先成为一个有 owner 的 todo，而不是 chat reminder。",
                "被阻塞或跨 lane 的修复通过 handoff gate 和 review packet 传递，不允许另一个 agent 猜测上下文。",
                "修复 agent 需要留下 diff、validation 和剩余 gate；后续工作创建 successor todo。",
                "owner review 仍在 PR/review surface 上完成，LoopX 只提供状态、证据和 handoff。",
            ],
            "user_outcome": [
                "用户不必手动 shepherd 每条 PR comment；控制面会显示谁 claim 了反馈、修复证据在哪里、还有什么需要 review。",
                "多 agent 协作不会变成“谁看过这条评论”的记忆题。",
            ],
            "source_refs": [
                ("event-sourced todo claim", "docs/reference/protocols/event-sourced-state-contract-v0.md"),
                ("review packet code", "loopx/review_packet.py"),
                ("peer prompt contract", "docs/heartbeat-automation-prompt.md"),
                ("todo lifecycle smoke", "examples/control_plane/todo-lifecycle-cli-smoke.py"),
            ],
        },
        "en": {
            "context": [
                "This case shows how PR review feedback can move across multiple agents without losing ownership. The important chain is comment, claim, handoff, fix, validation, and review packet rather than the chat transcript.",
                "The user-facing value is that every feedback item can answer three questions: who owns it, where the fix evidence is, and who still needs to review it. Public evidence spans the event contract, review packet, heartbeat prompt, and validation fixtures.",
            ],
            "evidence": [
                ("Todo ownership", "`event_sourced_state_contract_v0` defines `todo_claimed` as a canonical event for ownership, lease, or `claimed_by`."),
                ("Review packet", "`loopx/review_packet.py` preserves `claimed_by` in open-todo rendering and handoff ranking so review packets can show ownership."),
                ("Peer continuation contract", "`docs/heartbeat-automation-prompt.md` lets peers self-merge only small validated evidence-backed work and otherwise requires typed continuation."),
                ("CLI smokes", "`examples/control_plane/todo-lifecycle-cli-smoke.py` and `examples/control_plane/todo-cli-smoke.py` cover claims, typed successors, peer self-merge, explicit review handoff, and same-agent review rejection."),
            ],
            "mechanism": [
                "PR feedback becomes an owned todo rather than a chat reminder.",
                "Blocked or cross-lane fixes move through handoff gates and review packets instead of another agent guessing context.",
                "The fixing agent leaves diff, validation, and remaining gates; follow-up work becomes a successor todo.",
                "Owner review still happens on the PR or review surface; LoopX supplies state, evidence, and handoff.",
            ],
            "user_outcome": [
                "The user does not have to shepherd every PR comment manually; the control plane shows who claimed the feedback, where the fix evidence is, and what still needs review.",
                "Multi-agent collaboration stops depending on memory of which agent saw which comment.",
            ],
            "source_refs": [
                ("event-sourced todo claim", "docs/reference/protocols/event-sourced-state-contract-v0.md"),
                ("review packet code", "loopx/review_packet.py"),
                ("peer prompt contract", "docs/heartbeat-automation-prompt.md"),
                ("todo lifecycle smoke", "examples/control_plane/todo-lifecycle-cli-smoke.py"),
            ],
        },
    },
    "2026-06-23-overnight-project-refactor": {
        "zh": {
            "context": [
                "这个案例解决的是无人值守重构的风险：长时间 agent 容易把 cleanup、行为改变、发现的新问题和过期计划混成一个大 diff。",
                "LoopX 的公开证据不是某个私有夜间截图，而是 todo lifecycle、successor/supersede、validation writeback 和 review-packet 这些已经在仓库里有文档和 smoke 的控制面。`todo-lifecycle-cli-smoke.py` 里能看到 successor、supersede、handoff 和 self-merge 的完整回归覆盖。",
            ],
            "evidence": [
                ("successor path", "`docs/integrations/lark-kanban-control-plane-adapter.md` 明确 real successor 使用 `todo complete --next-*`，replacement 或 narrower split 使用 `todo supersede --next-agent-todo`。"),
                ("peer completion", "`docs/heartbeat-automation-prompt.md` 要求非平凡完成创建 typed successor todo 或写 no-follow-up rationale。"),
                ("CLI validation", "`examples/control_plane/todo-lifecycle-cli-smoke.py` 覆盖 `--next-agent-todo` successor、`todo supersede`、claim 继承、typed continuation、same-agent review rejection 和 peer self-merge evidence。"),
                ("review shape", "`loopx review-packet` 把当前 open todo、claimed_by 和 handoff 状态打包成 reviewer 可读的 packet。"),
            ],
            "mechanism": [
                "当前 refactor slice 必须是可 review 的单位，不把整夜发现都塞进一个 PR。",
                "发现的新工作写成 follow-up todo；路线变了就 supersede 旧 todo。",
                "每个 slice 用 focused validation 或文档/contract smoke 证明，而不是依赖原始 agent trace。",
                "大范围或风险不清楚的 slice 不自合并，进入 review handoff。",
            ],
            "user_outcome": [
                "用户可以让重构跑过夜，但早上看到的是一组有边界的 review 单元和剩余 todo，而不是一个不可 review 的巨型改动。",
                "项目可以继续快，但 review 面仍是人能处理的粒度。",
            ],
            "source_refs": [
                ("kanban control-plane adapter", "docs/integrations/lark-kanban-control-plane-adapter.md"),
                ("heartbeat prompt contract", "docs/heartbeat-automation-prompt.md"),
                ("todo lifecycle smoke", "examples/control_plane/todo-lifecycle-cli-smoke.py"),
                ("case narrative", "docs/showcases/cases/0623-overnight-project-refactor.md"),
            ],
        },
        "en": {
            "context": [
                "This case addresses the risk of unattended refactoring: a long-running agent can mix cleanup, behavior change, discoveries, and stale plans into one broad diff.",
                "The public evidence is not a private overnight screenshot. It is the control-plane behavior already documented and smoke-tested in the repository: todo lifecycle, successor/supersede, validation writeback, and review packets. `todo-lifecycle-cli-smoke.py` carries regression coverage for successors, supersede, handoff, and self-merge.",
            ],
            "evidence": [
                ("Successor path", "`docs/integrations/lark-kanban-control-plane-adapter.md` says real successors use `todo complete --next-*`, while replacements or narrower splits use `todo supersede --next-agent-todo`."),
                ("Peer completion", "`docs/heartbeat-automation-prompt.md` requires nontrivial completion to create a typed successor todo or a no-follow-up rationale."),
                ("CLI validation", "`examples/control_plane/todo-lifecycle-cli-smoke.py` covers `--next-agent-todo` successors, `todo supersede`, claim inheritance, typed continuation, same-agent review rejection, and peer self-merge evidence."),
                ("Review shape", "`loopx review-packet` packages open todos, claimed_by, and handoff state for reviewer consumption."),
            ],
            "mechanism": [
                "The current refactor slice must stay reviewable; overnight discoveries do not all land in one PR.",
                "New discoveries become follow-up todos; changed routes supersede stale todos.",
                "Each slice carries focused validation or doc/contract smoke evidence rather than raw agent traces.",
                "Broad or unclear-risk slices route to review handoff instead of self-merge.",
            ],
            "user_outcome": [
                "The user can let a refactor run overnight and wake up to bounded review units plus remaining todos, not one unreviewable giant change.",
                "The project can move quickly while the review surface remains human-sized.",
            ],
            "source_refs": [
                ("kanban control-plane adapter", "docs/integrations/lark-kanban-control-plane-adapter.md"),
                ("heartbeat prompt contract", "docs/heartbeat-automation-prompt.md"),
                ("todo lifecycle smoke", "examples/control_plane/todo-lifecycle-cli-smoke.py"),
                ("case narrative", "docs/showcases/cases/0623-overnight-project-refactor.md"),
            ],
        },
    },
    HARDWARE_CASE_ID: {
        "zh": {
            "context": ["hardware 中文页面是 canonical artifact，生成器不会重写。"],
            "evidence": [],
            "mechanism": [],
            "user_outcome": [],
            "source_refs": [],
        },
        "en": {
            "context": [
                "The Chinese hardware page remains the canonical artifact. This English companion follows its structure without rewriting the original.",
                "The case demonstrates a dynamic workflow around fuzzy long-running hardware goals: generated scripts coordinate bounded worker-agent actions while LoopX preserves goal state, quota, todo ownership, validation evidence, and run history outside any one chat thread.",
            ],
            "evidence": [
                ("Public artifact", "The canonical HTML page includes the approved hardware workflow artifact and five public-safe hardware cases."),
                ("Case family", "The companion note names closed validation, timing optimization, design-space exploration, Fmax optimization, and convergence to an engineering floor."),
                ("Boundary", "The public artifact excludes raw chats, screenshots, proprietary design details, private repos, local paths, task ids, credentials, and unpublished hardware artifacts."),
            ],
            "mechanism": [
                "LoopX owns durable state, quota, todos, claims, evidence, and history.",
                "Claude Code writes task-level orchestration and generated scripts.",
                "Worker agents perform bounded RTL, simulation, and validation work under explicit review boundaries.",
            ],
            "user_outcome": [
                "A contributor-approved page shows how multiple hardware-agent workers can coordinate under one control plane.",
                "Readers can inspect the product pattern without receiving proprietary hardware details or raw execution traces.",
            ],
            "source_refs": [
                ("canonical HTML", "docs/showcases/cases/0619-dynamic-workflow-hardware-agent.html"),
                ("companion note", "docs/showcases/cases/0619-dynamic-workflow-hardware-agent.md"),
            ],
        },
    },
    "2026-06-19-loopx-self-iteration": {
        "zh": {
            "context": [
                "这个案例是 public repo self-iteration：LoopX 用来推进 LoopX 自己，不是一个孤立功能 demo。仓库在 benchmark adapters、control-plane correctness、planning lanes、dashboard/frontstage、docs、smokes 和 multi-agent coordination 上同时高频变化。",
                "证据窗口固定到 anchor commit `86d6d9d`，避免文档更新改变自己的证据。这里的效率模型是保守的 public Git model，不使用私有聊天、active-state 原文或 benchmark 原始材料。",
            ],
            "evidence": [
                ("全仓库证据", "截至 anchor commit：801 个 public commits、570 个 touched files、265703 行新增、49895 行删除。"),
                ("近期窗口", "2026-06-18 起有 244 个 public commits、216 个 touched files、52898 行新增、20935 行删除。"),
                ("0619 当日", "2026-06-19 有 74 个 public commits、118 个 touched files、16087 行新增、1082 行删除。"),
                ("效率模型", "把公开仓库能力拆成 9 个 requirement clusters，保守估计 59-92 个 AI-coding-assisted developer-days，对 19.6 天 public window 得出方向性 compression。"),
            ],
            "mechanism": [
                "registry、prompt contracts 和 registered agents 命名 primary/side identities，不靠聊天记忆。",
                "benchmark、productization、documentation、planning 和 peer lanes 被拆成 reviewable obligations。",
                "quota/status projection 区分 executable work、monitor work、user gates 和 blockers。",
                "advisory peer scope 留在 profile/prompt，todo metadata 保留 `claimed_by` 和 typed continuation。",
                "public docs 和 smokes 把可复用经验沉淀为仓库 artifact。",
            ],
            "user_outcome": [
                "operator 可以让 primary benchmark lane 和 product/docs/control-plane side work 并行，而不会失去 ownership、gate、validation 和 merge discipline。",
                "对潜在用户来说，这是“长期 agent 项目仍然可读”的证据：未来 agent 能从公开表面恢复目标、owner、gate、验证、证据和 follow-up。",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0619-loopx-self-iteration.md"),
                ("workload signal", "docs/showcases/showcase-catalog.json"),
                ("frontstage fixture", "examples/fixtures/long-horizon-self-iteration-rollout.public.json"),
                ("frontstage smoke", "examples/long-horizon-self-iteration-rollout-fixture-smoke.py"),
            ],
        },
        "en": {
            "context": [
                "This is public-repo self-iteration: LoopX was used to improve LoopX itself, not just one isolated demo. Benchmark adapters, control-plane correctness, planning lanes, dashboard/frontstage, docs, smokes, and multi-agent coordination all moved under high churn.",
                "The evidence is fixed to anchor commit `86d6d9d` so this documentation update does not change its own evidence window. The efficiency model is a conservative public-Git model; it excludes private chats, active-state bodies, and raw benchmark material.",
            ],
            "evidence": [
                ("Whole repository", "Through the anchor commit: 801 public commits, 570 touched files, 265703 insertions, and 49895 deletions."),
                ("Recent window", "Since 2026-06-18: 244 public commits, 216 touched files, 52898 insertions, and 20935 deletions."),
                ("June 19 signal", "On 2026-06-19: 74 public commits, 118 touched files, 16087 insertions, and 1082 deletions."),
                ("Efficiency model", "The case maps public repo capabilities to 9 requirement clusters and estimates 59-92 AI-coding-assisted developer-days against a 19.6-day public window."),
            ],
            "mechanism": [
                "Registry and prompt contracts name peer identities instead of relying on chat memory.",
                "Benchmark, productization, documentation, planning, and peer lanes become reviewable obligations.",
                "Quota and status projection distinguish executable work, monitor work, user gates, and blockers.",
                "Advisory peer scope lives in profile and prompt; todo metadata keeps `claimed_by` plus typed continuation.",
                "Public docs and smokes turn reusable lessons into repository artifacts.",
            ],
            "user_outcome": [
                "The operator can let a primary benchmark lane and product/docs/control-plane side work run in parallel without losing ownership, gates, validation, or merge discipline.",
                "For a potential user, this is evidence that a long-running agent project can remain legible: future agents can recover goals, owners, gates, validation, evidence, and follow-up from public surfaces.",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0619-loopx-self-iteration.md"),
                ("workload signal", "docs/showcases/showcase-catalog.json"),
                ("frontstage fixture", "examples/fixtures/long-horizon-self-iteration-rollout.public.json"),
                ("frontstage smoke", "examples/long-horizon-self-iteration-rollout-fixture-smoke.py"),
            ],
        },
    },
    "2026-06-17-blocked-p0-safe-rotation": {
        "zh": {
            "context": [
                "这个案例展示 P0 被用户决策卡住时，系统不应该继续硬跑，也不应该让整个目标停摆。原场景是 benchmark rotation：一个 lane 需要大型本地 image，其他 no-upload benchmark work 仍然安全。",
                "公开仓库没有暴露原始 benchmark task 或本地 image 名，而是用 synthetic smoke 复现控制面行为。用户价值是明确看到一个需要决策的 P0，同时安全 fallback 可以继续，且 gated lane 不消耗额外自动推进预算。",
            ],
            "evidence": [
                ("synthetic fixture", "`examples/showcase-0617-blocked-p0-safe-rotation-smoke.py` 复现 P0 user gate、被 gate 阻塞的 P0 agent lane 和 P1 no-upload fallback。"),
                ("quota contract", "smoke 固定 `should_run=True`、`requires_user_action=True`、`safe_bypass_allowed=True`、`safe_bypass_kind=scoped_user_gate_fallback` 等关键 contract。"),
                ("selected fallback", "fixture 选择 `terminal_bench_no_upload`，同时保留 `ale_image` gate 的 user-visible blocker。"),
                ("rendered evidence", "smoke 检查 markdown 中包含 `scoped_user_gate_fallback` 和 safe no-upload Terminal-Bench rotation。"),
            ],
            "mechanism": [
                "用户 todo 具体命名 P0 决策，不用“owner gate”这种空话。",
                "agent 不在 gated lane 上花 compute；只选择不依赖该决策的 fallback。",
                "状态同时记录 blocker 和 fallback reason，方便之后恢复 P0。",
            ],
            "user_outcome": [
                "用户看到需要自己决定的具体问题，同时项目仍能在安全范围内推进。",
                "这减少了注意力负担：不需要每 10 分钟看一次 agent 为什么没动，也不会错过真正需要决策的事项。",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0617-blocked-p0-safe-rotation.md"),
                ("synthetic smoke", "examples/showcase-0617-blocked-p0-safe-rotation-smoke.py"),
            ],
        },
        "en": {
            "context": [
                "This case shows what should happen when a P0 route is blocked by a user decision: the system should neither keep forcing that lane nor stop the whole goal. The original shape was a benchmark rotation where one lane needed a large local image while other no-upload benchmark work remained safe.",
                "The public repository does not expose raw benchmark tasks or local image names. It reproduces the control-plane behavior with a synthetic smoke. The user-facing value is seeing one concrete P0 decision while safe fallback work can continue and the gated lane does not burn automated progress budget.",
            ],
            "evidence": [
                ("Synthetic fixture", "`examples/showcase-0617-blocked-p0-safe-rotation-smoke.py` reproduces a P0 user gate, a P0 agent lane blocked by that gate, and a P1 no-upload fallback."),
                ("Quota contract", "The smoke pins `should_run=True`, `requires_user_action=True`, `safe_bypass_allowed=True`, `safe_bypass_kind=scoped_user_gate_fallback`, and related fallback evidence."),
                ("Selected fallback", "The fixture selects `terminal_bench_no_upload` while preserving the `ale_image` gate as the user-visible blocker."),
                ("Rendered evidence", "The smoke checks markdown for `scoped_user_gate_fallback` and safe no-upload Terminal-Bench rotation."),
            ],
            "mechanism": [
                "The user todo names the concrete P0 decision instead of saying only owner gate.",
                "The agent does not spend compute on the gated lane; it selects fallback work that does not depend on the decision.",
                "State records both the blocker and the fallback reason so P0 can resume later.",
            ],
            "user_outcome": [
                "The user sees the exact decision they need to make while the project continues safely elsewhere.",
                "Attention load drops: the user does not need to watch repeated idle polls and does not miss the real decision.",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0617-blocked-p0-safe-rotation.md"),
                ("synthetic smoke", "examples/showcase-0617-blocked-p0-safe-rotation-smoke.py"),
            ],
        },
    },
    "2026-06-20-creator-operator-case-spec": {
        "zh": {
            "context": [
                "这是 appendix case：它展示非技术创作者/运营者如何用 LoopX 管一个长期 research + planning loop，但还不是 top-card proof，因为没有真实用户公开证据。",
                "公开材料完全使用 synthetic data，重点不是证明增长，而是证明产品边界：研究和素材整理可以继续，发布、品牌判断和对外动作必须停在人类 gate。",
            ],
            "evidence": [
                ("storyboard", "`creator-ops-fake-data-storyboard.md` 给出完整 fake fixture，不需要 live platform access，适合公开展示用户旅程而不泄漏真实运营数据。"),
                ("feedback contract", "`creator-ops-feedback-boundary-contract.md` 把 gate decision、preference hint、todo update、boundary correction、reward signal 和 product improvement note 分开，避免把偏好误当发布授权。"),
                ("source status", "contract 要求 topic、insight、draft、material item 都携带 source status；public repo 默认 `synthetic_demo`，不伪装成真实增长证据。"),
                ("no autopublish", "publishing 是 hard user gate；safe side work 可以继续，但不能把偏好或 reward 当成发布授权。"),
            ],
            "mechanism": [
                "creative objective 是 durable goal state，不是聊天窗口里的隐形任务。",
                "publish/no-publish 是 user gate；research、整理和 source-status 改进可以作为 safe side path。",
                "反馈被写成 preference hint、gate decision、todo update 或 boundary correction。",
                "下一次 agent run 前，用户能看到 blocked route、safe side path 和 validation expectation。",
            ],
            "user_outcome": [
                "非技术用户不用读 prompt、trace 或 raw logs，就能知道上次改变了什么、什么在等自己、什么可以继续。",
                "这个 case 适合展示产品方向，但页面会明确它是 synthetic spec，不声称真实增长、质量或收入效果。",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0620-creator-operator-case-spec.md"),
                ("fake-data storyboard", "docs/showcases/creator-ops-fake-data-storyboard.md"),
                ("feedback boundary contract", "docs/showcases/creator-ops-feedback-boundary-contract.md"),
            ],
        },
        "en": {
            "context": [
                "This is an appendix case: it shows how a non-technical creator-operator might use LoopX to manage a long-running research and planning loop, but it is not a top-card proof until real public user evidence exists.",
                "The public material uses only synthetic data. The point is not proving growth; it is proving the product boundary: research and material organization can continue, while publishing, brand judgment, and external action remain human gates.",
            ],
            "evidence": [
                ("Storyboard", "`creator-ops-fake-data-storyboard.md` provides a complete fake fixture with no live platform access, suitable for showing the user journey without exposing real operations data."),
                ("Feedback contract", "`creator-ops-feedback-boundary-contract.md` separates gate decisions, preference hints, todo updates, boundary corrections, reward signals, and product improvement notes so preference is not mistaken for publish approval."),
                ("Source status", "The contract requires every topic, insight, draft, and material item to carry source status; the public repo defaults to `synthetic_demo` instead of pretending to show real growth evidence."),
                ("No autopublish", "Publishing is a hard user gate; safe side work may continue, but preference or reward is not publication approval."),
            ],
            "mechanism": [
                "The creative objective is durable goal state, not a hidden task inside chat.",
                "Publish/no-publish is a user gate; research, organization, and source-status work can continue as safe side paths.",
                "Feedback becomes a preference hint, gate decision, todo update, or boundary correction.",
                "Before the next agent run, the user can see the blocked route, safe side path, and validation expectation.",
            ],
            "user_outcome": [
                "A non-technical user can see what changed, what is waiting for them, and what can continue without reading prompts, traces, or raw logs.",
                "The case is useful product direction, but the page clearly labels it as a synthetic spec and makes no claim about real growth, quality, or revenue.",
            ],
            "source_refs": [
                ("case narrative", "docs/showcases/cases/0620-creator-operator-case-spec.md"),
                ("fake-data storyboard", "docs/showcases/creator-ops-fake-data-storyboard.md"),
                ("feedback boundary contract", "docs/showcases/creator-ops-feedback-boundary-contract.md"),
            ],
        },
    },
}

UI = {
    "en": {
        "html_lang": "en",
        "alternate": "中文",
        "index_title": "Showcase & Good Case",
        "index_subtitle": "Real LoopX cases showing how long-running agent work stays reviewable, verifiable, and safe to continue.",
        "top_cases": "Top showcase cases",
        "appendix": "Appendix case",
        "proof": "Proof",
        "intervention": "LoopX intervention",
        "context": "Case context",
        "evidence": "Repository evidence",
        "behavior": "LoopX behavior",
        "outcome": "What the user sees",
        "sources": "Repository sources",
        "boundary": "Evidence boundary",
        "narrative": "Case note",
        "catalog": "Catalog",
        "home": "Showcases",
        "canonical": "Canonical artifact",
        "open": "Open",
        "demo": "Demo",
        "search": "Search showcase cases",
        "experimental_title": "Experimental today-value path",
        "experimental_intro": "A lower-priority entry point for users who want to pick one useful LoopX capability today without replacing the showcase first screen.",
        "experimental_rows": [
            ("PR review/comment -> fix loop", "Branch-ready fix packet with repro, smoke result, and remaining review owner.", "Fewer dropped review threads."),
            ("Overnight PR-sized refactor", "Reviewable slice list, validation notes, successor todo, and merge boundary.", "More merged commits without a giant diff audit."),
            ("P0 blocked -> safe fallback", "Kernel projection of the exact user gate, safe fallback todo, quota decision, and evidence boundary.", "Less idle agent time while preserving human judgment."),
        ],
        "footer": "Generated from docs/showcases/showcase-catalog.json. Private links, raw chats, local state, and internal media are excluded.",
    },
    "zh": {
        "html_lang": "zh-CN",
        "alternate": "English",
        "index_title": "Showcase & Good Case",
        "index_subtitle": "真实 LoopX 案例：长程 agent 工作如何保持可审阅、可验证、可继续推进。",
        "top_cases": "顶部 Showcase 案例",
        "appendix": "附录案例",
        "proof": "证明点",
        "intervention": "LoopX 介入",
        "context": "案例背景",
        "evidence": "仓库证据",
        "behavior": "LoopX 行为",
        "outcome": "用户看到什么",
        "sources": "仓库来源",
        "boundary": "证据边界",
        "narrative": "案例说明",
        "catalog": "Catalog",
        "home": "Showcases",
        "canonical": "Canonical 原页面",
        "open": "打开",
        "demo": "Demo",
        "search": "搜索 showcase 案例",
        "experimental_title": "Experimental today-value path",
        "experimental_intro": "一个放在首屏下方的实验性入口：帮助用户从三个 LoopX 能力里选择今天就能产生价值的一项。",
        "experimental_rows": [
            ("PR review/comment -> fix loop", "可复核的修复包：repro、smoke 结果、剩余 review owner。", "更少遗漏 review 线程。"),
            ("Overnight PR-sized refactor", "可 review 的 slice 列表、验证记录、后续 todo、merge 边界。", "增加可合并 commit，避免巨型 diff。"),
            ("P0 blocked -> safe fallback", "已有 goal 内由 kernel 投影具体 user gate、安全 fallback todo、quota 决策和证据边界。", "减少 agent 空转，同时保留人类判断。"),
        ],
        "footer": "由 docs/showcases/showcase-catalog.json 生成。不包含私有链接、原始聊天、本地状态或内部媒体。",
    },
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "case"


def repo_path(path: str) -> Path:
    return (REPO_ROOT / path).resolve()


def rel_href(source: Path, target: Path) -> str:
    return os.path.relpath(target, source.parent).replace(os.sep, "/")


def first_items(values: Any, limit: int = 6) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values[:limit]]


def ui(lang: str, key: str) -> str:
    return UI[lang][key]


def copy_for(case: dict[str, Any], lang: str) -> dict[str, Any]:
    localizations = case.get("localizations")
    if isinstance(localizations, dict):
        localized_copy = localizations.get(lang)
        if isinstance(localized_copy, dict):
            return localized_copy
    if lang == "zh":
        return ZH_COPY.get(str(case.get("id")), {})
    return {}


def localized(case: dict[str, Any], lang: str, key: str) -> str:
    copy = copy_for(case, lang)
    value = copy.get(key)
    if value is None:
        value = case.get(key)
    return str(value or "")


def table_for(case: dict[str, Any], lang: str) -> dict[str, str]:
    copy = copy_for(case, lang)
    table = case.get("showcase_table")
    if isinstance(table, dict):
        fallback = table
    else:
        fallback = SHOWCASE_TABLE.get(str(case.get("id")), {
            "proof_point": str(case.get("headline") or ""),
            "loopx_intervention": ", ".join(first_items(case.get("pattern_tags"), 4)),
        })
    return {
        "proof_point": str(copy.get("proof_point") or fallback.get("proof_point") or ""),
        "loopx_intervention": str(
            copy.get("loopx_intervention") or fallback.get("loopx_intervention") or ""
        ),
    }


def details_for(case: dict[str, Any], lang: str) -> dict[str, Any]:
    details = CASE_DETAILS.get(str(case.get("id")), {}).get(lang)
    if isinstance(details, dict):
        return details
    copy = copy_for(case, lang)
    source_refs = [(ui(lang, "narrative"), str(case.get("case_page") or ""))]
    evidence_links = case.get("evidence_links")
    if isinstance(evidence_links, list):
        for item in evidence_links:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or ui(lang, "sources"))
            url = str(item.get("url") or "")
            if url:
                source_refs.append((label, url))
    return {
        "context": [str(copy.get("problem") or case.get("problem") or localized(case, lang, "headline"))],
        "evidence": [(ui(lang, "proof"), table_for(case, lang)["proof_point"])],
        "mechanism": first_items(copy.get("loopx_behavior") or case.get("loopx_behavior"), 6),
        "user_outcome": [str(copy.get("user_value") or case.get("user_value") or "")],
        "source_refs": source_refs,
    }


def render_text_stack(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    return '<div class="text-stack">' + "".join(f"<p>{esc(item)}</p>" for item in cleaned) + "</div>"


def render_evidence_items(items: list[tuple[str, str]]) -> str:
    cleaned = [(label, text) for label, text in items if label or text]
    return '<div class="evidence-grid">' + "".join(
        f'<div class="evidence-card"><div class="evidence-label">{esc(label)}</div><p>{esc(text)}</p></div>'
        for label, text in cleaned
    ) + "</div>"


def render_source_refs(items: list[tuple[str, str]], current: Path) -> str:
    links: list[str] = []
    for label, path in items:
        if not path:
            continue
        href = path if path.startswith(("https://", "http://")) else rel_href(current, repo_path(path))
        links.append(
            f'<a class="source-ref" href="{esc(href)}">'
            f'<span>{esc(label)}</span><code>{esc(path)}</code></a>'
        )
    return '<div class="source-list">' + "".join(links) + "</div>"


def render_evidence_assets(case: dict[str, Any], current: Path) -> str:
    assets = case.get("evidence_assets")
    if not isinstance(assets, list):
        return ""
    figures: list[str] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        alt = str(item.get("alt") or "")
        caption = str(item.get("caption") or "")
        if not path or not alt:
            continue
        href = rel_href(current, repo_path(path))
        figures.append(
            f'<figure class="evidence-asset"><a href="{esc(href)}">'
            f'<img src="{esc(href)}" alt="{esc(alt)}" loading="lazy"></a>'
            f'<figcaption>{esc(caption)}</figcaption></figure>'
        )
    return '<div class="evidence-assets">' + "".join(figures) + "</div>" if figures else ""


def case_html_path(case: dict[str, Any], lang: str) -> Path:
    if str(case.get("id")) == HARDWARE_CASE_ID and lang == "zh":
        return HARDWARE_CANONICAL_PAGE
    case_page = str(case.get("case_page") or "")
    if case_page.endswith(".md"):
        base = repo_path(case_page[:-3] + ".html")
    else:
        base = CASES_DIR / f"{slug(case.get('id') or case.get('title'))}.html"
    if lang == "en":
        return base.with_name(f"{base.stem}.en{base.suffix}")
    return base


def index_path(lang: str) -> Path:
    return SHOWCASE_DIR / ("index.en.html" if lang == "en" else "index.html")


def is_hardware_canonical(case: dict[str, Any], lang: str) -> bool:
    return str(case.get("id")) == HARDWARE_CASE_ID and lang == "zh"


def assert_hardware_canonical() -> None:
    text = HARDWARE_CANONICAL_PAGE.read_text(encoding="utf-8")
    markers = [
        "LoopX Hardware-Agent Dynamic Workflow",
        "__bundler_thumbnail",
        "__bundler/manifest",
        "__bundler/template",
        "loopx 在芯片开发任务上的实践",
        "动态脚本编排",
        "DUDUCoder",
        "Claude Code",
        "DUDU",
        "CV32E40P",
        "VeeR EH1",
        "Viterbi",
    ]
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise AssertionError(f"canonical hardware showcase was overwritten or damaged: {missing!r}")


def ordered_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {str(case.get("id")): case for case in cases}
    primary = [by_id[case_id] for case_id in PRIMARY_CASE_ORDER if case_id in by_id]
    primary_ids = {str(case.get("id")) for case in primary}
    appendix = [case for case in cases if str(case.get("id")) not in primary_ids]
    return primary, appendix


def badges(items: list[str]) -> str:
    return "".join(f"<span>{esc(item)}</span>" for item in items)


def classification_badges(case: dict[str, Any], lang: str) -> list[str]:
    case_type = str(case.get("case_type") or "unclassified")
    strength = str(case.get("evidence_strength") or "unclassified")
    return [
        CASE_TYPE_LABELS.get(case_type, {}).get(lang, case_type),
        EVIDENCE_STRENGTH_LABELS.get(strength, {}).get(lang, strength),
    ]


def display_badges(case: dict[str, Any], lang: str, raw_tags: list[str], limit: int) -> list[str]:
    labels = classification_badges(case, lang)
    excluded = {
        str(case.get("case_type") or ""),
        str(case.get("evidence_strength") or ""),
    }
    seen = {label.casefold() for label in labels}
    for raw_tag in raw_tags:
        if raw_tag in excluded:
            continue
        label = raw_tag.replace("_", " ").strip()
        if not label or label.casefold() in seen:
            continue
        labels.append(label)
        seen.add(label.casefold())
        if len(labels) >= limit + 2:
            break
    return labels


def css(*, shared_asset: bool = False) -> str:
    selection_background = "#39417d" if shared_asset else "color-mix(in srgb,var(--accent,#6e79d6) 34%,transparent)"
    navigation_background = "#19191d" if shared_asset else "rgba(255,255,255,.025)"
    source_wrap = "overflow-wrap:anywhere" if shared_asset else "word-break:break-word"
    return """
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{background:#0b0b0c;color:#f1f2f3;font-family:'Geist',system-ui,-apple-system,BlinkMacSystemFont,sans-serif;-webkit-font-smoothing:antialiased}
  a{color:inherit}
  ::selection{background:__SELECTION_BACKGROUND__;color:#fff}
  .gh{min-height:100vh;position:relative;overflow-x:hidden;--accent:#6e79d6}
  .grain{position:fixed;inset:0;pointer-events:none;opacity:.025;mix-blend-mode:overlay;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
  article{position:relative;max-width:800px;margin:0 auto;padding:76px 28px 130px}
  .mlab{font-family:'Geist Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:.06em;color:#62666d;text-transform:uppercase}
  h1{font-size:clamp(31px,4.6vw,46px);font-weight:600;line-height:1.12;letter-spacing:-.03em;margin:16px 0 14px;color:#fafafa;text-wrap:balance}
  h2{font-size:22px;font-weight:600;letter-spacing:-.02em;color:#f1f2f3}
  h3{font-size:21px;font-weight:600;letter-spacing:-.02em;color:#f1f2f3}
  p{font-size:15.5px;line-height:1.75;color:#9ea3aa}
  strong{color:#e9eaec;font-weight:600}
  code{font-family:'Geist Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;color:#c4c7cc}
  .accent{font-size:clamp(17px,2.1vw,21px);font-weight:500;color:var(--accent);letter-spacing:-.01em;margin-bottom:22px}
  .nav{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 28px}
  .nav a,.case-link{font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.04em;text-decoration:none;color:#c4c7cc;border:1px solid rgba(255,255,255,.12);border-radius:6px;padding:7px 10px;background:__NAVIGATION_BACKGROUND__}
  .nav a:hover,.case-link:hover{border-color:color-mix(in srgb,var(--accent) 55%,transparent);color:#f4f4f5}
  .section-head{margin-top:74px;display:flex;align-items:baseline;gap:13px;margin-bottom:20px}
  .section-head span{font-family:'Geist Mono',ui-monospace,monospace;font-size:13px;color:var(--accent)}
  .panel{border:1px solid rgba(255,255,255,.1);border-radius:10px;background:#0e0e10;overflow:hidden}
  .panel-row{display:grid;grid-template-columns:150px 1fr;border-top:1px solid rgba(255,255,255,.07)}
  .panel-row:first-child{border-top:0}
  .panel-key{padding:18px 20px;border-right:1px solid rgba(255,255,255,.07);font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent);letter-spacing:.04em}
  .panel-val{padding:18px 20px;display:flex;align-items:center}
  .panel-val p{font-size:14px;line-height:1.6;margin:0}
  .diagram{border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:30px 24px 22px;background:#0e0e10;margin:22px 0}
  .diagram svg{display:block;width:100%;height:auto}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}
  .chips span{font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#888d95;border:1px solid rgba(255,255,255,.12);padding:4px 8px;border-radius:5px}
  .text-stack{display:flex;flex-direction:column;gap:16px}
  .evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:20px 0 6px}
  .evidence-card{border:1px solid rgba(255,255,255,.1);border-radius:10px;background:#0e0e10;padding:18px 18px 17px;min-height:128px}
  .evidence-label{font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);margin-bottom:10px}
  .evidence-assets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:20px}
  .evidence-asset{border:1px solid rgba(255,255,255,.1);border-radius:10px;background:#0e0e10;overflow:hidden}
  .evidence-asset a{display:block;background:#f4f4f2}
  .evidence-asset img{display:block;width:100%;height:auto;max-height:520px;object-fit:contain}
  .evidence-asset figcaption{padding:12px 14px;font-size:12px;line-height:1.55;color:#858a92}
  .source-list{display:flex;flex-direction:column;gap:10px;margin-top:20px}
  .source-ref{display:grid;grid-template-columns:150px 1fr;gap:14px;align-items:center;text-decoration:none;border:1px solid rgba(255,255,255,.1);border-radius:8px;background:#0e0e10;padding:12px 14px}
  .source-ref:hover{border-color:color-mix(in srgb,var(--accent) 55%,transparent)}
  .source-ref span{font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:var(--accent);text-transform:uppercase;letter-spacing:.04em}
  .source-ref code{color:#aeb3ba;__SOURCE_WRAP__}
  .boundary-box{border-left:2px solid var(--accent);background:#0e0e10;padding:18px 20px;margin-top:16px;border-radius:0 8px 8px 0}
  .flow{display:flex;flex-direction:column}
  .flow li{display:grid;grid-template-columns:28px 1fr;gap:14px;list-style:none}
  .flow-num{width:24px;height:24px;border-radius:50%;border:1px solid color-mix(in srgb,var(--accent) 50%,transparent);display:flex;align-items:center;justify-content:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11.5px;color:var(--accent);background:#0b0b0c}
  .flow-body{padding-bottom:18px;border-left:1px solid rgba(255,255,255,.12);padding-left:14px;margin-left:-27px}
  .flow-title{font-size:14.5px;color:#eceef0;font-weight:500;margin-bottom:7px}
  .metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:22px}
  .metric{border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:17px 19px;background:#0c0c0e}
  .metric strong{display:block;font-size:24px;letter-spacing:-.02em;color:#fafafa}
  .metric span{display:block;margin-top:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#62666d;text-transform:uppercase}
  .mini-metrics{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}
  .mini-metrics span{font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#aeb3ba;border:1px solid rgba(255,255,255,.1);border-radius:5px;padding:4px 8px;background:#0b0b0c}
  .search{width:100%;height:42px;margin:22px 0 16px;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:#0e0e10;color:#f1f2f3;padding:0 12px;font:14px 'Geist',system-ui,sans-serif}
  .cards{display:flex;flex-direction:column;gap:12px}
  .card{display:block;text-decoration:none;border:1px solid rgba(255,255,255,.1);border-radius:10px;background:#0e0e10;padding:18px 20px}
  .card:hover{border-color:color-mix(in srgb,var(--accent) 55%,transparent)}
  .card .meta{font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#62666d;margin-bottom:8px}
  .card p{font-size:14px;margin-top:9px}
  .hide{display:none}
  .experiment{margin-top:24px;border:1px solid rgba(255,255,255,.1);border-radius:10px;background:#0e0e10;padding:20px}
  .experiment h3{font-size:18px}
  .experiment p{font-size:14px;margin-top:8px}
  .experiment-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:16px}
  .experiment-card{border:1px solid rgba(255,255,255,.1);border-radius:8px;background:#0b0b0c;padding:14px}
  .experiment-card strong{display:block;font-size:14px;line-height:1.45}
  .experiment-card span{display:block;margin-top:9px;font-size:13px;line-height:1.55;color:#9ea3aa}
  .experiment-card em{display:block;margin-top:10px;font-style:normal;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;line-height:1.55;color:var(--accent)}
  footer{margin-top:76px;color:#62666d;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;line-height:1.7}
  @media(max-width:720px){article{padding:52px 18px 90px}.panel-row{grid-template-columns:1fr}.panel-key{border-right:0;border-bottom:1px solid rgba(255,255,255,.07)}.metric-grid,.evidence-grid,.evidence-assets,.experiment-grid{grid-template-columns:1fr}.source-ref{grid-template-columns:1fr;gap:6px}}
""".replace("__SELECTION_BACKGROUND__", selection_background).replace(
        "__NAVIGATION_BACKGROUND__", navigation_background
    ).replace("__SOURCE_WRAP__", source_wrap)


def html_head(title: str, *, current: Path | None = None, shared_asset: bool = False) -> str:
    stylesheet = (
        f'  <link rel="stylesheet" href="{esc(rel_href(current, SHARED_STYLESHEET))}">'
        if shared_asset and current is not None
        else f"  <style>{css()}</style>"
    )
    return f"""<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <link rel="icon" href="data:,">
{stylesheet}
</head>"""


def nav(current: Path, lang: str, alternate: Path) -> str:
    return f"""
    <nav class="nav" aria-label="Showcase navigation">
      <a href="{esc(rel_href(current, index_path(lang)))}">{esc(ui(lang, "home"))}</a>
      <a href="{esc(rel_href(current, CATALOG))}">{esc(ui(lang, "catalog"))}</a>
      <a href="{esc(rel_href(current, alternate))}">{esc(ui(lang, "alternate"))}</a>
    </nav>
"""


def control_diagram(case: dict[str, Any], lang: str) -> str:
    table = table_for(case, lang)
    left = localized(case, lang, "title")[:22]
    center = table["loopx_intervention"].replace("、", ",").split(",")[0].strip()[:24] or "LoopX"
    right = str(case.get("status") or "public case").replace("_", " ")[:24]
    return f"""
    <div class="diagram" aria-label="LoopX control-plane sketch">
      <svg viewBox="0 0 760 320" role="img">
        <rect width="760" height="320" fill="#0e0e10"></rect>
        <rect x="285" y="34" width="190" height="58" rx="8" fill="none" stroke="#6e79d6" stroke-width="2"></rect>
        <text x="380" y="68" text-anchor="middle" fill="#f1f2f3" font-size="15" font-weight="600">LoopX</text>
        <line x1="380" y1="92" x2="380" y2="132" stroke="#3a3d42" stroke-width="2"></line>
        <rect x="52" y="132" width="180" height="78" rx="8" fill="none" stroke="#6a6e76" stroke-width="2"></rect>
        <rect x="290" y="124" width="180" height="94" rx="8" fill="#6e79d6" fill-opacity=".16" stroke="#6e79d6" stroke-width="2"></rect>
        <rect x="528" y="132" width="180" height="78" rx="8" fill="none" stroke="#6a6e76" stroke-width="2"></rect>
        <text x="142" y="166" text-anchor="middle" fill="#f1f2f3" font-size="13" font-weight="600">{esc(left)}</text>
        <text x="380" y="163" text-anchor="middle" fill="#f1f2f3" font-size="13" font-weight="600">{esc(center)}</text>
        <text x="618" y="166" text-anchor="middle" fill="#f1f2f3" font-size="13" font-weight="600">{esc(right)}</text>
        <text x="142" y="188" text-anchor="middle" fill="#62666d" font-size="11">goal / trigger</text>
        <text x="380" y="185" text-anchor="middle" fill="#62666d" font-size="11">todo / gate / evidence</text>
        <text x="618" y="188" text-anchor="middle" fill="#62666d" font-size="11">public outcome</text>
        <line x1="232" y1="171" x2="282" y2="171" stroke="#6e79d6" stroke-width="2"></line>
        <polygon points="290,171 280,165 280,177" fill="#6e79d6"></polygon>
        <line x1="470" y1="171" x2="520" y2="171" stroke="#6e79d6" stroke-width="2"></line>
        <polygon points="528,171 518,165 518,177" fill="#6e79d6"></polygon>
        <path d="M618 210 L618 260 L142 260 L142 218" fill="none" stroke="#5a5e65" stroke-width="2"></path>
        <polygon points="142,210 136,221 148,221" fill="#8a8088"></polygon>
      </svg>
    </div>
"""


def evidence_metric_cards(case: dict[str, Any], lang: str) -> list[tuple[str, str]]:
    raw_metrics = case.get("evidence_metrics")
    cards: list[tuple[str, str]] = []
    if not isinstance(raw_metrics, list):
        return cards
    for item in raw_metrics:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        labels = item.get("labels")
        label = ""
        if isinstance(labels, dict):
            label = str(labels.get(lang) or labels.get("en") or "")
        if not label:
            label = str(item.get("label") or "")
        if value is None or not label:
            continue
        cards.append((str(value), label))
    return cards


def metrics(case: dict[str, Any], lang: str) -> str:
    cards = evidence_metric_cards(case, lang)
    if cards:
        return '<div class="metric-grid">' + "".join(
            f'<div class="metric"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>'
            for value, label in cards[:4]
        ) + "</div>"
    workload = case.get("workload_signal")
    if not isinstance(workload, dict):
        return ""
    public_git = workload.get("public_git") if isinstance(workload.get("public_git"), dict) else {}
    whole = workload.get("whole_repository") if isinstance(workload.get("whole_repository"), dict) else {}
    window = workload.get("window") if isinstance(workload.get("window"), dict) else {}
    model = workload.get("efficiency_model") if isinstance(workload.get("efficiency_model"), dict) else {}
    estimated = model.get("estimated_developer_days") if isinstance(model.get("estimated_developer_days"), dict) else {}
    compression = model.get("single_engineer_calendar_compression") if isinstance(model.get("single_engineer_calendar_compression"), dict) else {}
    if public_git.get("merged_commits") is not None:
        cards.append((str(public_git["merged_commits"]), "merged PR commits" if lang == "en" else "merged PR commits"))
    if window.get("hours") is not None:
        cards.append((f"{window['hours']}h", "public window" if lang == "en" else "public window"))
    if whole.get("commit_count") is not None:
        cards.append((str(whole["commit_count"]), "public commits" if lang == "en" else "public commits"))
    if estimated.get("low") and estimated.get("high"):
        cards.append((f"{estimated['low']}-{estimated['high']}d", "AI-assisted baseline" if lang == "en" else "AI-assisted baseline"))
    if compression.get("low") and compression.get("high"):
        cards.append((f"{compression['low']}-{compression['high']}x", "calendar compression" if lang == "en" else "calendar compression"))
    if not cards:
        return ""
    return '<div class="metric-grid">' + "".join(
        f'<div class="metric"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>'
        for value, label in cards[:4]
    ) + "</div>"


def render_case_page(case: dict[str, Any], lang: str, primary: bool) -> str:
    output = case_html_path(case, lang)
    alternate = case_html_path(case, "en" if lang == "zh" else "zh")
    title = localized(case, lang, "title")
    headline = localized(case, lang, "headline")
    table = table_for(case, lang)
    details = details_for(case, lang)
    frontend = case.get("frontend_card") if isinstance(case.get("frontend_card"), dict) else {}
    raw_tags = first_items(frontend.get("badges"), 5) or first_items(case.get("pattern_tags"), 5)
    tags = display_badges(case, lang, raw_tags, 5)
    behavior = [str(item) for item in details.get("mechanism", [])][:8] or first_items(case.get("loopx_behavior"), 6)
    narrative = repo_path(str(case.get("case_page") or ""))
    demo = case.get("demo_command")
    storyboard = case.get("storyboard_path")
    feedback = case.get("feedback_contract_path")
    appendix = "" if primary else f'<span>{esc(ui(lang, "appendix"))}</span>'
    links = [f'<a class="case-link" href="{esc(rel_href(output, narrative))}">{esc(ui(lang, "narrative"))}</a>']
    if isinstance(storyboard, str):
        links.append(f'<a class="case-link" href="{esc(rel_href(output, repo_path(storyboard)))}">Storyboard</a>')
    if isinstance(feedback, str):
        links.append(f'<a class="case-link" href="{esc(rel_href(output, repo_path(feedback)))}">Feedback</a>')
    demo_block = f'<div class="metric"><strong>{esc(ui(lang, "demo"))}</strong><span><code>{esc(demo)}</code></span></div>' if isinstance(demo, str) and demo else ""
    behavior_list = "".join(
        f'<li><span class="flow-num">{index}</span><div class="flow-body"><div class="flow-title">{esc(item)}</div></div></li>'
        for index, item in enumerate(behavior, start=1)
    )
    context_block = render_text_stack([str(item) for item in details.get("context", [])])
    evidence_block = render_evidence_items([(str(label), str(text)) for label, text in details.get("evidence", [])])
    evidence_assets = render_evidence_assets(case, output)
    outcome_block = render_text_stack([str(item) for item in details.get("user_outcome", [])])
    sources_block = render_source_refs([(str(label), str(path)) for label, path in details.get("source_refs", [])], output)
    shared_assets = case.get("page_assets") == "shared"
    diagram = control_diagram(case, lang) if frontend.get("show_control_diagram", True) else ""
    if shared_assets:
        diagram = " ".join(line.strip() for line in diagram.splitlines() if line.strip())
    return f"""<!doctype html>
<html lang="{esc(ui(lang, "html_lang"))}">
{html_head("LoopX Showcase: " + title, current=output, shared_asset=shared_assets)}
<body>
<div class="gh">
  <div class="grain"></div>
  <article>
    {nav(output, lang, alternate)}
    <div class="mlab">{esc(case.get("date") or "")} · {esc(case.get("domain") or "")}</div>
    <h1>{esc(title)}</h1>
    <div class="accent">{esc(headline)}</div>
    <p>{esc(localized(case, lang, "user_value"))}</p>
    <div class="chips">{badges(tags)}{appendix}</div>
    {diagram}

    <div class="section-head"><span>01</span><h2>{esc(ui(lang, "context"))}</h2></div>
    {context_block}

    <div class="section-head"><span>02</span><h2>{esc(ui(lang, "evidence"))}</h2></div>
    <div class="panel">
      <div class="panel-row"><div class="panel-key">{esc(ui(lang, "proof"))}</div><div class="panel-val"><p>{esc(table["proof_point"])}</p></div></div>
      <div class="panel-row"><div class="panel-key">{esc(ui(lang, "intervention"))}</div><div class="panel-val"><p>{esc(table["loopx_intervention"])}</p></div></div>
    </div>
    {metrics(case, lang)}
    {evidence_block}
    {evidence_assets}

    <div class="section-head"><span>03</span><h2>{esc(ui(lang, "behavior"))}</h2></div>
    <ul class="flow">{behavior_list}</ul>

    <div class="section-head"><span>04</span><h2>{esc(ui(lang, "outcome"))}</h2></div>
    {outcome_block}

    <div class="section-head"><span>05</span><h2>{esc(ui(lang, "sources"))}</h2></div>
    {sources_block}
    <div class="boundary-box"><p><strong>{esc(ui(lang, "boundary"))}.</strong> {esc(localized(case, lang, "evidence_boundary"))}</p></div>
    <div class="chips">{''.join(links)}</div>
    {demo_block}
    <footer>{esc(ui(lang, "footer"))}</footer>
  </article>
</div>
</body>
</html>
"""


def index_card(case: dict[str, Any], current: Path, lang: str) -> str:
    output = case_html_path(case, lang)
    title = localized(case, lang, "title")
    headline = localized(case, lang, "headline")
    table = table_for(case, lang)
    tags = display_badges(case, lang, first_items(case.get("pattern_tags"), 4), 2)
    metric_cards = evidence_metric_cards(case, lang)
    metric_terms = [f"{value} {label}" for value, label in metric_cards]
    search = " ".join([title, headline, table["proof_point"], table["loopx_intervention"], *tags, *metric_terms]).lower()
    canonical = f'<span>{esc(ui(lang, "canonical"))}</span>' if str(case.get("id")) == HARDWARE_CASE_ID and lang == "zh" else ""
    metric_line = ""
    if metric_cards:
        metric_line = '<div class="mini-metrics">' + "".join(
            f"<span>{esc(value)} · {esc(label)}</span>" for value, label in metric_cards[:3]
        ) + "</div>"
    return f"""
      <a class="card" href="{esc(rel_href(current, output))}" data-search="{esc(search)}">
        <div class="meta">{esc(case.get("date") or "")} · {esc(case.get("status") or "")}</div>
        <h3>{esc(title)}</h3>
        <p>{esc(headline)}</p>
        <p><strong>{esc(ui(lang, "proof"))}:</strong> {esc(table["proof_point"])}</p>
        {metric_line}
        <div class="chips">{badges(tags)}{canonical}</div>
      </a>
"""


def experimental_lane(lang: str) -> str:
    cards = "\n".join(
        f"""
        <div class="experiment-card">
          <strong>{esc(title)}</strong>
          <span>{esc(output)}</span>
          <em>{esc(value)}</em>
        </div>"""
        for title, output, value in ui(lang, "experimental_rows")
    )
    return f"""
    <section class="experiment" aria-label="{esc(ui(lang, "experimental_title"))}">
      <h3>{esc(ui(lang, "experimental_title"))}</h3>
      <p>{esc(ui(lang, "experimental_intro"))}</p>
      <div class="experiment-grid">{cards}
      </div>
    </section>"""


def render_index(cases: list[dict[str, Any]], lang: str) -> str:
    primary, appendix = ordered_cases(cases)
    current = index_path(lang)
    alternate = index_path("en" if lang == "zh" else "zh")
    primary_cards = "\n".join(index_card(case, current, lang) for case in primary)
    appendix_cards = "\n".join(index_card(case, current, lang) for case in appendix)
    return f"""<!doctype html>
<html lang="{esc(ui(lang, "html_lang"))}">
{html_head("LoopX " + ui(lang, "index_title"))}
<body>
<div class="gh">
  <div class="grain"></div>
  <article>
    {nav(current, lang, alternate)}
    <div class="mlab">LoopX · public-safe case surface</div>
    <h1>{esc(ui(lang, "index_title"))}</h1>
    <div class="accent">{esc(ui(lang, "index_subtitle"))}</div>
    <p>LoopX preserves goals, gates, todos, claims, quota, run history, and evidence outside any single agent session.</p>
    {control_diagram({"title": ui(lang, "index_title"), "status": "public surface", "domain": "showcase catalog", "showcase_table": {"proof_point": ui(lang, "index_subtitle"), "loopx_intervention": "catalog, pages, evidence boundary"}}, lang)}

    <div class="section-head"><span>01</span><h2>{esc(ui(lang, "top_cases"))}</h2></div>
    <input class="search" id="case-search" type="search" placeholder="{esc(ui(lang, "search"))}" aria-label="{esc(ui(lang, "search"))}">
    <div class="cards" data-cards>{primary_cards}</div>
    {experimental_lane(lang)}

    <div class="section-head"><span>02</span><h2>{esc(ui(lang, "appendix"))}</h2></div>
    <div class="cards">{appendix_cards}</div>
    <footer>{esc(ui(lang, "footer"))}</footer>
  </article>
</div>
<script>
const input = document.getElementById('case-search');
const cards = Array.from(document.querySelectorAll('[data-search]'));
input.addEventListener('input', () => {{
  const query = input.value.trim().toLowerCase();
  for (const card of cards) {{
    card.classList.toggle('hide', query && !card.dataset.search.includes(query));
  }}
}});
</script>
</body>
</html>
"""


def update_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    cases = catalog.get("cases")
    if not isinstance(cases, list):
        raise ValueError("catalog must contain cases")
    by_id = {str(case.get("id")): case for case in cases}
    reordered = [by_id.pop(case_id) for case_id in PRIMARY_CASE_ORDER if case_id in by_id]
    reordered.extend(by_id.values())
    for rank, case in enumerate(reordered, start=1):
        zh_path = case_html_path(case, "zh").relative_to(REPO_ROOT).as_posix()
        en_path = case_html_path(case, "en").relative_to(REPO_ROOT).as_posix()
        case["interactive_page"] = zh_path
        case["interactive_page_zh"] = zh_path
        case["interactive_page_en"] = en_path
        case["localized_pages"] = {"zh": zh_path, "en": en_path}
        case_id = str(case.get("id"))
        if case_id in PRIMARY_CASE_ORDER:
            case["showcase_rank"] = rank
            # Catalog-authored cases carry their own proof and intervention.
            # Retain the legacy defaults without duplicating new case copy here.
            table = SHOWCASE_TABLE.get(case_id, case.get("showcase_table"))
            if not isinstance(table, dict):
                raise ValueError(f"primary showcase {case_id} needs a showcase_table")
            case["showcase_table"] = table
            if not isinstance(case.get("frontend_card"), dict):
                metaphor, default_badges = DEFAULT_FRONTEND.get(
                    case_id,
                    ("a LoopX control-plane rail turns ambiguous agent work into reviewable evidence", first_items(case.get("pattern_tags"), 3)),
                )
                case["frontend_card"] = {
                    "visual_metaphor": metaphor,
                    "primary_metric_hint": table["proof_point"],
                    "badges": default_badges,
                    "story_beats": first_items(case.get("loopx_behavior"), 4),
                }
            case.pop("appendix_surface", None)
    catalog["cases"] = reordered
    return catalog


def read_catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean(text), encoding="utf-8")


def generate(write_files: bool) -> list[Path]:
    catalog = update_catalog(read_catalog())
    cases = catalog["cases"]
    primary, appendix = ordered_cases(cases)
    outputs: list[Path] = []
    if write_files:
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write(SHARED_STYLESHEET, css(shared_asset=True))
    outputs.append(SHARED_STYLESHEET)
    for case in primary + appendix:
        for lang in ("zh", "en"):
            output = case_html_path(case, lang)
            outputs.append(output)
            if is_hardware_canonical(case, lang):
                assert_hardware_canonical()
                continue
            if write_files:
                write(output, render_case_page(case, lang, primary=case in primary))
    for lang in ("zh", "en"):
        output = index_path(lang)
        outputs.append(output)
        if write_files:
            write(output, render_index(cases, lang))
    return outputs


def check() -> None:
    catalog = update_catalog(read_catalog())
    expected_catalog = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if CATALOG.read_text(encoding="utf-8") != expected_catalog:
        raise AssertionError("showcase catalog is not normalized; run examples/showcase-html-pages.py")
    expected_stylesheet = clean(css(shared_asset=True))
    if SHARED_STYLESHEET.read_text(encoding="utf-8") != expected_stylesheet:
        raise AssertionError("shared showcase stylesheet is stale; run examples/showcase-html-pages.py")
    cases = catalog["cases"]
    primary, appendix = ordered_cases(cases)
    for case in primary + appendix:
        for lang in ("zh", "en"):
            output = case_html_path(case, lang)
            if is_hardware_canonical(case, lang):
                assert_hardware_canonical()
                continue
            expected = clean(render_case_page(case, lang, primary=case in primary))
            if output.read_text(encoding="utf-8") != expected:
                raise AssertionError(f"{output.relative_to(REPO_ROOT)} is not generated from the catalog")
    for lang in ("zh", "en"):
        output = index_path(lang)
        expected = clean(render_index(cases, lang))
        if output.read_text(encoding="utf-8") != expected:
            raise AssertionError(f"{output.relative_to(REPO_ROOT)} is not generated from the catalog")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify catalog and generated HTML files.")
    args = parser.parse_args()
    if args.check:
        check()
        print("showcase-html-pages check ok")
        return 0
    for output in generate(write_files=True):
        print(output.relative_to(REPO_ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
