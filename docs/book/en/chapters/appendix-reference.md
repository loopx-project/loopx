# Terms and command entrypoints

Use this appendix with an actual problem; the source course is not a prerequisite. Current installed `--help` owns command arguments. This page gives entrypoints and judgment conditions, not a complete CLI reference or automatic mutation permission.

## Read before deciding to change state {#read-before-change}

Identify the exact existing registry, runtime root, Goal and Todo. Do not guess from display names or substitute teaching labels T1/M1 for real IDs. These commands do not request completion, acquisition or settlement. Set four variables first; absence stops the commands:

```bash
: "${REGISTRY:?Set the existing registry path}"
: "${RUNTIME:?Set the existing runtime root}"
: "${GOAL:?Set the exact goal id}"
: "${TODO:?Set the exact todo id}"
loopx --registry "$REGISTRY" --runtime-root "$RUNTIME" --format json \
  todo list --goal-id "$GOAL" --todo-id "$TODO"
loopx --registry "$REGISTRY" --runtime-root "$RUNTIME" --format json \
  task-lease inspect --goal-id "$GOAL" --todo-id "$TODO"
```

Reads can start the managed runtime; separate responses are not one atomic snapshot. Check identity, source, time and available revision in each. Report missing information instead of inventing approved, open or zero. To determine what happened to an actual operation, consult its own receipt too.

Relevant `quota should-run --codex-app` paths can create heartbeat receipts. `refresh-state` writes back; acquisition, renewal, settlement and scheduler ACK have execution conditions. They are not the two observational commands above and do not belong in an unbounded diagnostic retry loop.

## Route symptoms to an owner {#diagnostic-routing}

Find the first relationship lacking evidence rather than clearing the Goal. This is a reading route, not a new automatic repair machine.

