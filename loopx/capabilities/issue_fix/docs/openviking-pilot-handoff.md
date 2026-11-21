# OpenViking Issue-Fix Pilot Handoff

This plan starts one real OpenViking issue-fix agent without treating repository
memory or an expert bot as an oracle. The target is a reviewable maintainer
outcome: a focused fix PR, a useful public comment draft, or a justified triage
decision, followed by CI and review monitoring.

## What Makes VikingBot Repository-Aware

VikingBot does not rely on a special OpenViking-trained model. Its repository
awareness comes from composition:

1. [ContextBuilder](https://github.com/volcengine/OpenViking/blob/main/bot/vikingbot/agent/context.py)
   loads stable bootstrap files, skills, peer profiles, and relevant memory for
   the current message.
2. [MemoryStore](https://github.com/volcengine/OpenViking/blob/main/bot/vikingbot/agent/memory.py)
   separates facts, cases, reusable experiences, and diagnostic trajectories,
   then retrieves them with bounded quotas.
3. [VikingBot's README](https://github.com/volcengine/OpenViking/blob/main/bot/README.md)
   exposes OpenViking read, search, grep, glob, resource-add, and memory-commit
   tools and describes experience recall around write operations.
4. Repository-owned sources already encode important development knowledge:
   [CONTRIBUTING.md](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING.md)
   maps components to maintainers, while
   [.pr_agent.toml](https://github.com/volcengine/OpenViking/blob/main/.pr_agent.toml)
   captures review invariants and project-specific risks.

The practical lesson is to reproduce this composition, not to copy a large
prompt. A LoopX issue-fix agent should retrieve only the sources relevant to the
current issue, pin them to a repository revision, and preserve their provenance
in feasibility domain state.

## Use Order

Use repository evidence first, memory second, and expert consultation third:

1. Read repository policy, architecture, nearby code, tests, and recent related
   fixes at the current revision.
2. Retrieve compact prior lessons from OpenViking when they can narrow a route,
   repro, or validation choice. Verify every retrieved claim against the current
   checkout.
3. Ask VikingBot a targeted question only when architecture, ownership, repro,
   or validation remains uncertain. Store a compact conclusion and source ref,
   not the raw response. Verify the conclusion locally before patching.

An expert answer never supplies publication authority. External comments, PR
creation, merge, and other writes retain their existing LoopX gates.

## Existing Codex Memory Bridge

OpenViking already ships an
[OpenViking Memory plugin for Codex](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin).
It provides two useful paths:

- lifecycle hooks recall relevant memory on `UserPromptSubmit`, append new
  turns on `Stop`, commit before `PreCompact`, and recover orphaned sessions on
  later `SessionStart` events;
- a local stdio MCP proxy exposes OpenViking `search`, `store`, `read`, `list`,
  `grep`, `glob`, `forget`, `add_resource`, and `health` tools.

This is the fastest bridge for a real Codex pilot, but it is not read-only by
default. Its hooks may capture transcripts and compact tool-call/result text
into the memory system. Do not enable it silently as part of LoopX startup.
For the first issue, prefer explicit MCP `search` and `read` against a
public-only repository namespace. Enable automatic capture only after the owner
reviews the hooks and approves the memory boundary, workspace/peer isolation,
and credential source. LoopX still stores only compact memory refs, trust,
freshness, and verification results; OpenViking owns the memory body and
credentials.

## Immediate Launch

Start with one issue that has a bounded suspected surface and a focused test or
reproduction path. Prepare a compact context file from the current checkout:

```json
{
  "schema_version": "issue_fix_repository_context_input_v0",
  "repository_revision": "<current-commit>",
  "sources": [
    {
      "source_id": "contributing",
      "source_kind": "repository_policy",
      "reference": "CONTRIBUTING.md",
      "trust": "authoritative",
      "freshness": "current",
      "supports": ["change_scope", "ownership"]
    },
    {
      "source_id": "focused-tests",
      "source_kind": "test_surface",
      "reference": "<repo-relative-test-path>",
      "trust": "verified",
      "freshness": "current",
      "supports": ["reproduction", "validation"]
    }
  ]
}
```

Then run the existing workflow and feasibility surfaces:

```bash
loopx issue-fix workflow-plan \
  --url <public-github-issue-url> \
  --repo-path <approved-openviking-checkout> \
  --repository-context-json repository-context.json \
  --validation-label "<focused-validation-label>" \
  --format json

loopx issue-fix feasibility \
  --url <public-github-issue-url> \
  --reproduction-status planned \
  --reproduction-label "<focused-repro-plan>" \
  --scope-class bounded \
  --validation-label "<focused-validation-label>" \
  --repository-context-json repository-context.json \
  --goal-id <pilot-goal-id> \
  --format json
```

The feasibility command writes the compact context projection into the normal
issue-fix domain-state row by default. It does not create a second context
ledger or another workflow state.

## Short Term

- Run one issue at a time through `fix_pr`, `comment_only`, or `triage_only`.
- Pin every context packet to the checkout revision.
- Require grounded change-scope, reproduction, and validation evidence before
  treating repository context as strong confidence.
- Prefer explicit OpenViking MCP search/read for prior issue and validation
  lessons; automatic Codex capture remains an owner-approved opt-in.
- Consult VikingBot only for a specific unresolved aspect, then verify locally.
- After a PR exists, keep the existing lifecycle monitor responsible for CI,
  review, stale branch, merge, and close transitions.

## Medium Term

- Project the existing Codex memory plugin's retrieval and write events through
  explicit capability and authority checks; retain compact refs in LoopX, not
  memory bodies.
- Add controlled writeback after validated outcomes. Store distilled reusable
  facts with repository revision, provenance, freshness, and supersession.
- Add a read-only expert connector for VikingBot with targeted questions,
  timeout/failure behavior, and a mandatory repository-verification result.
- Convert CI failures, review corrections, rejected PRs, and merged outcomes
  into successor todos and reusable issue-fix lessons.

## Long Term

- Build revision-aware repository knowledge that can supersede stale concepts
  and distinguish stable architecture from issue-local observations.
- Rank issue candidates using reproducibility, validation cost, maintainer
  activity, expected scope, and permission risk.
- Compare agents with and without accumulated repository knowledge on time to
  first valid repro, first-review acceptance, rework rounds, and stranded PRs.
- Add import/export for mature interchange formats without coupling runtime
  decisions to a document layout.

## Open Knowledge Format

Google announced the
[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
as a portable Markdown and YAML-frontmatter knowledge bundle. The current
[OKF v0.1 specification](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
is explicitly a draft: concept identity is path-based, Markdown links form the
graph, and `index.md` and `log.md` support progressive disclosure and history.

This helps LoopX as an interchange and export shape for repository knowledge.
The near-term contract therefore accepts a generic
`knowledge_bundle` source ref and remains format-agnostic. Add a concrete OKF
importer only when a live producer and consumer need it and the draft has
stabilized enough to justify compatibility work.

## Pilot Success Signals

- time from issue intake to a named reproduction path;
- percentage of fix routes with grounded validation evidence;
- expert answers verified or rejected against repository sources;
- PR review rounds and CI recovery time;
- duplicate repository reads avoided across issues;
- stale memory detected before it influences a patch;
- issue-fix loops that end in merge, useful comment, or explicit no-follow-up
  instead of silent monitor-only drift.
