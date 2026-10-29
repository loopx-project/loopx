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
- The [three-layer protocol](../../reference/protocols/multi-agent-three-layer-minimality-v0.md)
  uses “kernel” for a broader reusable layer. Here, governance kernel names the
  authority boundary and boot closure names a selected entrypoint's required code.
  A reusable runner or presentation facility need not enter every boot closure.

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

## Appendix D: Rejected or superseded alternatives

**Multi-repository split.** Rejected for this RFC because it violates I1 while
the TypeScript control-plane migration is in transaction cutover, multiplies CI
and release surfaces, and requires per-repository maintainers that do not yet
exist. Evidence that could reopen the decision: migration RFC Stage 4 complete;
a `packages/` distribution with two or more non-kernel maintainers and no
cross-package breaking change for three consecutive releases.

## Appendix E: Incident and review lessons

None yet.
