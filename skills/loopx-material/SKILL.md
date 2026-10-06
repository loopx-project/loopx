---
name: loopx-material
description: Operate an explicitly activated LoopX Material Lifecycle for a connected project. Use for material-store inventory, lossless migration, candidate/archive transitions, exact-read-backed ranking, ranked-entry rebuilds, bounded Explore intake, owner-gated apply, rollback, and audit. Do not use for ordinary one-off reading or research when the project has not activated Material Lifecycle.
---

# LoopX Material

Use this skill for the lifecycle and authority of a project's durable material
store. Source discovery, domain-specific scoring, and note writing may be
provided by project skills; this skill owns the generic lossless lifecycle.

LoopX ships the canonical source for this skill, but does not install it into
the user's global skill directory. Install a managed copy only in a connected
project that explicitly enables Material Lifecycle:

```bash
loopx project-skill install \
  --project . \
  --skill loopx-material \
  --surface codex \
  --execute
```

Use `--surface claude-code` or `--surface opencode` for those hosts; repeat the
flag to install multiple host-native copies in one transaction. Managed copies
live under `.agents/skills/`, `.claude/skills/`, or `.opencode/skills/` and are
upgraded or removed through the same CLI. Project-local discovery does not
itself activate material-store writes; the selected execution owner still
needs explicit Material Lifecycle source authority.

## Activation Gate

Before changing a material store:

1. Resolve the current project and execution owner. For a Goal route, use the
   existing registered agent/active Todo. For an ordinary project route,
   resolve the current Core project context and accepted request; do not run
   `start-goal` or create a dummy Goal merely to obtain material metadata.
2. Run `loopx project-skill status --project . --skill loopx-material` and
   confirm the required host surfaces are current.
3. Require explicit activation: either the selected Goal Todo targets
   `material_lifecycle`/the Goal declares its source profile, or the project
   source owner declares an active material profile and an existing Core
   workspace write grant. A catalog entry, scope reference or skill copy is
   not activation.
4. Confirm the Goal boundary or Core project write grant covers the exact
   private adapter/authority paths. The project source verifier must bind the
   current caller, audience, store/profile and owner gate, including expiry
   and revocation. Ref names never grant access; enforce the authorization
   fence within staging/publication as well as preflight checks.
5. Run `loopx material-lifecycle architecture --format json` and preserve its
   default-off, owner-gated, provider-neutral boundaries. The project source
   route supports inventory, candidate intake/rollback, rerank packets,
   readable projection and settlement; it does not widen Goal-only migration,
   rebuild or Explore APIs.

If the project-local skill is missing, preview an explicit project install; do
not fall back to a global copy. If activation or authority is missing, stop
before source mutation. Create a bounded setup todo or owner gate; do not
invent a store or treat chat history as authority.

## Ownership Boundary

Keep these responsibilities separate:

- **Project source adapter**: locates and reads the project's private source
  files, database, documents, or provider.
- **Research/reader skill**: recalls candidates, performs exact reads, and
  produces source-quality and domain-value evidence.
- **Decision Context**: supplies revision-bound objectives, changed facts,
  conflicts, and accepted decisions.
- **Material Lifecycle**: owns inventory, migration, lifecycle transitions,
  ranked-entry rebuild, rerank proposals, apply receipts, and rollback.
- **Content or notes workflow**: consumes selected material and produces an
  artifact; it does not rewrite candidate/archive/ranking truth by itself.

## Workflow

### 1. Snapshot And Inventory

Read the source authority before proposing structural change.

- Record source revision, byte/content digest, lifecycle counts, parse errors,
  stable material references, and a verified backup.
- Keep raw content, private paths, URLs, provider payloads, and credentials out
  of public packets and commits.
- Preserve the original source until a verified cutover and rollback rehearsal
  have both succeeded.

No migration, rebuild, or rerank may begin from an unverified partial parse.

### 2. Normalize Lifecycle

Use stable material references across:

```text
candidate -> active -> archived
                 \-> carryover
archived -> active
```

Every transition needs a revisioned evidence or decision reference. Archiving
must preserve the original source reference and an archive reference. Reading,
summarizing, or publishing a note does not implicitly archive a material.

### 3. Promote Exact-Read Evidence

Recall is advisory. Before a material affects ranking or lifecycle:

1. retrieve the candidate through the configured provider or local search;
2. exact-read the authoritative source;
3. record source revision and read scope;
4. reject stale, conflicting, unreadable, or only-secondary claims;
5. pass only promoted evidence into Decision Context or ranking.

Do not start Explore merely because the current list feels incomplete. Explore
begins only from a named evidence gap, bounded query plan, budget, and stop
condition.

### 4. Settle Candidate Ranking

Before reporting candidate intake complete, settle its ranking against the
current Decision Context:

- record exactly one disposition: `top_window`, `ranked_backlog`, or
  `no_change`;
