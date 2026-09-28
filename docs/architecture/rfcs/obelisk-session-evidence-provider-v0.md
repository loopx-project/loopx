# RFC: Obelisk Session Evidence Provider v0

- Status: Accepted
- Supersedes / closes: none
- Date: 2026-09-01
- Tracking issue: [#3792](https://github.com/huangruiteng/loopx/issues/3792)
- Decision boundary: whether and how LoopX may retrieve prior agent-session history as bounded evidence for Replan and Turn admission
- Capability owner: existing `agent-turn-recall`
- Provider id: proposed optional `obelisk-session-evidence`
- Delivery boundary: independently installed extension or package; disabled by default

## 0. Summary

LoopX should evaluate [Obelisk](https://github.com/tommy0103/obelisk) as an
optional, read-only historical-evidence provider for Replan cold paths.

The valuable product seam is not "search every past transcript before every
Turn." It is a narrower question asked when current control state is
insufficient:

> Has this same agent already tried this route in the same project, what
> happened, and is the proposed next step materially new?

The provider may supply bounded, provenance-bearing historical observations.
LoopX remains authoritative for scope, admission, Replan semantics, Todo state,
quota, evidence acceptance, and action authority.

```text
typed Replan situation
  -> LoopX retrieval policy and scope
  -> optional Obelisk adapter
  -> bounded historical-evidence result
  -> public-safe retrieval receipt
  -> existing Replan reasoning and settlement
```

Retrieval is default-off, fail-open for the work lane, and unable to satisfy a
Replan obligation by itself. The first pilot is same-agent,
same-project/repository, recent or revision-aware, and read-only.

## 1. Motivation

Replan protects long-running work from repeating an unchanged plan. Its typed
contract can require a new surface, hypothesis, probe, grounded successor,
blocker, or coverage-backed exhaustion. However, the current Turn packet may
only contain recent LoopX outcomes. Earlier Codex or other agent sessions can
contain relevant attempts that were never converted into canonical LoopX
evidence.

This creates three practical gaps:

1. **Repeated dead ends:** an agent retries an old command, file path, or
   hypothesis because the failure lived in an earlier session.
2. **False novelty:** a successor looks new in the current packet but repeats
   prior work.
3. **Handoff loss:** a resumed or replacement agent sees durable control state
   but lacks the bounded historical observation needed to understand a past
   decision.

Obelisk indexes local Claude Code, Codex, Kimi, Pi, and DSH histories into
SQLite/FTS5. It supports session, message, tool, file, subagent, and workflow
queries. This makes it a plausible provider for the missing historical
observation, but not a new source of control-plane truth.

## 2. Empirical boundary

Obelisk's public evaluations argue for a constrained product, not broad memory
injection:

- [Obelisk issue #39](https://github.com/tommy0103/obelisk/issues/39) reports
  strong conversational and temporal retrieval and a reduction in repeated
  dead-end commands from 6.7% to 0.5%. It also reports 25–51k characters of
  retrieved context per query and no statistically significant SWE retry
  improvement from raw coding trajectories.
- [Obelisk issue #46](https://github.com/tommy0103/obelisk/issues/46) reports no
  measurable benefit from foreign-agent cross-task archives. The promising
  signal came from an agent's own longitudinal, same-repository history, with
  improved efficiency and quality indicators.

The pilot therefore optimizes first for **avoided repetition and lower
reconstruction cost**, not an assumed task-success uplift. Raw history must be
reduced before it enters a Turn.

## 3. Placement decision

This proposal does not add an `obelisk`, `session-database`, or generic
`retrieval` capability.

The capability owner is the existing `agent-turn-recall` boundary because its
caller outcome is already "prepare bounded prior guidance or context for an
autonomous Turn." The Replan path contributes a typed situation and retrieval
policy; it does not create a second memory lifecycle.

The proposed provider id is `obelisk-session-evidence`. It is delivered as an
optional extension/package because:

- Obelisk is not required for core LoopX behavior;
- it has its own Node.js runtime, index, CLI, upgrade, and failure lifecycle;
- users must explicitly opt into indexing local session history; and
- Obelisk is AGPL-3.0-only while LoopX is Apache-2.0.

LoopX MUST NOT vendor, statically link, translate, or copy Obelisk code into
core. The integration invokes an independently installed, unmodified Obelisk
process through a documented protocol. Packaging and license review remain a
promotion gate; this RFC is not legal advice.

### 3.1 Relationship to adjacent capabilities

| Surface | Owns | Does not become |
| --- | --- | --- |
| Replan | obligation, semantic novelty, successor or exhaustion | a transcript search engine |
| Agent Turn Recall | situation, admission, bounded recalled context | a durable memory store |
| Obelisk provider | read-only search over indexed local sessions | action or evidence authority |
| Reward Memory | reviewed memory lifecycle and utility | a raw transcript archive |
| Explore | research frontier, coverage, closure | an implicit session-history importer |
| OpenViking adapter | scoped context-provider retrieval | interchangeable with unreviewed session evidence |

Obelisk hits are historical observations. They are not
`reward_memory_active_record_v0` records and MUST NOT be presented as reviewed
Reward Memory. A later second caller, such as Decision Context or Explore, may
reuse the provider-neutral historical-evidence contract only after a concrete
need and independent admission policy exist.

### 3.2 Cross-session continuation caller

The [capable-manager/semantic-handoff RFC §5.7](capable-manager-semantic-handoff-v0.md#57-session-and-product-continuity) proposes an explicit restoration caller for `resume_or_handoff_gap`. It first restores current work and the available deliberate continuation brief, including [Stage A](cross-session-memory-substrate-v0.md) context where applicable, then requests only missing decision evidence. This is a proposed integration, not a shipped caller or a promotion of this provider. Same-Agent replacement can satisfy the initial scope; a different receiver cannot search the source Agent's archive by inheriting a handoff. Authorized source excerpts may travel in the brief without granting archive search. The provider stages, default-off behavior, scope, private result/public receipt boundary and qualification requirements below remain unchanged. Basic continuation must work without Obelisk.

## 4. Admission policy

The provider is not called on every Turn. A typed policy admits retrieval only
for one of these situations:

1. `replan_repetition`: two or more materially unchanged attempts or an
   explicit repeated-route signal;
2. `hypothesis_history_gap`: current hypotheses are exhausted but prior-session
   coverage is unknown;
3. `resume_or_handoff_gap`: durable state identifies the work but the earlier
   decision or failure evidence is missing;
4. `operator_requested_history`: the operator explicitly requests historical
   evidence.

The default scope is:

- the same `agent_id`;
- the same LoopX project and repository identity;
- recent sessions or sessions relevant to the current repository revision;
- explicit evidence types and a bounded result count/size.

Cross-agent, cross-project, and foreign archives are denied by default. An
extension enablement flag alone MUST NOT broaden these scopes. Each broader
scope requires separate configuration, privacy disclosure, and qualification.

Provider unavailability, an incomplete index, or zero hits produces a typed
empty result. It does not create a user gate and does not prevent the agent from
continuing with current evidence.

## 5. Proposed typed contract

The TypeScript control-plane boundary should own the schema, admission reducer,
size limits, and receipt projection. Python may invoke or adapt the provider but
must not recreate policy or Replan semantics.

### 5.1 Request

```ts
type HistoricalEvidenceRequestV0 = {
  schema_version: "historical_evidence_request_v0";
  request_id: string;
  situation_id: string;
  trigger:
    | "replan_repetition"
    | "hypothesis_history_gap"
    | "resume_or_handoff_gap"
    | "operator_requested_history";
  scope: {
    agent_id: string;
    project_id: string;
    repository_id: string;
    allow_cross_agent: false;
    allow_cross_project: false;
  };
  revision?: { current: string; merge_base?: string };
  evidence_types: Array<
    "prior_failure" | "decision" | "file_history" | "tool_outcome" | "summary"
  >;
  query_terms: string[];
  limits: {
    max_sessions: number;
    max_items: number;
    max_snippet_chars: number;
    max_total_chars: number;
  };
};
```

`query_terms` are bounded search terms derived from canonical situation fields,
not raw chat history. The adapter MUST escape them as data and MUST NOT generate
arbitrary JavaScript from model output.

### 5.2 Private result

```ts
type HistoricalEvidenceResultV0 = {
  schema_version: "historical_evidence_result_v0";
  request_id: string;
  provider_id: "obelisk-session-evidence";
  provider_version: string;
  index_revision: string;
  scope_applied: {
    agent_id: string;
    project_id: string;
    repository_id: string;
    cross_agent_used: false;
  };
  freshness: "fresh" | "stale" | "incomplete" | "unknown";
  items: Array<{
    session_ref: string;
    message_ref?: string;
    evidence_type:
      | "prior_failure" | "decision" | "file_history" | "tool_outcome" | "summary";
    observed_at?: string;
    revision_ref?: string;
    snippet: string;
  }>;
  truncated: boolean;
  omitted_count: number;
};
```

The private result may be consumed only inside the admitted Turn. It is not
copied into status, Todo metadata, public evidence, PR text, or logs. Results
must be capped before prompt construction; truncation is explicit.

### 5.3 Public-safe receipt

```ts
type HistoricalEvidenceReceiptV0 = {
  schema_version: "historical_evidence_receipt_v0";
  request_id: string;
  provider_id: "obelisk-session-evidence";
  provider_version: string;
  index_revision_digest: string;
  scope_digest: string;
  query_digest: string;
  result_digest: string;
  result_count: number;
  evidence_type_counts: Record<string, number>;
  freshness: "fresh" | "stale" | "incomplete" | "unknown";
  truncated: boolean;
  omitted_count: number;
  grants_new_action_authority: false;
  external_writes_performed: false;
  raw_content_captured: false;
};
```

Opaque session and message references may stay in owner-local private state for
debugging and replay. The public receipt contains only content-free digests and
counts. A receipt proves that a scoped lookup occurred; it does not prove a
historical claim true or a Replan obligation complete.

## 6. Provider protocol and failure behavior

Obelisk currently exposes free-form JavaScript query scripts and read-only SQL,
not a stable bounded JSON request envelope. The preferred integration is an
upstream structured JSON query interface that accepts explicit filters and
limits and rejects unknown options.

An MVP MAY use one fixed, versioned query template if all values are escaped as
data, all supported options are allowlisted, final scope is verified from the
returned rows, and output is capped before parsing. It MUST NOT execute
model-authored JavaScript or SQL.

The adapter fails closed on scope and parsing:

- unknown request fields or provider options: reject;
- missing agent/project/repository filter: reject;
- returned row outside the requested scope: reject the entire result;
- stale/incomplete index: return explicit freshness, never "no history";
- timeout, malformed output, or provider absence: typed empty failure;
- oversize output: terminate, return truncation/failure metadata, and do not
  leak partial raw output into public state.

These requirements directly address known upstream risks: missing CI coverage
([#34](https://github.com/tommy0103/obelisk/issues/34)), fail-open unsupported
options ([#94](https://github.com/tommy0103/obelisk/issues/94)), indexing cost
([#75](https://github.com/tommy0103/obelisk/issues/75),
[#105](https://github.com/tommy0103/obelisk/issues/105)), tokenizer false
positives ([#76](https://github.com/tommy0103/obelisk/issues/76)), and stale
same-mtime Codex updates ([#104](https://github.com/tommy0103/obelisk/issues/104)).

## 7. Replan integration

Historical retrieval refines reasoning; it does not add a Replan terminal
state.

```text
Replan required
  -> classify current coverage and history gap
  -> if admitted, retrieve bounded same-scope history
  -> compare proposed route with historical observations
  -> produce an existing legal Replan outcome
```

The legal outcome still needs one of the existing semantic advances:

- a new surface;
- a new or revised hypothesis;
- a new falsifiable probe;
- a grounded runnable successor;
- a newly evidenced blocker; or
- coverage-backed exhaustion with no follow-up.

"Obelisk returned results" and "Obelisk returned no results" are not legal
completion rationales. Absence from an incomplete or stale index is never
evidence that no prior attempt exists.

## 8. Product lifecycle

The following commands are proposed UX, not current shipped CLI:

```bash
# Install the independent provider distribution.
loopx extension install obelisk-session-evidence

# Explicitly enable it for one agent/project surface.
loopx capability enable agent-turn-recall \
  --provider obelisk-session-evidence \
  --surface replan-history \
  --agent-id <agent-id> \
  --project-id <project-id>

# Read back configuration, provider version, index freshness, and allowed scope.
loopx capability status agent-turn-recall \
  --provider obelisk-session-evidence \
  --project-id <project-id> \
  --format json

# Disable without deleting the user's Obelisk index.
loopx capability disable agent-turn-recall \
  --provider obelisk-session-evidence \
  --project-id <project-id>

# Uninstall only the adapter distribution; external Obelisk data remains
# governed by Obelisk and must not be silently deleted by LoopX.
loopx extension uninstall obelisk-session-evidence
```

Enablement grants read access only to the configured historical scope. It does
not grant action authority, external write authority, cross-agent access,
memory publication, Todo mutation, or deletion of Obelisk data.

## 9. Delivery stages

### Stage 0: offline qualification

- Build matched fixtures from public-safe or synthetic same-repository
  histories.
- Measure repeated-route detection, false matches, result size, latency,
  freshness, and scope enforcement.
- Establish no-Obelisk and current-context baselines.

No product registration or automatic Replan call occurs in this stage.

### Stage 1: operator-invoked read-only adapter

- Ship the optional provider package with explicit install and enablement.
- Support `operator_requested_history` only.
- Emit bounded private results and content-free receipts.
- Qualify timeout, stale index, unsupported option, and oversize behavior.

### Stage 2: typed Replan cold-path admission

- Admit `replan_repetition`, `hypothesis_history_gap`, and
  `resume_or_handoff_gap` only after matched evidence.
- Preserve default-off configuration and same-agent/same-project scope.
- Add Replan comparison evidence without creating a new settlement state.

### Stage 3: promotion decision

Promotion requires a measured reduction in repeated dead ends or reconstruction
cost, bounded context and latency, no task-success or evidence-quality
regression, and no silent scope broadening. Cross-agent retrieval, automatic
memory writes, and additional callers require separate RFC decisions.

## 10. Validation and no-go criteria

The evaluation should compare identical Replan cases under:

1. current LoopX context only;
2. Obelisk retrieval admitted by the typed policy; and
3. where useful, full native history as a cost ceiling rather than a product
   default.

Required measures include:

- repeated dead-end action rate;
- time, provider calls, and tokens to a grounded successor;
- task success and evidence quality;
- retrieval precision for same-route and prior-failure questions;
- private context size and truncation rate;
- query and indexing latency;
- stale/incomplete index detection;
- cross-scope rejection and unknown-option failure behavior.

Do not promote the provider if it shows no measurable repetition or efficiency
benefit, harms success/evidence quality, cannot bound results reliably, or can
silently broaden search scope. A positive anecdote is not enough.

## 11. Privacy, authority, and security boundary

Session histories can contain source code, prompts, identities, local paths,
secrets, and private organizational context. The provider therefore remains
owner-local by default.

- LoopX stores no raw transcript in canonical public state.
- Public projections contain only digests, counts, freshness, truncation, and
  provider metadata.
- Query terms and snippets are private ephemeral Turn context.
- Provider subprocess output is never copied wholesale into logs or errors.
- Credentials are neither requested nor transferred by this contract.
- Index deletion, retention, and import remain under Obelisk/user authority.
- Retrieval cannot grant permission, satisfy a user gate, or authorize a new
  external effect.

The adapter must scan provider output for boundedness and scope, but content
filtering is not a substitute for correct authorization. Cross-agent retrieval
is a distinct privacy capability, not an incidental query flag.

## 12. Non-goals

This RFC does not:

- make Obelisk required or built into LoopX;
- query history on every Turn;
- replace Replan, Explore, Reward Memory, OpenViking, or canonical goal state;
- infer authoritative evidence from unreviewed transcript text;
- write, summarize, or promote memories automatically;
- expose a general model-authored SQL or JavaScript execution surface;
- copy Obelisk's AGPL implementation into LoopX;
- promise cross-agent or cross-project learning; or
- define a universal retrieval abstraction before a second real caller exists.

## 13. Open questions

1. Can Obelisk expose a stable structured query envelope with strict unknown
   option rejection and explicit index revision/freshness?
2. What repository identity is stable across worktrees, forks, and renamed
   remotes without exposing private paths?
3. Which revision-window policy best balances long-term recall with stale
   history?
4. Should Stage 1 live in the LoopX monorepo `packages/` root or an independent
   provider repository after license and release-lifecycle review?
5. What matched Replan corpus is sufficient to distinguish avoided repetition
   from general model variance?
