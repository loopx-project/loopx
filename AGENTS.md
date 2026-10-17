# Agent Instructions

## What LoopX Does

LoopX is a local-first, runtime-neutral control plane for long-horizon agents
and personal agent teams. The Agent host supplies models and tools; LoopX keeps
goals, decisions, work ownership and evidence durable across sessions and hosts.
核心目标：让 Agent 围绕长期目标持续推进，协作、恢复，并交付可验证的结果。

- **Goals and work:** durable Goals, Todos, dependencies, acceptance and recovery.
- **Governed continuation:** heartbeat, scheduling, quota and explicit stop/wait
  conditions decide when authorized work can continue.
- **Peer collaboration:** claims, leases, scoped handoffs, dependent artifacts,
  receiver adoption and result return; a coordinator gains no implicit authority.
- **Personal Workspace:** App/frontend, Lark and CLI expose conversations,
  settings, work, decisions, artifacts and readback over the same typed owners.
- **Capabilities and extensions:** reusable engineering, research, knowledge,
  content and office workflows with explicit provider and permission boundaries.

Start with [README](README.md), the [overall roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md),
the [capability catalog](loopx/capabilities/README.md) and [stable protocols](docs/reference/protocols/README.md).
The roadmap distinguishes shipped boundaries from acceptance still to qualify.
Core rules live in `loopx/control_plane/`; capability contracts in
`loopx/capabilities/`; extension lifecycle in `loopx/extensions/`; optional
distributions in `packages/`; product surfaces in `apps/`.

## How To Use These Instructions

This file is the shared entry point for Codex and Claude Code. `CLAUDE.md`
imports it; maintain the rules here, with detailed procedures in the linked
documents and existing skills. Before an action, **read and follow its linked
section below**. These are required task-specific instructions, not optional
background. Read referenced skills when their workflow applies.