- let the project adapter classify value from exact-read evidence, current
  objectives, overlap, and artifact convertibility rather than tier alone;
- require high-value materials to gain verified membership in the Top-N or
  explicit ranked backlog;
- require a reason for `no_change` and use it only for standard-value or
  substantially duplicative materials;
- keep candidate intake and ranking as separate receipts and, when ranking
  changes, separate authority revisions; then join them with an intake-ranking
  settlement receipt;
- preserve displaced Top-N entries in the ranked backlog and preserve protected
  anchors unless changed Decision Context or explicit owner authority moves
  them.

If ranking apply, readback, projection, or rollback readiness fails, repair or
roll back before reporting the intake as complete.

### 5. Rebuild Ranked Entries

A ranked entry must represent one independently sortable reading or action
unit, not a display bucket.

- Default to at most three primary materials per ranked entry.
- When an entry exceeds the limit, create deterministic child entries and rank
  them independently.
- Do not hide overflow in an unranked supporting index.
- Preserve exact membership: every selected material appears exactly once
  across the ranked set.
- Preserve stable material references and canonical source records.
- Keep an explicit ranked backlog beyond a visible Top-N.

Splitting is semantic, not mechanical. Group materials only when they jointly
support one decision or learning outcome; separate materials whose value,
urgency, reader action, or evidence maturity differs.

### 6. Propose A Bounded Rerank

Rerank from revision-bound Decision Context evidence.

- Protect explicitly pinned entries and the project's declared stable prefix;
  a visible Top-N alone does not declare either protection.
- Limit moved entries and rank displacement unless the owner explicitly
  approves a structural rebuild.
- For one ranked material's evidence-backed move, use
  `plan_material_single_move` with the complete ranked set, including backlog.
  Preserve all other relative order and use its exact interval bounds in the
  existing proposal. Recompute that preview before apply; do not relax the
  constraints of a historical proposal.
- Use `material_rerank_receipt_chunks` when a transition affects more than 100
  refs. Build all existing receipts before CAS and persist all after readback;
  verify complete coverage under the same proposal, revisions and owner gate.
- Distinguish a rank move from lifecycle change and from new candidate intake.
- Emit a no-change proposal when evidence does not justify movement.
- Keep proposal and apply receipt separate.

### 7. Build A Readable Projection

The managed catalog remains authority, but operators need a readable view.
Build that view from exact-read-backed presentation records:

- preserve contiguous ranks and the explicit ranked backlog;
- keep at most three primary materials per entry;
- require every expected selected material exactly once;
- keep project-specific taxonomy, summaries, judgments, links, and language in
  the private project adapter;
- emit a content-free receipt with revision, counts, digest, and verification;
- never treat the readable Markdown as ranking or source authority.

LoopX owns validation, rendering, and the content-free receipt contract. The
project adapter owns source parsing and supplies already-promoted display
records. One-time legacy parsing and migration scripts remain project-local.

### 8. Apply With A Lossless Gate

Preview first. Apply only when all are true:

- source and backup digests still match the inventory;
- parse errors are zero or explicitly owner-approved;
- expected source/canonical counts match;
- ranked coverage and unique membership are exact;
- protected entries remain valid;
- compare-and-swap revision matches;
- rollback has been rehearsed;
- the owner gate authorizes this exact plan.

After apply, read the destination back and write an audited receipt. On any
mismatch, restore the previous authority and record the blocker. Never report
"migration complete" from a preparation plan alone.

## Project Adapter Contract

A project-specific skill or `AGENTS.md` may define:

- source locations and private provider setup;
- topic taxonomy and domain scoring;
- reading tools and source-specific fallbacks;
- user-specific priorities;
- note/archive destinations and public/private redaction;
- concrete backup and restore commands.

It should reference this skill instead of duplicating generic migration,
ranking, rebuild, apply, or rollback rules. Project rules may tighten these
invariants but must not weaken them.

## Completion Evidence

A complete material operation reports:

- source and destination revisions;
- backup and source-digest verification;
- parsed and canonical counts;
- lifecycle-count delta;
- intake ranking disposition and value classification;
- ranked-entry count and maximum primary-member count;
- exact coverage and duplicate count;
- promoted/rejected exact-read evidence;
- proposal and apply receipt references;
- intake-ranking settlement receipt reference;
- rollback result;
- next bounded action or explicit no-change.

## Stop Conditions

Stop without mutating the source when:

- the project or current execution owner is ambiguous;
- Material Lifecycle is not explicitly active for the selected Goal or the
  ordinary project's source profile with its existing Core write grant;
- a project source route attempts Goal-only migration, rebuild or Explore;
- source authority, backup, revision, or stable ids cannot be verified;
- exact-read evidence conflicts with the proposed change;
- overflow would be hidden rather than independently ranked;
- a migration would lose bytes, records, references, or recoverability;
- the required owner gate, write scope, or rollback path is absent;
- public output would expose private material or credentials.
