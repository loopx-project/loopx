---
name: loopx-change-quality
description: Qualify the exact final diff for a LoopX-managed goal. Use when goal policy enables change_quality_qualification, before a non-trivial delivery or merge, and when producing or repairing an exact-scope quality receipt. The workflow is language-neutral, permits at most one policy-authorized safe-fix pass, and never grants merge or repository authority.
---

# LoopX Change Quality

Use this skill only when the selected goal's
`change_quality_qualification.enabled` policy is true. LoopX owns the canonical
source but does not install it globally. Install a managed copy in a connected
project for the relevant host:

```bash
loopx project-skill install \
  --project . \
  --skill loopx-change-quality \
  --surface codex \
  --execute
```

Use `--surface claude-code` or `--surface opencode` for those hosts. Skill
discovery does not activate the capability; product behavior remains
default-off until goal policy enables it.

The CLI is the contract authority. This skill supplies a host-neutral review
workflow. Repository instructions, tests, linters, type checkers, and security
checks remain the project's quality oracles.

## Prepare The Exact Scope

From the repository worktree, run:

```bash
loopx --format json change-quality prepare \
  --goal-id <goal-id> \
  --repo-path . \
  --base-ref origin/main
```

Stop when the packet says `disabled` or `no_changes`. When it says
`review_required`, review only the files and exact fingerprint in the packet.
Read repository-local instructions before judging the change.

Run `loopx project-skill status --project . --skill loopx-change-quality` when
the host depends on skill discovery. If the managed copy is absent or stale,
preview an explicit project install; do not fall back to a global copy.

## Review Rules

## Simplify First

Spend the review budget on simplification before broad quality analysis:

1. Reuse an established helper or durable repository rule instead of copying
   behavior or knowledge.
2. Remove redundant state, parameters, branches, indirection, and speculative
   abstraction while preserving behavior.
3. Challenge a private helper or wrapper when it is only one to three lines,
   has at most two production callers, and owns no independent domain
   invariant, effect, or error boundary.
4. Prefer the smallest coherent edit that leaves ownership and intent clearer.
   Do not create churn when the current shape is already direct and cohesive.

Simplification must preserve decision meaning, including agent-consumed prose.
Compare ordering, evidence provenance, modality, qualifiers, continuation and
stop conditions before shortening instructions. Unchanged fields and green size
tests are insufficient. Follow the repository's budget decision guide; prefer
an evidence-backed regression-limit increase when compaction loses useful
meaning. Never treat a test ceiling as frozen authority or infer a semantic
change is authorized by a size target. Keep this judgment in the existing
reuse/simplification and validation evidence rather than a separate receipt.

Write one evidence-backed `reuse` conclusion and one evidence-backed
`simplification` conclusion. Do not emit a row for every remaining lens.
Those dimensions are guardrail categories for sparse `risks[]`: add an item
only when the changed surface, repository instructions, a native validator, or
the proposed simplification raises a concrete risk. LoopX derives each
guardrail's status from `risks[]` and `validation[]`.

The available review lenses are:

- **reuse:** established helpers and durable knowledge are reused instead of
  duplicated;
- **type/API boundary:** types, schemas, compatibility windows, and caller
  contracts remain explicit and coherent;
- **configuration:** configuration stays single-sourced, validated, and free
  of hidden mode coupling;
- **runtime ownership:** lifecycle, concurrency, state, and side effects live
  in the correct boundary;
- **quality/simplification:** unnecessary indirection, branching, duplication,
  and speculative abstraction are removed or explicitly justified;
- **efficiency:** hot paths, repeated work, memory growth, and unbounded loops
  are considered;
- **error/supervision:** failures remain observable and actionable without
  silent fallback or blanket exception handling;
- **test/validation:** tests and repository-native validators prove intended
  semantics and important negative paths;
- **documentation/comments:** names, comments, and docs describe current
  contracts without stale or duplicated narration;
- **security/release:** security, privacy, permissions, migrations, and release
  compatibility are handled at changed boundaries.