Keep this entry point under 200 lines and 12 KiB, leaving room within Codex's
default 32 KiB project-instruction budget. Do not automatically import the
detail documents. For host loading, verification and instruction maintenance,
read [instruction loading](docs/development/agent-delivery.md#instruction-loading-and-maintenance).

## Goal-Oriented Development

Resolve the requested outcome from current user direction, accepted contracts
or a demonstrated bug. Check latest target `main`, related PRs and canonical
Todos. Carry goal/source, gap, observable result, owning boundary and decisive
evidence through one complete, reviewable, reversible slice. Continue through
integration, negative cases and readback while authorized work remains feasible.
Counts of edits, tests, messages or merged PRs do not establish acceptance.
Reconcile task/RFC checkpoints when the boundary changes; distinguish passed,
failed and untested. Follow [goal-oriented development](docs/development/agent-delivery.md#goal-oriented-development).

## Commit And PR Hygiene

### Worktree And PR Gate

Beyond trivial typos, use a dedicated clean worktree on a `codex/` branch from
the latest remote target branch; preserve dirty work and unrelated artifacts.
Every tracked change goes through a branch and PR; only an explicit local-only
request skips this route. Before staging or pushing, read the complete
[worktree and submission policy](docs/development/agent-delivery.md#commit-and-pr-hygiene),
including explicit path staging, privacy scans, merge authorization and
self-merge limits. Green checks or a published review do not grant merge authority.

### Author Submission And Review Entry

Submit completed proposals as normal PRs; Draft means concrete unfinished
author work. Keep exact-head review, validation, presentation evidence and merge
authority separate. Follow [author submission](docs/development/agent-delivery.md#author-submission-and-review-entry).

### DCO Sign-Off

Every PR commit needs `git commit -s`. Follow [DCO](docs/development/agent-delivery.md#dco-sign-off).

### Bilingual RFC Diff Gate

Existing English/Chinese RFC pairs, including ledgers, change together. Run the
staged and committed bilingual checks against the actual PR base; failure holds
merge. Follow [RFC and staging checks](docs/development/agent-delivery.md#bilingual-rfc-diff-gate).

### Personal Feishu Release Guide

Named releases require a verified personal-account illustrated guide and
published readback. Follow [release communication](docs/development/agent-delivery.md#personal-feishu-release-guide).

### Release Contributor Attribution

Preserve bilingual community credit from the actual tag range and merged PRs.
Follow [attribution](docs/development/agent-delivery.md#release-contributor-attribution).

### Release Capability Usage Gate

Teach activation, runnable validation, rollback and authority/privacy boundaries
in both languages; validate the final published body. Follow the complete
[release usage gate](docs/development/agent-delivery.md#release-capability-usage-gate).

## First-Screen Review Evidence

Inspect public first viewports and put current public-safe visual evidence in
the PR description. Preserve scoped approval and actual reserved decisions;
follow the current [first-screen policy](docs/development/agent-delivery.md#first-screen-review-evidence)
before submitting or updating a PR that changes a prominent entry point.

## Product Delivery Completeness

Identify frontend/Lark/CLI companions and existing configuration editors. New
caller-facing capabilities need a discoverable, operable frontend journey with
error/recovery states and real packaged backend readback. Explicitly report a
staged prerequisite as partial. Follow [product delivery](docs/development/agent-delivery.md#product-delivery-completeness).

## UI Design Standard

Before changing or reproducing any LoopX UI, read [design.md](docs/development/design.md)
and the [UI workflow](docs/development/agent-delivery.md#ui-design-standard).
Production interactions also follow [frontend delivery](docs/development/frontend-delivery.md).

## Public And Private Boundary

Keep private organizational context, credentials, local paths and raw evidence
out of public code, docs, fixtures, screenshots, PRs and reviews. Scan before
staging; if already pushed, stop normal delivery and self-repair. Follow the
[privacy procedure](docs/development/agent-delivery.md#public-and-private-boundary).

## PR Review Comments

Use [loopx-pr-review](skills/loopx-pr-review/SKILL.md) for PR review and
[loopx-pr-merge](skills/loopx-pr-merge/SKILL.md) for approval/merge. Validate and
publish actionable findings on the PR unless local-only or sensitive; follow
the complete [review lenses and publishing rules](docs/development/agent-delivery.md#pr-review-comments).

## Engineering Quality And Right-Sized Scope

### TypeScript-First Implementation

Reuse the established typed TypeScript decision owner for generic control-plane
rules. Python may provide adapters or specialized providers with a placement
rationale, without a second source of truth. Follow [language placement](docs/development/agent-engineering.md#typescript-first-implementation).

### Source-Checkout Python Entry Points

Validate from the intended worktree using `uv run --extra test python ...` or
`uv run --extra test loopx ...`. Preserve interpreter/source provenance and
`sys.executable` subprocesses. Follow [environment rules](docs/development/agent-engineering.md#source-checkout-python-entry-points).

### Evidence-Based Budget Decisions

Compare the same base/head workload before changing budgets. Agent-consumed
prose is behavior: preserve obligations, modality, ordering and stop conditions.
Follow [budget decisions](docs/development/agent-engineering.md#evidence-based-budget-decisions).

### Refactor Real-Path Validation

Validate the affected real entrypoint/backend in isolation; PostgreSQL authority
changes require a real isolated PostgreSQL suite. Never test on active Goal
authority. Missing required evidence holds delivery. Follow [real-path validation](docs/development/agent-engineering.md#refactor-real-path-validation).

### Bounded Future-Facing Refactoring

During development and review, consider a small related behavior-preserving
refactor; apply with parity evidence, or record why deferred/unnecessary.
Follow [bounded refactoring](docs/development/agent-engineering.md#bounded-future-facing-refactoring).

## Capability And Extension Placement

Before adding code or contracts, identify the existing capability/provider
owner and real caller. Reuse typed semantics; keep core rules domain-neutral,
disclose default changes and prove feature-off parity. New vocabulary needs an
ownership decision and the diff-scoped semantic advisory before full-tree
checks. Read the complete [placement and scope-fit checklist](docs/development/agent-engineering.md#capability-and-extension-placement).

## Automation And Monitor Todos

Keep recurring watches in canonical `continuous_monitor` Todos; heartbeat
prompts discover work generically. Follow [monitor ownership](docs/development/agent-operations.md#automation-and-monitor-todos).

## Projection Sink Design

Build displays from bounded, audience-safe LoopX projections with stable ids,
evidence, gates and row lineage. Follow [projection boundaries](docs/development/agent-operations.md#projection-sink-design).

## Smoke Retention Policy

Retain durable public-behavior checks; keep temporary/private experiment
evidence out. Follow [smoke retention](docs/development/agent-engineering.md#smoke-retention-policy).

## LoopX Self-Repair

For surprising, contradictory or repeatedly insufficient behavior, use
[loopx-self-repair](skills/loopx-self-repair/SKILL.md) and repair the owning rule,
projection or regression; preserve gates. Follow [self-repair](docs/development/agent-operations.md#loopx-self-repair).

## Benchmark Smoke Classification

Keep active toolkit contracts separate from experiment runners, scoring and
dated evidence. Before benchmark cleanup or review, follow
[benchmark classification](docs/development/agent-operations.md#benchmark-smoke-classification)
and [archive placement](benchmark/README.md).
