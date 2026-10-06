# Auto-Research Command Path

This guide is the shortest operator path for running the LoopX auto-research
demo from a clean workspace. It explains what to run, which visible digital
employees appear, what artifacts to inspect, and how to stop or take over.

Use the deeper showcase and protocol docs only after this path is clear:

- [Multi-agent product recipe](../../docs/guides/multi-agent-product-recipe.md)
- [Stop, takeover, and state-aware wake walkthrough](../../docs/guides/auto-research-stop-takeover-wake-walkthrough.md)
- [Decentralized auto-research showcase](../../docs/product/use-cases/auto-research/decentralized-auto-research-showcase.md)
- [auto_research_role_state_machine_v0](../../docs/reference/protocols/auto-research-role-state-machine-v0.md)
- [auto_research_role_profile_v0](../../docs/reference/protocols/auto-research-role-profile-v0.md)

Implementation boundary: auto-research is a thin preset over the generic
multi-agent kernel. `demo/auto_research/preset.py` owns only the
research roles, handoff hints, metric/evidence loop defaults, and seed todo
phrasing. The generic kernel owns the real Codex TUI panes, pane-local A2A
tick, workspace/trust-safe launch, todo/evidence/status protocol, and compact
human status. The developer-facing recipe proof also follows that boundary:
`preset.py` calls the generic `multi_agent.recipe` helper instead of defining
decentralized A2A proof mechanics itself.
Use the [multi-agent product recipe](../../docs/guides/multi-agent-product-recipe.md) when a new
product wants to copy the pattern without copying auto-research code.

## Promotion Decision

The validated visible proof is promoted into this existing command path, not
into a second auto-research runner. The public recipe remains:

- the operator runs one command;
- the user layer supplies only topic, objective, rounds, reasoning effort, and
  optional role overrides;
- the auto-research preset supplies research roles, handoff hints, seed todos,
  and evidence defaults;
- the generic multi-agent kernel supplies runner, wake, pane-local tick, status,
  attach, retry, and stop mechanics.

A visible proof is ready for this path only when the configured roles open as
real interactive Codex CLI panes, the fixed-prompt wake causes each pane to run
its local A2A tick, role output is summarized through LoopX todo/evidence/status
artifacts, and any live evidence packet is compact and public-safe. The proof
must not depend on raw logs, private artifacts, credentials, local absolute
paths, or a product-specific launcher hidden inside the auto-research preset.

## User Contract Entrypoint

The smallest user-facing auto-research contract is one open question:

```bash
loopx auto-research "<open question>"
```

This command does not launch panes or claim that research has been completed.
It renders the fixed contract the visible multi-agent run must satisfy:

- `research_brief`: what has been read, what has not been read, and the claim
  boundary;
- `action_plan`: P0/P1/P2 work, capped at five todos;
- `evidence_refs`: code, docs, benchmarks, issues, and pull requests;
- `next_executable_step`: whether the next step can run automatically;
- `gate`: the exact user judgment needed before crossing a boundary.

This keeps the user layer and the auto-research preset thin. The user supplies
one open question; auto-research supplies a fixed output contract; the generic
kernel owns the runner, real Codex TUI panes, pane-local A2A tick, and
todo/evidence/status protocol.

When the run must end in a verifiable deliverable rather than only a research
finding, the curator writes `auto_research_delivery_contract_v0`. The envelope
keeps the original wish, assumptions, required artifacts, acceptance criteria,
failure fallbacks, and reentry conditions together with the existing
`research_contract_v0`. Evidence created from that file is bound to its
atomic `wish_id`, `contract_ref`, and `contract_revision` lineage; partial or
mismatched lineage is not eligible for verification.

After terminal decisions and any required independent reviews exist, build the
read-only delivery receipt:

```bash
loopx auto-research artifact-receipt \
  --contract delivery-contract.public.json
```

The receipt distinguishes `verified`, `partial`, `inconclusive`,
`not_fulfilled`, and `stale`. A single failed attempt is never enough for
`not_fulfilled`; that state requires a terminal retirement decision and any
independent review required by the delivery contract. Non-verified receipts
return the unmet criteria, verified boundary, available fallback artifacts, and
reentry conditions.

The contract also includes the next one-command launch surface:

```bash
loopx auto-research start "<open question>" --execute
```

