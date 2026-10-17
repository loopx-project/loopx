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

## Disable and qualification

Omit `--execute` or stop issuing the explicit operations to disable this entry;
there is no automatic activation switch. Published points stay inert. Change
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