The packet projects path-only references to applicable repository instructions,
ownership files, build manifests, language hints, and changed surface roots.
It also projects a provider-neutral validation plan discovered from structured
repository task declarations. This is discovery context, not copied repository
content: instruction text, task bodies, and manifest contents remain in the
worktree. Read every `required_reads` entry, inspect each candidate's
`source_ref`, and let the host resolve the named Poe, Hatch, Cargo, or package
task. Never execute a candidate merely because it was discovered. Unresolved
format, lint, typecheck, or test categories require reviewer judgment or a
repository-native instruction; do not fill them with guessed commands. Treat
`ignored_manifest_refs` as non-executable context, especially fixtures and
vendored projects.

Read every projected instruction reference, but do not copy its prose into the
result. Ground `reuse`, `simplification`, and each emitted risk with typed
`evidence_refs` using `path:`, `instruction:`, or `validator:`. Keep
`risks[]` empty when no guardrail is triggered. Record only validators that
were selected or required; failed validation and skipped required validation
are independently blocking.

Use `blocker` only for a concrete correctness, security, privacy, contract, or
required-validation failure. Style preferences and speculative redesigns are
`warning` or `advisory`, never blockers.

This first version is single-level. Review one final diff; do not recursively
review the review, spawn a hierarchy of quality agents, or require agreement
between several models.

The guardrail catalog is review guidance, not an Agent output checklist or
evidence that a particular model applies it well. Maintainers qualify model
behavior separately against the public clean-PR and seeded Python, Rust, and
TypeScript shadow matrix.
Normal delivery does not launch that low-frequency evaluation, and a model
shadow result cannot mutate goal policy or grant merge authority.

## Safe Fix

`safe_fix` and `strict_receipt` are independent:

- `safe_fix=true` permits at most one bounded repair pass.
- `strict_receipt=true` requires exact-scope evidence before merge and grants
  no permission to edit.

When `safe_fix` is false, report risks without modifying files. When it is
true, one repair pass may address a clear simplify opportunity or risk inside
the selected todo and goal boundary. Do not use destructive git, broaden
permissions, change product intent, add unrelated refactors, or conceal a
failing validator.

After any edit, rerun `prepare`. The old fingerprint is invalid. Review the
entire new final scope, not only the lines changed by the repair.

## Record The Receipt

Write a compact result conforming to the packet's
`change_quality_agent_result_v2` template. Beyond exact-scope metadata, the
Agent writes only `reuse`, `simplification`, sparse `risks[]`, and
`validation[]`. `simplification.safe_fix_applied` records the one permitted
repair pass. A skipped or failed validator needs a reason; failed validation or
skipped required validation makes the receipt non-passing. Keep raw
transcripts, private paths, credentials, and unbounded logs out of the result.

Then record and read back the exact receipt:

```bash
loopx --format json change-quality record \
  --goal-id <goal-id> \
  --repo-path . \
  --base-ref origin/main \
  --result-json <ignored-or-temporary-result.json> \
  --execute

loopx --format json change-quality verify \
  --goal-id <goal-id> \
  --repo-path . \
  --base-ref origin/main
```

A receipt with an unresolved blocker, failed validator, or skipped required
validator is not passing. A receipt for an earlier fingerprint does not
qualify a later diff. Earlier experimental receipt schemas are invalid and
must be requalified with the current protocol.

## Premerge Enforcement

Run the authoritative merge gate with the goal identity:

```bash
loopx canary premerge \
  --from-git-diff \
  --goal-id <goal-id>
```

Turn may transport the prepare packet or receipt reference as part of one
bounded transaction. Turn does not own quality policy and may not manufacture
or waive a receipt. `canary premerge` remains the enforcement authority.

## Completion Evidence

Report:

- final scope fingerprint and changed-file count;
- safe-fix allowed/applied and pass count;
- blocker, warning, and advisory counts;
- project validations run and their real results;
- receipt id and exact verification status;
- premerge status, failures or skips, and manual holds.

Stop before delivery when strict policy requires a receipt and the receipt is
missing, invalid, stale, or contains an unresolved blocker.
