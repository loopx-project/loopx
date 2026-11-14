# RFC: Monorepo Distribution and Small-Kernel Runtime Composition (v0)

- **RFC status:** Accepted, proposal only (open decisions D1–D7 remain unapproved)
- **Delivery maturity:** Proposal
- **Authors / owners:** LoopX maintainers
- **Created:** 2026-09-26
- **Last normative revision:** 2026-10-29
- **Implementation baseline:** packaging inventory `2f3d13ae9`; runtime composition audit `9cae3b28939f72fa4aadab5cfe49e19bcbdab372`
- **Related contracts:** [TypeScript Control-Plane Migration v0](typescript-control-plane-migration-v0.md), [Extensions reference](../../reference/extensions.md), [Capability catalog](../../../loopx/capabilities/README.md), [Overall Roadmap v0](loopx-overall-roadmap-v0.md) (S2, S8, S12), [import-boundary tests](../../../tests/architecture/test_control_plane_import_boundaries.py)
- **Tracking issue:** [#5072](https://github.com/loopx-project/loopx/issues/5072)
- **Supersedes / closes:** none
- **Language mirror:** [中文版](monorepo-distribution-split-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 are the durable design and acceptance contract. Section 11 is the
normative delivery plan. Section 12 lists unresolved decisions; proposed answers
are not approval. Appendices are non-normative. This RFC ships an English
document and a `.zh-CN.md` semantic mirror; a difference between them is a
defect.

This is the single design for directory ownership, installable distributions
and runtime composition. Its original filename and tracking issue remain
stable. Merge accepts a design basis; it does not establish runtime delivery,
performance, host qualification or a new default. Historical measurements below
retain their original baselines.

---

## 1. Decision summary

1. **The repository stays one monorepo.** One PR flow, one CI, one release
   train. Splitting into several Git repositories is a non-goal for this RFC.
2. **What is installed becomes several distributions.** The single `loopx`
   wheel is replaced over time by `loopx-core`, `loopx-workspace`, and
   per-capability packages under `packages/`, with `loopx` retained as a
   meta-package that depends on all of them.
3. **Top-level regrouping happens before any packaging change.** `loopx/chat_*`
   moves to `loopx/chat/`, `loopx/*_goal_mode/` moves to `loopx/hosts/`, with
   compatibility re-exports for established public imports for at least one minor
   release. Internal-only callers move together and the old internal entry is deleted.
4. **A new architecture test pins top-level growth.** The number of `loopx/*.py`
   modules may only decrease. This joins the existing zero-exception
   import-boundary checks.
5. **Kernel-only install must not require Node.js at import time.** The TS
   effect runtime remains required for effectful commands; its absence must
   surface as the existing typed `node_unavailable` diagnostic, not as an
   install or import failure.
6. **Unchanged:** kernel authority, CLI compatibility baseline, extension
   lifecycle rules, public/private boundary, and every stable protocol.
7. **Not approved here:** graduating any package to its own repository, changing
   licences per package, or reducing any schema.

The distribution tiers compose **one governance implementation** into minimal
and standard presets. Minimal must execute and recover a real governed task;
standard adds the product composition around the same kernel. A research
configuration freezes one of these compositions plus declared experimental
changes, without becoming a third governance implementation.

Smallness is measured separately in installed dependencies, loaded code,
activated services and model-visible context. The package table defines what
can be installed, not what every command must load. Installing standard grants
no service activation or permission. Existing defaults and machine-enforced
work obligations remain until a qualified, explicitly disclosed migration.

## 2. Problem and motivation

A developer who wants a CLI-only or kernel-only LoopX installs the same wheel as
someone running the desktop workspace, the Lark manager, and every built-in
capability. Historical packaging inventory on `2f3d13ae9`:

| Fact | Baseline value |
| --- | --- |
| `loopx/*.py` flat modules | 148 files, ~79.8k lines |
| `loopx/chat_*.py` modules at top level | 38 |
| `loopx/*_goal_mode/` host packages at top level | 9 |
| `loopx/control_plane` / `capabilities` / `extensions` | ~130k / ~116k / ~44k lines in one package |
| Declared Python dependencies | `dependencies = []` |
| Runtime prerequisite for every install | Node.js ≥ 22.22.3 for the effect runtime |
| Kernel/CLI/top-level modules importing `loopx.capabilities` | 52 |
| Capability modules importing `loopx.control_plane` | 80 |
| Independently packaged extensions already in `packages/` | 9 |

Concrete failures this produces:

- **First-use weight.** "Install the package, run `loopx dashboard`" pulls the
  chat server, presentation assets, and all capability code even when the user
  only wants `loopx status` for one goal. The roadmap's first-use journey
  (S1/S12) cannot become light while the wheel is monolithic.
- **Undiscoverable ownership.** A contributor looking for "where does chat
  live" finds 38 sibling files next to `quota.py` and `todos.py`. Module-level
  CODEOWNERS and first-round reviewers cannot be expressed cleanly on a flat
  namespace.
- **Two packaging conventions.** Finance, JEV, Obelisk and repo-health already
  ship from `packages/` with their own `pyproject.toml` and `extension.toml`,
  while 33 built-in capabilities ship inside the kernel wheel. New capability
  authors have no rule for which convention to follow.
- **Unbounded top-level growth.** Import-boundary tests protect inward edges of
  `control_plane`, but nothing prevents the next 40 `something_*.py` files from
  landing at the top level.

The owners of these surfaces cannot fix this locally: a capability author cannot
decide the kernel's packaging, and the kernel cannot decide which capabilities
are optional without a repository-level rule.

Directory and package changes also need an execution contract. A short CLI read
should not initialize a research stack or IM service; an embedded managed task
should not require an interactive App. Smaller installed code alone does not
prevent eager imports, idle polling or excessive context on every model call.
Section 9 therefore qualifies both structural moves and useful task journeys.

The design must support these observable journeys:

| Journey | Required outcome | Composition implication |
| --- | --- | --- |
| Bounded managed task | Start without an interactive App, execute, settle and read back the result | Minimal includes one qualified host path and durable authority |
| Long-horizon research | Produce a useful first result, then evidence-backed revisions or new outcomes as work continues | Optional planning, research and memory strategies; progress is more than elapsed time or repeated summaries |
| Two or three cooperating peers | Return evidence to the coordinator, record adoption, recover ownership after interruption | Reuse shared work/lease authority; peer lifetime and result adoption are distinct |
| App or IM request | Pause, correct, resume and receive the accepted result at the original destination | Frontends and delivery providers compose around the same work identity |
| Hardware or external operations | Execute several steps, reconcile uncertain effects, and verify stopping at the relevant boundary | An explicitly activated operation provider owns readback, interlocks and safe retry |
| Reproducible benchmark | Compare qualified product compositions with native controls and complete costs | Freeze experiment inputs; keep native runner, verifier and scoring ownership |

These are shared product requirements, not a commitment to ship every journey
or every combination at once. Content, office and engineering capabilities
use the same boundaries without becoming mandatory startup dependencies.

### Invariants

- I1. One repository, one PR, one CI run for any cross-package change.
- I2. Kernel truth (Goal/Todo/claim/lease/quota/effect/receipt) has exactly one
  owner and one implementation path per transaction; packaging never introduces
  a second copy.
- I3. Established public `import loopx.<old_module>` contracts keep working for
  at least one minor release after a move, with a documented deprecation.
  Internal-only moves update all active callers and delete the old entry; retain
  an internal bridge only for a named unmigrated caller and explicit exit condition.
- I4. A missing optional distribution degrades to the same behaviour as the
  extension "off state": core commands work, absent capabilities are reported as
  unavailable, never silently substituted.
- I5. Installing the kernel-only distribution does not require Node.js; running
  an effectful command without Node.js yields the typed `node_unavailable`
  diagnostic.
- I6. No public/private boundary rule, schema field, or licence changes as a
  side effect of a move.
- I7. Unselected optional components do not initialize, register timers/hooks,
  use the network or inject model instructions. Missing execution prerequisites
  fail before dispatch instead of falling back to ungoverned execution.
- I8. Required context remains complete and fresh; smaller prompts cannot drop
  requirements, required reads or stop conditions. Replay reconciles durable
  identities and external evidence before retry.
- I9. Performance and outcome claims name the measured composition, baseline,
  environment and scope. File-count reduction and faster help do not establish
  better task outcomes.

## 3. Scope and non-goals

### In scope

- Directory regrouping of `loopx/` top-level modules with I3 compatibility.
- An architecture test that pins the `loopx/*.py` count.
- Definition of three distribution tiers and the rule for which code belongs
  where.
- Migration of built-in capabilities to `packages/` behind the existing
  `CapabilityRegistry` provider boundary.
- Optional-extra or bundled delivery of the Node effect runtime for
  `loopx-core`.
- Minimal/standard composition, demand loading, activation, context boundaries
  and matched product/benchmark qualification using the same typed owners.

### Non-goals

- Splitting into multiple Git repositories (see Section 6 and Appendix D).
- Changing licences per distribution; all tiers remain Apache-2.0 unless a
  separate licensing RFC decides otherwise.
- Any change to kernel semantics, TS migration order, or stable protocols.
- Removing legacy compatibility facades (`loopx.status`, `loopx.quota`); their
  retirement stays governed by the migration RFC.
- A new model loop, scheduler, plugin system, memory database, benchmark-native
  runner, storage migration, device framework or mandatory daemon. No benchmark
  run, device operation or host promotion is authorized by this proposal.

## 4. Current-system contract

Facts audited on `2f3d13ae9`:

- `pyproject.toml` builds one distribution `loopx` with
  `packages.find(include=["loopx*"])`, ships TS sources and JSON as package data
  for `loopx.control_plane*`, and declares five console scripts.
- `loopx/control_plane/effect_runtime.py` probes Node at first effectful use
  and already produces `node_unavailable` as a typed startup diagnostic; the
  gap is that install documentation and the desktop path treat Node as a hard
  prerequisite for everything.
- `tests/architecture/test_control_plane_import_boundaries.py` rejects
  control-plane imports of presentation, CLI, capability, or benchmark-adapter
  layers with zero exceptions. It does not constrain top-level module count or
  capability → kernel imports.
- `packages/*/extension.toml` plus `loopx.extensions.manifest` define the
  extension lifecycle (readiness, version, default-off, uninstall). This is the
  contract capability packages must adopt; it is not redefined here.
- `docs/reference/extensions.md` already states that a capability is a product
  contract while an extension is a delivery unit. This RFC applies that rule
  to the kernel wheel itself.

Runtime composition audit on `9cae3b28939f72fa4aadab5cfe49e19bcbdab372`:

- [entrypoint.py](../../../loopx/entrypoint.py) has a small version path and
  selected native scheduler follow-ups. [cli_runtime.py](../../../loopx/cli_runtime.py)
  dispatches selected commands and retains a full-parser fallback.
  `performance-diagnosis` is registry-optional but outside the selected-command
  set: a bounded loading seam, not proof all startup is lazy.
- The [unified controller/executor RFC](controller-executor-unified-contract-v0.md)
  owns the governed work contract; [session modes](agent-session-execution-modes-v0.md)
  own managed/attached admission; [DSH/Pi selection](harness-selection-dsh-pi-v0.md)
  owns host qualification. Composition reuses these contracts.
- [Benchmark toolkit](../../../loopx/capabilities/benchmark_toolkit/README.md)
  owns reusable permissions, concurrency and integrity. [Benchmark policy](../../../benchmark/README.md)
  keeps native execution/evaluation with the benchmark; no second launcher is restored.
- The [three-layer protocol](https://github.com/loopx-project/loopx/blob/910ff3bca5c8ff5ee52bd691ae924cc37dbaf05f/docs/reference/protocols/multi-agent-three-layer-minimality-v0.md)
  uses “kernel” for a broader reusable layer. Here, governance kernel names the
  authority boundary and boot closure names a selected entrypoint's required code.
  A reusable runner or presentation facility need not enter every boot closure.

> The tmux multi-agent and auto-research demos are retired. Demo-contract links
> above are pinned to the pre-retirement revision for historical design context;
> they do not describe current entrypoints or shared runtime ownership.

## 5. Proposed architecture

### Ownership and authority

| Distribution | Contains | Owner boundary | Depends on |
| --- | --- | --- | --- |
| `loopx-core` | `loopx/control_plane`, `loopx/cli_commands`, `loopx/semantics`, minimal host adapters under `loopx/hosts/`, `loopx check`, doctor, status | Kernel maintainers | Python ≥ 3.11; Node effect runtime as `loopx-core[runtime]` extra or bundled artifact (D1) |
| `loopx-workspace` | `loopx/chat/`, `loopx/web`, `apps/presentation`, dashboard launcher, desktop shell glue | Frontstage maintainers | `loopx-core` |
| `loopx-capability-<name>` (in `packages/`) | one capability's contract, providers, CLI subcommands, docs | Capability owner from CODEOWNERS | `loopx-core`; optionally other capability packages |
| `loopx` (meta) | no code | Release owner | all of the above, pinned to one release train |

This is an installation layout. CLI handlers and host facilities inside
`loopx-core` are not thereby part of every startup dependency closure. Optional
provider distributions keep the existing `packages/<package-id>/` convention;
the capability-package row does not require one capability per provider.

Authority does not move: the kernel remains the only writer of Goal/Todo/claim/
lease/quota/effect state. A capability package may only register providers,
capability contracts and CLI subcommands through `CapabilityRegistry`; it never
imports kernel-private modules.

### Governance and runtime ownership

| Boundary | Existing owner and responsibility | Excluded responsibility |
| --- | --- | --- |
| Bootstrap | Entrypoint/CLI routes resolve commands and installed metadata | Goal decisions or provider activation during discovery |
| Governance kernel | Typed Goal, work, quota, runtime and coordination owners: requirements, admission, lease, budget, stop, effect, receipt, settlement and recovery | Model/tool loop, task strategy, UI, transport or scoring |
| Host | Admitted managed/attached provider: model/tools, session lifecycle and observations | A second work ledger or independent permission authority |
| Capability strategy | Nearest outcome owner: planning, context selection, research, recall and review | Overriding mandatory transitions, leases, stop or budget |
| Extension and product surface | Existing lifecycle/registry; frontends and delivery providers consume public-safe projections and submit commands | New plugin machinery or deriving authority from display/private source text |

Placement introduces no capability or provider id: governance remains in
`control_plane`, generic lifecycle in `extensions`, task behavior in its nearest
capability, and optional distributions in `packages/`. Keep new domain-neutral
rules in the existing typed TypeScript owner; Python may adapt but must not
reimplement state rules. A new capability needs an independently useful
provider-neutral caller contract. A real host/device integration owns its
provider-specific execution and recovery, without creating a generic framework.

A planner can recommend work or stride; the owning transition decides admission
and obligations. Presets cannot turn a machine-enforced `must_attempt_work`
contract into prose guidance. The kernel is a cohesive set of domains, not one
large file. The adjacent refactor should localize the next change and preserve
characterized behavior.

### Presets over the distribution tiers

| Composition | Required content | Explicit optional content |
| --- | --- | --- |
| Minimal | Bootstrap, same governance implementation, one supported authority provider, one qualified execution path, compact status/result readback and recovery | Task capabilities, persistent recall, observers, connectors, workspace UI |
| Standard | Same kernel and compatible ordinary-product composition | Load/activate only the capabilities, hosts, UI/IM services and connectors required by the chosen workflow |
| Research configuration | Pinned minimal or standard composition plus a declared experiment overlay | Only instrumentation or ablations named in the experimental design |

The `loopx` meta-package retains installation compatibility; installed standard
content is not an activation list. Minimal is an executable composition, not a
flag applied after standard fully initializes. A host-free read-only kernel
library does not qualify that executable preset. Exact contents and the first
supported host/authority combination are D2/D5 decisions.

Keep composition, managed/attached mode, host provider, one/two model sessions,
storage provider and benchmark arm independent. Minimal does not imply attached,
standard does not require two model sessions, and research does not mean
ungoverned. Publish only tested combinations and preserve existing mode defaults;
changing composition must not silently change a binding's execution owner.

### Supported host integration decision

Separately installed `generic-cli` host transports consume the existing Turn
wire protocol rather than register a capability. Their supported Python seam is
`loopx.extensions.turn_host`: it re-exports the single kernel owners for signed
request extraction, candidate construction, Goal instance validation and result
validation, and supplies a raw stdio relay over the existing TypeScript process
supervisor. The public `loopx.file_lock` API remains the owner of private
provider-state locking and atomic writes. Neither import grants work authority.

MemT uses exactly those two public modules; it must not import the executor or
other kernel-private modules directly. This is an explicit integration seam,
not permission for arbitrary package-to-kernel imports. The import ratchet
records both edges. Existing Ark/JEV private edges remain migration debt and
receive no new exemption. The facade forwards existing behavior; it must not
copy candidate, acceptance, process-lifetime or locking decisions.

The independently installed Codex provider-routing host uses
`loopx.extensions.chat_service_guard` for its existing authorized restart of
managed Chat consumers. The facade exports only `managed_chat_activation_guard`
and `RuntimeActivationDeferred` from the current runtime activation owner.
Accepted-work observation, exact service identity and the ingress lock stay
with that owner; the package neither reads private Goal state nor copies the
decision. Missing or unsupported Core defers the service restart. The no-service
path remains independent of Core. This one supported public edge is pinned;
it grants no authority to stop a service or add other kernel imports.

The guard's refusal is exactly as narrow as that observation. It holds the
ingress admission lock and refuses to continue while a non-terminal Turn
exists. A pending ingress receipt reserves external input before the provider
delivers it, so it is not a Turn, and the guard does not read the ingress
receipt ledger. A pending `loopx_queue` or `loopx_inbox` receipt therefore
passes through, while a queued Turn is refused with `chat_work_pending`.

The stdio relay cancels its owned process group on parent EOF, SIGTERM or
SIGINT and waits for the existing supervisor's cleanup before exiting. POSIX
process-group behavior and Windows best-effort tree cleanup retain their
existing qualification boundary. It is process supervision, not a sandbox;
operator-selected tools retain only their already authorized effects.

### Responsibility and installation acceptance

Directory regrouping and distribution splitting have separate acceptance.
For each move, show the rule's single owner and changed callers, import direction,
remaining compatibility seams and the failure-to-owner diagnosis path. For each
distribution, verify clean minimal installation, optional-package absence,
upgrade and rollback through its supported commands. Use the TS migration
payoff table for cross-runtime cost; do not introduce a parallel scorecard.

The file-count ratchet remains an architectural guard, not the optimization
objective. Moving a file while retaining its shim does not reduce that count;
combining unrelated responsibilities to meet it is not a successful refactor.
Measure representative change locality and installation cost separately.

### State model and schema

No canonical record changes. The only new durable artefact is the architecture
fixture:

```text
tests/architecture/top_level_module_budget.json
{ "schema": "loopx_top_level_module_budget_v0",
  "baseline_commit": "<sha>",
  "max_top_level_modules": 148,
  "allowlist": ["__init__.py", "entrypoint.py", "cli.py"] }
```

`max_top_level_modules` may only be lowered in a PR that also moves files. The
allowlist names modules that must remain at the top level for entry-point
reasons.

### Command or event lifecycle

Moves are executed per group with this fixed sequence:

1. `git mv` the group into its package (`loopx/chat/`, `loopx/hosts/`, or
   `packages/loopx-capability-<name>/src/`).
2. Inventory public imports and active internal callers. Re-export established
   public names at the old path with `DeprecationWarning` once per process.
   Update internal-only callers and remove the old entry; name any necessary
   migration bridge and its removal condition.
3. Remeasure top-level files, including retained shims, and lower
   `max_top_level_modules` only by the actual net reduction.
4. Run the import-boundary tests, the budget test, `loopx check` on touched
   docs, and the package smoke lane.

A move PR that fails any of these is not mergeable; a move PR that changes
behaviour is rejected as out of scope.

### Provider or extension contract

Capability packages adopt the existing `extension.toml` contract without
extension. The single new rule: **a capability under `loopx/capabilities/` that
has no kernel caller on `main` must move to `packages/`** before it accepts new
features. The kernel-caller inventory is the 52 import sites named in Section
2; each one is either retargeted to a registry lookup or documented as a
retained core capability (D2).

### Demand loading and context

1. **Resolve:** read local versioned metadata generated at build/install or
   explicit extension registration. Top-level help, command listing and unknown
   command rejection must not scan repositories, import every provider or use
   the network. Duplicate registrations and stale/incompatible metadata are
   reported; precedence is explicit, never import-order dependent.
2. **Load:** import the selected command and its dependency closure. Detailed
   command help may load its own parser, but not activate execution services.
   Optional imports stay behind their owning route, including fallback paths.
3. **Activate:** validate installed/enabled/ready state and scoped permissions,
   then start required services through their existing lifecycle owner. Missing
   optional code reports how to install/enable it. Missing required authority
   or runtime fails closed. Kernel import/help must not require Node; effectful
   commands retain this RFC's typed `node_unavailable` boundary.
4. **Provide context:** render the existing governed work contract for the
   selected work and executor. Reuse full-first, differential continuation and
   recovery semantics from the unified controller/executor design. Required
   reads and source completeness remain binding.

Context reuse must be invalidated by relevant source/requirement revisions,
session generation changes, compaction or lost acknowledgements. A smaller
packet cannot be treated as proof the executor has the prior context. Recover
through the owning contract when continuity is unknown. Capability instructions
are supplied when applicable; authority is never inferred from prompt text.

Optional observers consume committed events or bounded projections. Their
latency must not enter admission/settlement transactions unless the existing
contract explicitly requires their receipt. Batch best-effort telemetry only;
do not defer authoritative receipt persistence, weaken fsync/transaction
guarantees or cache stale permission/lease decisions to meet a timing target.

### Governed lifecycle and external effects

Use existing Goal/Agent/work/Turn identities, revisions, leases, effect ids and
receipts through the unified work contract. Resolve composition, verify
requirements and authority, admit work, execute through the bound host, validate
and persist evidence, settle once, then deliver/read back through the owning
channel. Each step uses its existing legal transitions and replay rules;
this description adds no competing status enum or transaction coordinator.

An executor crash, a lost effect acknowledgement and a lost result-delivery
acknowledgement are different recovery cases. An operation provider must expose
whether an effect can be reconciled and retried safely. Unknown external state
holds further dependent effects for readback or intervention; “not observed”
does not mean “not executed.” Device idempotency, physical interlocks and
compensation remain provider responsibilities, not invented kernel guarantees.

Model cancellation, process termination and confirmed device stop are separate
facts. A stopped model must not produce a “device stopped” claim without device
evidence. Providers needing persistent supervision cannot be unloaded while
effects are unresolved. Result acceptance, transport delivery and the user's
readback must also retain their existing distinct evidence.

### Product and benchmark execution

Follow the [long-horizon research program](long-horizon-harness-benchmark-research-program-v0.md):
native/plain controls, passive observation, governed execution and ablations
have explicit identities. A native control receives no LoopX instructions or
hooks. Passive observation must qualify non-interference. Governed arms use
the same qualified composition and transitions as product tasks; an ablation
that changes governance is a named experimental variant, never a product default.

Freeze product/host versions, resolved composition, model and sampling settings,
skills/tools, initial state, budget, checkpoint/recovery policy, dataset and
verifier versions. Scope memory by run/arm and prohibit cross-arm contamination.
Report controller, executor, compaction, retry, recovery and human-intervention
costs as well as task outcomes. Native runners/verifiers retain task semantics
and scoring. Performance work cannot bypass toolkit permission/integrity checks
or publish raw tasks, trajectories or verifier logs as public product evidence.

Long-horizon product qualification additionally needs evidence that later work
improves an already useful initial result or changes a decision. Deliberately
weakening the initial result, adding more text or running longer is not progress.

## 6. Alternatives and design choices

| Alternative | Why not chosen now |
| --- | --- |
| **Split into several Git repositories** | Violates I1 during the TS transaction cutover: kernel semantics change daily and downstream repos would break on every change. Multiplies CI, release and review cost while review capacity is the scarce resource. Reopen only when the kernel migration is complete and a package has independent maintainers (Appendix D). |
| **Keep one wheel, add extras only** | Reduces install weight but leaves the flat namespace and the two packaging conventions in place; contributors still cannot find boundaries. |
| **Move everything to `packages/` at once** | Breaks I3 for many callers simultaneously and mixes behavioural risk into a structural change. Per-group moves with I3 compatibility are cheaper to review and to roll back. |
| **Bundle Node into `loopx-core` unconditionally** | Solves I5 but makes the kernel wheel platform-specific and large. Kept as option D1 alongside the optional-extra approach. |
| **Separate minimal and standard engines** | Duplicates state transitions and breaks product/benchmark parity. |
| **Make every governance rule a plugin** | Budget, stop, lease and settlement could disappear with a preset. |
| **Use an upstream agent runtime as the LoopX kernel** | Model execution can be a host provider; it does not replace LoopX work authority. |
| **Require one daemon or remote service for every composition** | Adds lifecycle and deployment cost to bounded local use; any optional daemon remains under its existing contract. |

Pi demonstrates a small agent core with tool/event execution and explicit
context transformation separate from the coding product. DSH demonstrates
shared runtime composition through multiple presets and a self-contained
minimal SDK bundle. At the pinned revision, DSH web minimal and standard use
the same agent-preset package with different plugin lists; minimal includes
persistent shell, image reading and compaction. Its SDK minimal also supplies
working-directory state and persistence with optional configured MCP resources.
These are composition examples, not evidence that DSH has exactly two engines
or that LoopX's headless adapter and DSH plugin correspond to those presets.
Borrow the explicit boundaries, without adopting Cordis, upstream permission
defaults or a new LoopX model loop. Pinned sources are in Appendix C.

## 7. Safety, privacy, and compatibility

- **Default-off / feature-off parity:** absent `loopx-workspace` or capability
  packages behave like the existing extension off state (I4). No command
  silently falls back to another implementation.
- **Public/private boundary:** unchanged. `loopx check` continues to scan every
  package; moving a file does not change its scan class.
- **Legacy readers/writers:** import shims (I3) cover established public Python imports. Console
  scripts keep their names; `loopx` meta-package keeps `pip install loopx`
  working identically for one minor release.
- **Mixed versions:** the meta-package pins all tiers to one release train.
  Mixed-version installs are unsupported and reported by `loopx doctor`.
- **Fail-closed:** an effectful command without the runtime extra fails with
  `node_unavailable`; it never proceeds without the TS kernel.

Host availability, installation and capability declarations are observations,
not grants. Preserve Goal/Agent/workspace/delivery scope through provider calls.
No required context or stop/budget rule becomes optional with a preset.
Unmigrated CLI routes retain fallback until parsing, global options, output,
errors and exit codes qualify; incompatible versions fail before dispatch.
Retire the full parser only after every supported command has migrated and
help and diagnostic parity pass. Never run two writers against one binding
to make a mixed-version rollout appear available.

## 8. Migration and rollback

| Step | Gate | Rollback |
| --- | --- | --- |
| Budget test added at current count | none; additive | delete fixture |
| `chat_*` → `loopx/chat/` with I3 compatibility | import-boundary + budget + smoke green | revert PR and caller updates; retain required public shims |
| `*_goal_mode` → `loopx/hosts/` with I3 compatibility | same | same |
| Publish `loopx-core` / `loopx-workspace` / meta `loopx` | package-smoke lane installs each on a clean runner | unpublish pre-release; `loopx` meta keeps old layout one release |
| Capability package moves | per capability, kernel-caller inventory resolved | revert one package |
| Shim removal | one minor release after the move; deprecation recorded in update notes | not applicable; requires migration by callers |

Before changing an active binding's composition, check compatibility, quiesce
its execution owner and reconcile outstanding effects. Verify the new owner
and resolved composition before resuming; never hot-swap across an active
lease. Rollback uses the same reconciliation and retains durable state.
Any later irreversible data change needs a separate migration/export gate.

Rollback of these structural/composition changes requires no data migration.
Default changes need explicit owner decisions and release notes; this proposal
changes no current default.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Top-level module count never grows | `tests/architecture/test_top_level_module_budget.py` | fails when `len(loopx/*.py) > max_top_level_modules` | Does not judge module quality |
| Old imports keep working after a move | import tests for every moved public name | pass with one `DeprecationWarning` | One minor release only |
| Kernel-only install without Node | package-smoke lane on a runner without Node: `pip install loopx-core && loopx --help && loopx status --format json` | exit 0 | Effectful commands excluded |
| Effectful command without Node fails typed | same runner: `loopx heartbeat-prompt ...` | `node_unavailable` diagnostic, exit non-zero | — |
| Absent capability package = off state | install `loopx-core` only; `loopx capability list` | capability reported unavailable, core commands unaffected | — |
| No kernel truth duplicated | import-boundary tests + review of `packages/*/src` for kernel-private imports; interim guard: `tests/architecture/test_package_kernel_import_ratchet.py` pins every `loopx.*` import under `packages/*/src`, fails on an unreviewed new edge, and requires a retired edge to lower the pin in the same PR | zero private-kernel violations | Static imports only. The 13 pre-existing edges in Ark, Finance and JEV remain tracked migration debt, separate from MemT's 2 supported public integration imports. A green ratchet does not retire that debt or establish this row; supported public edges need not disappear |
| Meta-package parity | `pip install loopx` then existing full smoke | identical to pre-split smoke | — |

The seven structural rows above remain required for their move/package
milestones. Runtime and journey qualification adds the following rows.

These rows are proposed acceptance criteria, not claims of passing evidence.
Deterministic fixtures cover semantics independently of observed implementation
output. Live, performance and promotion evidence are reported separately.

| Id / claim | Test or evidence | Required result | Boundary |
| --- | --- | --- | --- |
| V1 / demand loading | Import/process/network tracing for import, help, listing, unknown command and a selected route, under both presets | Only declared dependency closure; no optional activation | Does not prove execution performance |
| V2 / authority parity | Characterization and negative tests for requirements, stale claims, stop, budget, replay and settlement | Identical legal/illegal transitions in both presets, exactly one settlement | No authority bypass in minimal |
| V3 / CLI compatibility | Differential supported-command corpus, including fallback and missing Node/provider cases | Stable parsing, output contract, exit codes and actionable failures | Unmigrated routes are explicitly listed |
| V4 / managed minimal | One real managed task, injected interruption, restart, evidence validation and result readback | Same identity and reconciled effects; no duplicate dispatch of confirmed work or settlement; recovery costs recorded; accepted result reachable | One qualified host is enough initially; mock-only execution is insufficient |
| V5 / context continuity | Initial context, unchanged continuation, source revision, compaction and lost-ACK cases | Complete required context or explicit recovery; stale context rejected | Token reduction measured separately |
| V6 / standard journey | Original App/IM request, correction/pause, resume, completion and return | One execution owner; visible accepted result and costs; no silent destination change | Each frontend/provider qualifies separately |
| V7 / research and peers | Useful initial result followed by evidence-backed improvement; peer result return and adoption after owner recovery | New outcome or justified decision revision with attributable evidence | More time, peers or prose alone cannot pass |
| V8 / benchmark fidelity | Frozen matched runs, native/passive/governed boundaries, memory isolation and full cost ledger | Native semantics intact; C0–C4 evidence follows the research/diagnostics owners | No score or causal benefit is claimed by this RFC |
| V9 / physical effects | Simulator/fault injection, then explicitly authorized provider-specific live qualification | Lost ACK reconciled, duplicate actuation prevented where guaranteed, device stop independently confirmed | No universal exactly-once or emergency-stop guarantee |
| V10 / overhead | Repeated paired cold-process and warm-path measurements on a named environment | Report median/p95, RSS, modules, file I/O, Node startup/IPC, bytes and model tokens; no durability regression | R0 sets numeric budgets; no paid task run required for CLI baselines |

Candidate local-development targets are import below 50 ms, top-level help below
100 ms and selected command help below 200 ms at p95. They are D6 proposals,
not verified results or portable CI thresholds. Define interpreter startup and
cache conditions, sample count and machine/runtime versions before adopting
them. Measure real status/admission/settlement latency separately on
representative small and large state, including SQLite contention/recovery;
do not apply a CLI-help target to those operations or subtract their required I/O.

## 10. Operational contract

- `loopx doctor` reports the installed distribution set and versions and flags
  mixed release trains.
- `loopx capability list` distinguishes "not installed" from "installed, not
  ready".
- Release notes name public import moves and each retained shim’s expiry release.
- No new daemon, storage or network surface is introduced.

Readback also names the resolved composition/versions, selected host and
session mode, required versus optional providers and why a component loaded.
Reuse current diagnostics/projections; do not add a status database. Distinguish
missing prerequisites, stale authority and uncertain effects. Record startup
and Turn overhead separately; observer failure does not rewrite committed
results, while required evidence/delivery receipts remain governed by their
owner. Capacity/backpressure and recovery retain existing work/host contracts.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Budget fixture and test at the current count; RFC index entry | this RFC accepted; remeasure the fixture baseline | budget test green; count pinned | delete test |
| R0 | Characterized `performance-diagnosis` selected route and measured loading seam | Current route/owner/dependency map; D6 measurement decision; independent of package publication | V1/V3 for this route, V10 baseline; unchanged behavior | Revert route extraction |
| M1 | `loopx/chat_*` → `loopx/chat/` with I3 compatibility; budget lowered by net reduction | M0 | Section 9 rows 1–2 | revert |
| M2 | `loopx/*_goal_mode` → `loopx/hosts/` with I3 compatibility | M0 | rows 1–2 | revert |
| M3 | `loopx-core` + `loopx-workspace` + meta `loopx` published as pre-release; Node as extra or bundle per D1 | M1, M2; D1 decided; selected D2/D5 composition mapped | rows 3–4, 7; repeat qualified R1/R2 journeys against clean split-package install | Unpublish pre-release |
| R1 | Opt-in executable minimal with one real managed Turn, restart and result readback | R0; selected D2 contents and D5 host/authority; may qualify source composition before M3 | V1–V5/V10 for the supported combination; source evidence alone does not qualify split-package installation | Return to previous qualified composition after reconciliation |
| R2 | Standard uses the same kernel and activates one chosen product workflow | R1; existing frontend/session admission; may qualify source composition before M3 | V2/V5/V6/V10 and feature-off parity; repeat cold-install journey at M3 before claiming split-package qualification | Disable opt-in composition, retain state |
| M4 | First capability without kernel callers moved to `packages/` | M3; D2 inventory | rows 5–6 | revert package |
| M5 | Remaining eligible capabilities moved; public shims from M1/M2 removed after the documented compatibility window | M4 | full smoke identical | not applicable |
| R3 | Matched product-path benchmark and a long-horizon/peer journey | R1 or R2 for the selected surface; frozen design and separate run authorization | V7/V8, complete costs, privacy and existing C0–C4 qualification | Retire experimental overlay, retain bounded evidence |
| R4 | One real external-operation provider with reconciliation and stop readback | D7; named operation owner and explicit operation authorization | V9 plus inherited governance gates | Quiesce/reconcile before disabling |

M0–M5 retain the original distribution milestones; R0–R4 are their runtime and
journey qualification slices in this same plan. R0 can ship before directory
moves; it does not qualify the kernel. R3/R4 are optional and do not block a
qualified CLI or managed task. No unused provider, field or framework is added
ahead of its real caller. Report the applied/deferred adjacent refactor in each PR.

## 12. Open decisions

1. **D1 — Node delivery for `loopx-core`.** Owner: kernel maintainers. Options:
   (a) `loopx-core[runtime]` extra that documents Node as prerequisite; (b)
   bundle a pinned Node runtime as a platform wheel; (c) both, with (a) as
   default. Recommendation: (c). Evidence needed: wheel size and platform
   matrix from the package-smoke lane. Deadline: before M3.
2. **D2 — Which capabilities remain in core.** Owner: kernel + capability
   maintainers. Input: the 52 kernel-side import sites. Recommendation: keep
   only capabilities that the Turn driver, quota or heartbeat prompt require at
   runtime; everything else is a package. Resolve accidental eager imports
   through the registry rather than treating their existence as proof of core
   need. Record exact install/load/activation closures for minimal and standard.
   Deadline: selected composition before R1/M3; full inventory before M4.
3. **D3 — Shim lifetime.** Owner: release owner. Options: one minor release
   (recommended) or two. Deadline: M1.
4. **D4 — Index placement.** Whether this RFC is listed under "Control-Plane
   Kernel, State, And Migration" or "Runtime, Capability, And Collaboration
   Integration". Recommendation: kernel section, since the budget test and
   `loopx-core` boundary are kernel-owned.

5. **D5 — First executable composition.** Owner: runtime/host and authority
   maintainers. Choose the exact minimal host/storage combination and standard
   mapping under the existing package scheme. Recommend opt-in minimal and
   the smallest already qualified managed path, retaining current defaults.
   DSH/Pi qualification stays with its selection RFC. Evidence: V4 recovery and
   session continuity before R1 exit, package readback before M3.
6. **D6 — Performance budgets.** Owner: performance maintainers. Choose
   machine-specific p95 budgets or relative regression limits; recommend the
   Section 9 candidate budgets with explicit process/cache/sample conditions.
   Evidence: reproducible R0 baseline; derive real Turn budgets independently.
7. **D7 — External operation caller.** Owner: operation-provider maintainer.
   Choose one concrete caller or defer physical qualification. Recommend no
   device framework before a real caller. Scope, idempotency/readback/stop
   contract and operation authorization are required before R4.

The added recommendations do not close D1–D4 or approve any new default.
Approval needs a reviewed decision, not implementation progress or silence.

---

## Appendix A: Execution ledger (non-normative)

### 2026-09-26 — RFC opened

- **Baseline:** `3e443ad7c`
- **Delivered:** proposal only; metrics in Section 2 measured on this baseline.
- **Evidence:** file counts from `ls loopx/*.py`, `ls loopx/chat_*.py`,
  `ls -d loopx/*_goal_mode`; import sites from `rg` over `loopx/`.
- **Known gaps:** all milestones.
- **Effect on normative design:** none.

### 2026-09-27 — Pre-merge re-measurement at the rebased base

- **Baseline:** `2f3d13ae9` (the rebased PR base; `main` advanced 174 commits
  between the RFC being written and this merge).
- **Delivered:** no design change. Section 2 metrics, the Section 5 fixture
  example, the Section 4 audit revision and the Appendix C rows were
  re-measured at the new base so M0 pins a count the tree actually has.
- **Evidence:** the same commands as Appendix C E1–E3; measured drift is
  `loopx/*.py` 143 → 148, `loopx/chat_*.py` 37 → 38, capability →
  `control_plane` imports 74 → 80, `loopx/*.py` lines 78.8k → 79.8k. The
  kernel-side capability import count (E2, 52) and the `packages/` count
  (9) are unchanged.
- **Known gaps:** all milestones; D1–D4 remain open.
- **Effect on normative design:** the budget numbers in Sections 2 and 5
  follow the new base; the decision to lower the count over time is unchanged.

### 2026-10-14 — Package → kernel import pin

- **Baseline:** `70bc98198`.
- **Delivered:** `tests/architecture/test_package_kernel_import_ratchet.py` and
  `package_kernel_import_pins.json` pin the 13 `loopx.*` modules that three
  co-located packages import today. A new edge fails; a removed edge must
  lower the pin in the same PR.
- **Evidence:** AST scan of `packages/*/src/**/*.py`, including function-body
  imports. `loopx-ark-turn` imports `file_lock` and
  `control_plane.turn_driver.host_candidate` without declaring `loopx` as a
  dependency; `loopx-jev` imports seven `control_plane`/`capabilities`
  modules; `loopx-finance-value-discovery` imports two `extensions` modules.
- **Known gaps:** the Section 9 row keeps zero violations as its required
  result; the 13 pinned edges are the migration backlog for M4/M5, not a
  sanctioned surface, and a green ratchet does not satisfy that row or the
  M4/M5 milestones that cite it. D1–D4 remain open.
- **Effect on normative design:** none; the required result is unchanged and
  the evidence column gains an executable interim no-growth check beside the
  manual review.

### 2026-10-15 — Checkpoint host composition and presentation SDK boundary

- **Baseline:** `77b404b92`.
- **Delivered:** native checkpoint inspection/reconstruction and research context
  composition move from `control_plane` to the existing `hosts` owner in
  Section 5. All internal CLI, benchmark and test callers move together; neither
  old module shipped in v1.4.0, so no public compatibility shim is retained.
  Typed state/effect authority remains in the existing control plane.
- **Boundary repair:** Finance consumes the shared local-path classifier through
  its existing `extensions.presentation` SDK import. It adds no package-to-kernel
  edge or copied privacy matcher. The pinned 13 legacy imports remain unchanged.
- **Evidence:** architecture/import ratchets, Finance's minimal public SDK fixture,
  real native checkpoint capture/fork/adoption and negative consistency cases.
  Capture keeps one inspection process across both logical reads and physical
  sealing, retaining the byte witness rather than reopening its history writer.
- **Remaining:** the wider M1–M5 distribution and zero-private-import acceptance
  is not closed; D1–D4 stay open. No packaging or authority decision changes.

### 2026-10-30 — Codex provider-routing activation guard boundary

- **Baseline:** `10c84d1c0c`.
- **Delivered:** the Section 5 seam for the Codex provider-routing host now
  states what its guard actually refuses. It holds the ingress admission lock
  and refuses to continue while a non-terminal Turn exists. A pending ingress
  receipt reserves external input before the provider delivers it, so it is not
  a Turn, and the guard does not read the ingress receipt ledger.
- **Boundary repair:** a pending `loopx_queue` or `loopx_inbox` receipt passes
  through, while a queued Turn is refused with `chat_work_pending`. The
  package's supported edge stays the single pinned
  `loopx.extensions.chat_service_guard` import; this entry changes no import,
  permission, default or persisted format.
- **Evidence:** `tests/test_chat_runtime_activation.py::
  test_a_pending_ingress_receipt_alone_does_not_defer_the_restart` and
  `test_a_queued_turn_still_defers_the_restart_with_a_pending_ingress_receipt`
  read both sides of the boundary through a real temporary Session and ingress
  store, against the real guard.
- **Remaining:** the wider M1–M5 distribution and zero-private-import acceptance
  is not closed; D1–D4 stay open. A pending ingress receipt remains uncovered
  by the guard, and covering it would be a runtime change with its own
  acceptance, not a documentation change.

### 2026-11-03 — Selected host-mode discovery loading

- **Baseline:** `6a6a8590e`. Earlier selected routes for `performance-diagnosis`
  and `goal-acceptance` are already on this baseline; the Section 4 audit above
  remains historical, not the current implementation inventory.
- **Delta:** `host-mode-plan` uses its existing registrar and handler through
  `cli_runtime`, without loading the full CLI or Chat graph on valid requests.
  Rejected syntax retains the full-parser diagnostic path. The host selector,
  preview commands, readiness and authority rules do not move or change.
- **M1 integration:** [#7878](https://github.com/loopx-project/loopx/pull/7878)
  is merged at `ffe84a693`: 17 internal-only Chat owners are under `loopx/chat/`,
  with the top-level count and budget both 128 (145 before this move group).
  This is a structural delta, not a performance or full-M1 qualification.
- **Evidence:** `tests/test_cli_entrypoint.py` checks owner-only help, JSON and
  Markdown parity, unavailable-host previews, invalid requests and zero process,
  network or file-write activation. A separately installed wheel exercises help,
  ready/unavailable previews and rejection without Node or a live registry.
  Local paired fresh-process measurements use 20 samples per revision/workload,
  one discarded warm-up and alternating order, with telemetry off and warm file
  caches. On macOS arm64/Python 3.13.5, the help/preview LoopX closure falls
  from 1,149 to 57 modules; byte-identical output is retained. Timing/RSS belong
  to the PR's scoped evidence, not a portable threshold or task-outcome claim.
- **Remaining:** this extends R0-style loading qualification to discovery;
  it does not complete R0's full V10 census, M1 public-import migration, split
  packaging or R1 managed minimal. D1–D7 remain open. No new preset or default.

### 2026-11-03 — M1 public status compatibility characterization

- **Boundary:** `ChatStatusRequestMixin` and `goal_read_runtime_root` still live
  in `loopx.chat_status_api`. The Chat HTTP handler and Todo detail are active
  consumers. This is a characterization prerequisite, not a relocated owner.
- **Compatibility evidence:** `tests/test_chat_status_public_compatibility.py`
  protects historical imports in three fresh-process import orders, historical
  pickle lookups, reload with already-imported references, and live registry,
  collector and host-observation patch points through the real HTTP handler.
  Discovery tolerates a missing registry, not unreadable or invalid metadata.
  A copied-export facade mutation fails the source-bound Todo-consumer patch
  oracle: re-exported functions alone do not preserve legacy module globals.
- **Next reviewed slice:** move the complete status owner under `loopx/chat/`
  with an identity-compatible public bridge and documented I3 deprecation.
  Retarget both the exact loader path and `_discovery_registry` exception in
  `tests/architecture/test_source_session_registry_denial.py` together, retaining
  metadata-only discovery and executable source-session refusal. The present
  slice leaves that owner and oracle unchanged; do not split ownership or relax
  the denial oracle just to make a partial move pass.
- **Remaining:** status relocation and its installed-wheel integration are not
  delivered by characterization. I3 retains at least one minor release; D3's
  exact expiry remains a release-owner decision. M1 is open. No module-count,
  loading-performance, split-package or R1 managed-Turn benefit is claimed.

### 2026-11-04 — M1 public status owner migration

- **Delta:** the complete `ChatStatusRequestMixin` and `goal_read_runtime_root`
  implementation moves to `loopx.chat.status_api`. Chat HTTP and Todo detail
  import that owner directly. The characterization prerequisite
  [#8001](https://github.com/loopx-project/loopx/pull/8001) is already merged.
- **Direct migration:** the historical top-level `loopx.chat_status_api` module is
  removed rather than retained as an alias or warning shim. The repository callers
  and published-surface audit found no external importer; all active callers and
  tests now use `loopx.chat.status_api`. This intentional compatibility exemption
  applies only to this internal-only relocation and creates no general precedent
  for removing a supported public import.
- **Authority boundary:** retarget both exact owner-path entries in
  `tests/architecture/test_source_session_registry_denial.py`, leaving every
  denial assertion and the `_discovery_registry` exception unchanged. Real HTTP
  coverage permits lifecycle-only registry discovery but refuses full status
  before collector execution. Moving the presentation owner grants no new
  runtime, source, provider or execution authority.
- **Evidence:** owner tests now require the canonical import and absence of the
  top-level shim. Existing HTTP, File/SQLite source routing, source loss/recovery
  and delivery-review tests protect the original behavior. Absolute-import-
  resolved AST comparison covers the complete owner; the denial oracle differs
  only in its two owner paths.
- **Remaining:** this is one public-owner M1 slice, not completion of the wider
  public Chat migration, D3, split distributions or R1 managed minimal. The
  removed internal-only top-level module is one representational cleanup, not
  evidence of loading-performance or long-horizon benefit. Source and isolated-
  wheel qualification do not establish an upgrade of an existing installed host.

### 2026-11-04 — Capability discovery selected loading

- **Baseline:** `79b781865`. `capability list` / `show` and command help now
  reuse the existing capability registrar and catalog handler without loading
  the full CLI or Chat. `inspect`, `bind` and `invoke` execution, the explicit
  full-CLI handler patch point, registry routing and canonical rejected-request
  diagnostics retain their existing paths. The local discovery set reuses CLI
  vocabulary; it creates no capability, provider, protocol or authority owner.
- **Evidence:** `tests/test_cli_entrypoint.py` covers output/help parity,
  declared-but-absent and real installed-disabled providers, hidden helpers,
  missing/invalid manifests and no process/network/write activation. The five
  loading oracles fail on the baseline and pass on the candidate. All 23 focused
  cases also pass against an independently installed wheel outside the checkout
  with Node absent; this does not upgrade an existing host. Eight additional
  immutable base/candidate rejection pairs preserve output and exit status.
- **Measured closure:** three fresh-process help/list/show workloads use 20
  samples per revision, one discarded warm-up and alternating order on macOS
  arm64/Python 3.13.5. Both source snapshots have warm filesystem caches and no
  bytecode; Node and telemetry are absent. The real `main()` path loads 362
  LoopX modules instead of 1,149, with 832 read-open observations instead of
  2,494. Output bytes are identical; observed writes, process starts, network
  connections and thread starts are zero. Timing and RSS evidence includes
  equal instrumentation and interpreter startup; it is scoped PR evidence,
  not a portable D6 threshold or installed-host/task-benefit claim.
- **Validation holds:** the broader source selection has 320 passing cases and
  one failing `test_settlement_plan_import_does_not_load_native_capture_io`.
  The immutable baseline fails identically. Configured mypy likewise reports
  the same 789 errors in 126 files on both revisions. Neither failure is waived
  or relabeled as a pass; focused parity does not certify all required checks.
- **Remaining:** this extends R0-style discovery qualification, independently
  of the public-status proposal #8011. It does not close full R0/V10, M1, split
  installation, R1 managed minimal or D1–D7. No preset, frontend operation,
  permission or persisted state changes; protected execution keeps its owner.

### 2026-11-07 — Caller process-transport loading proposal

- **Baseline and boundary:** `7f521ff4505fa916a98a7401032922189eef414a`
  loads `extensions.process_runtime` during the pure Turn-decision import.
  Doctor Git, contract scanning, caller validation and Host bridge transport
  now resolve their existing reexports on use. The original function objects,
  signatures, direct/from/star imports, discovery, caller patch points,
  preloaded-owner bindings and reload behavior remain available. This uses the
  existing lazy-export pattern; the export-name set is local compatibility
  metadata, not a shared state vocabulary. Python retains platform transport;
  TypeScript retains decisions, deadlines, authority and receipts. No provider,
  capability, default or process algorithm is added.
- **Executable evidence:** the four cold-caller oracles fail on the immutable
  baseline. Focused compatibility checks and the existing architecture oracle
  pass on the proposal; the Turn-preview unselected-module guard is retained.
  Real isolated Git, validation and bridge tests cover timeout, cancellation,
  exited-leader descendants, stdin policy, framing and unconfirmed cleanup.
  A newly built monolithic wheel repeats compatibility and real-process checks
  outside the checkout. Complete public discovery/star name sets and transport
  signatures match the baseline. This is source/wheel proposal evidence, not an
  upgrade or adoption result.
- **Measured scope:** pure Turn-decision import loads 337 LoopX modules instead
  of 338. Pure import and help start no processes or connections; supported help
  and unknown-command output/exit status remain identical. Shared synthetic
  SQLite fixtures exercise accepted and rejected registered Turn plans: both
  revisions start two Node processes and thirteen Git probes, with 62/61
  connection observations respectively. Their complete returned envelopes match
  except the source-decision digest from fresh observations. Registered plans
  still select process transport; the entire preview dependency closure is not
  qualified as cold. One removed import does not establish latency, RSS,
  complete-cost, managed-Turn or long-horizon benefit.
- **Remaining and rollback:** retain I3 for at least one minor release. D2, D5,
  D6, I3, D3 and full R0/V10/M1/R1/context/model/install acceptance remain open.
  Canary passes its six direct checks and 17 of 18 selected checks.
  `first-connect-contract-smoke.py` fails with `KeyError: should_run` on both
  baseline and proposal; its inline-field assumption needs separate repair.
  This failure and existing broader lint/browser failures remain open.
  The completed author proposal may enter normal review with disclosed
  holds; protected runtime merge still requires maintainer authorization.
  Revert this caller loading change to restore eager binding without state
  migration; no unrelated language rewrite is required.

### 2026-11-06 — History reader selected loading

- **Baseline and boundary:** `22b0f6c25`. Ordinary `history` reads and help use
  the existing registrar/handler through the shared CLI grammar and guard.
  Maintenance actions, rejected parser requests and a preloaded full CLI retain
  the original route, including handler/renderer patch points. History algorithms,
  evidence windows, public imports and the TS owner remain unchanged. This is a
  reversible R0 reader slice in the monolithic distribution; I3 still requires
  at least one minor release for public moves.
- **Real-path evidence:** 27 actual-console cases per baseline/candidate source
  and installed-wheel environment preserve stdout/stderr/exit codes, UTC order,
  Agent filtering, limits, empty/unknown inputs and current/stale evidence reads.
  Four additional installed/source facade probes preserve patched handlers and
  renderers. The preloaded-handler oracle failed before its correction; final
  history/parser selection passes 26 tests. Existing durable history, artifact
  and evidence oracles are retained. Required native services use disposable
  process and data namespaces, with authenticated exit readback.
- **Matched measurement:** Apple M4 Pro, 48 GiB, macOS 27 arm64, Python 3.13.5,
  Node 26.0.0; matched Python dependencies and source-pair Node dependencies.
  Each of four workloads has 30 interleaved baseline/candidate pairs, precompiled
  bytecode and fresh Python processes with warm native-service reuse. The final
  sampling takes 229 seconds; all three rounds total 698 seconds, below the
  frozen 20-minute sampling bound. Setup, validation and tracing are separate.
  Earlier environment/refinement observations retain their original scope.

| Workload | Median / p95 elapsed ms, base → candidate | Median CPU ms | Median CLI peak RSS MiB |
| --- | --- | --- | --- |
| Source help | 1552 / 1862 → 204 / 349 | 670 → 100 | 91.4 → 31.3 |
| Source Agent read | 1603 / 1833 → 417 / 482 | 665 → 150 | 91.9 → 34.4 |
| Wheel help | 1621 / 2053 → 220 / 375 | 695 → 100 | 91.8 → 31.0 |
| Wheel Agent read | 1648 / 1825 → 414 / 462 | 725 → 150 | 92.2 → 34.6 |

- **Loading and effects:** source/wheel help loads 155 rather than 1157 LoopX
  modules; registered reads load 208 rather than 1158, evidence reads 210 rather
  than 1159. Python audit read-open observations decrease from 1290 to 246 for
  source help and 1311 to 723 for its cold native read; wheel observations are
  1291 → 247 and 1710 → 723. Help has zero observed writes/processes/connections.
  Cold registered reads retain six write-opens, two process starts and one
  connection, with 252/355 client IPC bytes sent/received. Evidence reads retain
  one write-open, two connections and 1377/804 IPC bytes. These required effects
  are not optional activation. A serving runtime can work without Node on PATH;
  cold absence retains the existing failure. Output bytes match. Timing includes
  interpreter and process invocation; descendant CPU/RSS/I/O, full model and
  attention costs remain unknown.
- **Holds and remaining work:** measured help p95 remains above the proposed
  200 ms target; D6 is not adopted. Source-session loader inventory and Turn
  preview import checks fail identically on the untouched baseline. Their failures
  are retained as qualification holds. This does not close full R0/V10, M1,
  split installation, R1/context/host qualification or open design decisions.
  Revert the loader slice for rollback; no state migration is involved.

### 2026-11-06 — Selected version loading and matched costs

The independent proposal `8a79d8602e699e51452ca1f43dc9b9527c81aa1b` at baseline
`5a58635028fbef1316dc66531daf5fdb655e9b13` selects the existing version registrar
and handler through `cli_runtime`. It reuses the canonical global grammar,
format precedence, usage hooks and controller guard. Rejected parsing retains
full-CLI fallback and diagnostics; explicitly bound package or full-CLI exports
retain their registrar/handler patch points without triggering lazy exports
on ordinary queries. Public imports, identity and types remain
unchanged. No capability, provider, package, permission, preset or TypeScript
decision owner is added. The separate DSH/helper and negative cost proposal is
[#8185](https://github.com/loopx-project/loopx/pull/8185); its E7/E8 observations
are distinct from this version-path comparison and are not a dependency.

The initial `55e3ef45a` comparison retains 120 ordinary-query observations
(source median 1.546 → 0.059 seconds; wheel 1.554 → 0.072). A later independent
case finds that it ignores a patched package-facade handler, returning 0 instead
of 37. Its passing premerge did not cover that branch. The failure and original
inputs/data remain retained; they do not qualify facade compatibility. The
proposal repairs both package registrar and handler adoption, then freezes the
new exact-head comparison below.

- **Real caller:** source and freshly installed monolithic wheels at both
  immutable revisions exercise the actual `loopx` console. All 27 cases per
  distribution have identical stdout, stderr and exit status, including
  global/local/repeated formats, explicit paths, help, unknown/abbreviated
  options, missing/empty values and fallback. Independent unknown-command/
  option cases reject with exit 2 and no result. Version works with Node absent
  in all four variants; outer-controller read behavior and full-CLI patch points
  remain compatible.
- **Loading census:** separate parent instrumentation records 1,157 → 10
  LoopX modules in both distributions, with the candidate contained in the
  baseline set. Source read-opens are 1,286 → 28 and wheel 1,289 → 29.
  Each version capture has zero write-opens, subprocesses, observed local IPC
  connections and covered socket bytes. Stdout remains 81 bytes; stderr is empty.
- **Frozen comparison:** all four environments have verified provenance,
  CPython 3.13.5 and identical 57 dependency versions. Stdlib, dependencies and
  all 1,421 LoopX sources have valid timestamp bytecode in owned cache prefixes;
  post-measurement/profile source/header inventories have no missing/invalid
  caches and match the preflight. Every sample has fresh Python and disables
  bytecode writes; disk cache is uncontrolled. No broker/warm-broker comparison
  is made. Order is source then wheel, AB on even pairs and BA on odd pairs.
  Both corrected groups complete 30 pairs: 120 accepted observations in 62.246
  seconds. Including the first study, aggregate sampling is 166.191 seconds,
  below the unchanged 1,200-second cap. All observations are retained.

| Distribution | Baseline seconds, median / p95 | Proposal seconds, median / p95 | CPU seconds, median (base → proposal) | RSS MiB, median (base → proposal) | Median paired proposal minus baseline, seconds |
| --- | --- | --- | --- | --- | --- |
| Source | 0.980 / 1.056 | 0.055 / 0.075 | 0.520 → 0.037 | 90.17 → 16.81 | -0.9242 |
| Monolithic wheel | 0.948 / 1.147 | 0.056 / 0.077 | 0.511 → 0.038 | 89.89 → 16.60 | -0.8993 |

p95 uses nearest rank; paired differences and differences of arm medians are
distinct statistics. Median latency falls about 94% in both source and wheel
on this macOS arm64 environment; CPU and target RSS also fall. One-minute host
load ranges from 8.25 to 9.06. This qualifies a material version-query
improvement under frozen conditions, without adopting portable D6 budgets or
claiming real managed-Turn/long-horizon savings. Usage covers the target and
waited descendants; no child is launched in the census. Native detail and
complete authoring/model/maintenance costs remain unknown.

The heartbeat-to-final-freeze interval is about 2,404 seconds, including
overlapping recovery, initial study, correction, dependency/build/install,
test, cache and parity work; overlapping phases cannot be summed. Cache
preparation is recorded separately. Corrected console/facade parity takes
82.759 seconds, instrumentation 2.539 and four parent profiles 4.155. Earlier
parity/instrumentation/profile costs of 136.239 / 3.830 / 7.089 seconds remain
retained. Cross-study timing levels are not treated as a code comparison.
Existing `performance-diagnosis` recipes show export loading and full parser
construction in baseline sampled stacks; weights remain separate from repeated
latency. Monolithic packaging is verified, without split-install or installed
host-upgrade qualification.

Seven selected-loading cases fail on the baseline and pass on the proposal.
The package registrar/handler case passes on the baseline, fails on the initial
candidate and passes on the correction; canonical diagnostic and full-CLI
patch-point cases pass in both baseline and proposal. All 161 entrypoint cases and
focused lint pass. Owner-scoped mypy with `--follow-imports=skip` fails with
33 identical diagnostics at base/proposal after line normalization; this is
neither a pass nor the full configured type check. Adoption requires final-head
validation, independent review and merge readiness. I3's minimum one minor
release window remains in force; no D3/expiry decision is made. R0's wider census,
R1/minimal, D2/D5/D6, hard-lease/context/model and split-install acceptance remain
open. The bounded refactor reuses its existing parser/handler; larger runtime
and package changes retain their accepted owners.

### 2026-11-06 — Discovery integration and the next executable boundary

- **Integrated:** [#8012](https://github.com/loopx-project/loopx/pull/8012)
  merged the proposed head `58442be4d` as `83f569178` on November 4. On the
  independently prepared `main` revision `62e234c93`, all 23 discovery and
  protected-dispatch cases pass. Replay with
  `uv run --extra test python -m pytest -q tests/test_cli_entrypoint.py -k 'capability_discovery or capability_non_discovery'`.
  This verifies current source integration; the earlier installed-wheel and
  paired performance evidence remains bound to its original revision and
  environment. No installed host or new wheel is qualified by this replay.
- **Resolved prerequisite:** [#8054](https://github.com/loopx-project/loopx/pull/8054)
  (`886f2cdff`) removed capture I/O from the settlement plan's cold import.
  `uv run --extra test python -m pytest -q tests/architecture/test_control_plane_import_boundaries.py::test_settlement_plan_import_does_not_load_native_capture_io`
  passes on `62e234c93`. The November 4 failure remains historical evidence;
  the later fix removes that specific import blocker. The broader suite and
  configured mypy were not rerun on this revision, so their earlier results
  do not become a current passing qualification.
- **Next source slice:** characterize the existing `generic-cli` managed Turn
  path with SQLite authority and optional task hooks disabled, as a candidate
  for the selected D2/D5 composition. Reuse the Turn, host selector and typed
  authority owners. Existing CLI interruption and successor-replay tests in
  `tests/test_loopx_turn_driver.py` locate integration seams; they do not
  establish a minimal preset. The next slice must name the actual dependency
  closure and exercise an isolated task-producing host process, interruption,
  restart and independently validated result readback with original identities
  and no repeated confirmed effects. A deterministic local host can qualify
  its synthetic transport boundary, not a model host or complete V4 journey.
- **Remaining:** full R0/V10, M1/I3/D3, the separate status proposal #8011,
  split installation and R1 remain open. D2/D5 are still owner decisions;
  this candidate changes no preset, default, execution authority or release
  commitment. Further directory moves are not the next execution evidence.

### 2026-11-06 — Proposed generic-cli/SQLite composition and recovery evidence

This is a D2/D5 candidate and source characterization, not an approved preset.
It reuses the existing Turn, host selection, authority and settlement owners.

| Dimension | Candidate / observed boundary |
| --- | --- |
| Installation | Current monolithic `loopx` source checkout; Python 3.13.5 and Node 26.0.0 on macOS arm64. Test extras prepare the experiment, not a minimal install. No split distribution or installed host is qualified |
| Host | Existing `generic-cli`, `isolated-headless`, JSON stdio via `--host-adapter-command-json`; one local deterministic task-producing process and a separate trusted argv validator. No model session or workspace/IM service is needed by this fixture |
| Authority | Canonical SQLite through the existing TypeScript effect runtime and its local RPC transport; existing Turn journal, validation and settlement. The test scaffold seeds a `soft_claim` shadow; this evidence cannot alone qualify normal hard-lease V2 parity |
| Required logical core | Bootstrap/selected CLI, TurnEnvelope and host admission, current authority/stop/budget/claim rules, journal/recovery, process supervision, independent validation and result/settlement readback. Their owners remain shared with standard |
| Optional activation | No task-memory or other optional hook is enabled in the fixture; no model jobs or external operations. Imported settings/policy helpers are not proof of activation or permission to remove them |
| Identity and recovery | Resume the original journal with `--resume-turn-key`; a fresh `--turn-instance-id` is intentionally refused on that path. Cached confirmed host work is reused; unconfirmed work and other interruption points remain separately governed |

- **Actual task and recovery:** a real host process writes the squared integers
  zero through seven; an independent process checks the expected literal array.
  Both the ordinary path and an OS `SIGKILL` of the owned CLI after its typed host
  result was persisted end with one host invocation, one writeback and one debit
  per isolated SQLite fixture. The interrupted path has no debit before restart;
  restart validates the cached result without invoking the host again. A further
  committed replay reports no repeated host/writeback/debit/scheduler effect.
  The artifact bytes match across both paths. No active project state was faulted.
- **Actual loading:** at product revision `62e234c93`, the ordinary Python path
  loads 538 LoopX modules across 21 capability namespaces, without `loopx.cli`
  or the `loopx.chat` tree. A second drill uses an isolated existing-runtime
  broker and a transparent Node module-load observer: its bootstrap, execution
  and recovery load 231 TypeScript files and eight JSON files under
  `control_plane`. These are observed closures, not approved core membership.
  The private broker's identity, shutdown acknowledgement and process exit were
  verified; shared services were not stopped.
- **Costs and limits:** parent-side tracing distinguishes required local RPC
  from external network connections; none of the latter is observed. The
  ordinary invocation observes 34 Git subprocesses, one Node version probe, one
  Node host supervisor and one Python validator; the supervisor launches the
  task host. Child/broker CPU and RSS, full wire/context bytes and model-host
  costs remain unmeasured. These small instrumented drills are not repeated
  matched performance samples, a D6 budget or long-horizon benefit evidence.
- **Independent regression evidence:** the six SQLite cases of
  `test_managed_start_interruption_preserves_reservation_and_attempt`, plus
  `test_turn_run_once_cli_rejects_unproven_host_claim_before_writeback` and
  `test_turn_run_once_cli_replays_declared_successor_after_interruption` pass in
  `tests/test_loopx_turn_driver.py`. Probe setup refusals remain separate from
  product failures: an overbroad network observer, an incorrect host-request
  schema and conflicting resume flags were corrected without changing production
  code or relaxing authority; failed observations were retained.
- **Next owner change:** `loopx/cli_commands/turn_run_once.py` imports DSH host
  builders before selecting the host, although their call sites are under
  `args.host == "dsh"`. The generic path therefore loads
  `turn_dsh_host` and `dsh_goal_mode.turn_host_adapter` despite selecting neither.
  Characterize the existing exports/patch points, then defer those builders at
  their owning seam and recheck generic/DSH/Codex behavior and default-off parity.
  Namespace counts alone do not justify deleting shared policy helpers.
- **Remaining:** opt-in minimal selection, complete D2/D5 adoption, hard-lease
  and context V2/V5 parity, interruption during unconfirmed host work, model-host
  V4, full V10 and split-install qualification remain open. No new engine,
  provider, default, authority field or runtime self-merge is introduced.

### 2026-11-07 — Heartbeat pre-quota selected loading

- **Baseline and owner:** `79665b97a`. The heartbeat builder already emits
  `heartbeat-prequota` when external evidence polling is available. Its selected
  parser reuses the existing heartbeat registrar; dispatch uses the existing
  heartbeat transport module. The complete facade passes its original runner,
  renderer and global-registry resolver patch points. Explicit/implicit routing,
  aliases, format, timeout, exception, degraded-return and controller semantics
  remain characterized; reconciliation/hook ordering and typed owners are unchanged.
- **Source and package evidence:** 50 new transport/loading/real-backend cases;
  22 cases on each of four independently verified source/wheel consoles; 16
  configured/degraded File/SQLite console scenarios with replay, retained opaque
  work and zero quota debit. Dropping `continue_to_quota` is rejected by the same
  public-console oracle on both real stores. The existing acknowledgement smoke
  now exercises both CLI facades. The full source selection initially has 378
  passes and two route-test failures reproduced on the immutable baseline. The
  fixture now reads the original digest-bound progressive capture without
  re-admission; its complete affected file then passes all 17 cases. No failure
  was waived. Focused lint, configured mypy and the code canary pass.
- **Matched local cost:** macOS 27.0 arm64, Python 3.13.5, Node 26.0.0; identical
  dependencies and synthetic stores, precompiled bytecode, fresh Python processes,
  warm filesystem caches and reused isolated Node services. Thirty alternating
  base/head pairs for each workload/distribution yield 360 samples in 339 seconds;
  study setup/validation takes 260 seconds separately. This shared development
  host was not an idle-machine experiment. Values below are wall median/p95 in
  seconds and median process RSS in MiB, including interpreter startup.

| Workload / distribution | Base median / p95 | Candidate median / p95 | Base → candidate RSS |
| --- | --- | --- | --- |
| `source/prequota-help` | 1.433 / 1.889 | 0.172 / 0.226 | 91.5 → 24.7 |
| `source/prequota-file-off` | 1.494 / 1.927 | 0.545 / 0.667 | 92.0 → 47.0 |
| `source/prequota-sqlite-off` | 1.547 / 1.977 | 0.507 / 0.640 | 91.9 → 46.3 |
| `wheel/prequota-help` | 1.555 / 1.967 | 0.170 / 0.210 | 91.7 → 24.5 |
| `wheel/prequota-file-off` | 1.545 / 2.155 | 0.536 / 0.650 | 91.7 → 46.7 |
| `wheel/prequota-sqlite-off` | 1.397 / 1.866 | 0.531 / 0.667 | 92.0 → 46.9 |

- **Closure and complete-cost limits:** help loads 29 LoopX modules instead of
  1,158; default-off execution loads 277 instead of 1,160. Python open observations
  fall from 1,292–1,312 to 81–395. Opened-file size sums are footprints, not actual
  read bytes. Median user/system process CPU is 0.04/0.02 seconds for candidate
  help and 0.13–0.14/0.065–0.08 for execution, versus 0.40–0.42/0.16–0.185 at base.
  Output remains 737 bytes for help and 827 for JSON execution. A separate SQLite
  loopback profile observes 1,362 sent and 668 received bytes on each revision,
  two Node starts for version/server discovery with a cold service and none with
  a reused service. Its single instrumented cold/warm observations do not isolate
  Node readiness or service CPU; those costs remain unqualified. The initial IPC
  probe assumed Unix sockets and failed; loopback instrumentation corrected the
  probe without changing product behavior. Help p95 of 0.226/0.210 seconds exceeds
  the proposed 0.200-second target; that D6 proposal is not promoted or relabeled.
- **Remaining:** this is one R0/V1/V3/V10 caller proposal, preserving I3 for at
  least one minor release. Full R0/V10, M1/#8011, R1, context/model and split-install
  qualification, installed-host recovery, adoption and complete task costs remain
  open. The bounded Python change adapts transport; it adds no decision owner,
  preset, provider or authority rule. Required final-head review/CI remains a
  delivery gate distinct from these local observations.
### 2026-11-07 — Registry inspection selected loading

- **Baseline and boundary:** `ac6296225`. `registry` and `registry-boundary`
  use the existing selected CLI route. Their shared registrar and transport
  adapter stay in `cli_runtime`; the explicit full CLI reuses them through
  `support_control`, retaining its inspection and renderer patch points.
  Registry parsing, classification and Git decisions keep their existing owner.
  Python adapts the CLI here; no new decision owner, provider or dispatcher
  framework is introduced. Existing imports and I3's minimum one-minor-release
  compatibility window remain intact.
- **Compatibility evidence:** 197 source CLI and support-ownership cases pass,
  including 26 new inspection/result/rejection/patch-point cases and two help
  cases. An independent full-CLI loading exclusion fails on the immutable base
  and passes on the candidate through the same public entrypoint. Twenty
  successful and rejected requests preserve output, status and exception
  semantics across source and independently installed monolithic-wheel consoles.
  Help and read paths also work with Node absent and exclude the unselected
  Chat, Dashboard and benchmark execution modules. Both revisions pass the real
  Git registry-boundary smoke in disposable temporary directories; the earlier
  run under `.local` failed because that ancestor intentionally selects a private
  registry classification. The failure is retained as fixture-placement evidence.
- **Cost qualification:** 30 interleaved base/head pairs for each of four
  help/read workloads on both source and wheel yield 480 samples in 416.7s;
  study setup/compatibility/profiling takes another 278.1s, excluding earlier
  environment/wheel preparation and tests. On macOS arm64/Python 3.13.5 with
  identical dependencies, precompiled bytecode and warm filesystem caches,
  fresh-console help medians fall from 1.536–1.609s to 0.100–0.103s; registry
  reads from 1.584–1.631s to 0.301–0.310s, and boundary reads from 1.542–1.612s
  to 0.222–0.225s. Median RSS falls from about 91–92 MiB to 16–30 MiB.
  Both help closures fall from 1,158 to 9 LoopX modules; registry/boundary reads
  fall to 148/44. The PR records per-workload median/p95, CPU, separate audit
  open counts and unchanged output bytes. No Node starts/IPC or Python-audit
  socket events are observed; boundary reads keep their three Git queries.
  Warm-cache block I/O is zero, while exact bytes read and native activity
  outside the audit remain unknown. These results qualify local CLI cost
  reductions, not portable D6 limits, full preparation/model costs or adoption.
- **Validation and remaining work:** changed-file lint passes. The CI lint scope
  still fails on the baseline-existing F841, whose repair is already proposed
  separately; this result is not waived. Source and wheel evidence does not
  upgrade an installed host. M1/#8011, full R0/V10, R1's real managed Turn,
  context/model composition, split installation, adoption and complete costs
  remain open. No D2/D5/D6/D3, default or authority decision changes. The related
  bounded refactor shares the existing grammar and inspection adapter; larger
  support/control extraction remains outside this slice.
### 2026-11-06 — Selected Turn host loading seam

Following the generic-cli/SQLite candidate in
[#8180](https://github.com/loopx-project/loopx/pull/8180), the source proposal
`9b80cf7d6` defers DSH builders at `turn_run_once` until a caller selects DSH
or explicitly requests their established exports. Named imports, original
builder identity, static types, `dir()`, star imports and the CLI's builder
patch points remain available. The existing host/authority owners are reused;
this does not select a minimal preset or adopt D2/D5.

- **Observed delta:** the real generic-cli/SQLite task path loads 533 LoopX
  modules instead of 538 at `62e234c93`. The five removed modules are
  `turn_dsh_host`, `turn_driver.host_candidate`, `dsh_goal_mode` and its
  `host_failure_map`/`turn_host_adapter`. No module is added. The cold-import
  oracle fails at the baseline and passes at the proposal; the existing DSH
  CLI fixture also proves the public builder patch point is used.
- **Real-path parity:** the same private harness runs ordinary completion,
  interruption after typed host-result persistence, original-journal resume
  and committed replay on isolated SQLite fixtures at both revisions. Each
  fixture ends with one host invocation/attempt, one writeback and one debit;
  replay repeats none. Independent argv validators accept identical artifact
  bytes. Each arm uses an isolated native broker whose shutdown and OS exit are
  verified. This remains deterministic transport evidence, not model-host V4
  or normal hard-lease V2 qualification.
- **Observed costs:** one ordinary instrumented invocation per revision records
  2.962 → 3.169 seconds, parent CPU 0.737 → 0.775 seconds and parent peak RSS
  65.6 → 64.6 MiB; output remains 3,474 bytes. Waited-child CPU is
  0.567 → 0.599 seconds. Cache, run order and competing work are not controlled;
  these observations establish neither a latency improvement nor a regression
  threshold. Broker RSS observations and waited-child usage do not measure
  complete tree peaks, full wire/context bytes or model cost. The loading delta
  is small; long-horizon benefit and D6 qualification remain unproven.
- **Historical validation limits at `bc59a9ed4`:** the wider source selection records 596 passed and one
  failed preview cold-import test; the baseline independently fails the same
  test because `host_bridge_transport` eagerly imports
  `extensions.process_runtime`. Owner-scoped mypy with `--follow-imports=skip`
  reports the same 29 diagnostics at both revisions after line normalization;
  it is not the full configured mypy suite. Neither failure is waived or
  relabeled as passing. The process-helper seam below repairs that preview
  failure in the same author proposal while retaining the type-check limitation.
- **Remaining:** independent review, required validation and exact-head merge
  readiness still govern adoption. Opt-in minimal selection, D2/D5 decisions,
  full R0/V10/R1, hard-lease/context/model parity and split installation remain
  open; the separate #8011 status proposal retains its limits. No authority,
  provider, default, installed-host or release change is introduced.

### 2026-11-06 — Preview process helpers remain with execution

The follow-on runtime proposal `a44e27d96` repairs the unchanged preview import
oracle. Deferring the bridge helpers first exposes a second eager import in
`doctor_git`; both adapters now resolve the existing `process_runtime` owner
at execution/cleanup or explicit import. Named/star imports, original function
identity, static aliases, `dir()` and public patch points remain compatible.
The TypeScript Host lifetime and process/permission decisions do not move.

- **Observable result:** importing the preview command loads 337 LoopX modules
  instead of 338 at `bc59a9ed4`, removing only `extensions.process_runtime`.
  Successful real Git reads likewise leave cleanup unloaded. Actual generic-cli
  execution still loads 533 modules in both arms because it needs those helpers.
  This fixes a loading boundary; it establishes no speedup or long-horizon benefit.
- **Real paths:** the same isolated SQLite harness passes ordinary completion,
  typed-result interruption, original-journal recovery and committed replay at
  both immutable runtime revisions. Artifacts remain identical; each fixture
  has one host invocation/attempt, writeback and debit, with no replayed effect.
  Both owned brokers stop with verified OS exit. Real bridge send/cancel,
  backpressure, deadline and unconfirmed-cleanup cases retain their behavior;
  real Git timeout/terminal cancellation retire the query's helpers. A cleanup
  error retains the unknown diagnostic through the existing patch point.
- **Limits:** seven Windows Job Object cases are skipped on macOS. One combined
  validation attempt fails because the Git fixture's hook did not start within
  its existing budget; the isolated Git selection subsequently passes without
  changing that budget or oracle. Owner-scoped mypy checks all three touched
  loading adapters and retains the same 29 diagnostics at base and proposal.
  Full configured mypy, packaged/split installation, minimal selection, D2/D5,
  complete R0/R1/V10, hard-lease/context/model parity and full-cost benefit remain
  unqualified. Independent exact-head review and required validation still govern
  adoption; the historical failures above remain historical failures.

### 2026-11-06 — Paired cost campaign remains unqualified

The bounded campaign at `a9485073f` versus `86d9c542d` produces useful negative
evidence, not an attributable speedup. It ends at the frozen 20-minute sampling
wall limit with 499 accepted observations: eight source groups have 30 pairs
each; wheel JSON-version has nine pairs and one unmatched candidate observation.
The other wheel groups were not sampled. The original criteria and every
observation remain retained; neither the cap nor the incomplete groups become
a passing qualification. Later integration of target `cd677182c` is outside
these immutable measurements.

The experiment uses macOS arm64, CPython 3.13.5, Node 26.0.0 and the real
Node-owned SQLite 3.53.1 backend. Source and disposable monolithic `loopx` 2.2.0
wheel environments have the same 57 dependency versions and verified import
provenance. The public entrypoint exercises JSON version, selected
`performance-diagnosis` help, status, quota admission and deterministic
generic-cli work on the existing `_write_live_fixture(provider="sqlite")` owner.
Order is fixed: source before wheel, that command order, cold before warm,
alternating baseline/candidate order by pair. Each task has fresh synthetic
state. Cold uses a fresh Python process and retired owned broker; warm uses a
fresh Python process and retained broker. Filesystem caches are uncontrolled
and bytecode writes are disabled; this is not a cold-disk claim.

**Cache limitation:** existing bytecode was not normalized. A subsequent
inventory finds 7 versus 1,241 LoopX `.pyc` files and 2 versus 344 dependency
`.pyc` files in the source arms. JSON version loads the same 1,157 LoopX modules
at both revisions; its parent read-open census is 2,402 versus 1,253. This
invalidates attribution of the observed source reductions to the five-module
loading change. The numbers below describe these cache conditions only.

| Source operation | Broker condition | Baseline seconds, median / p95 | Candidate seconds, median / p95 |
| --- | --- | --- | --- |
| JSON version | absent | 3.092 / 3.394 | 1.707 / 2.110 |
| Selected help | absent | 0.093 / 0.174 | 0.101 / 0.165 |
| Status | cold | 2.104 / 2.437 | 1.832 / 2.074 |
| Status | warm | 1.304 / 1.422 | 0.985 / 1.115 |
| Quota admission | cold | 2.481 / 2.720 | 2.029 / 2.331 |
| Quota admission | warm | 1.785 / 1.881 | 1.313 / 1.460 |
| Generic-cli task | cold | 3.910 / 4.158 | 3.446 / 3.803 |
| Generic-cli task | warm | 2.837 / 3.025 | 2.424 / 2.635 |

- **Covered costs:** cold task CPU for the target and waited descendants is
  1.769 / 1.832 versus 1.283 / 1.388 seconds, median / p95; their maximum RSS
  is 79.2 / 87.4 versus 75.5 / 78.7 MiB. Separate broker CPU is 0.720 / 0.780
  versus 0.735 / 0.800 seconds, with end RSS 126.6 / 129.7 versus
  128.3 / 133.5 MiB. These are not a concurrent tree peak or complete CPU ledger.
  Outputs remain 81, 325, 21,829, 23,795 and 3,557 bytes for the five operations.
  One-minute host load ranges from 5.00 to 14.42; this is a named-host observation.
- **Separate instrumentation:** task loading remains 538 → 533 in source and
  wheel. Cold task parent read/write-open counts are 2,179/284 → 1,643/284 in
  source and 2,184/284 → 2,173/284 in wheel. All four captures make 238 local
  IPC connections; covered socket send/receive bytes are 895,335/391,794 and
  895,339/391,794 in source, 894,790/391,543 in both wheels. These cover the
  observed Python sockets, not all context, transport or descendant I/O.
  A separate source cold-task audit records parent/waited-child CPU
  1.234/0.560 → 0.802/0.582 seconds and maximum RSS
  88.7/75.3 → 63.4/75.4 MiB; these instrumented samples are not timing evidence.
  The existing performance-diagnosis recipes inspect separate Pyinstrument
  and actual Node-worker CPU profiles. Parent waits, workspace capture and
  reads, and worker startup/retirement remain investigation leads; profile
  elapsed/idle weights do not enter the timing table. Full JSON version uses
  the full parser/export graph, while `--version` loads two LoopX modules.
- **Semantic readback:** all sampled tasks independently accept the same eight
  squared integers. Source and wheel at both revisions pass ordinary committed
  replay, interruption after typed-result persistence, original-journal resume
  and replay. Each accepted fixture has one host attempt/invocation, writeback
  and debit; replay repeats none. Dropping the last square makes all four
  independent validators reject, with one dispatch and zero writebacks/debits.
  Owned brokers finish with OS PID-exit readback. One seed shutdown initially
  targets a different interpreter-alias fingerprint; its failure is retained
  and recovery uses the verified owned locator, without touching host state.
- **Complete-cost limits and next work:** setup/orchestration is 888 seconds,
  including 194 seconds of fixture preparation and a 98-second successful
  preflight; unsuccessful bundle-build, invocation and preflight attempts remain
  failures. Sampling target elapsed totals 1,001 seconds inside the 20-minute
  wall cap. Separate semantic validation takes 39 seconds; artifact rejection
  plus diagnosis/cleanup recovery takes 184 seconds; the instrumented census
  takes 108 seconds, and task/version profiling 58/20 seconds. Subsequent
  authoring/tool/model costs, complete native/thread work and full-tree peaks
  remain incompletely covered. Both wheels contain 1,997 files and
  approximately 8.9 MiB compressed; no split install is qualified. The next
  cost experiment must match bytecode conditions and choose a feasible
  representative source/wheel corpus from measured setup and invocation costs
  before freezing it. Further loading edits await that comparison. R0/V10,
  R1/minimal, D2/D5/D6, I3/D3, model/context/hard-lease and installation gaps
  retain their existing owners and gates; this checkpoint closes none of them.

### 2026-11-06 — Matched-bytecode representative cost checkpoint

A new frozen comparison of `546c08c0d3f22358ef09b13e69f7edaa806121b8`
and `0df5ad7921faab3b9ab8ec809f2fd71060692cce` completes all six selected
source/wheel groups, each with 30 interleaved pairs: 360 accepted observations
in 948.573 seconds, within the unchanged 1,200-second sampling cap. E7 remains
an incomplete, cache-confounded experiment; these are separate observations.
The measured proposal includes target main through `546c08c0d`. Later target
integration is outside this immutable cost comparison.

The environment remains macOS arm64, CPython 3.13.5, Node 26.0.0 and real
Node-owned SQLite 3.53.1. All four source/monolithic-wheel environments have
verified provenance and identical 57 dependency versions. Before freezing,
each arm compiles stdlib, dependencies and all 1,421 LoopX Python sources into
an owned `PYTHONPYCACHEPREFIX`; timestamp-header inventories find no missing or
invalid own/dependency caches. Relative source sets match within each
distribution. Inventories and frozen harness hashes remain identical after
sampling, recovery and profiles. Targets disable bytecode writes. Source runs
before wheel, selected help before task, cold before warm; even pairs use
baseline/candidate order and odd pairs reverse it. Every invocation has fresh
Python; cold retires the owned broker and warm retains it. Disk cache remains
uncontrolled. One-minute host load ranges from 8.35 to 88.73; retain every tail
observation, without attributing the source cold p95 difference to the code.

| Selected operation | Broker | Baseline seconds, median / p95 | Proposal seconds, median / p95 | Median paired proposal minus baseline, seconds |
| --- | --- | --- | --- | --- |
| Source help | absent | 0.136 / 0.193 | 0.138 / 0.168 | +0.0010 |
| Source SQLite task | cold | 3.579 / 5.997 | 3.617 / 9.234 | -0.0195 |
| Source SQLite task | warm | 2.673 / 3.019 | 2.657 / 2.979 | -0.0602 |
| Wheel help | absent | 0.087 / 0.107 | 0.083 / 0.116 | -0.0033 |
| Wheel SQLite task | cold | 3.722 / 4.266 | 3.663 / 4.430 | -0.0847 |
| Wheel SQLite task | warm | 2.707 / 3.111 | 2.797 / 3.156 | -0.0457 |

The p95 uses nearest rank. Median paired differences and differences of arm
medians are different statistics. No material latency or CPU benefit is
demonstrated: arm medians and tails have mixed directions, and task CPU remains
similar. These data qualify the representative comparison's completion and
semantic parity, not a speed threshold, full V10 or long-horizon benefit.

| Task path | Target plus waited descendants CPU seconds, median (base → proposal) | Target/waited peak RSS MiB, median (base → proposal) | Owned broker CPU seconds, median (base → proposal) | Broker end RSS MiB, median / p95 (base → proposal) |
| --- | --- | --- | --- | --- |
| Source cold | 1.311 → 1.325 | 75.75 → 75.58 | 0.770 → 0.775 | 127.9 / 134.8 → 128.6 / 133.5 |
| Source warm | 1.235 → 1.226 | 76.15 → 76.16 | 0.180 → 0.185 | 146.5 / 148.4 → 129.8 / 148.7 |
| Wheel cold | 1.353 → 1.369 | 75.94 → 76.56 | 0.805 → 0.810 | 129.5 / 134.4 → 130.1 / 134.1 |
| Wheel warm | 1.273 → 1.260 | 76.05 → 76.69 | 0.190 → 0.180 | 100.4 / 136.3 → 101.4 / 140.2 |

These RSS observations are not concurrent process-tree peaks; warm broker
end RSS also reflects its retained lifetime. Help target/waited median RSS is
17.56–17.85 MiB and median CPU is 0.046–0.056 seconds. Uninstrumented stdout is
325 bytes for help, 3,557 for source tasks and 3,550 for wheel tasks in both
arms; stderr is empty. Target/waited block-I/O counters do not measure all
broker/native I/O.

Separate instrumentation confirms task loading 538 → 533 in both distributions,
with only the previously named five DSH modules removed; help stays at 12.
Parent read-open counts are source cold 1,680 → 1,675, source warm 1,269 → 1,264,
wheel cold 1,683 → 1,677 and wheel warm 1,272 → 1,267. Task write-opens stay at
284 cold and 279 warm, with 238 observed parent local socket connections.
Covered task send/receive bytes range from 893,698–894,864 / 392,608–392,859;
the source proposal removes five send bytes and wheel bytes are unchanged.
Help opens 31 files in source and 32 in wheel, with no writes or socket traffic.
Instrumented task parent CPU is 0.635–0.774 seconds and peak RSS 62.3–65.5 MiB;
waited-child CPU is 0.528–0.573 seconds and peak RSS 76.1–78.6 MiB. These single
captures are a census, separate from the repeated timing results.

The same isolated real SQLite fixtures at all four variants pass ordinary
committed replay, interruption immediately after typed-result persistence,
original-journal recovery and committed replay. Independent squared-integer
artifact/transition oracles retain identical artifact bytes and one host
invocation/attempt, one writeback and one debit. Resume does not redispatch;
replay repeats no effect. Removing the final square produces exit 1 / failed
with one dispatch and zero writebacks/debits in every variant. All owned
brokers have shutdown/OS-exit readback; no experimental broker remains live.

Setup/orchestration costs 1,018.786 seconds, including overlapping build,
installation, fixture and cache work; those phases must not be summed.
The separate rejection phase costs 16.056 seconds, instrumentation 39.116 and
eight parent/actual-Node profile captures 54.880. Recovery/replay target time
is recorded separately; its complete orchestration cost remains uncovered.
Existing `performance-diagnosis` recipes report workspace capture, journal
checkpoints, Git probes and quota preparation among Python observations; Node
profiles include startup, request dispatch, idle and retirement. Sampled stack
weights are neither benchmark latency nor proof of a root cause; inclusive
weights, warmup and retirement cannot be added as task savings. Full context
and process-tree/native/thread/model costs remain unknown. Monolithic wheels
remain approximately 8.9 MiB; this does not qualify split installation.

**Evidence-conditioned next action:** retain the tested compatibility/loading
seams, and stop expanding these helpers for an unproven speedup. Prioritize a
bounded characterization of the existing full-parser/export closure or the
measured workspace/journal/bridge owner, then require a new matched real-path
comparison before adopting a performance change. This checkpoint creates no
new owner, budget, preset or default. R0's complete census, R1/minimal,
D2/D5/D6, I3/D3, hard-lease/context/model and split-install acceptance remain
open; independent exact-head review and required validation govern adoption.
### 2026-11-07 — Governed CLI composition cost and archived closeout boundary

The complete small-state CLI sequence is compared on main
`8e86f173f08bd55eb833d8cdf42360f93bf989fe` and a private experimental overlay of
#8240 at `4df990c9e66465171f980176fba4e3602d41f826` plus #8243 at
`668bed2f03a274f273d39c187121a1a4ede67165`. Only their seven production Python
paths enter the candidate. The independently installed monolithic wheels have
verified source bytes for 1,836 runtime resources each, 1,998 total files and
approximately 8.91 MiB compressed. All environments use CPython 3.13.5,
Node 26.0.0, real Node-owned SQLite 3.53.1 and the same 57 dependency versions
on macOS arm64. This overlay is an author experiment; it changes neither the
installed host nor a shipped preset, authority provider or public runtime.

Inputs, code/resource/wheel hashes, workload, cache policy, instrumentation,
serial pair order, sampling and interpretation are frozen before sampling.
Each CLI invocation has fresh Python, an empty owned bytecode-cache prefix and
bytecode writes disabled. Each episode starts its own cold Node service at
prequota and reuses it for subsequent phases; compile-cache and temporary roots
are independent. Disk cache and concurrent host load remain uncontrolled.
Pairs alternate baseline/candidate order. The 1,500-second cap includes fixture
setup, execution, teardown and sampling orchestration. Pilot, negative-case,
packaging and instrumented-census costs are recorded separately.

Each fixture uses ordinary hard-lease claim/admission, a separate Git worktree,
complete contiguous native initial reads, an independently verified JSON sum
artifact (`45`), Todo completion, fresh evidence-linked Vision/writeback, one
spend, terminal result/readback and exact completion/writeback/spend/terminal
retries. Global synchronization and external sinks are explicitly suppressed;
standard fanout is outside this experiment. The small state has one active
Todo; large state adds 100 completed background Todos with substantial text.
Large-state failures remain failures and do not become accepted cost samples.

All 60 planned observations finish in **1,164.996 seconds**, within the unchanged
cap: 52 small-state episodes complete and eight large-state episodes retain the
same terminal failure. Code, script and freeze inventories remain unchanged.
Small-state groups have 8/6/6/6 serial interleaved pairs; large groups have one
pair each and qualify only the observed compatibility failure.

| Small-state path | Pairs | Baseline seconds, median / p95 | Candidate seconds, median / p95 | Median paired difference, seconds | Exploratory 95% percentile interval |
| --- | --- | --- | --- | --- | --- |
| Source / Sqlite | 8 | 16.942 / 17.173 | 14.986 / 15.399 | -1.806 | -2.156 to -1.669 |
| Source / File | 6 | 16.751 / 17.036 | 14.725 / 14.917 | -2.086 | -2.159 to -1.875 |
| Wheel / Sqlite | 6 | 17.066 / 22.878 | 15.169 / 19.378 | -1.932 | -5.246 to 2.348 |
| Wheel / File | 6 | 16.689 / 17.454 | 14.801 / 15.195 | -1.917 | -2.491 to -1.553 |

The p95 uses nearest rank and is the observed maximum at these sample sizes;
it is not a tail bound. The interval uses 10,000 fixed-seed paired resamples.
Differences of arm medians and median paired differences are distinct. Arm
medians improve approximately 11–12%; wheel/SQLite's interval crosses zero and
retains both directions of tail variation. This is scoped evidence for the
small-state path, not a universal latency threshold or long-horizon benefit.

| Small-state path | Target plus waited descendants CPU seconds, median (base → candidate) | Maximum per-invocation target/waited RSS MiB, median (base → candidate) | Owned broker CPU seconds, median (base → candidate) | Broker end RSS MiB, median (base → candidate) |
| --- | --- | --- | --- | --- |
| Source / Sqlite | 10.536 → 9.190 | 126.60 → 90.02 | 0.767 → 0.775 | 126.59 → 125.28 |
| Source / File | 10.507 → 9.101 | 127.77 → 87.15 | 0.716 → 0.702 | 126.77 → 126.70 |
| Wheel / Sqlite | 10.782 → 9.289 | 134.83 → 91.51 | 0.799 → 0.797 | 125.77 → 126.91 |
| Wheel / File | 10.606 → 9.348 | 132.01 → 88.21 | 0.729 → 0.723 | 127.97 → 127.77 |

RSS is the maximum of individual CLI invocation peaks, not a concurrent
process-tree total. Broker counters exclude retirement and use Darwin's
calibrated timebase. Default SQLite target/waited CPU falls about 13–14%; broker
CPU/RSS have no consistent improvement. Source/SQLite prequota median falls
3.425 → 1.468 seconds; guard remains 1.845 → 1.863 seconds and becomes the
largest candidate phase. Wheel/SQLite prequota is 3.463 → 1.581 seconds, while
guard is 1.844 → 2.033 seconds. The gain is concentrated in fresh Python
prequota loading, not faster authority execution.

Setup and retirement medians across groups are 2.15–2.29 and 0.36–0.40 seconds.
Including setup, teardown and driver overhead, source/SQLite median is
20.238 → 18.146 seconds and wheel/SQLite 20.393 → 19.793 seconds. Independently
building/installing the two wheels costs 43.881 seconds. Setup, validation,
recovery and census may overlap and must not be summed. Preparatory authoring,
complete native/thread/process-tree I/O, model tokens, model/human costs and
long-horizon outcomes remain incompletely measured. Kernel-observed broker disk
writes stay at roughly 5.85 MiB per SQLite episode and 220 KiB per File episode;
those counters are not total filesystem traffic.

Across all 60 observations the artifact bytes, original bindings, complete
settlement obligations, one writeback and one debit agree. Required native
initial bodies are 23,498–24,442 bytes for small states; `bytes / 4` is only a
rough token proxy, not tokenizer output or proof of model retention. The
candidate's extra 85 source / 165 wheel bytes are path/reference presentation;
normalized settlement obligations compare clause for clause without deletion.

With 101 Todos, ordinary completion automatically archives the selected Todo
at its original early presentation position, together with 90 background
Todos. Validation, writeback and one spend commit, but the later original-Turn
`todo complete --no-follow-up` rejects with `todo_archived`. All four
source/wheel × File/SQLite pairs reproduce this on both arms. No failed episode
is counted as accepted or used to claim a complete large-state speedup. The
existing `todo_terminal_lifecycle.ts` and `todo_archive_selection.ts` owners
must reconcile archival with original-receipt closeout. Related #5677 already
owns freshness/terminal recovery; its published 0/1/4-history matrix does not
qualify this automatic-archive case. Preserve fresh archived-Todo, changed
actor/lease/evidence and duplicate-effect refusals when extending that owner.

A separate census over all eight source/wheel × File/SQLite variants preserves
invalid Vision/workspace refusals. Four additional failure checks per variant
cover missing Node admission, missing provider, mismatched capture digest and
released-claim completion: no writeback/debit commits. Prequota's soft failure
continues to quota; it does not grant admission. These checks take 69.559 seconds;
the instrumented census takes 90.358 seconds, with overlap between phases.
Every sampled/census broker has observed shutdown and OS exit; pilot's initial
wrong-source retirement/pending wait remains in history and was subsequently
resolved in its owned namespaces.

Excluding the census's two extra refusals, complete-episode RPCs are 271 → 270
in every variant; spend alone is 83 → 82. Observed receive traffic is
999,060–1,004,527 → 966,984–972,825 bytes; send traffic is
1,025,077–1,037,023 → 1,026,956–1,040,098 bytes. Extra send bytes reflect the
longer candidate paths. Parent read-opens are 13,532–13,561 → 11,700–11,728;
write-opens 285 → 284. Instrumented prequota module graphs are source
1,404 → 480 and wheel 1,403 → 479, including instrumentation imports; these
are not uninstrumented loading budgets. Cold Node/readiness takes 0.217–0.249
seconds and version probing 0.026–0.032 seconds in the census. Single census
timings do not establish a repeated latency gain; socket coverage and open
counts do not include all native/broker I/O.

**Evidence-conditioned next action:** carry the archive case to the existing
terminal/freshness owner and #5677 integration before qualifying the full
large-state lifecycle. Preserve the original operation/read/receipt identity
and every refusal boundary; do not change archive policy merely to pass this
workload. Then characterize the remaining `quota should-run` parser/loading and
shared projection caller before selecting another performance change. The
small-state data support continued exact-head review of #8240/#8243, not merge
or installed-host adoption. Retain I3 for at least one minor release and keep
M1/#8011, full R0/V10/R1, D2/D5/D6/D3, context/model/split-install, actual adoption
and complete-cost acceptance open. No new budget, preset, provider or authority
is introduced.

For a source replay, fetch the exact PR heads and apply only the production
`loopx/` diffs from their recorded merge bases (`79665b97a0a453380da02e15b1b62eee895147df`
and `97cc1e0c40e766c73193b8077526a7f565dc561b`) to an isolated archive of the
baseline. `uv sync --extra test` supplies the console. Build the two monolithic
wheels independently with `uv build --wheel`; validate the Chat bundle through
its existing owner before building. Reuse the source fixtures in
`tests/control_plane/test_quota_settlement_cli.py`: author the synthetic Goal
and background Todos before `_initialize_fixture_authority`, select File or
SQLite and `hard_lease`, and use ordinary claim for execution. Declare the
artifact write scope in both the Todo and Goal boundary, and use the existing
Git-fixture helper plus a distinct worktree. Scope every command below to that
fixture's registry/runtime and worktree; the guard supplies the full read and
recovery commands. Keep the same Goal, Agent, Todo, Turn and claim operation:

```text
loopx heartbeat-prompt --thin --codex-app --goal-id <fixture-goal> --agent-id <fixture-agent>
loopx heartbeat-prequota -g <fixture-goal> -a <fixture-agent>
loopx todo claim --goal-id <fixture-goal> --todo-id <fixture-todo> --agent-id <fixture-agent> --claimed-by <fixture-agent> --claim-operation-id <fixture-claim>
loopx quota should-run --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --codex-app --context-delivery on
# Execute first_read_command and every emitted contiguous next_command.
# Produce {"sum_0_through_9":45}; verify it independently before completion.
loopx todo complete --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --claim-operation-id <fixture-claim> --evidence artifact:verified-sum
loopx refresh-state --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --classification synthetic_cli_cost_artifact --delivery-boundary semantic_closeout --delivery-batch-scale single_surface --delivery-outcome outcome_progress --agent-vision-json <fixture-vision> --delivery-workspace-path <fixture-worktree> --no-global-sync --suppress-external-sinks
loopx quota spend-slot --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --slots 1 --source heartbeat --execute
loopx todo complete --goal-id <fixture-goal> --agent-id <fixture-agent> --todo-id <fixture-todo> --turn-instance-id <fixture-turn> --claim-operation-id <fixture-claim> --evidence artifact:verified-sum --no-follow-up
loopx todo list --goal-id <fixture-goal> --todo-id <fixture-todo> --include-archived
```

Declare the fixture's observed shell, filesystem-write, network and evidence-poll
capabilities on admission/writeback/spend; use the actual installed console for
generated reader commands. Repeat completion, writeback, spend and terminal with
unchanged arguments. Missing Node uses an isolated PATH, missing provider an
empty disposable runtime, stale source a mismatched capture digest, and stale
claim a normally released lease. These cases must refuse before writeback/debit;
invalid Vision/workspace also remain refusals. The temporary measurement harness
and raw captures stay private; this recipe describes the repeated workload and
its oracles, not a distributed benchmark runner or public reproduction receipt.

### 2026-11-07 — Original-Turn closeout survives automatic Todo archival

The complete CLI comparison in #8257 exposed a lifecycle failure with 101
synthetic Todos. Fresh source-console reproductions on `4a33eb4b5` retain
`todo_archived` on both File and default SQLite after ordinary completion,
durable writeback and one quota debit. The frozen comparison remains unchanged;
these failed episodes are not accepted latency samples.

The proposed repair stays in `todo_terminal_lifecycle.ts`. Its existing
same-Turn fence and exact ordinary-completion receipt verification also qualify
an archived Todo's closeout before the active-only rejection. One local helper
owns both active and archived parent checks. Original claim/lease, task digest,
validation declaration/receipt and provider CAS checks remain in force; archive
selection and retention thresholds are unchanged. This permits only the
original completion's terminal phase, without reopening archived work.

Real File/SQLite console fixtures now complete the large-state sequence and
replay completion, writeback, quota spend and terminal closeout with one debit
and an unchanged independently verified JSON result. The durable CLI regression
uses a real validator and ordinary hard-lease claim: premature settlement,
unrelated archived work, another actor, claim or Turn, and changed completion
intent are refused. Terminal closeout retains the original passed validation
receipt, first completion time and archive state without rerunning the validator.
Existing provider conformance retains source/digest, validation, lease and replay
checks; an isolated real PostgreSQL suite qualifies its shared owner separately.

The author proposal also integrates main `abf144202` (#8161), whose explicit
Goal-scoped checks do not gate local Todo completion. The archive regression
covers a failed Goal commitment alongside the local validator: ordinary and
terminal completion still preserve the original receipt and one debit, never
rerun the Goal checker, and leave the overall check failed. This is author-side
integration coverage; it does not replace independent review or reviewed-repair
wheel qualification.

Independent review of #8264 at `12627eff8` found that changing only `--evidence`
could replace the archived Todo's accepted artifact while returning the old
validation receipt. All four File/SQLite × Goal-check fixtures reproduce this
failure on that head. The revision integrates target main `10f66ec5e` and rejects
different supplied evidence before archived closeout. Its new archived-closeout
receipt also retains the accepted evidence, so a changed retry cannot bypass the
check through receipt recovery. Omitted or empty evidence with a valid completion
note keeps the original value; the CLI's completion-note requirement still applies.
Historical fingerprints and older receipts remain compatible; active
completion behavior is unchanged. The regression checks canonical evidence,
original validation and completion time, and once-only validation/writeback/debit
before and after terminal replay. The earlier reviewed head remains unaccepted.
The revised `7ec8d19b9` received an independent
[APPROVE review](https://github.com/loopx-project/loopx/pull/8264#pullrequestreview-5811807012)
and #8264 merged as `172d80d63`.

Qualification against target main `32167518b` now covers both the source console
and a freshly built, isolated monolithic wheel. The Chat bundle was built and
verified through its existing owner before wheel construction. All four
File/SQLite × source/wheel episodes contain 101 synthetic Todos and use the real
console, ordinary hard-lease claim and an independently verified JSON artifact.
After the single committed debit, the fixture stops only its own Effect processes;
new console processes recover the original terminal result through a restarted
runtime. Canonical readback retains the accepted evidence, first completion time
and archived state. Completion, writeback, debit and terminal retries retain four
committed receipts and one debit; changed evidence is refused before terminal
commit and on replay. The final original-Turn guard reports no runnable work or
execution attempt. Wheel console/import provenance is checked separately from
the source fixture authoring environment.

| Distribution / provider | Complete episode | Initial guard | Result |
| --- | ---: | ---: | --- |
| Source / File | 22.682 s | 2.148 s | Original result recovered; one debit |
| Source / SQLite | 22.220 s | 2.219 s | Original result recovered; one debit |
| Wheel / File | 20.929 s | 2.020 s | Original result recovered; one debit |
| Wheel / SQLite | 24.205 s | 2.787 s | Original result recovered; one debit |

These are single correctness episodes, including negative/replay checks and
controlled process stops, on macOS with Python 3.13.5 and Node 26.0.0. They do
not rerun the frozen paired cost study or establish a latency improvement.
The original eight large-state failures and the earlier uncertainty remain
historical evidence. Three isolated parser inputs also confirm source/wheel
acceptance of an emitted archive pointer while unexpected prose and mismatched
region markers remain refusals. The older reader's pointer failure is preserved;
this comparison neither changes the grammar owner nor upgrades an installed host.

This qualifies the repaired large-state source/wheel closeout and controlled
Effect restart boundary. The remaining guard loading/shared-projection cost
profile and complete comparison stay with the existing work. Abrupt-interruption
and model-host V4 recovery, split installation, full R0/V10/R1, M1/#8011,
D2/D5/D6/D3, context/model, actual adoption and complete-cost benefit remain
open. I3 remains at least one minor release.

### 2026-11-07 — Guard loading and settled-replay diagnosis

Source-console diagnosis at `6c10e61ad` reuses the real 101-Todo hard-lease
episode above, with isolated File/SQLite authority and owned Effect processes.
Five additional episodes preserve accepted evidence, original completion time,
four committed receipts and one debit through controlled restart; changed
evidence and renewed execution of the settled Turn remain refusals. Only guard
and immutable-context reads are profiled. These are single diagnostic
observations, including profiler overhead, rather than paired performance runs.

| Bytecode condition / provider | Profiled guard | Import cumulative | Traced runtime requests | Request elapsed sum | Loaded LoopX modules |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bytecode disabled / SQLite | 3.012 s | 2.281 s | 89 | 0.370 s | 503 |
| Bytecode disabled / File | 1.972 s | 1.255 s | 89 | 0.344 s | 503 |
| Normal inherited bytecode / SQLite | 1.584 s | 0.845 s | 89 | 0.394 s | 503 |
| Normal inherited bytecode / File | 1.465 s | 0.772 s | 89 | 0.342 s | 503 |

The disabled-bytecode observations include 0.634 / 0.555 seconds in source
compilation for SQLite / File; neither normal-bytecode observation invokes
source compilation. Import, loader-read and compilation values are nested and
must not be added. Bytecode conditions explain part of startup cost while the
loaded graph remains unchanged. They do not correct or supersede the frozen
comparison's failures. In a fifth normal-bytecode SQLite episode, the guard
loads 503 modules with 89 traced requests, while the immutable context reader
loads 68 modules with one traced request: profiled elapsed times are 2.140 /
0.246 seconds, and request elapsed sums are 0.389 / 0.067 seconds respectively.
This characterizes different required operations, not interchangeable paths.

Runtime request elapsed includes Python transport, shared runtime work and
provider work. It is an upper bound on the provider contribution within those
traced requests, not a separate database measurement or complete service CPU.
The source snapshot takes approximately 0.048–0.050 seconds in the four guard
observations. The next cost investigation therefore prioritizes the loaded
decision/status graph over a new provider or fingerprint optimization. Moving
`quota_context` imports alone cannot eliminate work that the later status
collector still requires; quota already omits presentation projections, and
the immutable context reader already has its selected loading path.

Real File/SQLite Monitor episodes also qualify the settled and closed replay
identity when an independent sibling becomes due. Each original Turn retains
one poll and zero debits, without dispatching its sibling. Through the public
`configure-goal --quota-compute 0` entrypoint, pause still takes precedence and
returns `quota_skip`. The initial probe changed legacy `status` fields instead
of the typed activation or quota owner; its failed pause expectation is a
fixture error, retained separately from the corrected checks.

| Provider / replay phase | Profiled replay | Import cumulative | Traced runtime requests | Request elapsed sum | Loaded LoopX modules |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite / settled | 1.135 s | 0.666 s | 68 | 0.150 s | 486 |
| SQLite / closed | 1.149 s | 0.726 s | 62 | 0.157 s | 486 |
| File / settled | 1.113 s | 0.713 s | 68 | 0.143 s | 486 |
| File / closed | 1.128 s | 0.691 s | 62 | 0.149 s | 486 |

The bounded next step stays with the existing quota/status and typed replay
owners: identify which live facts and imports each decision actually needs,
then remove unused loading or redundant shared requests with public-facade
parity. A receipt-only early return is not yet qualified: current pause, scoped
gates, independent pending intents, actor/lease/source checks and archived or
closed lineage must survive. Any implementation still needs the same real
source/wheel lifecycle and matched, unprofiled default-SQLite comparison;
supported-backend correctness/recovery and required PostgreSQL qualification
remain in force when affected. This diagnosis changes no production behavior,
preset, budget, provider, authority or installed host. All earlier acceptance,
adoption and complete-cost gaps remain open.

### 2026-11-07 — Guard cost candidate selection and context transport composition

An isolated source-console counterfactual defers three Monitor adapter imports
until their functions are invoked. The 101-Todo SQLite lifecycle still retains
the original artifact, completion time, four receipts and one debit. The guard
loads 502 rather than 503 modules and still makes 89 traced runtime calls;
only `todos.external_wait_contract` disappears. Monitor history lookup and
quota accounting admission execute on the guard path, so their dependencies
remain necessary. This rejects that import deferral as the next cost-reduction
slice; the temporary wrapper is not a production or compatibility qualification.

The existing context-transport candidates are separate from loading reduction:
[#8286](https://github.com/loopx-project/loopx/pull/8286) at `efce6cb9c` skips
inactive Anti-Entropy validation, and
[#8290](https://github.com/loopx-project/loopx/pull/8290) at `9537687be` omits the
governing source from status-only context validation. A temporary combined
overlay against runtime base `32167518b` was exercised through four complete
File/SQLite × base/overlay source-console episodes with 101 Todos. Accepted
evidence, first completion time, controlled Effect restart, four committed
receipts and one debit remain intact; evidence replacement and settled-Turn
execution remain refusals. The combined source also passes 91 focused Python
cases and 20 TypeScript cases with zero skips. The applied paths are restored
after qualification; this creates no competing implementation or merge.

| Provider | Traced guard calls, base → overlay | JSON parameter bytes, base → overlay | Loaded modules, base → overlay |
| --- | ---: | ---: | ---: |
| SQLite | 89 → 87 | 1,904,862 → 1,772,448 (−6.95%) | 503 → 503 |
| File | 89 → 87 | 1,903,569 → 1,771,587 (−6.93%) | 503 → 503 |

These single instrumented observations count the UTF-8 JSON parameters passed
to the shared runtime. They exclude transport headers, retries, responses,
startup and service CPU; they are not total wire bytes or a latency comparison.
The SQLite status-only validation inputs total 134,868 bytes on base; the one
remaining input is 2,045 bytes in the overlay. Immutable context and settlement
owners remain in place. This independently confirms a bounded transport delta,
without qualifying either candidate's merge, a combined wheel, installed-host
adoption or complete task benefit. The frozen cost studies remain unchanged.

The largest remaining guard parameter producers are `todo.context.page`
(789,198 bytes across nine calls), `todo.quota_planning.project` (390,361 bytes)
and `goal.vision_wait.project` (185,559 bytes), identical in the SQLite base and
overlay. The next bounded investigation targets reuse of the canonical Todo
source in those existing typed projection owners, before implementing one
coherent source-read transaction. It must preserve source/provider revisions,
actor/lease fences, complete exact reads, archive/replay lineage, pause/gates
and pending intents. More leaf RPCs or lossy source-field pruning do not qualify
the transaction cutover. Existing Goal-route work in #8123 and its unresolved
performance holds remain separate. Full source/wheel correctness and a matched,
unprofiled default-SQLite whole-path comparison are still required for a new
implementation; all earlier milestone, adoption and complete-cost gaps remain.

### 2026-11-07 — Reuse complete Todo display sources within a managed request

The existing `todo.context.page` owner can compose summary, planning and retained
terminal display views from one complete supplied source. The quota preparation
caller explicitly requests the Agent source's three views and passes retained
terminal rows to its frontier consumer. User-source and general display callers
keep their requested single view. Source selection and each display rule retain
their existing owners. The orchestration fallback reuses an independent copy of
the already selected summary instead of requesting it again.

Inside an already revision-pinned logical request, exact source JSON and request
options bound the transport memo. Changed source facts and later requests
revalidate; mutable callers receive independent decoded values. Large opt-in
inputs keep the original per-view transport, and incomplete bundles fail before
memo admission. The batch input threshold does not predict response size or
promise inline transport. An explicit multi-view request can use the existing
private response snapshot when the combined output exceeds the socket limit.

Independent review of the initial proposal found that the generic transport
expanded single-view calls into three views. An 8,000-row delivery-response
caller retained the same final output but materialized about 11.18 MB of unused
combined response instead of its roughly 3.73 MB single-view socket response.
That performance failure is retained. The corrected caller-owned composition
removes the implicit expansion; matching single-view and complete governed
source/wheel qualification are recorded with the revision being proposed.

The initial proposal against `main@32167518b3` passed six synthetic source and isolated-wheel
101-Todo File/SQLite governed CLI lifecycles. They retained the verified artifact,
original completion time, four settlement receipts, one debit, Effect restart
recovery and rejected evidence replacement. A dropped write-scope response
mutation fails at the independent public-guard scope check. Frozen display
fixtures and focused source-isolation tests also cover the existing three views.
One profiled SQLite guard reduced JSON parameter bytes from about 1.900 MB to
1.556 MB and calls from 89 to 87; loaded modules remained 503. These counts are
diagnostic and do not qualify complete wire cost, latency or long-horizon benefit.


The corrected single-call comparison uses the same synthetic source on
`main@dee549663`, the reviewed defective head and the repaired source. All three
produce the same final delivery response. The repaired request parameters and serialized response
bytes match main exactly; its nonempty response snapshot bytes are
zero for both row counts. Five warm calls per arm gave the following exploratory
medians, including the display and delivery-response projections:

| Rows | Main seconds | Defective proposal seconds | Repair seconds | Defective response snapshot bytes | Repair snapshot bytes |
| --- | --- | --- | --- | --- | --- |
| 8,000 | 0.159 | 0.636 | 0.161 | 9,480,165 | 0 |
| 15,000 | 0.284 | 1.344 | 0.286 | 17,775,165 | 0 |

This reproduces and removes the single-view regression. It does not qualify the
complete CLI, daemon CPU/memory, full marginal cost or long-horizon benefit.


The repaired source and isolated wheel pass six 101-Todo File/SQLite governed
CLI lifecycles with the verified artifact, original completion time, four
receipts, one debit, restart and refusal/replay checks. A recursive dropped-scope
response mutation fails at the independent public guard oracle. The isolated
PostgreSQL 16.15 suites pass 390 store and 11 service cases with zero skipped.

Three interleaved unprofiled default-SQLite whole-CLI pairs have median wall time
12.24 seconds on main and 12.71 seconds on the repair. Two candidate runs are
slower; ranges overlap. A separate profiled guard reduces parameter bytes from
1,899,603 to 1,546,162 and calls from 90 to 87, with 503 modules unchanged.
These observations do not establish a whole-path performance benefit. Daemon
CPU/memory, total wire/file writes, complete marginal cost and adoption remain
unqualified; the provider-first transaction remains the next accepted boundary.

This is a reversible display-transport increment, not the complete provider-first
Todo projection transaction. Provider reads, revisions, lease/actor fences and
state writes retain their existing owners. The remaining canonical-source
transaction, matched complete-cost qualification and affected backend acceptance
stay open. I3 remains at least one minor release; M1/#8011, full R0/V10/R1,
D2/D5/D6/D3, context/model/split-install, adoption and all frozen failures remain.

#### Provider-first transaction requirements before the next proposal

The following records the prerequisite gap at the display-only revision. The
next checkpoint reports the canonical transaction proposal and its measured
limits; it does not turn these earlier observations into a historical pass.

The next implementation must compose the existing provider-admitted
`canonicalTodoCollection` and projection owners. The native snapshot owner binds
Goal, store incarnation, provider revision, cursor and query to complete rows,
leases, read-model validation and acceptance guards. A continuation is a position
within that read, not a reusable current-head handle; a changed snapshot restarts
the complete read. Displaying a retained source does not obtain fresh authority.

`quota_selection.py` still sends a complete summary source alongside encoded
selection rows and source-row facts. Its request also includes the clock,
actor/profile, capabilities, closure evidence, route/handoff facts and resume
inputs. Move canonical row materialization into the existing typed owner before
collapsing that transport. Preserve the Python-value codec where strict integer,
boolean or text distinctions remain observable. Provider rows cannot substitute
for independent caller evidence or pending intent; all-done counts alone cannot
reconstruct a closure declaration.

`goal_vision_wait` separately consumes complete lineage, scoped blockers, acceptance gaps and resume facts. The combined transaction must preserve each
causal obligation, including when two obligations share a witness. Full notes,
write scopes, archive/settled identity and source provenance remain part of its
parity oracle.

The real PostgreSQL integration case connects the existing native snapshot read
to all three display views, checks unchanged provider/source data, observes a
new provider revision after a separate commit, and refuses a stale continuation.
This qualifies a composition prerequisite. The combined guard transaction,
PostgreSQL source/wheel host journey and complete-cost benefit remain open.

### 2026-11-07 — Canonical Todo transaction proposal and observed lifecycle cost

The proposal materializes canonical selection and resume rows in the existing
TypeScript owners after binding actor/profile/clock inputs. `todo.read` composes
the Agent source's explicitly requested summary, planning and retained-terminal
views, the User summary, quota selection and the existing Vision-wait basis.
Within that quota request, matching native references share one complete
provider read. Complete field digests, store incarnation, provider revision,
cursor and the original query/selection replay recipe bind the source. A later
request reads again; this is not a cache of provider authority.

The actual governed CLI query now retains its source recipe through quota
preparation. Previously, the full query response lost the ephemeral origin
even though status had preserved it. Both query and selection paths now carry
the complete provenance into the same typed transaction. Provider outages keep
their original diagnostic code/payload, and stale references fail before
projection. Source fields, Python scalar facts and owner-declared Todo source
lanes cannot be overwritten through reference metadata. Independent caller
metadata remains complete; callers that transform provider rows use the full
supplied-source codec. Strict integer/float/bool and list/tuple distinctions,
legacy fnmatch facts, lane/ordinal precedence and distant microsecond clocks
retain their observable semantics. No Todo-id lookup restores lost provenance.

The existing Vision-wait owner retains causal obligations and shared witnesses.
Prepared basis reuse requires the same actor, complete terminal rows and logical
request identity; independent lineage callers keep their existing path. A
standalone display still requests one view. Complete notes, evidence, write
scopes, archive/replay identity and independent closure/pending intent remain
in the parity boundary. Existing source-lane constants are exported for native
provenance validation; no new lane vocabulary, persisted state or authority is
introduced.

Production code at `66ff8bf48`, compared with `main@e2371c476`, passes four
101-Todo source/isolated-wheel × File/default-SQLite CLI lifecycles. A separate
verifier accepts sum=45; original completion time, four receipts, one debit,
Effect restart, invalid Vision/workspace refusal, evidence-replacement refusal
and settled replay remain intact. The wheel's affected sources match the source
checkout. The final focused suites pass 132 Python and 86 TypeScript cases;
the broader real CLI suite passed 244 cases before the final diagnostic/lane
corrections, which the focused suites cover. Real disposable PostgreSQL 16.15
passes 391 store and 11 service cases with zero skips, including two-role native
quota composition, one complete provider load, unchanged data and stale-source
refusal. This does not qualify a PostgreSQL source/wheel host journey.

Five interleaved, unprofiled source CLI pairs use the same 101-Todo fixture and
default SQLite provider. Each runs provenance, bootstrap, claim, guard, required
context reads, artifact verification, completion, negative cases, writeback,
spend, restart and terminal/refusal replay. Fixture setup and final teardown are
reported separately. Normal bytecode conditions are inherited equally. The
candidate is slower in three pairs and wall-time ranges overlap:

| Observation, median unless stated | Main | Proposal |
| --- | ---: | ---: |
| Lifecycle wall time (range) | 14.093 s (13.765–14.933) | 14.145 s (13.461–15.190) |
| Fixture setup / final teardown | 2.403 / 0.062 s | 2.338 / 0.062 s |
| CLI child CPU + observed daemon CPU (historical, invalid unit conversion) | 5.511 s | 5.757 s |
| Daemon boundary physical footprint | 140,787,912 B | 146,456,920 B |
| CLI maximum resident size | 70,746,112 B | 70,221,824 B |
| Observed daemon disk / logical writes | 9,048,064 / 14,090,240 B | 10,227,712 / 15,646,720 B |
| CLI output / required context bytes | 177,595 / 21,168 B | 179,482 / 21,257 B |

The CPU row above used an invalid Mach-clock conversion and must not be used
as CPU evidence. Three independent CPU-bound child calibrations show that this
macOS arm64 runtime reports daemon usage in Mach ticks: multiply by the measured
timebase `125/3` before converting nanoseconds to seconds. The original raw
observations remain unchanged. Correcting the five historical pairs gives
7.071→7.469 seconds of combined observed CPU including setup daemon work.
Subtracting each initial daemon baseline gives 6.706→7.107 seconds for the CLI
episode; corresponding daemon disk/logical writes are
8,892,416/13,783,040→10,072,064/15,355,904 bytes. These corrected observations
still do not demonstrate a whole-path benefit.

Daemon CPU/writes are cumulative process observations at CLI boundaries,
excluding final exit work. CLI CPU excludes the unsampled provenance process
and harness work. CLI RSS and daemon footprint are separate observations, not a
continuous full-system peak. These bounds do not measure complete model cost,
human attention or installed-host/long-horizon benefit.

A separate instrumented pair observes the following communication delta; its
elapsed time is excluded from the wall-time comparison:

| Diagnostic | Main | Proposal |
| --- | ---: | ---: |
| Guard RPCs / JSON parameter bytes | 90 / 1,903,233 | 84 / 837,940 |
| Full episode socket sent / received bytes | 3,537,350 / 6,380,604 | 1,948,250 / 6,388,112 |
| Loaded guard LoopX modules | 503 | 503 |

Both guard role sources use the native transaction. Fewer request bytes and
calls demonstrate transport adoption, while received bytes, observed writes
and CPU do not improve together. Whole-path acceleration or complete marginal
cost benefit is therefore **not qualified**. The next decision remains with
this transaction's original acceptance: independent review and attribution of
the remaining lifecycle costs before proposing further optimization. These
negative results do not justify another leaf-RPC migration or a new provider.

The proposal was rebased onto `main@e58ba30bc`. A final scope-fit check found that
the read transport's provenance binding exceeded the decision-function budget.
That complete binding and selected-row evaluation restoration were extracted
into a local helper; 24 focused parity cases and the maintainability ratchet
pass. The cost table remains the observation at `66ff8bf48`, before this helper
extraction, and is not an exact-final-head cost qualification. TypeScript
typecheck and Ruff pass. Mypy retains main's existing missing return annotation in
`decision_capture.py`; the standard premerge also retains main's peer-boundary
scanner failure on unchanged benchmark documentation. Neither is a passing
skip. Required exact-head review, validation/readiness and maintainer merge
authorization remain separate; there is no merge or installed-host upgrade.
The original task stays open for benefit and remaining acceptance. Retain I3
for at least one minor release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, adoption/full-cost gaps and every frozen failure.

### 2026-11-08 — Complete quota response transport and calibrated cost

Independent review reproduced a remaining regression at `e7db1711f`: an
8,000-row composed quota result serialized 13,080,176 bytes and caused
13,080,177 bytes of synchronous response-snapshot writes. Main's separate
views stayed below that response threshold. The existing Todo read adapter now
uses lossless gzip for quota results above the shared 8 MiB inline budget and
within the existing complete-frame budget. It returns the existing result-piece
envelope with encoding and decoded extent, keeping base64 pieces within the
socket budget. Larger reads retain their existing paging. The Python adapter
checks complete digest/identity, bounded decoded size, exact extent and one
complete gzip member; unknown encoding, truncation and trailing data fail.
The original response-budget value is shared with the server rather than
duplicated. No provider cache, persisted state, rule owner or authority changes.

The same 8,000-row diagnostic now emits 561,291 wire bytes with zero response
snapshot bytes. Complete results match the previous independent summary,
planning and terminal views; unknown fields and Unicode remain intact. A
single diagnostic elapsed time is not acceleration evidence. Twelve transport
cases plus the existing 24 parity cases pass; two native File/SQLite 8,000-row
quota cases preserve both role sources, complete views and provider data/revision
with no response snapshots. Twenty-two TypeScript context/quota cases and eight
real receipt/replan/reentry cases pass. The earlier over-specific test oracle
failed because display owners intentionally expose different fields; the final
oracle compares their complete independent outputs rather than changing them.

Production `2c9d6360e`, based on `main@8a4d4f9da`, passes four fresh 101-Todo
source/isolated-wheel × File/default-SQLite CLI lifecycles, including sum=45,
original completion time, four receipts, one debit, restart and negative/replay
cases. The final wheel matches the affected source files. Real disposable
PostgreSQL 16.15 again passes 391 store and 11 service cases, zero skips.
TypeScript typecheck, Ruff and the maintainability ratchet pass. These checks
do not qualify a PostgreSQL source/wheel host journey or installed-host adoption.

Five new interleaved unprofiled default-SQLite pairs use the unchanged 101-Todo
fixture, full negative/replay lifecycle and inherited bytecode conditions. No
heavy tests run concurrently. CPU uses the calibrated Mach timebase `125/3`;
daemon CPU/writes subtract the initial setup baseline:

| Observation, median unless stated | Main | Proposal |
| --- | ---: | ---: |
| Lifecycle wall time (range) | 13.894 s (12.686–16.481) | 14.974 s (14.131–15.344) |
| Fixture setup / final teardown | 2.347 / 0.062 s | 2.299 / 0.062 s |
| CLI child + observed daemon CPU after setup | 7.086 s | 7.403 s |
| Observed daemon disk / logical writes after setup | 8,884,224 / 13,795,328 B | 10,076,160 / 15,130,624 B |
| Daemon boundary physical footprint | 141,032,832 B | 150,520,728 B |
| CLI maximum resident size | 71,467,008 B | 71,696,384 B |

Four candidate runs are slower and wall ranges overlap. The large-response
write regression is repaired, but complete lifecycle benefit remains
**unqualified**. Final daemon exit, unsampled provenance/restart helpers,
harness CPU, continuous system peak, model cost and human attention remain
outside these observations. The original transaction task stays open for
independent exact-head review and attribution of the remaining CPU/write cost.
Earlier premerge peer-boundary and mypy failures remain validation holds until
an exact final check proves otherwise. Retain I3 for at least one minor release,
M1/#8011, full R0/V10/R1, D2/D5/D6/D3, context/model/split-install,
adoption/full-cost gaps and all frozen failures.

### 2026-11-08 — Legacy projection kind and remaining native read cost

Independent review found that source precedence survived composition but its
projection kind did not. When canonical and legacy asset inputs referenced the
same dictionary, object identity selected the user-summary path even when the
existing precedence rule had chosen the legacy asset. Legacy string counts
became null; a `next`-only input bypassed the missing-Todo-ID refusal.

Production `4c3ed5d39` makes the existing selector return both source and its
existing projection kind. Every display/inventory caller still uses the same
selected source. No new vocabulary, persisted field or authority is introduced.
Final static validation required an explicit variadic source-candidate tuple
annotation. Its execution bytecode remains identical; the typing failure is
retained and the corrected mypy run leaves only main's existing annotation hold.
Thirteen new cases compare complete summaries with the independent original
selector for both roles, shared/copy inputs, canonical precedence and actual
guard selection/refusal. Six fail at the preceding head; all pass after repair.
The first test draft assumed numeric legacy counts and an unbound guard; its
failures are retained, and the final oracle preserves the original string
values and bound Agent/Turn path. The focused suite passes 96 cases. Source and
isolated wheel independently preserve legacy counts and refuse missing IDs;
four fresh 101-Todo source/wheel × File/SQLite lifecycles preserve the artifact,
completion time, four receipts, one debit, restart and negative/replay checks.
Real disposable PostgreSQL again passes 391 store and 11 service cases with
zero skips; its server is stopped.

Attribution of the preceding five paired cost runs points to repeated native
reads during guard, writeback, spend and settled replay. A separate instrumented
complete lifecycle observes eight additional native `todo.read` calls, each
adding 147,456 observed daemon disk-write bytes: 1,179,648 bytes in total.
Instrumented elapsed times are excluded from qualification. A standalone probe
of the exact native snapshot owner records four read-only SQLite connections
per snapshot and the same 147,456-byte process-counter increment. Alternating
identity reads use one read-only connection and 36,864 bytes; provider revision
and database content digest remain unchanged. These observations locate read
lifecycle cost; they do not identify physical-device writes or justify removing
identity, revision, WAL, lease or recovery checks.

The Python `invalid-vision` profile is dominated by imports with the same call
counts in both arms. Its wall weights do not attribute the paired CPU difference
or justify another import migration. The next accepted work is a bounded study
of the existing SQLite read/connection lifecycle, preserving fresh incarnation
and revision checks through the typed owners. Provider edits require their
ordinary task scope/ownership preparation. The previous corrected cost table
remains at `2c9d6360e`, not this compatibility repair; no fresh speedup claim is
made. Keep the original task, independent exact-head review, whole-path/full-cost
and host/model/adoption acceptance open, with I3, M1/#8011, R0/V10/R1,
D2/D5/D6/D3, context/split-install and all frozen failures retained.

### 2026-11-07: selected SQLite snapshot read lifecycle

The existing `AuthorityStore` now offers an optional storage-only paired
identity/head read, consumed by the existing canonical snapshot owner.
`SqliteAuthorityStore` exposes it only for `existingOnly` stores. Identity and
the complete proven head share one synchronous read transaction; no connection,
head or provider authority is retained between calls. General SQLite stores,
File, PostgreSQL and custom stores without the optional method retain the
original identity/load ordering and failure precedence. No persisted state,
wire vocabulary, capability, selector or Python rule owner is added.

Provider selection still validates the configured incarnation, and snapshot
admission still independently reopens the pathname after loading the head.
Revision, cursor, full read-model digest, stale continuation, WAL, lease and
receipt/replay checks remain. A real directory replacement after the head read
is refused without partial rows. Suppressing the final incarnation check in a
private mutation makes that same public snapshot oracle fail.

Runtime commit `61f7c3c33` passes 873 related TypeScript cases and type checking,
four complete source/wheel File/SQLite 101-Todo lifecycles, and isolated real
PostgreSQL 16.15 store391/service11, with no skips. The lifecycles preserve sum45,
original completion time, four receipts, one debit, restart and refusal/replay;
all 23 changed production files match the wheel. Native before/head diagnostics
retain identical complete response and database hashes, reducing read-only
connections from four to three and observed daemon disk-counter bytes from
147,456 to 110,592 per snapshot. Those counters do not measure physical-device
writes, and the instrumented diagnostic does not qualify elapsed performance.

Five new interleaved, unprofiled complete default-SQLite groups compare the
same 101-Todo fixture at main `8a4d4f9da`, prior `c40d8ec31` and runtime
`61f7c3c33`, on CPython3.13.5 / Node26.0.0 / qualified SQLite3.53.1. All fifteen
lifecycles preserve the same independently verified outcome and settlement.

| Observed metric, median (range where shown) | Main | Prior | Paired read |
| --- | --- | --- | --- |
| Lifecycle wall time | 14.945 s (12.664–16.597) | 14.924 s (14.570–19.232) | 14.871 s (14.412–16.775) |
| CLI + sampled daemon CPU after setup | 7.084 s | 7.388 s | 7.292 s |
| Daemon disk-counter bytes after setup | 8,896,512 | 10,076,160 | 8,314,880 |
| Daemon logical-write counter bytes after setup | 13,897,728 | 15,618,048 | 12,840,960 |
| Fixture setup / final teardown wall time | 2.373 / 0.060 s | 2.387 / 0.062 s | 2.336 / 0.062 s |

Two of five candidate episodes are slower than main and two slower than the
prior head; the wall-time ranges overlap. Reduced observed writes do not remove
the higher median CPU than main or qualify overall benefit. CPU uses the
verified Mach timebase125/3, subtracting each daemon's setup baseline. Sampling
excludes final daemon exit, unsampled restart helpers, provenance and harness
CPU. CLI RSS and daemon boundary footprint are separate observations, not a
continuous whole-system peak. No heavy validation ran during these groups;
model cost, human attention and installed-host adoption remain unmeasured.
These new observations do not replace or relabel any historical campaign.
The next route is independent review and attribution of the remaining complete
CLI CPU cost before another optimization, retaining the fresh read fences.

The standard premerge passes six direct checks and 18 of 19 selected checks.
The peer hard-cut scanner still fails on the same unchanged benchmark README
at both main and candidate. The original task remains open for independent
exact-head review, matched whole-path/full marginal costs and host/model/adoption
acceptance. Preserve I3 for a minor release, M1/#8011, R0/V10/R1, D2/D5/D6/D3,
context/split-install and every prior failure; neither connection counts nor
passing validation establishes the complete minimal-kernel outcome.

Latest-main integration qualification at `ce0e41a26` over `df67371c3` retains
the same 23 PR production-source hashes. Fresh source/wheel File/SQLite four
complete lifecycles, 49 TypeScript cases, types and isolated PostgreSQL391/11
pass. The saved-context/legacy Python checks report 107 passed and 14 skipped;
those explicit native Codex cases remain unqualified. The inherited missing
return annotation is repaired on main and fresh kernel mypy passes; retain its
historical failure. Standard premerge still passes six direct and 18/19
selected checks, with the existing peer scanner failure.

Five fresh unprofiled default-SQLite pairs at this integration preserve the same
101-Todo outcome. Main/candidate lifecycle wall medians are 14.631/14.633 s
(ranges14.260–16.869/14.122–14.941); observed CLI plus daemon CPU after setup is
7.038/7.080 s; daemon disk-counter bytes are 8,880,128/8,290,304 and logical-write
counter bytes14,966,784/14,282,752. Fixture setup medians2.362/2.346 s and final
teardown0.062/0.058 s remain separate. Two candidates are slower and ranges
overlap. The same sampling exclusions above apply; reduced write counters do
not qualify a complete speedup or marginal-cost/adoption benefit. Complete the
remaining cost coverage and establish any repeatable CPU regression before
choosing further implementation; do not assume that another read/import
optimization is justified.

### Complete-cost coverage and current-main transaction checkpoint

The existing proposal is integrated at `23227ee5` with main `0c28f6004`.
Its 23 PR production-source hashes remain unchanged. Fresh checks pass 59
focused Python and 63 TypeScript cases, typing/mypy, four complete source/wheel
File/SQLite 101-Todo lifecycles and isolated PostgreSQL16.15 store391/service11,
with no skips in those runs. The same artifact, original completion time,
receipts, debit, restart and refusals/replay are preserved. Standard premerge
retains the existing peer scanner failure (six direct and18/19 selected pass).
The first PostgreSQL attempt lacked the checkout's `pg` dependency; its failure
is retained separately from the successful installed-environment run.

Five new unprofiled default-SQLite pairs at this current integration preserve
the identical fixture/outcome. Main/candidate wall medians are14.255/14.382s
(ranges13.971–14.887/13.428–14.684); sampled CLI+daemon CPU after setup is
6.873/7.141s; observed daemon disk counters8,904,704/8,306,688B and logical-write
counters14,491,648/13,443,072B. Setup2.314/2.187s and teardown0.061/0.057s remain
separate. Three candidate episodes are slower and ranges overlap. Reduced
write counters still do not qualify overall or complete marginal-cost benefit.

A separate passive exit-observation pair expands the accounting boundary.
Python self/waited-child usage covers the harness, provenance/verifier and CLI
children; the initial owned daemon is already in waited-child usage, so only
the later orphan daemon is added. Independent production locator PIDs require
both daemon exit records; dropping one record fails the coverage oracle.
Main daemon CPU after its last samples is0.147/0.045s; candidate0.149/0.046s.
The nearly equal tails do not explain the sampled CPU difference. These exit
callbacks cover the controlled shutdown/cache-retirement path, while observer
emission, native post-callback work, orphan native children, physical-device
writes and continuous system peaks remain outside this accounting. This
instrumented pair is a coverage diagnostic, not a latency qualification or a
replacement for any historical metric.

A second instrumented complete pair records per-RPC calibrated process CPU.
`todo.read` increases from18 to26 calls and adds0.172s sampled Node CPU; this
locates the next investigation in the existing Todo/provider read transaction.
Its timings cannot certify performance or justify removing current-source,
incarnation, revision, WAL, lease or recovery checks. Characterize a cohesive
reuse opportunity within one admitted request before further implementation;
do not infer an import migration or cross-request authority cache from these
observations. Original Task/vision, independent exact-head review,
I3/minor-release, M1/#8011, R0/V10/R1, D2/D5/D6/D3, context/model/split-install,
PostgreSQL host/wheel, full-cost/adoption gaps and all frozen failures remain.

### Refresh summary transaction checkpoint

At runtime `68fae1d0` integrated with main `87a153d4d`, a complete-lifecycle caller trace
locates two independent native User/Agent summary requests inside one refresh
qualification. The existing Python adapter now passes the pair through the
existing TypeScript Todo transaction. That owner validates the source and each
request, sharing complete provider materialization when their references match.
Independent metadata, clock/actor binding and all source/identity/revision/WAL,
lease, receipt/recovery checks remain; supplied sources or references from a closed request scope retain
the standalone path. No authority is cached across requests. This is an internal
existing CLI/readback improvement with no new product operation or configuration.

The same 101-Todo source/wheel File/SQLite lifecycles preserve sum45, original
completion time, four receipts, one debit, restart, refusal/replay and settlement
readback. A recursive dropped-scope response mutation fails the independent
scope oracle in the real SQLite CLI; its disposable daemon is stopped. The
instrumented lifecycle reduces todo.read26 to24 and each refresh summary pair
from two RPCs to one; that is a mechanism diagnostic, not a latency result.
Current113 Python/63 TypeScript, typing/mypy and isolated real PostgreSQL16.15
store391/service11 pass, zero skips in the qualified runs; both servers stopped.
All24 PR production files match the isolated wheel. The first missing argument
and first service-env skip are retained as failed/unqualified attempts. Standard
premerge passes six direct and18/19 selected checks, retaining the unchanged
peer hard-cut scanner failure. The public-boundary subcheck is clean; separate
complete local checks still expose unrelated registered-Goal authority errors.

Five matched, interleaved, unprofiled default-SQLite source episodes per arm
compare main `b78246ff7`, the prior proposal `94c99e28a` and runtime
`fc032cbf5`, before the later main integration. Each fixture driver uses that
arm's exact CLI interpreter; all15 prequota PID-set checks preserve the same
prewarmed daemon. The earlier overlapping-validator and mismatched-interpreter
campaigns are retained only as diagnostics. Runtime isolation is preserved.

| Observation median | Main | Prior proposal | Refresh proposal |
| --- | ---: | ---: | ---: |
| Lifecycle wall, seconds | 22.092 | 23.620 | 22.033 |
| Timed child + sampled daemon CPU after setup, seconds | 14.756 | 15.353 | 14.907 |
| Sampled daemon disk counter, bytes | 8,888,320 | 8,310,784 | 8,093,696 |
| Sampled daemon logical-write counter, bytes | 13,946,880 | 13,840,384 | 13,107,200 |
| Observed daemon lifetime peak, bytes | 141,508,280 | 139,887,008 | 138,510,608 |

Wall ranges are21.898–24.716 /22.615–24.863 /21.824–24.767s;
setup medians2.336/2.404/2.392s and final teardown0.062/0.062/0.058s are
separate. All five refresh candidates are faster than the prior proposal, but
three are slower than main and ranges overlap. Timed child CPU includes the
independent verifier. These counters exclude complete exit/helper/harness,
provenance, model and attention costs; disk counters and sampled lifetime peaks
are not physical-device writes or continuous system peaks. The complete benefit
remains unqualified. These observations do not qualify the later main integration,
or replace historical/frozen failures. The integrated production hashes remain
identical, and its four source/wheel File/SQLite lifecycles were rerun.


The original full declaration and every historical/frozen failure remain. I3
for at least one minor release, M1/#8011, R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, PostgreSQL host/wheel, full-cost/adoption and
independent exact-head review remain open. No main merge or installed-host
upgrade is included. Further implementation must follow the complete cost
comparison and preserve every current freshness/authority fence.

### 2026-11-08 — Matched source/wheel process cost through native exit

The original proposal #8302 is integrated with target main `21013c0c1` as
`9e147ddf2`. All24 PR production-source hashes remain unchanged. Each arm's
1840 packaged tracked sources match its rebuilt isolated wheel; five unchanged
source-only files remain outside package-data patterns: the Canary baseline,
three Claude plugin/config files and the shadow-drain test process. This checks
packaged-source parity, not identical source/wheel inventories or fingerprints.

The frozen default-SQLite comparison completes five alternating pairs per
distribution,20 complete101-Todo lifecycles, on macOS27 arm64,
CPython3.13.5/Node26.0.0. Fixture initialization, CLI and verifier use the exact
arm interpreter/package. Package loading precedes checkout fixture helpers;
temporary/process isolation precedes module imports. An initial wheel preparation
refused source-loaded fixture provenance before authority initialization; that
failed preparation remains separate. Both wheels required the existing verified
Chat build before construction. Every matched prequota PID-set check retains
the correctly sourced prewarmed daemon; controlled restart creates a second,
separately accounted daemon. Bytecode is disabled and each CLI is a new process.
No builds or validators overlap the matched runs, and the frozen count is not
expanded after results.

All20 episodes independently retain the same sum45 artifact, original completion
time, four committed receipts and one debit, required scopes, changed-evidence
refusal, completion/writeback/spend/terminal replay, controlled restart and
settled-Turn execution refusal. Identical passive OS waiters observe both native
daemons through real exit. External `wait4` captures the harness and its waited
subtree; directly reaped daemon waiters are already included, so only orphan
native subtrees and observed orphan-waiter CPU are added. This avoids double
counting and includes native exit CPU that earlier sampled comparisons omitted.

| Distribution | Whole harness wall median (range), seconds, main → candidate | Observed process CPU median, seconds | Complete managed-daemon CPU median, seconds |
| --- | --- | --- | --- |
| Source | 25.904 (25.589–26.629) →26.477 (25.603–26.643) | 17.489 →17.749 | 1.782 →1.887 |
| Wheel | 26.248 (25.670–28.200) →26.489 (26.135–27.437) | 17.984 →18.341 | 1.787 →1.932 |

Source setup/lifecycle/teardown medians are2.602/22.581/0.062 seconds on main
and2.669/22.807/0.058 on candidate; wheel medians are2.712/22.568/0.062 and
2.651/22.878/0.062. Whole wall measures harness start through OS exit, including
readback and observer work outside these three phases; their medians need not
sum to its median. Candidate wall is slower in4/5 source pairs and2/5 wheel
pairs; observed CPU is higher in4/5 pairs for each distribution. Wall ranges
overlap, and no significant wall improvement is claimed. Nearest-rank empirical
p95 from five observations equals the maximum: source26.629→26.643 seconds,
wheel28.200→27.437; these are not stable population percentiles.

A separate instrumented wheel pair preserves all lifecycle/provenance/exit
oracles and traces367→344 RPCs with3,506,215→1,874,964 JSON parameter bytes.
It diagnoses the transport mechanism and does not qualify speed. Managed-daemon
CPU is1.864/1.896 seconds, about9.5%/10.0% of observed process CPU in that pair.
Other Node helpers/probes remain inside the CLI waited subtree together with
Python startup and local processing; their individual contributions are
unresolved. Calling the entire remaining CPU Python startup would misattribute
the evidence. Further work must first attribute this subtree's material costs,
then select a bounded change through the existing typed owner and repeat the
same outcome/recovery checks. The original task remains open for this work and
independent exact-head review; fewer RPCs or passing tests do not establish
whole-path benefit.

Current-main integration passes99 focused Python and83 TypeScript cases, zero
TypeScript skips, and control-plane typing. The earlier real PostgreSQL16.15
store391/service11 qualification belongs to unchanged production sources; it is
not a new run at this integrated head. Current-head File whole-CLI and
PostgreSQL host/wheel journeys remain unqualified. Observer CPU after result
publication, physical-device writes, continuous system peak, environment/build
cost, model/attention and installed adoption remain unknown or outside this
comparison; missing tails are not zero. Earlier failures and frozen campaigns
are neither replaced nor relabeled. Full marginal/long-horizon benefit remains
unqualified. Keep I3 for at least one minor release, M1/#8011, full R0/V10/R1,
D2/D5/D6/D3, context/model/split-install, full-cost/adoption and all freshness,
identity/revision/WAL/lease/receipt/recovery fences. No main merge or host
upgrade is included.

### 2026-11-08 — CLI CPU attribution and fresh source-scan path construction

One separate wheel attribution pair compares main `21013c0c1` and proposal
`24ca29f4e`, retaining the complete101-Todo lifecycle and every prior
source/package, scope, warm-PID, artifact, original completion-time, four-receipt,
one-debit, refusal/replay/restart and native-exit oracle. Each arm records22
Python console processes, three independent Node helpers/probes and two managed
daemons through actual exit. The helpers are two version probes and one setup
helper; all their exits precede the associated caller's readback. PATH-resolved
Node programs keep their exact executable and arguments. Python console CPU
profiles use `process_time`; no timing qualification is inferred from this
instrumented pair.

| Diagnostic CPU, seconds, main → proposal | Observation |
| --- | --- |
| Python CLI self, sampled before profile dump | 20.447 →20.099 |
| Python profile exclusive total | 18.230 →17.922 |
| Import machinery cumulative | 14.505 →14.403 |
| Compilation exclusive | 7.488 →7.464 |
| Complete managed-daemon subtree | 1.780 →1.890 |
| Three independent Node helper/probe subtrees | 0.286 →0.281 |

Import cumulative time includes nested compilation and must not be added to
the exclusive compilation total or the self sample. Module loading may happen
inside an operation; this does not separately identify interpreter startup.
The fixed bytecode-disabled condition makes loading/compilation prominent, while
the helper contribution is bounded. Python interpreter/wrapper/profiler overhead
and publication tails prevent treating these figures as an unprofiled cost
decomposition. They do not explain or reverse the earlier frozen pair's CPU
increase, establish speedup, or qualify normal-bytecode installed adoption.

The same profiles locate source-scan path handling in the existing filesystem
adapter. Runtime `a57fd3ef7` computes the POSIX relative-directory prefix once
per directory instead of constructing an additional relative `Path` for every
candidate file. Source selection, sorted rows, file metadata, `Path.stat` and its
platform error policy remain; full-source hashing and every freshness/identity,
revision/WAL/lease/receipt/recovery fence are unchanged. This bounded companion
refactor adds no capability, provider, persisted field, vocabulary or authority
cache. The native TypeScript owners retain their existing rules.

The immutable parent and new scanner produce the exact same404 metadata tuples
over the same real source tree.64 focused Python cases and kernel mypy/Ruff
pass. Before/head real `runtime.ping` calls over disposable copied packages
both rotate fingerprint and PID after an owned JSON file changes. A deliberate
omitted-JSON-source mutation fails that same independent runtime oracle; all
owned probe runtimes are stopped. Eight before/head × source/wheel × File/SQLite
complete CLI lifecycles retain the original artifact, completion time, scopes,
four receipts, one debit, refusals/replays and both native exits. These qualify
correctness and the exercised real path, not performance.

The rebuilt monolithic wheel matches1840 packaged tracked sources; only the
existing `effect_runtime.py` changes among the original24 production files.
The same five source-only omissions remain. One independent import probe first
ran in a checkout working directory and loaded source; its failed preparation
is retained. Corrected qualification uses a non-checkout working directory,
clears `PYTHONPATH` and requires the module to belong to the isolated installation
before comparing bytes. The lifecycle harness retains its own independent
package-root checks. The initial wrong-directory Chat build command also remains
a preparation failure; the dashboard owner's build then verifies the bundle.

Wider loading changes remain a separate next decision: compare real normal and
disabled bytecode behavior at the existing quota/context and refresh dispatch
owners before moving required dependencies. The path-allocation delta is an
interim local reduction with no whole-path/marginal-cost benefit claim. Current
PostgreSQL host/wheel, model/attention, continuous peak, physical-device writes,
installed adoption and full long-horizon qualification remain open. Unchanged
PostgreSQL store/service owners retain their earlier real qualification; no new
PostgreSQL run is claimed here. Preserve all frozen failures, the existing peer
hard-cut validation hold, I3 for at least one minor release, M1/#8011, full
R0/V10/R1, D2/D5/D6/D3 and context/model/split-install/full-cost/adoption acceptance.
The original task and independent exact-head review remain open; no main merge
or installed-host upgrade is included.

### 2026-11-08 — Normal-bytecode lifecycle and observation limits

One before/head wheel pair exercises the same complete 101-Todo SQLite
lifecycle at `24ca29f4eb2afe0bbf27df00002abd005febf256` /
`1a4d9d8f92491db5913cfa59061bcd58c677868d`; packaged runtime bytes are
`9e147ddf2085b080eba0cd2d7888ec896b270333` /
`a57fd3ef771013c80add55821321e98e577836e6`. Target main remains
`21013c0c113445b71a30f6ce06ab61455b56f1f2`. This checkpoint changes only the
paired RFC; the production proposal is retained for independent review.

Both arms use newly installed wheels, the same CPython 3.13.5 and dependency
versions, and fresh byte-identical copies of the existing fixture helpers.
Installation and fixtures start with zero pyc files. Pre-run provenance checks
use `-B`, a non-checkout working directory and cleared `PYTHONPATH`; imported
module ownership is checked before all 1,840 packaged tracked source hashes.
The same five source-only omissions remain. Measured processes use default
bytecode, with write-disable, cache-prefix and importtime variables unset;
`sys.dont_write_bytecode=False` and `sys.pycache_prefix=None` are read back.
Data and temporary directories are isolated before fixture imports. Interpreter
stdlib and OS file caches are shared and are outside the isolation claim.

| Cache stage, identical path sets in both arms | All installed pyc | LoopX pyc |
| --- | ---: | ---: |
| Installation complete | 0 | 0 |
| Driver before fixture import | 1 | 0 |
| Fixture/setup complete | 317 | 223 |
| Bootstrap complete | 358 | 264 |
| Prequota complete | 433 | 339 |
| Guard and required reads complete | 665 | 571 |
| Settled replay and teardown complete | 699 | 605 |

The driver's first pyc belongs to virtualenv support. Five fixture pyc files
appear during setup in each fresh fixture copy. All 605 final LoopX pyc headers
match the interpreter magic and their source timestamp/size. These are cache
validity and phase observations, not a count of modules loaded by one guard or
a measurement of default-mode compilation/import CPU.

Both original lifecycle runs exit successfully. Artifact sum45 and its digest,
original completion time, required scopes, four committed receipts, one debit,
unchanged prequota warm PID set, controlled restart, evidence-replacement
refusals and completion/writeback/spend/terminal/settled-Turn replay pass. Each
arm retains all 22 console command/phase/wrapper exit records, three exact-argv
Node helper exits and both managed daemon exits. Waited descendants are counted
once; only orphan daemon subtrees and their observed waiter CPU are added to
the outer waited subtree.

**The actual console-PID observation subcheck failed.** The first observer
incorrectly interpreted macOS `proc_listchildpids`' PID count as a byte count,
recording 0/22 actual console PIDs. An independent disposable child probe
reproduced and corrected that API interpretation before the unstarted head
arm. Head captures 21/22 actual console PIDs; bootstrap is still outside the
bounded sampling window. Wrapper PID/argv/exit records remain complete, but
actual-PID coverage is incomplete. Both failed post-run subchecks are retained;
neither lifecycle is rerun. A preliminary parse with the host's incompatible
Python 3.9 also fails; syntax validation with the executing 3.13 interpreter
passes. The fix and evidence gap remain in the original task's recovery record.

| Single-pair raw observation, before → head | Value |
| --- | ---: |
| Whole harness wall | 24.044 → 25.180 s |
| Observed process CPU including orphan additions | 10.698 → 10.724 s |
| Rounded console waited-subtree CPU from OS time files | 6.020 → 5.980 s |
| Complete managed-daemon subtree CPU | 1.900 → 1.920 s |
| Three Node helper subtrees | 0.273 → 0.291 s |
| Setup / final teardown wall | 5.503 / 0.062 → 3.338 / 0.054 s |

The observer correction changes bounded sampling waits between arms, and the
head bootstrap has a large wall delay. Fixture, verifier, observer and readback
work remain in the whole-harness total. These observations cannot qualify a
speedup, regression or paired CPU difference, explain the earlier frozen cost
campaign, or be subtracted from the bytecode-disabled profile as a compilation
saving. Console waited CPU also includes local work and descendants; it is not
Python startup alone. No frozen sample is repeated, expanded or relabeled.
Publication/exit tails, physical writes, continuous system peaks, build/setup
environment cost, model/attention and installed adoption remain unknown.

This closes the finite default-cache correctness question while preserving the
cost/attribution gap. Retain the existing typed transaction and source-scan
proposal. The next bounded work should inspect the required dependency closure
at the existing quota/context/refresh callers, then select a complete reversible
loading change with normal-default semantic parity. Valid caches alone do not
justify moving a required import or removing a fresh read; do not repeat this
pair to recover PID observations. The original task, independent exact-head
review/readiness, peer hard-cut hold and all I3/minor-release, M1/#8011, full
R0/V10/R1, D2/D5/D6/D3, context/model/split-install, PostgreSQL host/wheel and
full-cost/adoption requirements remain open. No main merge or host upgrade is
included.

### 2026-11-08 — Default caller loading and immutable recovery

A complete source-checkout/default-SQLite lifecycle at
`a02be126957953b7a61796e3b99fcfe578eb5174`, with Python 3.13.5 and normal
bytecode settings, records imports from 22 actual console processes. The
unchanged synthetic 101-Todo workflow preserves its independently verified
sum-45 artifact, original completion time and write scopes, four receipts, one
debit, restart, evidence-replacement refusal and settlement replay. Both owned
native daemons exit. This is current-source correctness and loading evidence;
it is not another paired cost sample or installed-distribution qualification.

The fresh guard loads 505 LoopX modules; actual refresh writeback loads 432.
Explicit-reference context recovery loads 71 on its initial selected read and
69 on subsequent pages, with no capability modules. A separate recovery probe
makes every capability import unavailable and reconstructs the original
60,888-byte capture exactly across two pages. An altered capture still fails
its digest check, and a copied reference with public file permissions fails
its private-file check. The first observer incorrectly required a console-path
continuation; accepting the observed exact interpreter/module prefix resumes
the same pinned offset. Preserve that observer failure and the copied-reference
preparation refusal. The probe does not establish new authority-write costs.

The successful import trace locates a remaining composition boundary:
`cli_runtime` imports `periodic_report.post_writeback_hook` before refresh
validation; even the default-disabled report path loads its report-source, profile and
preset construction dependencies. Loaded modules alone do not establish
requiredness or cost. The next bounded loading change should separate this
existing provider's subscription resolution from report-source/profile/preset
construction,
preserve live machine-default and Goal-override resolution, module exports and
patch points, and retain all hook/replay/authority checks. Characterize both
disabled and enabled real writeback before changing that boundary. The already
isolated immutable reader does not justify a broad context-reader migration.

The feedback and lexical-scope proposals remain separate review dependencies;
this checkpoint includes neither proposal in its measured source. Existing
File/wheel/PostgreSQL evidence retains its earlier source boundary. Independent
review/readiness, I3 for at least one minor release, M1/#8011, full R0/V10/R1,
D2/D5/D6/D3, context/model/split-install, PostgreSQL host/wheel, full costs and
adoption remain open. All frozen failures and samples are retained; no speedup,
complete kernel acceptance, main merge or host upgrade is established.

### 2026-11-08 — Feedback validation without mutation-adapter loading

Initial runtime `2c5212fdb15035c26c3e158420d67197d45715f2` makes the two existing
retained-state mutation adapters in `loopx.feedback` load on demand. Pure
feedback validation now works when those adapters are unavailable; the parent
fails at module import. Reward mutation resolves both adapters before registry
reads or writes, while the original guard and writer call sites still look up
the current module binding. Adapter object identity, mid-call patch behavior,
index-before-state lock ordering, TypeScript guard ownership, credential refusal
and partial-write handling remain. A local compatibility map, module `__getattr__`
and `__dir__`, and a computed `__all__` preserve all 50 original star-export
names, including the later append/render functions. There is no new capability,
provider, persisted vocabulary, decision owner or cross-request authority cache.

The two relevant groups pass 291 feedback-policy and 48 adapter/write/recovery
cases. The independent unavailable-adapter tests fail on the parent before the
change; head preserves pure validation and rejects a missing writer before
reward mutation. Focused mypy and Ruff pass. The production semantic advisory
finds no supported new carriers; explicit test-file inclusion is outside that
scanner's supported `loopx/` scope. Test refusal strings remain local fixtures.

The rebuilt monolithic wheel first passes isolated installed-module ownership,
then matches all 1,840 packaged tracked sources outside the checkout with
`PYTHONPATH` cleared. The five source-only omissions remain; relative to the
frozen runtime candidate, only `effect_runtime.py` and `feedback.py` bytes change.
The existing dashboard Chat build supplies the required bundle after the first
wheel preparation failed for its absence. Four new candidate source/wheel ×
File/SQLite 101-Todo CLI lifecycles use default bytecode and pass the original
artifact, completion-time, scope, four-receipt, one-debit, restart, refusal and
replay oracles. Both managed native daemons exit in every case. These are new
runtime correctness checks, not additional matched cost samples.

A normal-cache profile of an already settled synthetic recovery dry-run is
used only to inspect loading dependencies. Its 410-module closure and nested
import/compile timings cannot establish default-provider performance, fresh
authority behavior or lifecycle cost. The initial stdlib-shadowing profile
filename failed before CLI execution and remains a preparation failure. No
frozen sample or preceding attribution pair is repeated, expanded or relabeled.
No new whole-path benefit, PostgreSQL host/wheel, model/attention or installed
adoption qualification is claimed. Preserve the peer hard-cut hold and all
earlier failures, I3 for at least one minor release, M1/#8011, full R0/V10/R1,
D2/D5/D6/D3 and context/model/split-install/full-cost/adoption acceptance.
The original task and independent exact-head review/readiness remain open;
the next loading decision stays with the existing quota/context/refresh callers.
No main merge or installed-host upgrade is included.

During submission preparation, a maintainer merges #8302 at
`481436a56b47220c7c43bd6e272c1fa59d5038fe`; that head excludes this feedback
change. The two unsubmitted feedback/checkpoint commits move to a clean successor
based on target main `0ed909880`, retaining identical feedback and focused-test
bytes. The successor wheel matches 1,841 packaged tracked sources, with the same
five omissions and installed ownership checked first. Its fresh source/wheel ×
File/SQLite lifecycles pass all four original oracle sets and both native exits
per case. On this changed integration baseline, 48 write/recovery and 265 shared
feedback-policy cases, mypy and Ruff pass. These checks qualify the successor's
changed baseline; the initial runs remain historical evidence. No performance
sample is repeated or added. The successor still requires independent exact-head
review/readiness; the original task's complete acceptance remains open.

### 2026-11-08 — Declaration codecs preserve selected-owner loading

The inherited TypeScript CI failures reproduce on target main `94366314f0`.
Cold handler loading reaches lease execution through the interaction/acceptance
and outcome owners, because work declarations import `normalizeWriteScopes`
from the full acquire runtime. Runtime `b0c42af8f` moves the existing lexical
scope/value functions to `work_items/write_scope_codec.ts` and keeps their old
exports. Coercion, raw request spellings, character classes, the 160-character
limit and rejection behavior remain. Lease IO, admission, repository/workspace
identity and source/revision/WAL/receipt fences stay with their existing owners.
The digest guard also registers the existing quota action-wildcard matcher;
its visible 64-hex rejection is unchanged.

Both failing CI cases now pass; 19 loading/digest cases pass on Node 22.22.3.
The related Node 26 groups pass 60 and 70 cases; the first group's missing-PG
skip is subsequently exercised on an isolated PostgreSQL 16.15 server. Its
store/workspace groups pass 391/32 cases and the separate service suite passes
11 with no skips. Initial missing service environment and reused-incarnation
failures remain recorded; the corrected service uses its own fresh database.
Sixteen old/head scope-value/refusal comparisons and original export identities
pass, as does TypeScript typing. Four new source/wheel × File/SQLite 101-Todo
CLI lifecycles preserve the original artifact, completion time, scopes, four
receipts, one debit, restart/refusal/replay and both native exits. The wheel
matches 1,842 packaged tracked sources after installed-root verification; the
same five source-only omissions remain. These qualify this runtime's correctness,
not complete cost or installed benefit. All earlier frozen failures, peer
hard-cut evidence, I3/minor-release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3 and
context/model/split-install/PostgreSQL-host/full-cost/adoption gaps remain open.
The original task and independent review/readiness remain open; no main merge
or installed-host upgrade is included.

### 2026-11-08 — Report construction loading at Turn composition

The existing `periodic-report` Python provider and CLI Turn adapter now resolve
report source, preset, stage projection and request exports on demand. At runtime
revision `7858d96e83ef98174cc43870e283f8f0e6f40c0b`, disabled subscriptions do
not import those construction providers. The capability package already had lazy
exports; this change fixes its concrete composition callers. Subscription
validation remains with the same owner, including its cadence-to-profile schedule
validator. Goal overrides, live machine defaults, configuration warnings, original
export identities and patch points, hook profile digest/policy version, source
selection, receipt replay and authority are retained. It adds no capability,
configuration, UI, persisted vocabulary or decision owner.

Against baseline `c263f7c78353fcaa914f23475d4d99d07eab7c01`, eleven identical
synthetic configuration cases preserve hook output, trigger decisions and complete
configuration error messages. The cold-process refusal oracle fails before the
change when disabled composition imports the report source. Eight focused cases
now cover absence, disabled inheritance, overrides, invalid disabled configuration,
effect-free reads and export/patch compatibility. An initial stronger prohibition
also rejected the canonical profile schedule validator; that failed probe is
retained, and this slice preserves the validator rather than bypassing it.

Source and separately built wheel File/default-SQLite console journeys pass the
original complete 101-Todo lifecycle: unchanged fixture/artifact, four receipts,
one debit, source/lease/refusal/restart/replay readback and two owned native exits
per journey. Installed package ownership was verified before comparing 1,920
present tracked entries; seventeen source-only or documentation/media omissions
remain explicit. Wheel test-dependency and frontend-build preparation failures
remain recorded. Isolated PostgreSQL 16 store/service suites pass 391/11 cases
with zero skips; the initial service run's missing environment declaration remains
a skipped run. This does not qualify PostgreSQL host/wheel journeys. Report suites
pass 100 cases. The broader digest inventory check has one identical baseline/head
failure on an unchanged Todo selector; that check remains failed.

A separate report-composition probe used CPython 3.13.5 on macOS arm64, normal
bytecode in both arms, an uncounted prewarm and eight alternating pairs per
disabled/enabled case. Disabled median wall/CPU observations were 0.255/0.111 s
before and 0.246/0.107 s after; enabled observations were 0.331/0.117 s and
0.321/0.113 s. Loaded LoopX modules fell from 196 to 179/182. These small,
concurrent-test observations do not establish material speedup, sustained tail
latency or complete CLI/Node/model/attention cost benefit. The earlier frozen
20-cost, attribution and normal-bytecode campaigns were not repeated, enlarged or
relabeled. The independently reversible delta is off-state construction isolation.
Exact-head review, CI/readiness and explicit protected merge authority still govern
adoption. Preserve I3 for at least one minor release, M1/#8011, full R0/V10/R1,
D2/D5/D6/D3, context/model/split-install, full-cost/adoption gaps and all historical
failures. The existing runtime-composition work continues through these boundaries.

### 2026-11-08 — Synthetic Host UTF-8 recovery qualification

The Windows CPython 3.11 CI arm of #8360 fails six existing original-task
revalidation cases. Its synthetic Python Host inherits a locale decoder for a
pipe, while the existing generic Host transport sends UTF-8 JSON. A disposable
File-provider counterprobe changes only that Host decoder: CP1252 produces
`host_failure` without `task_failure`; UTF-8 reaches the independent artifact
validator and produces `validation_failed` at `task_postcondition`, with
`revalidate_cached_result`. The source macOS arm independently passes all 12
existing revalidation cases. These observations locate a fixture mismatch;
they do not justify widening production failure classification.

Fixture commit `a4ce613585fdb329ba3600f0ede768864214ed84`, over main
`4e949f405528fd8087f0623526c62f890c1ce982`, explicitly configures its stdin as
UTF-8 before reading the request. One new File/SQLite regression first sets
only the generated Host's decoder to CP1252, sends a Unicode brief and requires
the rejected artifact to reach task validation. After repairing the artifact,
revalidation must accept it with the original turn key and exactly one Host
invocation. The same regression fails with the immutable baseline Host on the
same CPython 3.13.5/Node 22.22.3 environment, and passes with the correction.
No transport, production owner, persisted state or protocol vocabulary changes.

On macOS arm64 with Node 22.22.3, CPython 3.11.15 source and a newly installed,
isolated monolithic wheel each pass the new two provider cases plus all 12
existing original-task recovery cases. Wheel imports are verified outside the
checkout before loading the source-owned fixtures; all 1,920 present packaged
tracked runtime entries match the source bytes. The same 17 source-only
exclusions remain: canary baseline, host plugin/config, fault-test helper and
documentation/media. This qualifies the existing File/SQLite
synthetic Host recovery path, including retained MCP disconnection and lock
timeout recovery cases, not model adoption, split installation or native
Windows. The failed original Windows arm remains historical evidence until a
new exact-head Windows run passes. The independent Frontstage manifest request
still returns HTTP 504 after its bounded retry.

The original task stays open. Preserve I3 for at least one minor release,
M1/#8011, full R0/V10/R1, D2/D5/D6/D3, context/model/split-install,
PostgreSQL host/wheel, whole-path/full marginal-cost/adoption requirements and
all historical and frozen failures. No cost campaign is repeated or relabeled,
and this fixture qualification establishes no performance benefit. The report
loading proposal #8360 retains its independent review and CI gates; the next
step is exact-head review of this correction and native Windows readback. No
main merge or installed-host upgrade is included.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | 148 top-level modules | `2f3d13ae9` | `ls loopx/*.py \| wc -l` | 148 | counts files, not public API |
| E2 | 52 kernel-side capability imports | `2f3d13ae9` | `rg -l "loopx\.capabilities\|from \.\.capabilities\|from \.capabilities" loopx/control_plane loopx/cli_commands loopx/*.py` | 52 | static imports only |
| E3 | 80 capability → control_plane imports | `2f3d13ae9` | `rg -l control_plane loopx/capabilities` | 80 | includes docs strings; upper bound |
| E4 | Selected CLI loading seam | `9cae3b28939f72fa4aadab5cfe49e19bcbdab372` | Linked entrypoint/CLI in Section 4 | Source-audited | No new timing claim |
| E5 | DSH composition example | Pinned upstream commit | [`minimal.patch.yml`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/web-app/presets/minimal.patch.yml), [`standard.patch.yml`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/web-app/presets/standard.patch.yml), [`sdk-minimal`](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/bundle/sdk-minimal/README.md) | Source-audited | Shared preset composition and a self-contained SDK bundle; not a LoopX host qualification or permission recommendation |
| E6 | Pi core/context example | Pinned upstream commit | [`agent` core](https://github.com/badlogic/pi-mono/blob/f14813e96510a7cf7562a120e809c6a134af073d/packages/agent/README.md) | Source-audited | Agent/tool/event core with `transformContext`, `convertToLlm`, `prepareRequest` and `finishTurn` seams; no LoopX authority or performance claim |
| E9 | Selected version loading and matched cost | `5a5863502` / `8a79d8602`; macOS arm64 / CPython 3.13.5 | Version checkpoint above; existing console/version and performance-diagnosis owners | Corrected two groups × 30 pairs; 120 observations; public-facade/parity/cache readback pass; material query improvement | Initial 120 observations and facade failure retained; public-safe aggregates; full Turn/minimal, split install, host upgrade and complete costs unqualified; E7/E8 remain with #8185 |
| E7 | Bounded paired cost campaign | `a9485073f` / `86d9c542d`; named environment above | Paired cost checkpoint above; existing entrypoint/SQLite fixture and performance-diagnosis owners | 499 observations; source groups complete, wheel incomplete; cache attribution unqualified | Aggregate public-safe evidence only; no speedup, full V10, split-install or model-cost qualification |
| E8 | Matched-bytecode representative comparison | `546c08c0d` / `0df5ad792`; named environment above | Matched-bytecode checkpoint above; same CLI/SQLite and performance-diagnosis owners | Six groups × 30 pairs; 360 observations; parity/rejection and cache readback pass; no demonstrated material benefit | Aggregate public-safe evidence only; later integration, full V10, minimal, split install and model costs unqualified |

## Appendix D: Rejected or superseded alternatives

**Multi-repository split.** Rejected for this RFC because it violates I1 while
the TypeScript control-plane migration is in transaction cutover, multiplies CI
and release surfaces, and requires per-repository maintainers that do not yet
exist. Evidence that could reopen the decision: migration RFC Stage 4 complete;
a `packages/` distribution with two or more non-kernel maintainers and no
cross-package breaking change for three consecutive releases.

## Appendix E: Incident and review lessons

### Generated Todo pointer read compatibility (non-normative)

The generated archive-summary and per-Todo active-field omission pointers belong
to the existing Todo projection owner. The observed strict `machine_region.py`
reader matching `8d314af49` rejects the archive-summary comment, which has no
recognized Todo metadata. It already accepts the emitted per-Todo omission row
through valid `todo_id` metadata. Their shared `loopx:todo-archive-pointer` prefix
does not establish reader drift; attribute an omission-row fault only after the
exact installed reader rejects that exact generated line. The archive-summary
reader repair is already on `main` through
[#7834](https://github.com/loopx-project/loopx/pull/7834). An affected installed
reader needs composition qualification, rather than another source fix or removal
of live projection metadata.

Author qualification at `97cc1e0c4` used the same synthetic source and real
File/SQLite stores under Python 3.13 and 3.14 with Node 26. The prior reader's
grammar matches `8d314af49`. Across the prior snapshot, current source and its
built wheel, 36 cases covered plain, archive-summary and foreign-narrative rows;
they did not test per-Todo omission. They produced 20 expected successes and
16 expected refusals: four prior-reader archive-summary failures and twelve
foreign-narrative failures. All four prior-reader failures recovered and replayed
through the current source
under the original Turn identity. Canonical work remained unchanged and quota
debits stayed zero. Eight additional source/wheel console cases read the native
capture back with synthetic Session identities; fixture preparation failures
remain failures, and a committed capture was recovered without rerunning
admission. The existing region, projection and SQLite archive regressions passed
(83 cases).

Review qualification on runtime baseline `ebb39f4db` filled the missing per-Todo
comparison through the real installed-snapshot, source and isolated-wheel console
entrypoints on File/SQLite, Python 3.13.5 and Node 26. Eighteen cases produced ten
successes and eight expected refusals: the old reader rejected two archive
summaries, all three distributions rejected six foreign-narrative cases, and all
six per-Todo omission cases succeeded. Ten successful cases executed the emitted
native read command and replayed the original Turn; both old archive-summary
failures recovered/replayed through source with that same identity. Source and
canonical work stayed unchanged, with zero quota debits. The 83 existing
regressions passed again. The packaged runtime resources matched source bytes.
Fixture preparation failures remain recorded; an initial process-exit wait was
unconfirmed, so evidence directories were retained and serving retirement was
checked separately. This does not claim process-exit qualification.

The 36-case matrix took 135.6 seconds; this is observation cost, not a speed or
long-horizon benefit qualification. Total model, service CPU and human costs are
unmeasured. Actual installed-host upgrade and readback remain unqualified.
Preserve I3 for at least one minor release. This evidence does not close full
R0/V10, M1/#8011, R1, context/model or split installation, or promote D2/D5/D6/D3.
Use the existing self-repair lookup for `marked_todo_archive_pointer_reader_drift`
with the combined query `quota_unexpected_collection_error non-generated content
inside marked Todo region`. Thin Codex App output may expose only the error code;
read-only `loopx --format json quota status --goal-id <goal-id> --verbose` supplies
the detailed symptom. Distinguish a shipped repair from installed-reader drift
without weakening foreign-content rejection or rewriting active state.

### 2026-11-08 — Combined loading proposal and nullable staged reads

The declaration-codec proposal now integrates target main `efd49352c`, the
feedback-loading component from #8339 and the equivalent anchored-clock repair
already on main. Component approvals and checks remain evidence for their old
heads; the combined proposal still needs its own review and validation.

A real precision-preserving `quota replan-view --replan-stage explore` read
fails when the staged view contains only strings, booleans and null. The
existing edit encoder compacts the whole root into an empty property path,
which its Python transport rejects. Restricting that compact replacement to
nested records preserves the property-path contract and existing numeric/source
lineage. The independent root-path oracle fails before the change and passes
afterward; the real read now returns the original context/hash and an absent
Explore basis. No decision owner, vocabulary, provider or authority is added.

The integrated source and packaged wheel research-record checks retain the
original negative paragraph, two original history rows, Goal isolation and
exports; the packaged conversation scenario also passes. The earlier CI timeout
is not reproduced locally, so its platform/timing cause remains unknown rather
than being declared repaired. Four fresh source/wheel × File/SQLite 101-Todo
correctness episodes preserve the original fixture/artifact, scopes, completion
time, four receipts, one debit, refusal/restart/replay and two owned native exits.
An initial driver used `python` while the source console selected `python3`,
producing two distinct runtime fingerprints and failing the warmup/exit oracle.
That failure is retained; the subsequent cases use each console's actual
interpreter. They do not repeat or relabel any frozen cost sample.

An isolated PostgreSQL 16 server passes 391 store and 11 service cases without
skips. The installed wheel matches 1,921 tracked package entries byte for byte;
17 tracked documentation/media/testing or source-only entries are omitted, and
general distribution coverage beyond the exercised callers is not inferred.
Whole-path cost benefit, installed adoption, PostgreSQL host/wheel and all
I3/minor-release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3,
context/model/split-install and historical failure qualifications remain open.
The next useful boundary remains report subscription/construction loading after
the combined proposal's review; the original task stays open.

### 2026-11-08 — Integrated loading and recovery qualification

The combined proposal adopts the existing feedback/declaration-codec repair
from #8346, report construction isolation from #8360 and UTF-8 synthetic Host
fixture repair from #8365 over target main `fe36f31fd`. Runtime qualification
uses `251d7eaeff4581d3436340f68c5b6cd8c8ebf1ad`. The existing lexical
`write_scope_codec.ts` already removes the observed dependency on full lease
admission; an additional interaction decoder layer is unnecessary. Error and
vocabulary owners, declared scope spellings and retained receipts stay intact.

On Node 22.22.3, the loading/digest checks and adjacent work-requirement,
lease and effect-program regressions pass (60 cases), as does the control-plane
typecheck. Selected-owner load failure still rejects without fallback. Four
fresh source/wheel × File/SQLite 101-Todo console correctness episodes preserve
the prior immutable fixture/artifact fingerprints, scopes, original completion
time, four receipts, one debit, refusal/restart/replay and two owned native exits
each. All 1,921 present tracked wheel entries match source bytes; seventeen
source-only, documentation and media omissions are unchanged.

Real PostgreSQL 16.15 store/service suites pass (391/11, zero skips) on an
isolated disposable server that was stopped afterward. The first service run
omitted its dedicated environment variable and skipped one case; that result
remains a failure of qualification, separate from the corrected run.

A browser-fixture interruption check also depended on the shared five-second
completion window and checked the request ledger before its asynchronous receipt
returned. The scenario now holds only the correction's exact Session/Turn stream,
checks that it is still active and waits for the matching interrupt response.
The original ten-second interrupted-message assertion remains intact. A delayed
receipt probe fails the original scenario; removing only the stream hold from the
candidate reproduces that message timeout, while the complete candidate passes.
This is synthetic browser qualification, not proof of a native-runtime defect or
an installed repair. Completion priority and stale-Turn guards remain unchanged;
the combined head still requires packaged validation, CI and independent review.

These episodes qualify integration correctness. Whole-path and full marginal
cost and adoption remain unqualified. Preserve I3 for at least one minor
release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3, context/model/split-install,
PostgreSQL host/wheel and every frozen failure. The original task remains open;
this checkpoint does not authorize merge or declare the minimal kernel complete.

### 2026-11-08 — Canonical source scalar-comparison refinement

The existing `todo.read` owner now uses exact equality for unchanged primitive
fields after its complete canonical-field digest and JSON clone. Containers and
altered scalar values keep the full canonical digest comparison; missing fields,
source-lane additions, scalar-fact protections and stale-reference rejection
remain with their current owners. No source allowlist, provider cache, protocol,
configuration or competing decision owner is introduced.

A fresh synthetic complete-CLI caller trace on `main@aee777008` confirms the
existing two-role native transaction is adopted. Quota before/after projections
belong to different budget decisions, so their repeated read recipes do not
justify an authority cache. The bounded refactoring stays in the existing
canonical source comparison instead of collapsing those decisions.

Final `read_transaction.ts` SHA-256 `ac0ae4311e4fe5c385ec75b3569cd8ebbe98328189e2885b5ecdcccb11c476b3`
passes 61 Python and 56 TypeScript cases plus real isolated PostgreSQL16.15
store391/service11, zero skips in these qualified runs. The immutable original
passes the selected native PostgreSQL source-protection case; a mutation that
treats all primitives as unchanged fails it. Parent replacement, deletion and
restoration, changed notes/scopes/counts and provider-state readback remain covered.
Four complete 101-Todo source/wheel × File/SQLite CLI lifecycles preserve sum45,
original completion time, four receipts, one debit, restart and refusal/replay;
each owns and observes two normal native process exits. The isolated wheel's
runtime source bytes match the checkout. Validation uses macOS arm64,
CPython3.13.5 and Node22.22.3. Earlier toolchain/setup failures and a service-env
skip remain preparation evidence, distinct from the qualified runs.

This removes redundant scalar digest work; it is not a matched-cost or full
marginal-cost/adoption result. Frozen cost, attribution and normal-bytecode
campaigns remain unchanged. Preserve the original complete transaction and all
I3/minor-release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, PostgreSQL host/wheel and historical failure gaps.
Exact-head review, required CI/readiness and maintainer merge authority remain
separate gates; no installed-host upgrade or Goal completion is claimed.
### 2026-11-09 — Reuse validated complete source-basis bytes

The existing Python Todo compatibility transport now retains its validated
canonical field bytes as the complete source basis. It previously encoded each
field twice, once for the basis and once for digest validation. The basis is
only decoded as JSON; mapping insertion order is not a source difference. Full
digest mismatch fallback, scalar-fact protection, request-scope provenance and
source/revision/lease/refusal/replay guards remain. No decoder, cache, provider,
configuration, protocol or competing decision owner is added.

On the same real isolated SQLite field observation, the immutable original
encodes two fields four times and the candidate twice; both retain complete
values and empty source differences. The original passes two mapping-order and
scalar-protection characterization cases; the candidate passes all nine canonical
source cases across File/SQLite. Final `summary_item.py` SHA-256 `a8ca890eee547929060ac326fc8b85ca9c14a8fd5ec850deb2fe3e6586628513`
matches the isolated wheel. Four complete 101-Todo source/wheel × File/SQLite
CLI lifecycles preserve sum45, original completion time, four receipts, one debit,
restart and refusal/replay, with two normal native exits per lifecycle.
Validation uses macOS arm64, CPython3.13.5 and Node22.22.3.

This is an encoding mechanism refinement, not matched-cost or full marginal-cost
and adoption qualification. Frozen cost, attribution and normal-bytecode
campaigns remain unchanged. PostgreSQL host factory absence is already recorded;
its store/service checks do not qualify a source/wheel host journey. Retain every
original acceptance and I3/minor-release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, installed-host, full-cost/adoption and historical
failure gap. Exact-head review, CI/readiness and maintainer merge authority remain
separate gates.

### 2026-11-09 — Combined report and canonical-source caller qualification

The original #8360 proposal now combines report construction isolation with
the scalar comparison from #8372 and complete validated source-basis reuse from
#8374 over target main `aee777008`. Runtime qualification uses
`8b4df2fac966933e200ba15bb36b2f7afb9a194a` and preserves the original proposal's
ancestry. The same existing report and typed Todo owners retain configuration
validation, complete canonical reads, source/revision/lease protection and
receipt/refusal/replay semantics; no decoder, authority cache or provider is added.

Four fresh 101-Todo source/wheel × File/SQLite complete CLI lifecycles retain
the original fixture/artifact fingerprints, scopes and completion time, four
receipts, one debit and two observed normal native exits each. All 1,921 present
tracked wheel entries match source bytes; seventeen existing source-only,
documentation and media omissions remain. On Node 22.22.3 and CPython 3.13.5,
the control-plane typecheck and 105 adjacent TypeScript cases pass. Real isolated
PostgreSQL 16.15 store/service checks pass (391/11, zero skips), and the owned
server is stopped afterward.

The isolated wheel's packaged browser passes `typed-actions`,
`conversation-activity` and `conversation-history-recovery` with synthetic
upstream data. The original exact interruption assertion and completion/Turn
isolation remain. Extended report caller checks return 70 passes and one failure:
the calendar capability-action test expects a missing `turn_envelope`. The same
test fails identically on immutable target main `aee777008`; this is retained
as an unresolved validation gap, not relabeled as passing. Initial relative
interpreter, installed-package provenance and missing-browser preparation
failures are retained separately from the qualified runs.

This combined boundary qualifies integration correctness, not whole-path or
full marginal-cost/adoption benefit. Frozen cost, attribution, normal-bytecode
campaigns, every original acceptance gap and historical failure remain intact.
#8372/#8374 remain separate open proposals pending reconciliation; combination
is not merge. Exact-head review, CI/readiness and explicit maintainer merge
authority remain required. The original task stays open.

### 2026-11-09 — Reconcile merged loading and the remaining source proposal

Before publication, #8360 was observed merged as `e37a1d691`; #8011 is also in
target main `520a1ab47`. The remaining source refinements now continue through
the original #8372 proposal, preserving its `cbda59c6e` ancestry and incorporating
#8374's complete-basis change. The merged report proposal is not recreated.

Fresh runtime qualification at `4e72980bee91c7bbfcedb2eb481b8573113340b3` over
that main passes 82 focused Python cases, 105 TypeScript cases, typecheck,
real isolated PostgreSQL store/service (391/11), four complete 101-Todo
source/wheel × File/SQLite lifecycles and the three packaged browser scenarios.
Wheel ownership remains 1,921 matching entries and seventeen existing omissions.
The standard premerge at that runtime revision passes six direct and nineteen
selected checks. The expanded report family remains 70 passed/one failed;
immutable `520a1ab47` reproduces the same `turn_envelope` assertion failure.
That unresolved packet-contract qualification remains a hold alongside exact-head
review, required CI/readiness and maintainer merge authority. Initial collection,
environment and interrupted interim runs retain their original limits.

This reconciles shipped loading with the remaining reviewed source boundary;
it does not close the original task, installed-host, PostgreSQL host/wheel,
full-cost/adoption or any other retained acceptance. No frozen campaign is rerun
or relabeled, and this lane performs no main merge.

### 2026-11-09 — Reconcile the M0 ratchet after the merged status move

The exact #8372 qualification head's CI fails its kernel-static prerequisite:
merged #8011 moved `chat_status_api.py` into `chat/status_api.py`, reducing the
root from 128 modules to 127, while the fixture still pins 128. The downstream
Stage 2C, checks and aggregate pytest jobs reject their skipped/failed inputs;
those results do not establish new runtime failures or passes.

The existing M0 fixture now records measured main `520a1ab47` and lowers its pin
to 127. Its schema, entrypoint allowlist, exact-count rule and over-budget
rejection remain intact. The same seven kernel-static checks pass on CPython
3.13 and 3.11. In the same isolated source tree, an added unapproved 128th root
module still fails; removing only that synthetic module restores the pass.
The original CI and local 127-versus-128 failures remain retained evidence.

This repairs the validation handoff for an already merged module relocation;
it neither moves another runtime owner nor changes a production limit. Pages,
the separate report packet-contract gap, full CI/review/readiness and every
original installed-host, PostgreSQL host/wheel, full-cost/adoption and frozen
qualification remain separate. The complete original task stays open.


### 2026-11-09 — Complete-source report caller qualification

The M0 pin repair passes CI, together with the real PostgreSQL, typed kernel,
TypeScript, browser and Stage 2C checks at the recorded proposal head. Integration
now includes target main `1cba745f5` while retaining the original proposal ancestry.
The report calendar CLI smoke still fails on that integrated source because it
reads the retired `turn_envelope` carrier. Its `None` replan assertion also
belongs to the old projected adapter: the complete source retains competing
advice; final capability precedence remains with the existing typed controller.

The smoke now gives the single `work_contract` directly to the existing controller.
Across plan, dry-run and executing generic/Codex CLI calls, it checks the same
owned next command, idle host, absent premature report artifacts and unchanged
effect flags. A changed command with the original source binding is rejected.
Dropping the next command from the real CLI result makes the independent smoke
assertion fail. No runtime decoder, compatibility adapter or authority owner is added.

The source and isolated-wheel report families each pass all 71 cases; 101
adjacent Python cases and the control-plane typecheck pass. All inventoried
present tracked wheel entries match the source bytes.
This closes the stale caller-test gap without claiming new execution semantics,
full-cost/adoption benefit or installed-host qualification. Initial reader-choice
and missing-bundle preparation failures remain separate evidence. Pages retains
its known out-of-scope broken link; exact-head CI, review/readiness, maintainer
merge authority and every original acceptance/frozen campaign remain open.


### 2026-11-09 — Complete CLI qualification across native context integration

The retained follow-through plan now runs through complete 101-Todo CLI
lifecycles, rather than stopping at report cases. Source and isolated wheel ×
File/default-SQLite pass at proposal `bb65769cc`. The next target-main snapshot
`3b16b8491` adds native preparation/navigation changes; the combined runtime
`9baeafd8a` passes the same four complete lifecycles with a newly rebuilt wheel.

Every case retains the original immutable synthetic fixture and independently
verified sum-45 artifact, complete source/lease/refusal/replay/recovery checks,
four committed receipt kinds, one debit, unchanged completion time and two
observed normal native exits. The existing scalar/source-basis owner bytes
remain unchanged. All inventoried present tracked wheel entries match this
runtime source. Seven progressive-context contract cases and its typecheck pass.
The stale frontend bundle's initial wheel-build refusal remains separate from
the rebuilt package's qualification.

This is integration correctness for the recorded source/package snapshots.
It does not rerun or relabel the frozen cost, attribution or normal-bytecode
campaigns, qualify current browser/installed-host or PostgreSQL host/wheel
behavior, or establish full marginal-cost/adoption benefit. Current exact-head
CI, review/readiness, maintainer merge authority and the existing out-of-scope
Pages hold remain separate. The original task and all original acceptance stay open.

### 2026-11-09 — Minimum supported Python CLI qualification

CPython 3.11.15 now qualifies the complete 101-Todo source/isolated-wheel ×
File/default-SQLite lifecycle at `15c937821`, extending the earlier 3.13
qualification. Target main `a3b589610` subsequently changes context reading and
settlement guidance; integration `220275f6a` repeats the four complete paths
against that new input and a newly built wheel. Both groups preserve the same
immutable fixture and independently verified sum45 artifact, original completion
time, full source/lease/refusal/replay/recovery behavior, four committed receipts,
one debit and two observed normal native exits per case.

The earlier source and wheel each pass nine canonical-source cases; the current
integration separately checks the merged report CLI caller in both distributions.
Loaded wheel modules retain package provenance. Inventoried tracked entries
present directly in the new wheel match source bytes; inventory counts are not
used as a benefit comparison. Merge reconciliation retains the upstream module
baseline and unchanged 127-module limit, explicit transaction-detail delivery,
capability precedence and the original source-binding negative assertion.

The private verifier's initial Python syntax failure and stale-bundle build
refusal remain preparation failures, distinct from runtime qualification. The
previous-head synthetic 200-item lens-switch CI budget failure remains a hold;
these CLI checks do not qualify browser performance. No frozen cost, attribution
or normal-bytecode campaign is rerun or relabeled. I3/minor-release, M1/#8011,
full R0/V10/R1, D2/D5/D6/D3, context/model/split-install, PostgreSQL host/wheel,
installed-host, full-cost/adoption and all historical gaps remain open. Exact-head
review, required CI/readiness and maintainer merge authority still apply.

### 2026-11-09 — Current-main required-Vision refusal qualification

The proposal incorporates `main@aa475b29f` and its merged #8388 qualification
repair at integration `2dd6c3d97`. The existing qualification adapter preserves
the native `refresh-state` refusal output for required Vision while retaining
its nonzero status and existing feedback bound. A missing stride cannot append
writeback or spend; the caller can inspect the structured refusal, correct the
same draft and then settle once. The typed writer remains the decision owner.

On macOS arm64 with CPython 3.11.15 and Node 22.22.3, the complete existing
required-Vision family passes 35 cases from source and 35 from an isolated wheel.
The source-pinned `a3b589610` adapter fails the new missing-stride correction
case. An initial comparison accidentally loaded the candidate through pytest's
checkout insertion; that pass is invalid as a baseline result. The corrected
comparison and wheel run verify every loaded LoopX module's package origin.
These are scripted synthetic native-CLI qualification cases, not model results.

The wheel matches 1,921 directly corresponding tracked ZIP entries, including
the qualification adapter. This count is not compared with historical inventory
methods. `read_transaction.ts` and `summary_item.py` retain the bytes qualified
at `86d08a01c`; the earlier complete 101-Todo File/SQLite source/wheel lifecycle
and PostgreSQL API/store evidence are retained, not rerun or relabeled. Standard
premerge at the integration head passes six direct and nineteen selected checks.
The bounded refactoring pass needs no additional production abstraction here;
the existing writer and feedback boundary already own this behavior.

The review published for `86d08a01c` remains historical evidence for that head.
The updated proposal still needs current-head CI, review and merge readiness;
this qualification grants no merge authority. Preserve the original transaction,
I3/minor-release, M1/#8011, full R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, PostgreSQL host/wheel, installed-host, complete-cost
and adoption gaps, and all frozen negative results. No kernel completion or
whole-path benefit is claimed.

### 2026-11-09 — Reuse fresh canonical field digests in quota reconstruction

The proposal incorporates `main@c44eb156d`. A complete canonical read already
returns a digest for each projected Todo field. Quota reconstruction now uses
that digest from the same fresh in-process read rather than hashing the entire
field again. The existing selector path retains its direct hash comparison.
Complete cloning, immutable provider facts, source revisions, role checks and
caller-change rejection remain in the existing TypeScript owner. The digest is
neither caller-provided authority nor retained across requests; supplied-source
fallback is unchanged. No protocol, configuration or provider is added.

The current source and isolated wheel each pass 24 complete-source cases; 76
adjacent planning cases, 23 native read cases and control-plane typing pass.
A swapped user/agent digest is rejected, followed by a successful valid pair.
The same negative case passes on the current-main baseline; removing digest
rejection in an isolated source copy makes both File and SQLite cases fail.
Every loaded LoopX module is checked against its selected source or wheel.
Current-main and candidate source File/SQLite CLI lifecycles, plus candidate
wheel File/SQLite lifecycles, preserve the same 101-Todo fixture and sum45
artifact, original completion time, refusals/replay/restart, four committed
receipts, one debit and two normal native exits per case. The initially stale
frontend bundle refused packaging; rebuilding through its existing owner makes
the new wheel build pass. These are correctness checks, with no cost comparison
or rerun of frozen campaigns. All earlier acceptance gaps and negative evidence
remain open; the changed head needs fresh review, CI and merge readiness.


### 2026-11-09 — Kernel integration with the prospective M2 qualification

The original #8372 branch integrates target main `6787fc8d5`, including the
prospective Dashboard qualification in #8433. Kernel runtime `17d85c971` passes
53 source and 53 isolated-wheel Python cases, twelve focused TypeScript cases
and typing. All 1,921 directly corresponding tracked wheel entries match. The
same six complete 101-Todo base/candidate source and candidate-wheel File/SQLite
CLI lifecycles retain the fixture, sum45, original completion time, complete
scopes, four receipts, one debit, restart/refusal/replay and two normal native
exits per case. Real isolated PostgreSQL16.15 store391/service11 pass without
skips. The same current PostgreSQL source-protection harness passes base/head;
a primitive-protection mutation fails its expected-rejection oracle. Standard
premerge passes six direct and nineteen selected checks. Local runtime checks
use CPython3.13.5; CLI and mutation qualification use Node22.22.3, while the
focused source and full PostgreSQL suites use Node26.0.0. Initial packaging,
fixture-import and mutation-snapshot preparation failures remain distinct.

This qualifies kernel integration correctness. It neither repeats frozen cost,
attribution or normal-bytecode campaigns nor qualifies Linux browser recovery,
installed-host, PostgreSQL host/wheel, full marginal-cost or model/team adoption.
Keep every historical failure and original acceptance open. The prospective M2
rule does not turn a historical frozen failure into a pass; unchanged-head
review, required validation and fresh merge readiness still govern delivery.

### 2026-11-09 — Release qualification partition proposal

Two unchanged-head Release Artifacts runs passed the complete 86-scenario
packaged browser catalog and original companion flows after approximately
28 minutes, then cancelled during snapshot qualification at the existing
30-minute job limit. A constructed snapshot is not a qualified snapshot;
these cancellations and all earlier frozen latency failures remain failures.

The S12 distribution qualification proposal separates package/sdist/installed
frontend/DSH and snapshot checks from three isolated browser partitions, using
the existing catalog, partition selector and packaged catalog/team/craft
commands. Every original scenario and companion remains required; the craft
command also includes its existing command-palette check. This changes release
browser concurrency and repeats dependency setup/static checks. It does not
change job limits, scenario deadlines, M2 thresholds or the six shared Python
validation lanes. The first complete Linux qualification,
[run 42633152296](https://github.com/loopx-project/loopx/actions/runs/42633152296),
finished preparation in 1m43s, browser workers in 12m46s/9m25s/7m29s and the
final gate in 22s. All 86 unique scenarios and required companions passed,
and the snapshot checks completed. The tested synthetic merge `ef048a32c`
and integrated proposal `5a9fd8020` have the same complete tracked source tree;
this qualifies that candidate only. Later main integration changes product
sources and requires fresh packaged qualification. Complete CI cost and
comparable end-to-end marginal-cost benefit remain unqualified.

The subsequent complete Linux qualification,
[run 42649914601](https://github.com/loopx-project/loopx/actions/runs/42649914601),
passed all 86 scenarios, team/craft companions and snapshot checks. Its product
source `4747408cc36067a9175bd4a00c440466c316ffc3` has the same complete tracked
tree as proposal `9b4ab03a96eff9c3866193819d7da50945912db7`. Downloaded final
receipts and bundle checks independently confirm common source, catalog coverage
and package/runtime digests. Later integration of the merged kernel work and
current main changes that product tree again; the integrated proposal requires
its own complete qualification. The separate coverage-enabled source M2 failure
remains failed. Its prospective measurement repair in
[#8456](https://github.com/loopx-project/loopx/pull/8456) does not reclassify that
run or replace this proposal's exact-head CI and merge-readiness gates.

Workers install the same candidate wheel and adopt only the producer's exact
selected fixture commit/paths. Source-side fixture authorities also receive the
prebuilt frontend copied from that wheel, validated against the product source;
they do not rebuild it. The initial partition run omitted these source assets
and failed two research companion paths; preserve that failed run separately. Package, checksum, snapshot and source digests,
plus the candidate artifact id, bind the final `build` gate to successful
partition receipts. The gate imports the actual catalog without starting a
browser and rejects missing/failed/unknown/duplicate/mispartitioned scenarios,
wrong provenance and incomplete companions before attestation or the existing
validated-bundle upload. Publication-only recovery retains its successful
original-build and immutable-asset checks. Local negative-case qualification
is separate from a complete GitHub runner qualification.

This is a release-pipeline qualification repair for the minimal-kernel work;
no kernel code or performance attribution changes here. Exact-head review,
required validation and fresh ready=true still govern merge. The original task
and I3/minor-release, M1/#8011, R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, PostgreSQL host/wheel, full-cost and adoption gaps
remain open.

### 2026-11-11 — Integrated main packaged release qualification

The complete Linux Release Artifacts
[run 42905934918](https://github.com/loopx-project/loopx/actions/runs/42905934918)
(attempt 1, from [#8531](https://github.com/loopx-project/loopx/pull/8531))
qualifies the integrated source at `main@b89a574b05a3e7d5920a8e7184476dfb7b041790`.
The bundle's product, browser-fixture and workflow commit are all
`3606315c86f907961b0c26d4f1a1dad6bbaed8cf`, with no fixture overrides; its
complete tracked tree equals that merged main tree. This supplies the missing
packaged qualification after the kernel and release-pipeline integration.

Offline verification of final artifact `13807484684` checks the downloaded ZIP
against GitHub's SHA-256, every package/runtime file against `RELEASE-SOURCES.json`,
and the checksum manifest against both packages. All three browser receipts bind
to candidate artifact `13806066680` and the same release-source digest. Comparing
them with the actual source catalog and partition selector confirms exactly
86 unique passing scenarios across 29/29/28 entries. Team, craft, command-palette
and navigation companions pass. The producer also passes clean-wheel install,
sdist rebuild, installed frontend, DSH install shape and runtime qualification,
including upgrade, doctor, skills, frontend HTTP, license notices, operation
without frontend build tools and restoration after activation failure.

The completed ordinary packaged M2 observation retains 200 items, five switches
with three samples each and 15 long-task windows. Its five medians are
336.34/313.05/441.09/200.01/194.94 ms; the maximum is 601.68 ms, with no sample
over the 1,000 ms target. The existing ordinary median <1,000 ms and every-sample
<2,000 ms rules pass without a threshold change. This is neither a new
coverage-enabled measurement nor a cost or kernel-attribution comparison;
historical frozen failures and their raw observations retain their original status.

This evidence qualifies the named integrated Linux packaged source, not later
product changes. The pull-request run skips attestation, release upload and PyPI
publication; no public release or current installed-host upgrade is established.
It does not establish all other CI results or complete CI/marginal cost. The
original task and I3/minor-release, M1/#8011, R0/V10/R1, D2/D5/D6/D3,
context/model/split-install, PostgreSQL host/wheel, installed-host and adoption
gaps remain open. The next qualification stays with those original owners and
acceptance boundaries; the minimal-kernel effort is not complete.

### 2026-11-11 — Canonical snapshot failure provenance

Paged and complete snapshot reads distinguish a rejected persisted Todo contract
(`canonical_snapshot_projection_invalid`) from malformed request arguments. The
existing typed validator still rejects unknown manifests and invalid record
digests, preserves the original reason and returns no partial rows or legacy
fallback. Historical supported manifests remain readable. This is a diagnostic
correction in the existing source transport owner; it grants no forward
compatibility to older readers and does not upgrade an installed runtime.

Real File/SQLite RPC and CLI regressions, plus an isolated PostgreSQL read,
qualify the failure boundary without rewriting authority. The original
installed-host qualification and full-cost/adoption gaps remain open.


### 2026-11-12 — Standalone core artifact and installation diagnosis

The opt-in `packages/loopx-core` source builder reuses tracked runtime sources
and produces an independently installed wheel without Chat, web assets, the
canary fleet or the full CLI facade. It retains existing built-in capability
and host compatibility imports. This is the core-artifact prerequisite for
R1/M3; it does not qualify the complete minimal closure or publish a release.

The installed journey uses the existing generic CLI host, independent artifact
validation and canonical hard leases. The original admitted key/version travels
through the shared TypeScript preparation and checked Turn context to the
existing completion owner. Process loss resumes the confirmed result; replay
does not invoke the host or debit quota again. A renewed/released lease or an
invalid artifact refuses completion without state or quota effects.

Deep installation diagnosis now selects the actual distribution's RECORD and
required entrypoints. A healthy core passes; an unrecorded launcher or missing
runtime file remains a failure. Ordinary distributions still require their full
CLI. Core supports the advertised history action through its existing handler
and parses global version options without the omitted facade. The installed
qualification covers default SQLite and explicitly configured File hard leases;
help/import remain usable without Node, while governed work retains the existing
Node prerequisite. Ordinary-distribution CLI and installation parity have their
own matched checks. The package-smoke lane executes the installed qualification.

Preserve D1-D7 and all original milestones, historical failures and acceptance
boundaries. Workspace/meta-package publication, complete install/load/context
closure, independent Standard packages, other host/platform adoption, matched
model effectiveness and complete costs remain open. This synthetic task and
recovery evidence establishes neither model benefit nor an installed-host upgrade.

### 2026-11-12 — Replan consumes only required Todo views

The replan writeback caller now requests its two role summaries and complete
Agent terminal rows through the existing quota read transaction. The existing
TypeScript display owner accepts an explicit subset; omitted selection retains
all three ordinary quota views. User source-summary rows, unused Agent views
and prepared Vision wait facts are not generated for this caller. Source
references, complete supplied-source fallback and provider error diagnostics
retain their existing owners. No storage, lease, scheduler or quota-debit rule
changes.

Against immutable base `0abd0dc27`, candidate code `46f844e9d` uses the same
disposable mixed-state fixtures and six alternating pairs per workload on real
File/default-SQLite. SQLite replan qualification medians at 320/3,500 extra
Agent Todos are 93.2→83.3 ms and 612.3→572.0 ms; canonical-read plus
qualification medians are 139.4→130.0 ms and 900.9→857.0 ms. Strict refresh
dry-run rejection medians are 142.4→135.1 ms and 934.4→911.8 ms. At zero extra
Todos that refresh median is 28.9→29.3 ms; retain this observation without a
noise or causality claim. Up to 128 User rows are included. Normalized
obligations and rejection details match; counting fewer RPCs alone is not the
acceptance oracle. Seventy focused production-provider and transport checks
pass, including complete terminal tails, stale/missing sources and ordinary
display defaults.

Preserve #8624's original negative SQLite qualification (83.0→91.2 ms and
605.9→704.1 ms) at its original head and criteria. This new scoped result
qualifies the consumed replan read cost; full CLI process, durable writeback,
model and end-to-end outcome costs are not inferred from these timings. The
complete minimal load/context closure, R1/M3, independent Standard packages,
workspace/meta publication and installed-host adoption remain open.


### 2026-11-12 — Dashboard source acceptance executes its backend journeys

Source coverage now executes the existing first-Goal and runtime-readiness
journeys through the real disposable Chat service. Both source and packaged
lanes already prepare the verified Chat bundle. Declared backend journeys
require successful backend assertions before a PASS receipt; an early return
cannot qualify them. Scenarios with other legitimate coverage boundaries keep
their existing behavior. The shared browser proxy adds EPIPE to its existing
three-attempt GET/HEAD socket recovery, recognizes typed errno or the first
Playwright socket-error line, and never retries an HTTP write or arbitrary prose.
Research-record search checks bind help dismissal to its current keyboard trigger
and require the selected query to survive dismissal.

On immutable source `eedfc5e24`, local macOS/Node26 qualification passes all
86 scenarios across three non-overlapping 29/29/28 partitions with a common
catalog and source tree, verified backend-readback markers and nonempty merged
coverage. The passing partitions take 1,943.74 seconds; earlier failed runs,
setup and repair remain separate records. This is validation cost, with no
complete model/service/human cost comparison.

The matched 200-item M2 pair retains all 15 samples and long-task windows.
Ordinary medians are 225.77/172.99/310.77/128.45/146.52 ms, maximum 366.08 ms;
coverage medians are 328.21/371.20/332.49/174.73/173.02 ms, maximum 421.22 ms.
Both have zero samples above the 1-second target. Full-suite coverage M2 also
passes: medians 325.35/270.28/326.59/164.22/166.47 ms, maximum 463.88 ms.
The real 1,200 ms repeated Board delay is correctly rejected by ordinary
qualification (slow median 1491.77 ms, maximum 1519.98 ms; all 15
samples/windows retained). Existing strict-boundary and missing-sample tests
pass without a budget change.

Earlier failed source partitions (EPIPE, query loss and temporary-directory
failure) remain recorded separately, as do historical frozen 1.162-second
failures and cancelled runs. This repairs
source-acceptance execution and its bounded transport recovery, with no render
threshold, kernel runtime or authority change. The first-Goal fixture uses an
exit-only Agent: registration and honest failed-Turn recovery do not prove
natural-language task completion or model benefit. Complete minimal closure,
R1/M3, workspace/meta publication, installed-host adoption and full-cost
acceptance remain open.

### 2026-11-13 — Standalone natural-language startup reaches existing owners

The independently installed core now routes `start-goal`,
`bootstrap-command-pack`, `register-agent` and `bind-agent-thread` to the
ordinary product's existing registrars and handlers. Previously all four
required the omitted full CLI facade. The goal preview preserves exact text,
remains read-only and exposes the same ordered bootstrap, identity, Todo and
host/guard transaction. Fresh registration, collision rejection, global/source
readback and bound-session reuse execute against the default SQLite authority.
No new engine, model service, authority rule or capability activation is added.
The ordinary installation retains its complete-parser diagnostic fallback;
invalid standalone arguments retain the existing explicit unavailable failure.

Installed qualification passes 16 checks, including the guided identity journey
and existing SQLite/File execution, rejected artifacts, renewed/released lease
refusal and confirmed-result recovery/replay. Eight focused comparisons match
the ordinary parser and preview. The original baseline independently installed
wheel refuses natural-language startup; the repaired wheel executes it. These
are executable behavioral deltas, not help-text-only delivery.

A separate isolated generic-CLI task was performed by the current host model:
create a reusable inventory generator and JSON/Markdown reports from source
catalog/movement files, with exact decimal valuation and excluded records. Its
independent validator accepts the result. Killing the driver after its typed
result was journaled, then resuming the original key, completes the Todo and
records one settlement without invoking the model again; a further replay has
no state or quota effects. An earlier pre-work attempt expired while awaiting
model input; its unknown-outcome recovery rejection remains recorded. There
were two host transport dispatches in total and one confirmed model result;
this is not a claim of one dispatch across the entire experiment.

A three-pair local loading census (macOS arm64, Python 3.13.5, Node 26.0.0,
new Python processes with ordinary filesystem/bytecode caches, plus two warm
calls per process) observes 8 core modules for top-level help and 186 for guided
help/preview. The direct ordinary full-facade comparison loads 1,151/1,164
modules. Preview includes two subprocess events and nine Python socket audit
events, so process/IPC costs must remain in the closure inventory. Different
source/install cache and launch conditions, three samples, Python-only audit
coverage and unavailable model-token counters prevent a performance or complete
V10 claim. This does not approve D2 or a minimal preset.

The source-regression attempt that exhausted local disk remains failed; its
recovered run is recorded separately. Complete minimal install/load/context
closure, initial/revised/compacted/lost-ACK context qualification, supported host
adoption, full V1–V5/V10, R1/M3, Standard/workspace/meta splitting and complete
costs remain open. Rollback the routing slice while retaining task authority and
reconciling original attempts. Existing exact-head review, premerge and bilingual
requirements, original failures, budgets and separate research/release authority
remain in force.

### 2026-11-13 — Startup metadata isolation and installed context recovery

Default goal-start help and preview previously imported issue-fix execution,
candidate preflight and repository analysis merely to render command hints.
The existing command templates now have a capability-owned metadata module;
bootstrap and catalog consumers use it, while the workflow module preserves
its existing Python import API and an explicit issue-fix route retains its complete preflight
contract. Independently installed base/head startup output is byte-identical;
default help/preview load 186→171 LoopX modules and the explicit route 186→179.
Module counts alone establish a narrower loading boundary, not a speed claim.

Thirty alternating installed base/head pairs per workload on macOS arm64,
Python 3.13.5 and ordinary OS/bytecode caches observe first-entry median/p95
of 314.73/362.55→181.25/303.01 ms for help and
542.36/670.44→311.08/572.66 ms for preview. Two subsequent calls per process
have warm medians 0.66→0.54 ms and 11.83→8.94 ms. Median peak RSS is
43.22→32.46 MB and 46.05→36.18 MB. All complete outputs match;
preview remains 52,727 bytes. Retain the head preview maximum 1,092.15 ms.
First entry includes imports; this is neither OS-cold nor full V10 qualification.
Source regression passes 348 checks with one native Windows/PowerShell skip;
the final installed wheel passes 20 checks. Earlier failed attempts remain.

Installed CLI qualification exercises the existing context-project/read/hook
owners with complete source or explicit recovery: off keeps original values,
partial delivery and lost ACK cannot establish a baseline, unchanged/revised
views preserve complete constraints, standalone PostCompact invalidates the
baseline, and a changed captured file is refused. Warm transport may choose
full when complete delta framing would cost more. The test runs against the
original installed core as well; it qualifies the existing installed protocol
and supplies a regression boundary rather than introducing another context
owner. Native events are fixtures, not evidence of model retention or adoption.

Instrumented SQLite/File Turn witnesses retain Python, actual Node-loader and
RPC observations. The selected Turn entry still loads 525 Python modules,
including 111 capability modules; its Node runtime retains eager shared typed
support. Complete work contracts and transport costs remain in the inventory.
This diagnostic does not qualify endpoint/network completeness, OS-cold cost,
model tokens or the full minimal composition. Keep earlier failed checks and
the separate startup/context boundary; full V1–V5/V10, R1/M3, independent
Standard/workspace packages, installed-host adoption and complete original-goal
regression remain open under their existing owners and acceptance.

The installation gate exposed a project-skill supporting-example link that left
the installed bundle. The link now resolves to the byte-verified public source
at an immutable revision; the procedure, consent and check-scope clauses remain
unchanged. It is an on-demand supporting reference, not a runtime fetch or new
authority. Preserve the earlier failed installation gate and disk-limited base
replays separately from the repaired-head validation.

### 2026-11-13 — Installed context regression enters the package lane

The standalone core lane now invokes `tests/test_core_distribution.py` and
`tests/control_plane/test_installed_core_context.py` together, and both pull
request and main-push filters observe the context suite. The previous lane
omitted that suite despite its local qualification. An independent workflow
check rejects the unchanged base for all three omissions and accepts the
repaired command and filters. The clean Python 3.11.15 wheel's 1,885 tracked
runtime files match the source bytes; the installed artifact remains separate
from the source test environment.

Additional macOS arm64/Python 3.13.5/Node 26 observations use the same installed
core 2.3.0 and isolated SQLite/File complete-Turn fixtures, with synthetic Hosts
and usage telemetry disabled. The two selected Turn processes still load 525
LoopX Python modules, including 111 capability modules, and issue 220/221 Effect
RPCs. All 1,024 observed Python connect/address-resolution events target
loopback; the two Node listeners and their DNS lookups also use loopback. Four
Node processes, including the Host supervisors, load 247 distinct files. The
observer's own local-connection and named-import subprocess controls pass;
the earlier incomplete observer remains separate from this checked observation.

These observations identify local RPC traffic in this fixture; they do not
establish OS-wide network completeness, ordinary telemetry behavior, model
context adoption, paired performance or full costs. This delivery closes the
installed protocol's CI coverage gap without selecting D2/D5 or a minimal
preset. Real Host initial/revised/compacted/lost-ACK adoption, complete V1–V5/V10,
R1/M3, Standard/workspace publication and original-goal regression remain open.
Preserve the earlier failures, frozen budgets and unchanged governance owners.

### 2026-11-13 — Managed Codex preparation respects start admission

The existing built-in Codex CLI route can prepare an eligible hard-lease Todo
through the exact ordinary claim advertised by its typed decision. The caller
then rereads the same Todo and requires ready before Host launch. This removes
the separate manual claim step; it does not select a new host or grant authority.
The prior proposal at `d4348db14d51952a74247e3003768a2f969c7216` still wrote a
lease and Turn receipt before invalid validator input or cadence waiting was
rejected. The repaired route validates the existing Host arguments and obtains
start admission from the same TypeScript cadence owner before those writes,
then reserves the exact Turn binding before the ordinary claim. Start admission
runs inside the existing cross-runtime Turn binding lock, so a competing caller
cannot reserve one Todo's interval and then let a different binding win.

Preparation and execution reuse the settlement effect identity for the start
reservation. A claim acknowledgement lost before journal creation can therefore
recover its already confirmed lease and original unstarted reservation. The
existing transaction-key reservation is retained as an exact recovery fallback
for historical Codex journals; a started request cannot use that fallback to
run again. An uncertain claim reports unknown state effects instead of asserting
that nothing was written. A successful claim receipt remains visible if the
subsequent Host step fails. The existing spend precheck refuses a fresh claim
for a settled Turn even after its lease is released, directing recovery to the
recorded journal. No new persisted schema or governance owner is added.

Real source CLI checks on isolated File and SQLite reject malformed validator
JSON/argv and scheduled waits with unchanged leases, Turn receipts, journals
and Host calls. The same independent oracles fail on the prior proposal.
Interruption after the ordinary claim recovers with one Host attempt and one
settlement; recorded-key replay has no effects. Installed core qualification
uses the same existing package lane and synthetic Codex transport, with an
independent artifact validator and result readback.

This is an executable-start prerequisite for V2/V3/V4, not whole R1 acceptance.
The Host is synthetic: actual model adoption, complete V1–V5/V10, default
closure, paired complete costs, progressive product control, R1/M3 and original
Goal regression remain unqualified. Keep the nine original requirements,
previous failed attempts and frozen budget failures; no minimal preset,
provider default, package publication or paid operation is promoted here.

### 2026-11-13 — Full Turn defers unselected provider implementations

The installed core Turn previously loaded BM25 and OpenViking while resolving
Reward Memory configuration, and loaded the anti-entropy inspector even when
retirement governance was disabled. Context-provider exports and the existing
builtin builder registry now resolve implementations on explicit use. A pure
OpenViking scope owner is shared by binding normalization and the provider;
the original URI grammar, public aliases and Python serialization identity are
retained. Anti-entropy still uses the existing typed activation and applicable
basis; its feedback builder resolves only after those checks. No capability,
provider default, authority, claim, stop or settlement rule is added or relaxed.

The 12 fresh-process checks cover registry rejections/custom builders, real
local BM25 retrieval, binding classification, disabled/enabled retirement and
attribute/from/star exports. The original 11-check corpus fails five loading
oracles on immutable base `441c6077e7c5c8e896ea79562761fd483baf157e`, while its
six export checks pass. Sixteen URI success/rejection cases and protocol-4
pickle bytes match between installed base and candidate; cross-version reads
pass both ways. Definitions move to the pure scope source file: declaration
source introspection is not preserved by the original-module alias.

The final independent Python 3.11 core wheel contains 1886 runtime files that
match source bytes (SHA256
`bc36c92dd437b9418e21031e185fefd04984c91a30169cbd866a3c712d92b4fa`).
Source capability regression passes 628 cases. Real isolated File and SQLite
execution commits a checked artifact and one settlement; original-key replay
has no Host, state or quota effects. Instrumented full-Turn module counts,
including the package root, change from 525 to 516 on execution and 438 to 432
on replay; capability counts change from 111 to 102 and 56 to 50. Execution
RPC counts remain 221/220 and replay remains 11. The unselected OpenViking,
BM25 and anti-entropy inspection implementations disappear from those paths.
These counts describe the observed Python path, not the complete minimal closure.

Matched installed observations use macOS 27 arm64, Python 3.11.15, Node 26.0.0
and five
alternating base/candidate pairs per backend and phase. Each invocation starts
a new Python process. Before first execution, the isolated managed RPC server
is normally stopped and must restart; completed-Turn replay reuses that server.
Setup and independent readback are outside timing; OS, bytecode and compilation
caches are not reset. Nearest-rank p95 is the maximum of five samples.

| Backend / phase | Wall median / p95 ms, base → candidate | Median invocation RSS MiB, base → candidate |
| --- | --- | --- |
| SQLite / fresh execution | 2922.5 / 3561.9 → 3006.8 / 4139.9 | 81.81 → 81.25 |
| SQLite / completed-Turn replay | 809.6 / 948.6 → 889.0 / 1415.0 | 44.73 → 44.42 |
| File / fresh execution | 2858.7 / 3005.5 → 2862.5 / 2985.0 | 81.09 → 81.36 |
| File / completed-Turn replay | 645.4 / 679.7 → 692.5 / 773.9 | 44.73 → 44.28 |

All 40 invocations preserve the same independent result/effect fingerprint;
stdout sizes remain 4002 bytes for execution and 4548 for replay. The candidate
has slower SQLite medians and a higher tail: no latency or complete-cost
improvement is qualified. Invocation RSS excludes the persistent Node server;
zero block-I/O counters do not prove zero file access. Separate controlled
observers verify Python import/open/process/loopback events and Node module/
loopback/exit events, but do not provide OS-wide closure, complete IPC/I/O or
model-token, service-cost and human-attention accounting. The Host remains
synthetic. Full V1–V5/V10, real model adoption/recovery, progressive product
control, R1/M3, independent Standard packages and original-goal regression remain
open. Preserve all nine original requirements and earlier frozen failures.

### 2026-11-13 — TraeX continuation consumes its runtime thread

The public `start-goal` entry now reads TraeX's `TRAECLI_THREAD_ID` through the
existing host-thread adapter. A verified `traex-cli` binding reuses its Agent
without another `--thread-id` or `--agent-id`; an explicit thread still takes
precedence. Missing, new, foreign-host and conflicting contexts retain the
existing identity fences. Trae App keeps its separate binding scope.

Matched isolated File/SQLite CLI checks cover source, the standalone core built
from `09886ba7a`, and the candidate core: 36 calls retain exact objective text,
registry/state bytes and canonical provider revision across ordinary entry,
explicit control/override, unknown context and replay. The base still loses the
ambient thread; both repaired entrypoints reuse the bound Agent. Source
regression passes 215 cases, including foreign-host and conflict refusal;
the installed core/recovery/context suite passes 23 on Python 3.11.15, with
source on Python 3.13.5 and Node 26.0.0. All 1,887 packaged runtime files match
the reviewed source bytes. Initial fixture setup failures remain failed attempts.

TraeX activation guidance reads native Goal tools or current host context first,
preserves an active original objective on continuation, and retains user stops.
Only an authorized fresh start with no existing native Goal may create one;
the visible `/goal` fallback remains for unavailable tools/context. This is an
entry and instruction repair, not proof that a model adopted the guidance.
No new lifecycle owner, model execution, preset or installed-host release is
introduced. Full R1/V1–V5/V10, progressive control, complete cost evidence and
the original-goal regression remain open; all original failures are retained.

### 2026-11-13 — Original record reads stop when their disclosure closes

The optional attached-work conversation retries an interrupted detail read once
at the same source, record and character offset. Closing either the record or
its work-activity group cancels that read, including its hidden retry; reopening
retains the loaded text and position for an explicit continuation. The existing
HTTP reader, fingerprints and native-input boundary remain the owners.

The unrepaired candidate's real HTTP/browser negative case issued a second read
after closure. Source and an isolated Python 3.11 wheel now qualify both closure
boundaries, interrupted-success recovery, denied-body refusal and complete
Unicode readback through the real LoopX reader with a synthetic Desktop peer.
This qualifies optional record reading, not model-context retention, managed
V4/R1, split-package M3, installed-App adoption or complete costs. The original
Goal regression, acceptance gaps, authority and frozen failures remain intact.

### 2026-11-13 — Standalone continuation retains its installation

Standalone startup previously returned an ambient `loopx` command even when
the caller selected an isolated core interpreter. Executing that complete
command could import another installation through PATH/PYTHONPATH. Earlier
installed tests discarded the executable and replayed its arguments, hiding
the failure. Core-generated commands now retain the same environment's Python
with `-I -m loopx.entrypoint` through the existing invocation owner. An explicit
global `--cli-bin` and ordinary-distribution defaults retain their behavior;
invalid arguments retain complete parser diagnostics. Multi-Goal selection
uses the same command renderer.

Core module diagnosis verifies its actual imported distribution, recorded
module and present recorded console entrypoints. Version/catalog/help probes
use the same isolated module invocation, so a foreign PYTHONPATH cannot
silently change the artifact being checked. Missing or redirected module
records, absent console records/files, unrecorded launchers and missing runtime
files remain failures. The installed qualification executes whole returned
activation and diagnosis commands, with a foreign-package sentinel and an
explicit-route control, alongside existing SQLite/File recovery and context
checks. This repairs command provenance without adding a lifecycle, authority,
configuration or protocol owner.

Full R1/V1–V5/V10, real model recovery/adoption, progressive product control,
minimal dependency closure, M3/Standard/workspace/meta packaging and complete
cost acceptance remain open. This does not upgrade an installed host, start a
model service or establish effectiveness from protocol fixtures. Preserve all
nine original requirements, the original-Goal regression and earlier failures.

### 2026-11-13 — Successful work records a fresh checkpoint on the same route

The generic Host result boundary previously required a fresh Vision to use
`material_replan` and `replan_required`. A successful task retaining its route
could only reuse an unchanged reason, even when it had new outcome evidence.
An explicit `path_delta_mode: unchanged` now accepts the existing Vision packet
with `path_delta.outcome: continue` or `no_change`, without an unchanged reason.
The existing TypeScript Vision, task, validation and settlement owners still
decide durable effects. Material replans, stop decisions, malformed packets and
conflicting declarations retain their refusal. Omitted modes retain legacy
inference; generic and Codex guidance describe the same boundary.

One existing isolated inventory task used the current model's tools through a
stdio adapter and a Python 3.11 standalone core artifact on SQLite. Revised
inputs yielded quantities 22/10/5 and value 185.25, versus 172.25 previously.
Independent checks verified the 13.00 reconciliation, all six excluded rows,
reusable generator and preservation of all five original files. After the
normalized result was journaled, SIGKILL interrupted the driver and validator.
The original Turn resumed at validation without invoking the Host, completed
the Todo and spent once; completed replay added no Host, writeback or spend.
The initial invalid completion/replan reply and three resume setup refusals
remain failed attempts. There were two Host dispatches and one model work pass;
the rejected reply was not treated as confirmed work.

Source executor/driver checks and Codex/dsh regression pass. Installed File and
SQLite checks retain legacy completion and qualify both fresh checkpoint
outcomes plus replay; the initial oversized inline test command failed its
existing bound, then the fixture used an ordinary script path. No command
budget, authority, default or historical receipt was changed. This qualifies
the touched success/checkpoint and recovery path, not full V1–V5/V10 or R1.
Complete context retention, minimal closure, progressive product control,
comparable complete costs, M3/Standard/workspace delivery and the original-Goal
regression remain open. All nine original requirements and frozen failures
remain intact; no installed host or model service was upgraded.

### 2026-11-13 — Task presentation omits outer executor data

Current built-in model renderers derive `loopx_turn_model_context_v0` from the
validated complete v1 source in an adapter request. Task requirements, scope, required reads,
backlog and unknown additions survive; source binding, effect projection and
scheduler/automation execution data remain with the outer executor. Existing
replan authoring and Explore guidance references resolve before their owner is
omitted, preserving source numbers and text. The model view cannot substitute
for a signed source. Generic adapter stdin, private journals, original-reader
provenance and historical envelope-only v0 rendering keep their existing contracts.

Seven alternating paired fresh-process probes and 49 warm renders per version
on the same captured inventory request measure model JSON at 44,841 versus
34,986 bytes and Codex prompt at 48,724 versus 38,859 bytes. Warm median/p95
rendering is 9.31/11.27 ms versus 9.02/10.26 ms. An earlier candidate batch
measured 9.18/11.46 ms versus 10.09/12.44 ms. Final fresh-process median/p95 is
25.03/435.52 ms versus 23.81/1,405.85 ms; retain the higher candidate tail and
batch variability without claiming latency improvement. These use warm OS/file
caches. Model tokens, Node startup/IPC breakdown, service/tool costs and human
attention remain unavailable; these probes do not establish complete-cost or
long-horizon benefit.

The current native model completed a new movement audit in an existing isolated
inventory Goal/Agent through stdio and the Python 3.11 standalone core on SQLite.
All 14 rows, six excluded rows, quantities 22/10/5 and value 185.25 reconcile
independently. A deliberately altered report is refused without overwriting
audit outputs; 11 retained input/generator/report files keep their hashes. The
closed preceding stage first refused task admission; normal evidence-linked
replanning established the audit stage before a fresh work Turn. That extra
replan settlement remains a cost. Completed replay invokes no Host, writeback
or spend. Source and installed Host checks use synthetic providers separately
from this native-model task; the initial compatibility failures and the
pre-fix dangling-reference counterexample remain negative evidence.

Broader Ark adapter regression initially failed eight SDK model-input checks
while 817 other cases passed. Those checks still expected `work_contract` in
the model presentation, so their subsequent original-receipt replay assertions
were not reached. The counterpart uses the shared task/source parity oracle,
requires unchanged operator input and its original receipt binding, and rejects
the model-view version before any provider call. Complete-source historical
v0/v1 input and running/completed replay remain in the matrix. The optional CI
command exits on the first test failure so its traceback precedes the existing
ten-minute deadline; passing runs retain the complete case selection. Equal
baseline/head cancellations at that deadline did not establish unrelatedness
or semantic success. SDK HTTP fixtures qualify transport and recovery rather
than live cloud-model adoption.

This qualifies a task-presentation slice of V4/R1. Full model-context retention,
minimal default closure, progressive product control, comparable complete costs,
V1–V5/V10, R1/M3, Standard/workspace publication and original-Goal regression
remain open. All nine original requirements and frozen failures remain intact;
no installed host or model service was upgraded.

### 2026-11-14 — Standalone successor recovery preserves original settlement

A genuine dependent successor can be recorded after replan admission. Its new
dependency must not erase the original lifecycle source and strand that Turn's
settlement. Reconstruction now retains the source only for the exact bound
review, matching owner/actor and an unambiguous later update; the existing
trigger/digest checks still apply. The existing runnable-successor ACK can then
settle that original Turn without rewriting completed Todos. Missing or invalid
ACKs retain lifecycle recovery; successor execution still needs fresh admission.
An empty operation inbox also defers its optional workspace action-store import.
A present action store without its provider continues to fail visibly.

The current native model used an independently built Python 3.11 core on SQLite
and stdio in an existing isolated task. Normal successor creation, Vision
writeback and original replan settlement recovered the blocked route. Initial
and unchanged complete task reads produced a reusable inventory close/check tool
and checked JSON/Markdown: 14 movements, six exclusions and value 185.25. An
incorrect 185.26 candidate was refused without overwriting prior results. A
controller-generated revision, sealed from the model until fresh task/source
delivery, added six rows. Independent validation checked all 20 movements,
nine exclusions, quantities 31/7/7, value 250.20 and the 64.95 difference, while
preserving the original cutoff, decimal rules and preceding artifacts.

The model rejected the older task for revised work and read the complete current
requirements and inputs. This demonstrates the model's source-selection decision,
not an automatic stale-input enforcement gate. After withholding the revised
caller's JSON reply, exact committed-journal recovery performed no new Host,
writeback or spend; the historical spend count remained one. The initial raw
adapter reply failed result binding before effects; the supported same-Turn retry
used the existing result builder. Two initial Host calls performed one artifact
implementation, with one eventual writeback/spend; retain that repair cost.
The generic bridge has no persisted provider session id. These checks qualify
complete task recovery and committed-result replay, leaving automatic provider
thread retention and a separately controlled real compaction case untested.

Source recovery regression and a fresh standalone-install regression cover the
touched lifecycle and optional-import paths, including invalid ownership,
timestamps, bindings, ACK fallback and a present-but-unavailable provider.
The earlier failed tests and original acceptance remain negative/open evidence.
Full V1–V5/V10, R1/M3, default closure, progressive product control, comparable
complete costs, Standard/workspace delivery and original-Goal regression remain
open. No installed host, model service, authority history or frozen budget changed.

### 2026-11-14 — Installed composition recovers the original receipt chain

PR #8707 (`d695b40f633742fb0a5e1cb6b970a56c46dba5e9`, integrated as
`dd44cdcd0c9482bdf135e54ae0b71eb398b4cfe7`) repairs explicitly requested
Core composition after writeback. The adapter reuses the canonical parser,
quota registrar and registry codec, validates the complete current CLI prefix
and original binding, and executes the packaged entrypoint. It retains the
existing TypeScript settlement owner and producing workspace.

Independent Python 3.11 installed File/SQLite comparisons cover default and
explicit routes: the baseline commits writeback and fails on the absent facade;
the candidate recovers that same identity, returns three receipts and retains
one debit after replay. Ordinary refresh still leaves spend owed without an automatic
debit. Further installed comparisons place rejecting foreign modules in
PYTHONPATH and the working directory; the originating Core remains selected.

The preserved installed governance corpus now has 16 confirmed cases in each
Core and ordinary edition at `78e936f9d1f1a00505c4026ee0a5857de95cee27`.
Earlier resource failures remain failures. The wider source run retained one
pre-existing incomplete mocked-readback failure, reproduced at the same base
and candidate. Its existing settled-intent test now supplies the canonical
identity/result types; all 32 affected cases pass without weakening the
production readback or changing the independent intent/no-reopened-work oracle.
The earlier full-run failure is not rewritten as a passing run.

This qualifies a V2/V3 installed-settlement prerequisite. Full V1–V5/V10,
R1/M3, real-host compaction/thread retention, progressive control, comparable
complete costs, Standard/workspace delivery and original-Goal regression remain
open. No default activation, host upgrade, release or frozen-budget change is
implied by these protocol fixtures.

### 2026-11-14 — Coordination loads terminal evidence on its selected route

The local-authority runtime defers `ResearchTerminalEvidenceHost` until a valid
terminal lifecycle request selects it. Ordinary coordination no longer loads
that adapter and its research implementation dependencies. The existing typed
terminal owner, registry witness, research qualification, writer locks and
resource cleanup remain responsible for completion; no competing work schema
or execution pipeline is introduced.

A fresh-process fault injection makes the terminal adapter unavailable. The
ordinary claim persists and replays its original receipt, malformed claims
leave state unchanged, and a selected terminal request fails without a new
receipt or state mutation. The retained base fails this same ordinary-claim
probe. The source authority/terminal suite passes 93 cases. Applying the same
patch to the exact C1 candidates in #8266 (`b15c443ee64d`) and #8484
(`b7e4e01cc423`) passes 188 and 193 authority, context and recovery cases
respectively; the latter also covers real SQLite admission.

This is a loading and failure-isolation prerequisite, aligned with C1's existing
owners. A valid terminal request still loads the adapter, including when its
ordinary-Todo guard returns early. Complete workflow isolation, paired complete
costs, real-host adoption, R1/M3 and C1 writer/consumer cutover remain open.
These tests do not authorize C1 default promotion or establish model benefit.


### 2026-11-14 — Ordinary completion defers research implementations

`ResearchTerminalEvidenceHost.qualify` loads its research execution and
configuration implementations after its existing Todo applicability check.
A Todo without research or replan applicability signals can therefore persist
its completion evidence and replay its original receipt while either research
implementation is unavailable.
Research-qualified Todos still select the same policy and evidence owners;
module failures retain their error and commit no completion state or receipt.
The existing source checks, writer fence, qualification, CAS and cleanup apply.

Fresh-process fault cases cover both unavailable implementations on real File
and SQLite authority. Both retained baseline probes fail at the ordinary
completion; the candidate completes and independently reads back the result,
replays without a new effect and refuses repeated selected-research failures.
The available-module comparison passes the same 107 existing native cases;
the candidate additionally passes the failure-isolation case, for 108 total.
The real research CLI suite passes 11 cases. The same production delta and
fault cases pass with the exact C1 candidates #8266 (`b0b930bfb92a`) and #8484
(`b7e4e01cc423`), with 190 and 189 authority, context and recovery cases. An independently built Core wheel, installed alone, also passes the same File/SQLite fault paths with production module bytes equal to the source; this does not qualify an installed model Host or adoption.

This closes the evidenced ordinary-completion dependency failure using the
existing research guard, without duplicating the C1 work contract or its
producer/consumer pipeline. Research- or replan-associated Todos still enter the existing policy check and
load its implementations; a disabled policy does not remove that remaining
loading dependency. Complete D2/D5 closure, real-model Host/context continuity, progressive controls,
comparable complete cost, R1/M3 and C1 default cutover remain unqualified.

### 2026-11-14 — Disabled research composition resolves before execution loading

The existing Explore configuration module now owns the composition policy for
the documented scalar modes and unchanged public-safe identifier validation. Research execution and evidence
reuse that owner; their established exports remain available. Native terminal
qualification checks the current source and resolves the same policy before
loading research execution. The original applicability, source, qualification,
writer, lease, CAS, receipt and cleanup rules remain in their existing owners.

Fresh-process File/SQLite cases complete and independently read back ordinary
and generic-replan results without research execution or graph implementations
when composition is disabled, including an explicit Goal-off override. Enabled
and inheritance-only configurations retain selected dependency failures with no
completion or receipt. Invalid modes and scope identifiers remain rejected;
an unavailable execution implementation cannot hide those configuration errors.
After an enabled dependency failure, an explicitly disabled current source can
complete the same failed operation once and replay its receipt; stale source
requests remain refused. Both retained baseline backend probes fail on the
original loading dependency, while the same candidate fixtures pass.

The same 21 policy and 13 identifier observations retain full return values and
error details. An independently built Core wheel, installed alone, passes the
same File/SQLite fault and recovery paths with production bytes equal to source.
The delta also passes selected authority/context/recovery combinations with
C1 #8266 (`b4f958186532`) and #8484 (`b7e4e01cc423`), retaining their original
tests and producer/consumer owners. This is dependency-failure isolation, not a
measurement of latency, complete cost or model benefit. Configuration errors
now precede unavailable execution IO intentionally. Full selected D2/D5 closure,
real-model Host/context adoption, progressive controls, R1/M3, C1 cutover and
original-goal regression remain unqualified.

The final counterexample review also rejects non-string composition modes.
Previously a single-element array such as `["explicit_only"]` passed string
coercion but produced an inactive policy, silently skipping research evidence.
The existing configuration owner now refuses this malformed shape before IO;
documented scalar modes, defaults and errors retain their semantics. Real
File/SQLite negative cases guard this boundary without changing valid activation.


### 2026-11-14 — Policy observers recover the original signed source

- **Baseline:** `main@8d7d234c6f55`; C1 #8266 at `c3cb8b2fe7bf`.
- **Gap:** three current C1 guided-start and visible-Goal selection cases
  rejected a required presentation as if it were the original signed decision.
  The original capture passes both its physical digest and complete-contract
  validation. Same-view Todo references and their read guidance change the
  presentation body while retaining the original binding.
- **Delta:** the existing test policy observer follows the presentation's
  declared immutable capture and digest before comparing decision facts. It
  reuses the physical capture reader; inline/off outputs without such a
  reference retain their current behavior. No producer, decision, hash codec,
  admission, settlement owner or default changes.
- **Evidence:** the existing real guided-start/visible-Goal CLI cases pass on
  the pinned C1 combination. The durable File/SQLite regression exercises the
  shared typed projection and verifies original-source equality, its complete
  binding, zero spend and rejection of changed or missing captured bytes, including a valid
  replacement whose original binding differs. A
  private matched observer comparison distinguishes the old and corrected
  readers. Earlier failed and incomplete runs remain failed or incomplete.
- **Remaining:** this repairs a consumer-qualification prerequisite. Complete
  V1–V5/V10, R1/M3, current D2/D5 closure, actual Host/model context adoption,
  progressive product control, comparable complete costs, C1 default cutover
  and original-Goal regression remain open.

### 2026-11-14 — Disabled Turn recall defers execution dependencies

- **Baseline:** `main@4604f938b612`; C1 #8266 at `79d7ebc81fbc`.
- **Gap:** independent Core File/SQLite Turn journals report recall disabled,
  no context and zero provider calls, yet admission imports the recall execution
  hook and read-authority implementation through the recall package.
- **Delta:** the existing recall and checkpoint functions import those two
  implementations when called. Experiment resolution, ingest reconciliation,
  schema ownership, exported function identity, permissions and C1 composition
  remain with their existing owners. No shared orchestration or default changes.
- **Evidence:** the disabled helper cases and the actual installed CLI cases
  fail on the baseline and pass on the repair. The capability regression covers
  active recall, authority rejection, provider failure, deduplication and outcome
  lifecycle. Independent Core installation, execution, interruption/recovery,
  replay and context suites pass on main and the pinned C1 combination; runtime
  bytes match their declared sources. Observed initial File/SQLite Turns load
  514 rather than 516 Python modules, with 100 rather than 102 capability modules;
  RPC counts remain 223/222, and replay remains 432/50 modules with 12 RPCs.
  Four observed Node launches are two version probes and two instrumented
  runtime/Host-bridge processes. This is a loading-boundary result; instrumented
  elapsed times do not qualify latency or complete cost.
- **Remaining:** built-in capability packaging and complete D2/D5 closure,
  actual model/Host context adoption, progressive controls, comparable complete
  costs, full V1–V5/V10, R1/M3 and C1 default cutover remain unqualified. The
  process Host and native context events are synthetic fixtures.

### 2026-11-14 — Current-model task result survives Core recovery

- **Sources:** `main@83809355f84a` and C1 #8266 at `6a31d83497a6`,
  each in a separate installed Core environment. The executed entrypoint,
  Turn driver/executor, complete-source owner and recall entrypoints match
  their declared source bytes.
- **Observed outcome:** the current interactive model adopted each actual
  generic Host request and used tools to produce JSON and readable Markdown
  inventory reports from isolated synthetic input. An independent input-derived
  Decimal oracle checks duplicate merging, zero stock, over-reservation, a
  non-ASCII SKU, ordering, each group and the totals.
- **Recovery:** after the model result reached the durable `typed_result`
  phase, the owning CLI process was killed with SIGKILL. Both original Turns
  resumed without another Host invocation, completed independent validation
  and Todo readback, and retained exactly one debit after replay. On C1,
  a damaged report was rejected with the Todo still open and zero debit;
  restoring the checked report allowed the same Turn to finish.
- **Owner boundary:** a private rendezvous adapter only transports the current
  model's request/result. C1 and the existing Turn driver retain composition,
  identity, admission, validation, capture, recovery and settlement. No new
  execution pipeline, schema, product default or capability activation is added.
- **Remaining:** this strengthens task-result recovery evidence. It does not
  qualify an installed native model/App adapter, natural-language onboarding,
  model retention through native compaction, progressive product controls,
  complete D2/D5 closure, comparable full costs, V1–V5/V10, R1/M3 or C1 default
  cutover. The model reused its report generator between the two checks;
  these are not independent inference trials or an outcome/cost comparison.

### 2026-11-15 — One confirmation for a complete conversational Goal draft

- **Gap:** a complete draft skipped the form but still required a preview click
  followed by drawer confirmation. The user could not see the resolved executor
  and workspace until taking the first of those actions.
- **Delta:** the Dashboard prepares a visible complete draft's existing typed action,
  displays its server-resolved Agent and workspace, and offers one explicit
  `Create and start` confirmation. Incomplete drafts and read-only views cannot
  prepare creation; Edit and typed availability/stale/recovery gates remain.
  Existing locale-scoped operation identities are read before preparation;
  reload, language changes and repeated clicks retain the original operation.
  The visible draft owns one confirmation/recovery entry; the canonical proposal
  stays available when its draft is not loaded. Collapsed unseen history does
  not initiate preparation.
- **Evidence:** `goal-draft` covers preparation failure, no Goal writes before
  confirmation, Edit/cancel, repeated clicks, reload, locale and narrow views.
  `goal-draft-live` uses the real discovery and action store on isolated state
  for resolved scope, lost-response readback, original-operation retry and
  unavailable-Agent refusal. Its conversation is synthetic and its stub Agent
  fails the first Turn; that failure is preserved rather than called success.
- **Boundary:** this is a V4/R1 entry increment using the existing Goal draft,
  typed review/apply and creation/recovery owners. It adds no shared schema,
  authority grant, alternate execution pipeline or C1 composition owner.
  Full task-artifact success through this entry, installed native App/model
  adoption, compaction retention, complete D2/D5 closure, comparable total cost,
  V1–V5/V10, R1/M3 and C1 default cutover remain unqualified.

### 2026-11-15 — First-Goal reply adopted by the original governed Task

- **Source:** `main@74ca883d5bd3`. The same source combines without conflict
  with C1 #8266 at `2c099be41e68`; the isolated combined tree passes 287
  native-session, work-context, first-read and Turn-settlement tests using
  isolated state and synthetic provider/model fixtures. This does not approve
  C1 or qualify its default cutover.
- **Observed product path:** the real Chat discovery/action/store creates one
  Goal and its initial Task from a synthetic reviewed request. The current
  Host model reads the retained request and input through a temporary stdio
  relay, produces an eight-group read-only report, and the real packaged Goal
  conversation renders it. Independent CSV/Decimal checks verify groups and
  totals. A real Chat restart preserves the original Session, reply and action;
  retrying that action dispatches no new Chat Turn.
- **Governed adoption:** the fixture caller declares an independent argv
  validator through the existing Task owner, then the current model adopts
  that original reply through the existing Core + SQLite + generic Host route.
  It uses the same Goal, Agent and initial Task. SIGKILL after durable
  `typed_result` resumes the original Turn without another Core Host invocation.
  A damaged reply fails validation with the Task open and zero debit; restoring
  the checked reply completes that Task with one debit, retained on replay.
- **Remaining entry gap:** the first Chat request is still a read-only planning
  conversation with a separate explicit LoopX-mode control. Its observed model
  input is 24,372 UTF-8 bytes; this is one observation, not a cost comparison.
  The fixture's explicit validation declaration and governed adoption are not
  automatic product behavior. Reuse the registered managed-session, Task
  validation/admission and C1 owners for that missing integration; do not infer
  write authority or weaken independent validation to activate it.
- **Boundary:** these source and transport checks strengthen first-result
  readback and original-Task recovery evidence. They do not qualify one-goal
  native onboarding, installed native App/model adoption, model compaction
  retention, complete D2/D5 closure, comparable full costs, V1–V5/V10, R1/M3 or
  C1 cutover. No runtime, schema, activation, permission or public evidence
  policy changes; one-run relay/validator files remain private qualification
  artifacts rather than a new shipped execution pipeline or CI fixture.

### 2026-11-15 — First-Goal admission failure preserves creation and confirmed work

- **Gap:** a failed first-Goal admission read or decision was converted into
  permission to dispatch. Conversely, retrying a lost submit response could
  apply a later admission failure or denial to already dispatched work.
- **Delta:** the existing creation owner retains Goal, initial Tasks and
  completion requirements before checking admission. An unverifiable admission
  now returns a safe retryable action failure without opening a Session or
  sending model work. Creation without an executable Host does not read work
  admission. Recovery first reads the original Session/client operation through
  the existing Task-start reader, verifies its request and first-Turn state,
  and retains dispatched work without new admission. Queued work still requires
  admission; foreign Sessions and terminal-before-start work remain refused.
- **Evidence:** on `main@58cc5817e35e`, the real Chat HTTP/action/store path with
  synthetic Codex protocol passes 36 creation-completion cases and 11 existing
  Session/checkpoint recovery cases. Independent review identified two recovery
  defects in the first candidate; their failing cases were retained and the
  final candidate resolves both. The final runtime/test files also pass 22
  selected File/SQLite and creation-only cases in an isolated combination with
  C1 #8266 at `b8ac1ec5443b`, with exact source-byte readback. These are bounded
  source and synthetic-Host checks, not current native-model qualification.
- **Boundary:** this repairs V2/V4/R1 admission and recovery using existing
  Session, Task and quota owners; it adds no shared vocabulary, authority grant,
  validator installation or alternate C1 pipeline. Automatic original-Task
  adoption, selected D2/D5 closure, native App/model and compaction retention,
  progressive controls, comparable complete costs, full V1–V5/V10, R1/M3 and
  C1 cutover remain unqualified. Preserve original requirements and earlier
  negative evidence; run full original-goal regression before completion.
