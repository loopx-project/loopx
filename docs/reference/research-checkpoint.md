# Explicit research checkpoints

`loopx research-checkpoint` captures a settled research boundary and adopts its
history into an independently admitted Goal. It uses the existing TypeScript
artifact and Todo authority owners, Explore event codec, backup inventory and
Codex Host session provider. Ordinary Goals do not need the benchmark package.
Benchmark adapters separately supply their task, feedback and budget state.

This is a CLI/Host stage. The packaged frontend journey, automatic ordinary-Goal
replan capture and sustained research utility remain open. It is not disaster
replacement, a process-memory snapshot or a promise of arbitrary Host support.

## Prepare and capture

Use a quiescent source with a settled LoopX Turn, completed native cursor,
registered agent-scoped session and promoted canonical coordination. Supply a
physical registry scoped to this Goal and explicit workspace artifacts. The
caller must stop concurrent writers; rereads reject observed source movement.

Set the qualified `CODEX_BIN`, `CODEX_HOME`, `MODEL_NAME` and `REASONING_EFFORT`.
The source profile must match the explicit project and sandbox. Create a private
request file, using physical absolute paths appropriate to the local machine:

```json
{
  "destination": "/private/checkpoints/point",
  "components": [{"id": "code", "source_path": "/private/workspace/candidate.py"}],
  "point_metadata": {"purpose": "compare continuations"}
}
```

```sh
loopx --registry SOURCE_REGISTRY --runtime-root SOURCE_RUNTIME --format json \
  research-checkpoint capture --goal-id SOURCE_GOAL --agent-id SOURCE_AGENT \
  --project SOURCE_WORKSPACE --sandbox workspace-write \
  --turn-instance-id SETTLED_TURN --request-file REQUEST_JSON
```

Capture, admission and adoption preview by default, without reading the source
or calling the native provider. Add `--execute` to perform that explicit
operation. Capture adds revision-bound Todo context, complete Explore events and
indexed vision history, the current session binding, full paginated native
history and qualified native stores. The native inventory excludes credential
files; arbitrary caller-declared artifacts remain private and are not privacy
certified. A new destination is required; capture never overwrites a point.

Artifact operations use the existing private local snapshot transport for full
inventories and retained metadata above the ordinary 2 MiB Effect wire limit.
The existing 64 MiB local snapshot bound and original operation deadlines still
apply. No witness is truncated, no capture is enabled implicitly, and a lost
capture response must be reconciled under its original identity before another
operation is considered.

