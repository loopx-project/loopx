# Semantic preferences

For the built-in OpenViking project-scoped adapter, see
[OpenViking project peer provider](docs/openviking-project-peer.md).

LoopX supports explicit local Agent preferences and optional provider recall.
They have different update rules: explicit preferences use the lifecycle below;
provider-owned experiences keep their existing provider and application contract.

LoopX can optionally recall semantic preferences before a domain action and
build a compact application receipt afterwards. The hook is deliberately thin:
the provider owns storage, ranking, and semantic content; the caller owns how a
preference affects its output and writes the receipt through existing LoopX
evidence or state surfaces.

The external recall hook is disabled unless a caller supplies an enabled local-private JSON
config. Config files inside a git project must be ignored; tracked configs are
rejected. LoopX never copies the provider command, config path, recalled
semantic content, or raw provider errors into receipts.

The preferred provider path is an explicitly activated extension. The
compatibility path still accepts a direct subprocess `argv`; both paths use the
same core request, response, failure-policy, and receipt contracts.

## Module-owned surfaces

`surfaces` is a mapping keyed by arbitrary module-qualified ids. The runtime
does not branch on `issue_fix`, `content_ops`, or any other domain name.

```json
{
  "schema_version": "semantic_preference_hook_config_v0",
  "enabled": true,
  "provider": {
    "id": "local_memory",
    "args": ["--project", "."]
  },
  "surfaces": {
    "issue_fix.pr_description": {
      "query": "PR description structure and reviewer language preferences"
    },
    "content_ops.draft_language": {
      "query": "Draft language and section preferences",
      "limit": 3
    }
  }
}
```

LoopX resolves the installed provider from the `semantic-preference` capability
and `semantic_preference_provider_v0` protocol in runtime state. `args` are
appended after the manifest-owned entrypoint arguments. The manifest owns
protocol, permission, timeout, and doctor; config cannot override them.
`extension_id` remains an optional compatibility selector when migrating an
existing config or disambiguating multiple installed implementations.
`extension_state_file` is an optional local-private override for tests or
specialized embeddings; the CLI's global `--runtime-root` selects the normal
isolated runtime. If an activated extension is later disabled or unavailable,
recall follows the surface's existing `fail_open` or `fail_closed` policy.

For a legacy provider that has not adopted the extension manifest, replace the
provider object with:

```json
{
  "id": "local_memory",
  "argv": ["semantic-preference-provider"],
  "timeout_seconds": 30,
  "probe_argv": ["semantic-preference-provider", "doctor"]
}
```

`argv` and `extension_id` are mutually exclusive. Omitting both selects the
unique installed extension implementation from runtime state.

A domain module owns the surface id, query, context keys, and decision about
how recalled items influence its output. Adding another module is a config
change, not a LoopX runtime change.

## Provider protocol

On `recall --execute`, LoopX sends one
`semantic_preference_provider_request_v0` JSON object on stdin. A provider
returns one `semantic_preference_provider_response_v0` object on stdout:

```json
{
  "schema_version": "semantic_preference_provider_response_v0",
  "items": [
    {
      "preference_ref": "provider-owned-reference",
      "summary": "Use concise Chinese sections for this surface."
    }
  ],
  "corpus_inventory": [
    {
      "corpus_id": "project_preferences",
      "scope_ref": "provider-owned-scope-reference",
      "read_role": "primary",
      "write_mode": "provider_managed",
      "write_actor_ref": "provider-owned-actor-reference",
      "source_of_truth": "repository_revision_and_explicit_feedback",
      "writeback_triggers": ["explicit_feedback", "source_truth_changed"],
      "closure_policy": "write_wait_l2_read_scoped_recall"
    }
  ]
}
```

`corpus_inventory` is optional and provider-neutral. It describes which bounded
corpora contributed to the recall and what closes a maintenance decision; it
does not contain raw memory. LoopX validates the inventory and derives
`semantic_preference_maintenance_guidance_v0`. A fixed function boundary can
therefore expose the corpus ids, writeback triggers, and closure policy in the
same provider call instead of relying on the agent to remember a separate
runbook. Providers that omit the field remain compatible.

An explicit feedback or source-of-truth change does not imply that every corpus
must be rewritten. The caller either performs the provider-owned update and
verifies the configured closure policy, or records a `no_write_rationale`.
LoopX does not infer semantic updates, mirror provider storage, or turn a soft
preference into an execution permission.