Without `--execute`, `start` returns the same contract-anchored runner packet as
a dry-run preview. With `--execute`, it creates an isolated research frontier
and starts the visible Codex TUI lanes through the generic multi-agent kernel.
The launcher then broadcasts the fixed pane-local A2A wake prompt so each pane
runs its own `$LOOPX_PANE_A2A_TICK` against LoopX quota/frontier state. Research
evidence must be authored by the visible role after it does real work. This is still
decentralized A2A, not a workflow driver: the broadcaster does not select todos,
run worker turns, or write LoopX state.
The wake summary is state context for the pane to read, not a research result or
skip gate. Later wake rounds still ask each role to check its own LoopX state and
tick when runnable work remains.
The fixed wake is session-scoped: it targets stable pane lane metadata inside
the requested tmux session and must not infer work from mutable Codex pane
titles, stale panes, or other sessions on the host.

For the first visible demo, prefer a Codex CLI goal/pane-driven surface with a
LoopX state-driven frontier. Do not replace it with a hidden dynamic workflow
driver. The launcher may open panes and broadcast the fixed wake prompt, but
dependency ordering must live in normal LoopX todos, resume gates, quota, and
frontier projection. For example, an evaluator pane should wait on executor
evidence through a resumable todo instead of closing its own review todo when
evidence has not landed yet.

The user still only supplies one open question; agent ids, pane-local tick
commands, evidence schemas, and runner wiring stay inside the kernel. Pass
`--attach` when you want to skip the default visible role wake and immediately
enter the tmux session.
By default, visible panes open in a stable user-owned workspace under
`~/loopx-auto-research/<run>/visible-workspace` so the first screen does not
land in a generated temp directory. Pass `--workspace` to choose a different
scratch directory.

## Start From A Clean Workspace

Use a user-owned directory for the visible demo, while keeping LoopX state in
the normal shared control plane. This keeps research scratch files separate
from the LoopX repository but lets every lane read the same registry, quota,
todo, frontier, and rollout-event state. For the smoothest first run, start
from a directory the Codex CLI already trusts, or from a plain non-git demo
directory you own.

```bash
mkdir -p loopx-auto-research-demo
cd loopx-auto-research-demo
export LOOPX_REGISTRY="$HOME/.codex/loopx/registry.global.json"
export LOOPX_RUNTIME_ROOT="$HOME/.codex/loopx"
```

Install or repair the CLI when needed:

```bash
curl -fsSL https://huangruiteng.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor
```

## 0. Prove The Worker-Loop Positive Path

The fastest honest positive check is the one-question start path. It seeds a
fresh demo-local LoopX goal, lets role-compatible workers read quota/frontier
state, opens visible Codex TUI panes by default, and keeps the first output tied
to the fixed user contract. It is intentionally small and still does not claim
that visible Codex lanes authored the research result unless a compact live
evidence packet is supplied.

To run the normal human-facing path and open visible panes:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --execute \
  --replace-existing
```

That command is the user-facing UX for auto-research. It creates a fresh
isolated demo goal by default, launches the visible tmux lanes, broadcasts the
fixed decentralized A2A wake prompt, and records compact wake/tick evidence
before user takeover. Each tmux window should open as a real interactive Codex
CLI TUI role, not as a JSON/status stream. Generic launcher internals stay
inside LoopX; the operator does not need to know `demo-e2e`, agent ids, wake
flags, or implementation paths.

Visible Codex TUI panes default to a stable
`~/loopx-auto-research/<run>/visible-workspace`, not to a demo-local git
worktree or generated temp directory. The demo registry, runtime root, queue,
and evidence state stay isolated, but the first screen should not be a
generated worktree trust prompt. If you want an explicit scratch location, pass
`--workspace "$HOME/loopx-auto-research-demo" --create-workspace`; avoid
pointing `--workspace` at the demo-local control-plane directory.

When this demo is being advanced from a broader productization goal such as
`loopx-meta`, do not change `--goal-id` to that meta goal. Omit `--goal-id` for
an isolated demo goal, or pass a purpose-built research frontier goal when you
want visible lanes to read an existing target. Add `--tracking-goal-id loopx-meta`
only when the caller needs metadata that says which parent goal is tracking the
product work; tracking metadata never drives the visible lane frontier.

If you want to inspect before opening visible Codex lanes, start with the
read-only dry-run. It tells the operator which command will run the
multi-round positive path:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?"
```

When the dry-run looks right, run the multi-round positive path:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --execute
```

That command starts visible panes, wakes each pane with the fixed
decentralized A2A prompt, and keeps the current shell available for the compact
JSON result. To attach immediately instead, pass `--attach`; that means
operator takeover first and skips the default visible role wake:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research start "How should we evaluate autonomous research agents?" \
  --execute \
  --attach
```