| Symptom | Facts to obtain first | Rule and operating entrypoint | Completion or stopping condition |
| --- | --- | --- | --- |
| Page and Todo disagree | Same object's source, mode, revision/time and projection | [State](state-substrate.md), [projection repair](04-runtime-boundaries.md#projection-repair) | Restore the source and reread; display does not take over authority |
| HTTP succeeded; execution is unclear | Original request, operation receipt and current source | [Workspace pause case](workspace-v1.md#pause-readback) | Confirm that operation; disagreement alone does not prove cause |
| Earlier acquire succeeded; writes now fail | Current owner/key/version, mode and Todo state | [Authority](work-graph-and-authority.md#authority-layers) | Obtain legal current proof, or stop if work ended |
| External request timed out | Original operation identity and provider readback | [Recovery](04-runtime-boundaries.md#recovery-or-new-execution) | Act on supported committed/absent results; retain unknown responsibility |
| Writeback exists; records remain incomplete | Original settlement state and outstanding action | [Settlement](03-one-turn.md#settlement-recovery) | Complete missing records without repeating artifact or debit |
| Independent work appears during an original-Turn wait | Original binding, blocked writeback and later candidates | [Wait closeout](03-one-turn.md#wait-closeout) | Follow the installed contract to close out before new selection |
| Quiet Monitor or repeated replan | Due state, target, current lane, selectable work and Host | [Observation](04b-budget-and-admission.md#observation-owners) | Explain actual waiting or restore its execution surface |
| Old CI is green; current code changed | Actual artifact revision, affected requirements and evidence | [Changed evidence](04-runtime-boundaries.md#changed-evidence) | Support current conclusions without erasing history |
| Green branches cannot be delivered together | Artifacts, recipient-adopted revisions and integration checks | [Handoff and integration](work-graph-and-authority.md#handoff-to-integration) | Close integration and acceptance responsibility |
| Artifact generated; recipient did not receive it | Target, route, send/delivery readback and authority | [First delivery](05-connect-existing-project.md#first-delivery) | Complete required return or identify remaining responsibility |

Public feedback should retain minimal reproducible versions, error codes and public-safe references, not live registries, credentials, raw transcripts or private run records. When evidence is insufficient, name the unknown conclusion, missing readback and confirming owner. Destructive test injection is not a live recovery procedure.

## Requirement one: state outlives context {#requirement-one-state-outlives-context}

| Term | Distinction to preserve |
| --- | --- |
| Goal / Acceptance | Objective identity and observable acceptance, not one Todo's status |
| Source / Projection | Owning facts and rebuildable views; legacy Markdown's role depends on mode |
| Evidence / Receipt | Support for a conclusion versus an accepted-operation record; both need correct binding |
| Kernel | Generic transition owner, not the owner of every external system's reality |

See [state](state-substrate.md). Information in a prompt alone does not prove durable lifecycle writeback. Persisted chat also does not automatically become current authority.

## Requirement two: identify interrupted work {#requirement-two-interruption-stops-at-identifiable-points}

A Todo identifies work; the frontier supplies candidates under current constraints; Turn/operation identity binds execution and recovery. Historical receipts differ from new execution eligibility. Artifact revision, provider revision and lease version/epoch cannot substitute for one another; see [revision bases](04-runtime-boundaries.md#revision-bases).

## Requirement three: current executor and scope {#requirement-three-one-accountable-actor}

Agent identity denotes a lane, not a Host. Vision describes an applicable per-Agent route. Claim expresses assignment; leases participate in proof on applicable writers and modes; Gates cover named decisions; Hosts supply execution and waking. These do not imply one executor for the whole system or an OS sandbox. See [work graphs](work-graph-and-authority.md).

## Requirement four: bounded, observable consumption {#requirement-four-bounded-externally-observable-spend}

Quota participates in admission and internal accounting. Monitors retain targets and observations. Scheduler/backoff arranges timing; Hosts or integrated event channels perform waking. Replan changes the route, not just an interval. No-spend is not free actual resources. See [observation ownership](04b-budget-and-admission.md#observation-owners).

## Words outside the four requirements {#words-outside-the-four-requirements}

Capability describes a caller outcome contract; Provider supplies implementation or external access; Extension manages independent package installation, activation and versions. Installed, doctor-ready, Goal-configured and currently authorized are different facts. See [placement](08-extension-placement.md) and [lifecycle](10-extension-lifecycle.md).

## Core command quick index {#core-command-quick-index}

| Need | Entrypoint | Boundary |
| --- | --- | --- |
| Inspect installation and prerequisites | `loopx --version`, `loopx doctor` | Does not prove a delivery succeeded |
| Inspect connection and status | `loopx registry`, `loopx status`, `loopx history` | Verify actual registry/Goal; display may be bounded |
| Read an exact Todo | `loopx todo list --goal-id <goal-id> --todo-id <todo-id>` | Absence from a display is not absence of work |
| Compact a work list | `loopx todo list --goal-id <goal-id> --thin --format json` | Bounded display is not the whole candidate set |
| Inspect a lease | `loopx task-lease inspect` | Observation does not acquire new proof |
| Read Agent evidence | `loopx history --goal-id <goal-id> --agent-id <agent-id> --limit 30` | Separate history from current facts |
| Request admission | `loopx quota should-run` | Read the full contract; relevant Host paths can record receipts |
| Inspect a managed Turn journal | `loopx turn inspect-journal` | Preserve original Turn key; diagnosis does not recover |
| Discover configuration and capabilities | `loopx capability list/show`, `loopx configure-goal` | Separate reads without setting flags from execution |
| List independent packages | `loopx extension list --format json` | Visibility is not current run eligibility |

The `todo list --thin` option added in `v0.5.4` is an explicit bounded projection without changing default selection, ordering, quota, or lifecycle semantics. Use exact reads for details instead of changing display limits to bypass business checks.

## Project onboarding and actual delivery

```bash
loopx connect --dry-run
loopx start-goal --guided --project . \
  --goal-id <goal-id> --agent-id <agent-id> \
  --goal-text "<goal text>" --host-surface codex-app
```

These are preview entrypoints. Confirm actual connection writes and subsequent packet actions separately. Select `codex-cli-tui` for that actual Host, not an invented one. Omitted identities may lead to selection gates; an existing lane is not implicit takeover. Follow [onboarding](05-connect-existing-project.md#three-completions) and [first delivery](05-connect-existing-project.md#first-delivery).

## Scheduler convergence entry {#scheduler-entry}

When a packet requires Host apply, apply its current recommendation and read the result before executing the bound ACK:

```text
loopx quota scheduler-ack-current <packet-bound-args...>
```

This is a command shape, not copy-ready arguments. Use full `ack_hint.cli_args`. Do not fabricate success after failure or unknown outcome; use the applicable `failure_hint.cli_args`. A Host already confirmed to match the desired cadence may only need ACK, not a redundant update. See [budget and observation](04b-budget-and-admission.md).

## Safe updates and rollback

```bash
loopx update check
loopx update plan
loopx update apply
loopx doctor
```

Confirm installation owner, target release and plan before authorizing apply. Ordinary users should not default to `--ref main`. Updating the CLI does not establish that every Host, Provider and existing Goal migrated. Recheck skills, state, Host readback and enabled Extension readiness for the surfaces actually used.

For backup, preview before execution:

```bash
loopx backup-state --project .
loopx backup-state --project . --execute
```

Backups are private recovery material and may contain sensitive runtime state; do not commit them. Before `loopx update --rollback previous`, inspect current help and verify that the installation supports that recovery. Reverting the program does not necessarily revert independent packages, written state formats or external effects. Those retain their own owners; see [recovery and compensation](04-runtime-boundaries.md).

## Extension lifecycle

```text
init → install package → install preview → install --execute → doctor/readback
     → run valid input → disable/enable → upgrade/rollback → reread
```

Do not reinstall an already installed Extension. Use an isolated state file and one Python environment for the [complete scaffold](09-extension-scaffold.md) and [lifecycle exercise](10-extension-lifecycle.md). `rollback_available=false` differs from a valid target whose doctor fails. Retaining old activation metadata does not guarantee the old entrypoint remains executable.

## Protocol index

| Topic | Primary entrypoint |
| --- | --- |
| Goal, identity and Host activation | [Goal command](/loopx/docs/reference/protocols/loopx-goal-command-v0/) |
| Source/projection and long-running work | [Long-horizon state](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/) |
| Agent-scoped history | [Evidence ledger](/loopx/docs/reference/protocols/agent-scoped-evidence-ledger-v0/) |
| Retired Todo event API | [Retired event source](/loopx/docs/reference/protocols/event-sourced-state-contract-v0/) |
| Active-state read model | [Structured projection](/loopx/docs/reference/protocols/active-state-structured-projection-v0/) |
| Work graphs, Gates and peers | [Task graph](/loopx/docs/reference/protocols/task-graph-projection-v0/), [Decision scope](/loopx/docs/reference/protocols/decision-scope-v0/), [Peer runtime](/loopx/docs/reference/protocols/peer-agent-runtime-v1/) |
| Vision and route changes | [Vision/replan](/loopx/docs/reference/protocols/goal-vision-replan-contract-v0/) |
| Explicit managed turns | [LoopX Turn](/loopx/docs/reference/protocols/loopx-turn-v0/), [Turn envelope](/loopx/docs/reference/protocols/turn-envelope-v0/) |
| Host, Session and writeback | [Host integration](/loopx/docs/reference/protocols/host-integration-surface-v0/), [Session projection](/loopx/docs/reference/protocols/session-runtime-loopx-projection-v0/), [Controlled writeback](/loopx/docs/reference/protocols/session-runtime-controlled-writeback-v0/) |
| Concurrent write bases | [Local write correctness](/loopx/docs/reference/protocols/local-state-write-correctness-v0/) |

Protocol pages have versions and implementation status. Accepted RFCs, experimental contracts and shipped behavior are not interchangeable. Advanced source comparisons use fixed commits; verify that your checkout contains the implementation before running it. Enter [Course exercises](12-control-plane-course.md#reader-checkpoints) and the [contribution path](source-protocol-map.md) when preparing a rule change.