Provider stderr and non-zero output are reduced to a bounded failure kind.
`fail_open` returns no items and lets the domain continue; `fail_closed` stops
the caller with an actionable error. Provider failures do not become user
gates automatically.

`provider.id` and `setup_hints` are optional. Legacy `probe_argv` must be a
read-only health check owned by the provider. Extension providers use the
manifest doctor instead. Neither doctor path installs packages, starts
services, changes config, or writes credentials; setup hints remain guidance
for an explicit operator action.

## CLI

```bash
loopx semantic-preference recall \
  --project . \
  --config <ignored-config.json> \
  --surface issue_fix.pr_description \
  --context repository=owner/repo \
  --execute

loopx semantic-preference doctor \
  --project . \
  --config <ignored-config.json> \
  --execute

loopx semantic-preference receipt \
  --surface issue_fix.pr_description \
  --application-id pr-123-description-v2 \
  --outcome applied \
  --preference-ref <provider-owned-reference> \
  --artifact-ref https://github.com/owner/repo/pull/123

loopx semantic-preference maintenance-receipt \
  --trigger source_truth_changed \
  --outcome verified \
  --corpus-id project_preferences \
  --scope-ref <provider-owned-scope-reference> \
  --evidence-ref project-preference-readback-v2
```

Receipts contain only surface, application id, outcome, optional public
artifact reference, and hashes of provider-owned preference references. The
command returns the receipt without writing a file. Callers can attach it to
the existing evidence log, todo evidence, or `refresh-state` record; the hook
does not maintain a second reward or memory ledger.

Maintenance receipts are also stateless. They contain only the trigger,
outcome, corpus ids, optional compact evidence reference, and hashes of scope
references. A `verified` outcome means the provider-specific write, queue or
index wait, direct read, and scoped recall required by the inventory have all
passed. A `no_write_rationale` outcome records that the trigger was assessed
but no durable semantic change was needed.

`--context` is repeatable and each entry uses `lower_snake=value` syntax.
Invalid config, context, surface, or fail-closed requests return a structured
`semantic_preference_error_v0` payload with exit code 2 instead of a Python
traceback.

## Domain integration

For reviewed reward-memory records, Stage 3 also exposes
`run_semantic_preference_reward_memory`. The caller supplies the exact corpus,
module-owned surface, query steps, read-authority checkpoint, provider binding,
and model application callback. The shared reward-memory core performs the
scope/freshness/conflict guards and returns a compact receipt; this module does
not add another store, router, or scheduler. Function-boundary mode permits one
query, while bounded agentic mode permits at most three caller/model-authored
queries.

```python
from loopx.capabilities.semantic_preference import application_receipt, recall

preferences = recall(
    config_path,
    project=project_root,
    surface="issue_fix.pr_description",
    execute=True,
)
# The same result identifies provider-owned corpora that must be assessed after
# explicit feedback or a source-of-truth change.
guidance = preferences.get("maintenance_guidance")
# The issue-fix module decides whether and how to apply preferences["items"].
receipt = application_receipt(
    surface="issue_fix.pr_description",
    application_id="pr-123-description-v2",
    outcome="applied",
    preference_refs=[item["preference_ref"] for item in preferences["items"]],
)
# Write `receipt` through an existing LoopX evidence/state surface.
```

## Explicit Agent preferences

Use `semantic-preference agent` for an owner's durable instructions about how
an Agent should work. This is **advisory context**, not a permission store,
Goal configuration, capability enablement or an automatic execution engine.
Existing authorization and exact-head review rules still govern actions.
The local trusted caller attests the user instruction; a source reference is
provenance, not cryptographic authentication of the speaker.

The built-in local provider reuses `AuthorityStore` transactions, conditional
revisions and retained history. TypeScript owns validation, replacement,
retirement, expiry and replay. Python only resolves the registered Goal and
adapts CLI/host inputs. This store is private runtime context, separate from
Goal/Todo authority and its selected File/SQLite/PostgreSQL provider. It needs
no optional memory service. The lifecycle is also tested against real SQLite;
this does not introduce a new user-selectable memory backend or claim remote
memory synchronization.

The exact scope is runtime + resolved Goal state file + Goal instance (when
present) + Goal id + Agent id. Global/project registry aliases pointing at the
same Goal share preferences; another Goal or Agent does not inherit them.
No copying host conversations, cross-home rebinding or implicit global corpus.
Moving the Goal state file requires an explicit context migration; it is not
silently treated as the same private scope.

### Read, remember, correct and retire