For JSON-only automation, make the headless path explicit:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research start "How should we evaluate autonomous research agents?" \
  --execute \
  --headless
```

Visible panes default to the Codex CLI TUI and the product start path stays in
the current shell after recording wake evidence. The pane-local
`$LOOPX_PANE_LOOPX` wrapper is for human-readable LoopX commands inside that TUI, and
`$LOOPX_PANE_LOOPX_JSON` is reserved for redirected machine artifacts. Raw JSON
should not be dumped into the first visible screen; future control surfaces can
render those artifacts separately.

Expected minimal E2E result:

- visible launch controls create real Codex TUI panes with role-local
  bootstrap prompts;
- each pane can run `$LOOPX_PANE_A2A_TICK` to read its own quota/frontier;
- `pane_local_a2a.worker_turn_configured` is `false` for the default
  auto-research preset;
- no dev or holdout metric is claimed unless a lane-authored public-safe
  evidence packet is explicitly supplied;
- visible launch controls stay separate from the research result and only prove
  that panes can be inspected, stopped, or retried.

Maintainer acceptance for this path is:

```bash
python3 examples/auto-research-layered-e2e-acceptance-smoke.py
```

That smoke checks the shortest layered contract, the visible Codex TUI runner
contract, todo/frontier routing, and the no-fake-metric boundary without making
the auto-research preset own generic runner machinery.

Concrete KNN visible command:

```bash
loopx --format json auto-research start \
  "How can the KNN solver improve exact-neighbor speedup?" \
  --preset knn-demo \
  --language zh \
  --execute
```

`--preset knn-demo` is the public signal that LoopX should materialize a small
generated KNN benchmark workspace. The visible panes get `solution.py` as the
only editable file, `task.py`/`eval.py`/`eval.sh` as protected files, and real
commands for `bash eval.sh dev` and `bash eval.sh test`. The open question names
the research topic; it does not smuggle the benchmark baseline into the system
through natural-language inference.

The generic path without a preset also works:

```bash
loopx auto-research start \
  "How should we evaluate whether multi-agent auto research creates value?" \
  --execute
```

KNN acceptance:

- the preset materializes the generated benchmark workspace, not an inferred
  natural-language baseline;
- panes are named by research role, not backend agent implementation names;
- the first tick is quota/frontier/status, not research completion;
- dev/holdout uplift is absent until visible roles write real public-safe
  evidence;
- the public boundary records no raw logs, private artifacts, credentials, or
  local absolute paths.

Remaining boundary: video/showcase evidence should come from visible Codex panes
performing the research work. A headless smoke can validate state plumbing, but
it is not a claim that visible Codex panes authored the research result.

For maintainer-level rehearsal, `demo-e2e --launch-visible --attach` is still
accepted as a lower-level command. It is not the user-facing product path; the
short `auto-research start "<open question>" --execute` command owns the
default visible role wake behavior.

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research demo-e2e \
  --agent-id auto-research-operator \
  --reasoning-effort high \
  --execute \
  --launch-visible \
  --launcher tmux \
  --attach
```

If a previous visible rehearsal is still alive, retry with
`--replace-existing` or stop it first:

```bash
tmux kill-session -t loopx-auto-research
```

The one-command path must not record raw logs, private artifacts, credentials,
or local absolute workspace paths. It may write only public-safe LoopX state
that the visible roles authored.

## 1. Preview The One-Command Start

The default user path starts from one open question and previews the visible
Codex TUI lane packet without starting processes.

```bash
loopx --format json auto-research start \
  "How should we evaluate autonomous research agents?"
```

When the preview is acceptable, launch the visible lanes:

```bash
loopx --format json auto-research start \
  "How should we evaluate autonomous research agents?" \
  --execute
```

The visible lanes use LoopX state for quota, frontier, todo projection, and
public-safe evidence. The first pane tick is only a guard/frontier read; the
research result must be authored by the visible roles, not by a hidden demo
metric loop. Raw JSON is written only to local artifacts. Lower-level
`demo-e2e` commands remain available for maintainers, but they are not the
user-facing entrypoint.

## 2. Inspect The Visible Employee Plan

The supervisor is a host launcher, not a leader agent. Start with the dry-run
packet and inspect it before launching Codex.

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research demo-supervisor \
  --goal-id loopx-auto-research-demo \
  --workspace "$PWD"