The inventory covers only declared file roots plus the automatically owned
session and LoopX state participants. A conversation mentioning an external
candidate or report does not capture that file. Declare every needed research
directory before capture, keep its source quiescent, and verify its bytes at the
receiver. Never broaden capture to an entire temporary or credential directory
just to obtain apparent coverage. Missing historical bytes cannot be supplied
from a later state under the original point identity. Domain receivers must
explicitly materialize their selected research components; the
[SForge warm transport](../../benchmark/README.md#explicit-warm-worker-composition-qualification-in-progress)
provides that selection for its data paths.

For new checkpoint-enabled native EdgeBench runs, the startup prompt and workspace
`AGENTS.md` now tell the solver to keep reusable research artifacts in a task
workspace subdirectory, including local inputs and negative results. The prompt
states whether the supplied declaration covers that workspace. A declared
directory includes files created later; an explicit file list does not grow
when a conversation or report mentions another path. Capture-disabled prompts
are unchanged. This is storage guidance, not an enforced completeness check.

For new donors, declare the task workspace directory before launch. For existing
runs, preserve their frozen instructions and source; use a newly qualified
attempt when adopting this guidance. Previously external artifacts still need
explicit capture and receiver validation. Do not rewrite an old point using
later live files or claim that the new guidance repairs missing historical bytes.

Verify the returned digest before preparing a receiver:

```sh
loopx --format json research-checkpoint verify \
  --root CHECKPOINT_ROOT --expected-sha256 CAPTURE_DIGEST
```

## Admit a fresh session and adopt context

Prepare a fresh isolated environment preserving the captured guest paths and
the qualified native Host/configuration. Register a distinct current Goal and
Agent through normal LoopX configuration. Use a new native home at the qualified
fixed path; do not replace an active user's home. Supply the destination Host
environment and separately retain the captured configuration file. Automatic
native Goals must remain disabled.

```sh
loopx --registry DEST_REGISTRY --runtime-root DEST_RUNTIME --format json \
  research-checkpoint admit-session --goal-id DEST_GOAL --agent-id DEST_AGENT \
  --project QUALIFIED_WORKSPACE --sandbox workspace-write \
  --root CHECKPOINT_ROOT --expected-sha256 CAPTURE_DIGEST \
  --config-path CAPTURED_CONFIG --execute
```

This reads and forks the native history, verifies its cursor/configuration and
binds the distinct session through current Goal lifetime authority. It starts
no model and grants no work. Acquire a current claim and lease on a destination
Todo with an empty note, then use its current provider revision:

```sh
loopx --registry DEST_REGISTRY --runtime-root DEST_RUNTIME --format json \
  research-checkpoint adopt-context --goal-id DEST_GOAL --agent-id DEST_AGENT \
  --project QUALIFIED_WORKSPACE --sandbox workspace-write \
  --root CHECKPOINT_ROOT --expected-sha256 CAPTURE_DIGEST \
  --source-todo-id SOURCE_TODO --todo-id DEST_TODO \
  --operation-id CONTEXT_OPERATION --expected-revision ORIGINAL_REVISION \
  --lease-key CURRENT_LEASE_KEY --lease-version CURRENT_LEASE_VERSION --execute
```

The existing continuation owner prepares/adopts a complete context reference.
Explore knowledge seeds the fresh Goal with preserved counterevidence, topology,
authorship, timestamps and checkpoint provenance. Ordered-prefix recovery rejects
divergent destination evidence. Historical operational observations, decisions,
leases, grants, timers and effects remain historical. Neither command imports
them as authority or resets a benchmark's budget or feedback opportunities.

After an uncertain response, retain the same source digest, destination and
original operation/revision arguments and read back through the owner. Do not
invent a new operation, overwrite the destination or rerun a model to resolve
uncertainty. A branch that has already advanced cannot adopt an older cursor.

Read the destination Todo and `explore turn-context`, obtain current work
admission and inspect the next accepted replan. A useful readback cites retained
evidence, explains the next complete experiment and later checks its outcome.
Restoring files or accepting a structured review alone proves no research gain.

For a restored benchmark conversation, recapture also requires a settled,
checkpoint-linked current review and a completed destination Codex conversation
turn. That conversation turn is not a benchmark evaluation. A successor Todo
can be a valid replan outcome without satisfying this recovery check. When the
current guard offers original-Turn closeout, include
`checkpoint:<capture-digest>` in `agent_vision.path_delta.evidence_refs` in that
writeback and finish its settlement. If the Turn has ended, carry the unresolved
review to the next admitted replan; do not replay or rebind historical Turns.
Normal research cadence resumes only after the existing recovery gate passes.

## Default bank rotation

**Default change:** new controller-enabled benchmark checkpoint banks rotate
automatically after a successful capture reply, keeping only the newest complete
publication and explicitly pinned points. Capture itself still requires its
existing explicit enablement. Use `--no-checkpoint-retention` to retain every
point, or `--checkpoint-retention-policy RETENTION_POLICY` to supply a custom
policy. The default policy is saved in the bank's private
`.checkpoint-retention/policy.json`; it can be inspected and edited for pins.
An explicit policy file remains the active configuration when supplied.

Ordinary single-point capture accepts an arbitrary destination and does not
implicitly sweep its parent. Select a bank explicitly through `rotate` or the
storage watcher below. Existing running processes are not hot-patched.
Retention is local storage maintenance through the same artifact owner; it does
not select scientific results, qualify a point, change feedback opportunities,
or delete the native evaluator's separate bank. It remains part of this CLI/Host
stage; a packaged frontend retention editor is not delivered here.

Use one bank per source lineage. The typed default is:

```json
{
  "strategy": "uniform",
  "keep_latest": 1,
  "keep_first": false,
  "minimum_age_seconds": 0,
  "history_slots": 0,
  "protect_since": null,
  "pins": []
}
```

This compact policy retains only the newest complete publication plus explicit
pins. It has no origin anchor, sampled history or age grace. To preserve more
history, enable `keep_first`, increase `keep_latest`, set
`minimum_age_seconds`, or set positive `history_slots` for equal-time history.
For nonuniform history, replace `strategy` with `tiered` and `history_slots`
with `bucket_seconds` (for example, `14400`). That retains the newest
representative in each age band measured from the newest publication: under
4 hours, 4–8 hours, 8–16 hours, and so on. Idle time alone does not shift bands.
Uniform slots include the historical end;
repeating a policy on an unchanged bank does not progressively thin it.
Uniform history requires `keep_first=true` to preserve its time anchors.
`keep_latest` must be at least one. Protection is additive, so pinned or recent
points can exceed the sampling count. There is no hard byte cap that overrides
these protections.

Before cleanup, put every selected study point, source still needed by a
receiver, and referenced dependency in `pins` as an exact
`{"id":"POINT_DIRECTORY_NAME","sha256":"CAPTURE_DIGEST"}`. References in
arbitrary plans, sessions or conversations are not automatically discovered.
For example, a source used by two continuation arms remains pinned until both
arms no longer need it. The ID is the immediate point directory name and the
digest is the `sha256` field in that point's `checkpoint.json`. Remove the entry
to make an older point eligible on the next rotation. Stop the watcher or
quiesce the capture supervisor before changing pins, preview the new policy,
then resume maintenance; editing a policy does not recover already deleted data.
`protect_since` optionally preserves every publication at or after a Unix epoch,
useful while a chronological selection is unresolved. This is filesystem seal
time, not scientific elapsed age or source qualification. A missing/changed pin
holds cleanup, including pending quarantine recovery.

Preview the complete retained/deletion lists, logical byte totals and plan digest.
Without `--request-file`, the CLI reads the bank's policy file when present,
otherwise the typed latest-only default. Supplying the file explicitly overrides
that selection for this operation; it does not rewrite another worker's configuration:

```sh
loopx --format json research-checkpoint rotate --root CHECKPOINT_BANK
loopx --format json research-checkpoint rotate --root CHECKPOINT_BANK \
  --request-file RETENTION_POLICY --expected-plan-sha256 PLAN_DIGEST --execute
```

Execution requires a private, operator-owned bank on the qualified POSIX
transport. Preview does not change it. Before any deletion or quarantine recovery,
the complete retained points must pass member-integrity verification. A corrupt
newest point holds pruning of older recovery points. This storage RPC allowance
does not extend a research capture, native deadline or continuation budget.
Execution excludes supported readers, rechecks the inventory and atomically
moves only the selected whole point directories into a private quarantine.
It preserves response/error sidecars, unknown paths, incomplete captures and
staging directories. Unlinking runs outside the reader lock, with a separate
single-flight lock preventing concurrent recovery. Compact digest-bound records
under `.checkpoint-retention/receipts/` distinguish planned, quarantined and
deleted points. A failed unlink resumes from its original quarantine on the next
explicit rotation; it never recaptures newer state into an old identity.
Missing/ambiguous files or lock failures hold the affected operation.

The shipped verify/admit/adopt CLI and native benchmark restore/upload adapters
use the bank's shared read lock once retention has been activated, so same-point
arms can read concurrently while rotation waits. Older readers and
direct Effect clients must use an explicitly pinned point or quiesce before
cleanup. At first activation also quiesce or pin any reads that already began
before the marker existed. New supervisor-enabled banks install the marker before
capture starts. Pinning is still required for references that outlive one read.
Preview inventory is metadata-only. The execution-time member check still does
not qualify scientific dependencies, session adoption or research admission.

For an existing donor, run an independently managed storage watcher without
editing its frozen runtime:

```sh
loopx --format json research-checkpoint rotate --root CHECKPOINT_BANK \
  --request-file RETENTION_POLICY --execute --watch --interval-seconds 300
```

It reacts to changed local publication/policy generations and due grace expiry. It makes no
model or Goal calls. Errors stop the watcher with a failure result for operator
reconciliation. Stop this process to disable automatic rotation. For a new
controller-enabled benchmark run, pass `--no-checkpoint-retention`; removing a
custom policy option selects the default and does not disable rotation.
Previously deleted bytes are not
restored by disabling; recover them only from an independently retained copy.

## Disable and qualification

Omit `--execute` or stop issuing the explicit operations to disable capture and
adoption; stop a retention watcher separately. Published points stay inert. Change
adopted Todo notes through their normal owner; dispose of an isolated receiver
through its normal lifecycle. Do not delete or rewrite source history to roll
back a continuation.

The ordinary-Goal test forbids every benchmark import and exercises the shipped
CLI with real SQLite/artifact owners. Its native variant uses actual Codex
processes and deterministic local inference to capture, fork, adopt a retained
counterexample, accept a current typed review, execute a complete tool experiment
and settle it. The joined qualification recipe in `benchmark/README.md` also
runs this Core case. It validates mechanics and isolation; real-model decisions,
long-run reliability and net score/cost improvements require separate evidence.