```bash
loopx semantic-preference agent read --goal-id demo --agent-id author --format json

# A truly empty store reports revision=null. Otherwise pass the exact revision.
loopx semantic-preference agent remember --goal-id demo --agent-id author \
  --key review.collaboration --statement 'Ask the designated reviewer before merging.' \
  --source-ref owner-message-1 --source-quote 'Use the designated reviewer for my changes.' \
  --expected-revision none --operation-id preference-1
# Inspect the preview, then repeat exactly with --execute and read back.
```

Keys name stable subjects, not individual messages. A correction reuses
`review.collaboration`, a **new operation id**, and the revision from a fresh
read. `--expires-at` optionally bounds validity with a UTC ISO timestamp.
The CLI does not classify natural language or run an LLM. The host interprets
an explicit user message and calls this typed transition; retrieved documents
and model-generated lessons are not admissible write sources.

| User intent | Update | Next fresh decision |
| --- | --- | --- |
| Use the designated reviewer from now on | Remember the collaboration preference | Read and apply it under current authority |
| Stop asking a reviewer | Replace the same key with the negative preference | Do not act on the superseded positive preference |
| This time skip the reviewer | Keep durable preference; use the current task exception | Later tasks still read the durable preference |
| Forget this preference | Retire the same key | Tombstone invalidates cached guidance; no older value is resurrected |
| Here is an example: “stop asking a reviewer” | No update | Quotation is not a user correction |

```bash
loopx semantic-preference agent retire --goal-id demo --agent-id author \
  --key review.collaboration --source-ref owner-message-2 \
  --source-quote 'Forget my review preference.' \
  --expected-revision '<fresh read revision>' --operation-id preference-2 --execute
loopx semantic-preference agent history --goal-id demo --agent-id author --format json
```

History is paginated (`--after-cursor`), preserves sources and predecessor
operation ids, and is not injected into the action context. Retirement is not
physical erasure: older statements remain in private history/backups. There is
no automatic upload. Back up the private runtime directory with normal host
backups; do not publish its journal or include it in public fixtures.

Uncertain writes retry the **same operation id and exact original arguments**.
Replay returns the old receipt with the **current** view, never rewrites its old
projection. Stale revisions and changed same-id requests are explicit conflicts;
read and reconcile before proposing a new operation. Do not blindly retry with
a newly fetched revision, which would hide a concurrent correction.

### Fresh-turn adoption

After any preference has been committed, CLI quota admission and native Turn
planning/execution disclose a required owner-local read through the existing
turn-start hook. Counts and the read command enter the turn envelope; private
statements and source quotes do not enter generic quota/status or public sinks.
The command reads all current scoped entries, including retired/expired markers,
instead of relying on embedding or keyword ranking to find a prohibition.

The managed `/loopx` instructions teach the host to persist explicit corrections,
read them back, and re-read before a preference-dependent external action.
A new user instruction overrides old context immediately, including while a
write is being recovered. An unreadable store is unavailable, not empty: do not
act on a cached preference; independent work can continue. This is a host
obligation, **not** a claim that a generic memory engine intercepts every tool
call atomically. Native hosts must execute the disclosed read, and ordinary
unmanaged conversations have no automatic hook.

The bounded current view accepts at most 64 subject keys and 32 KiB of records.
Capacity failure rejects the write; it never silently evicts an old constraint.
Expired/retired statements are not actionable. A later remember of a retired
key needs a fresh explicit user source and current revision. Journal retention
and physical erasure are separate work, not implemented by `retire`.

No frontend configuration editor is added: this slice is owner-local CLI/host
context, not Goal settings. Dashboard/Lark memory inspection and authenticated
message ingestion remain outside this slice; external messages must not be
silently promoted to preferences by a display surface.

### Design evidence

The design deliberately separates explicit instructions from probabilistic
experience recall. [LangGraph](https://docs.langchain.com/oss/python/concepts/memory)
distinguishes procedural, semantic and episodic memory and namespaced long-term
state. [Letta blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks) illustrate
bounded always-visible working context, separate from archival search.
[Zep temporal search](https://help.getzep.com/searching-the-graph) distinguishes
when facts are valid from when the system learned or invalidated them.
[Mem0 Dream](https://docs.mem0.ai/platform/features/dream) retains superseded
memories and offers latest-only filtering. These are documented mechanisms,
not comparative performance evidence. Here explicit correction commits
synchronously, preserves history, and deterministic current-state reading
excludes superseded instructions from action guidance; it does not wait for
background consolidation or a relevant search hit.