```

The default visible digital employees are:

| Pane | Role | What it owns |
| --- | --- | --- |
| `research-curator:research-curator` | Research curator | Keeps the research contract, protected boundary, metric, stop policy, evidence review, and operator gates explicit. |
| `hypothesis-proposer:hypothesis-proposer` | Hypothesis proposer | Turns ideas into todo-backed hypotheses, successor links, and retirement rationale. |
| `research-executor:research-executor` | Research executor | Executes selected hypotheses under an isolated attempt boundary when mutation is required and preserves scored or unscored evidence. |
| `evaluator-promoter:evaluator-promoter` | Evaluator/promoter | Checks holdout/verification evidence, classifies claims, and keeps promotion boundaries explicit. |

Each Codex TUI role must route through its own quota/frontier/worker-turn path
inside the pane. The supervisor only makes those roles visible and interactive.
The panes share the same LoopX goal surface: registry, runtime root, frontier,
todo projection, and evidence graph. Do not move every pane into an unrelated
empty workspace; isolate only mutating research-executor attempts with a claimed
git worktree or equivalent execution boundary.
Visible Codex TUI panes should default to the caller's workspace or an explicit
user-owned scratch workspace. They should not default into the demo-local
control-plane repository or generated lane worktrees, because that exposes
workspace-trust prompts before the user sees the actual research roles.

For compatibility or product experiments, `--agent` can still name explicit
lanes, including a separate evaluator-promoter lane.

## 3. Run The Worker Loop

The visible panes should do work through the same CLI path a heartbeat worker
uses: each turn re-reads quota, frontier, todo projection, and rollout evidence
before writing anything.
The user-facing terminal should first show the Codex CLI TUI. Role progress,
selected todo, action, and blocker summaries should appear as normal Codex
interaction when the role runs LoopX commands; raw JSON belongs in
`.public.json` artifacts, not on the first visible screen.

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research worker-loop \
  --goal-id loopx-auto-research-demo \
  --agent-id research-curator \
  --agent-id hypothesis-proposer \
  --agent-id research-executor \
  --agent-id evaluator-promoter \
  --max-rounds 4
```

When the dry-run shows the selected lane work is safe, add `--execute` and
`--complete-selected-todo`. This is the smallest real multi-agent loop: it is
state-mediated, not a hidden leader workflow.

## 3b. Stop, Takeover, And State-Aware Wake

Operator control stays on this command path:

- place `workspace/.loopx-auto-research-stop` to exit the next worker-loop round
  with `stop_reason = operator_stop_requested` (individual `worker-turn` calls
  remain available for takeover);
- pass `--execute --attach` on `auto-research start` for immediate tmux
  takeover without a background wake;
- pass `--no-attach --wake-visible-after-launch` when you want a state-aware
  wake that skips quiet-completion, empty-frontier, or quota-blocked lanes.

`--attach` and `--wake-visible-after-launch` cannot be combined. For the full
stop → takeover → resume cycle and the synthetic smokes that pin it, see the
[stop/takeover/wake walkthrough](../../docs/guides/auto-research-stop-takeover-wake-walkthrough.md).

## 4. Launch A Visible Rehearsal

Use tmux when available so the user can watch several Codex CLI TUIs in one
place:

```bash
loopx --registry "$LOOPX_REGISTRY" \
  --runtime-root "$LOOPX_RUNTIME_ROOT" \
  auto-research demo-supervisor \
  --goal-id loopx-auto-research-demo \
  --workspace "$PWD" \
  --execute \
  --launcher tmux \
  --attach
```

The user can stop a tmux rehearsal with:

```bash
tmux kill-session -t loopx-auto-research
```

Or take over a lane by attaching, interrupting the pane, and continuing from
the visible prompt:

```bash
tmux attach -t loopx-auto-research
```

## 5. Inspect Progress

Useful read-only checks:

```bash
loopx --registry "$LOOPX_REGISTRY" --runtime-root "$LOOPX_RUNTIME_ROOT" status
loopx --registry "$LOOPX_REGISTRY" --runtime-root "$LOOPX_RUNTIME_ROOT" \
  --format json auto-research frontier --goal-id loopx-auto-research-demo \
  --agent-id auto-research-operator
```

The demo is healthy when the user can identify the active hypothesis, see which
lane owns the next transition, inspect evidence or retry rationale, and stop or
take over before any private material, credential, protected file, or
production action is needed.

## Boundary

This command path is for local, visible, user-takeover rehearsals. It is not a
claim that a research result is production-ready, not a public first-screen
approval, and not permission to publish private evidence. Promotion still
requires rollout-backed evidence, held-out checks when relevant, and normal
LoopX gate/writeback rules.
