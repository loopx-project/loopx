# RFC: Monorepo Distribution Split (v0)

- **RFC status:** Accepted, proposal only (open decisions D1–D4 remain unapproved)
- **Delivery maturity:** Proposal
- **Authors / owners:** LoopX maintainers
- **Created:** 2026-09-26
- **Last normative revision:** 2026-10-01
- **Implementation baseline:** `2f3d13ae9`
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

## 2. Problem and motivation

A developer who wants a CLI-only or kernel-only LoopX installs the same wheel as
someone running the desktop workspace, the Lark manager, and every built-in
capability. On the baseline:

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

### Non-goals

- Splitting into multiple Git repositories (see Section 6 and Appendix D).
- Changing licences per distribution; all tiers remain Apache-2.0 unless a
  separate licensing RFC decides otherwise.
- Any change to kernel semantics, TS migration order, or stable protocols.
- Removing legacy compatibility facades (`loopx.status`, `loopx.quota`); their
  retirement stays governed by the migration RFC.

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

## 5. Proposed architecture

### Ownership and authority

| Distribution | Contains | Owner boundary | Depends on |
| --- | --- | --- | --- |
| `loopx-core` | `loopx/control_plane`, `loopx/cli_commands`, `loopx/semantics`, minimal host adapters under `loopx/hosts/`, `loopx check`, doctor, status | Kernel maintainers | Python ≥ 3.11; Node effect runtime as `loopx-core[runtime]` extra or bundled artifact (D1) |
| `loopx-workspace` | `loopx/chat/`, `loopx/web`, `apps/presentation`, dashboard launcher, desktop shell glue | Frontstage maintainers | `loopx-core` |
| `loopx-capability-<name>` (in `packages/`) | one capability's contract, providers, CLI subcommands, docs | Capability owner from CODEOWNERS | `loopx-core`; optionally other capability packages |
| `loopx` (meta) | no code | Release owner | all of the above, pinned to one release train |

Authority does not move: the kernel remains the only writer of Goal/Todo/claim/
lease/quota/effect state. A capability package may only register providers,
capability contracts and CLI subcommands through `CapabilityRegistry`; it never
imports kernel-private modules.

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

## 6. Alternatives and design choices

| Alternative | Why not chosen now |
| --- | --- |
| **Split into several Git repositories** | Violates I1 during the TS transaction cutover: kernel semantics change daily and downstream repos would break on every change. Multiplies CI, release and review cost while review capacity is the scarce resource. Reopen only when the kernel migration is complete and a package has independent maintainers (Appendix D). |
| **Keep one wheel, add extras only** | Reduces install weight but leaves the flat namespace and the two packaging conventions in place; contributors still cannot find boundaries. |
| **Move everything to `packages/` at once** | Breaks I3 for many callers simultaneously and mixes behavioural risk into a structural change. Per-group moves with I3 compatibility are cheaper to review and to roll back. |
| **Bundle Node into `loopx-core` unconditionally** | Solves I5 but makes the kernel wheel platform-specific and large. Kept as option D1 alongside the optional-extra approach. |

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

## 8. Migration and rollback

| Step | Gate | Rollback |
| --- | --- | --- |
| Budget test added at current count | none; additive | delete fixture |
| `chat_*` → `loopx/chat/` with I3 compatibility | import-boundary + budget + smoke green | revert PR and caller updates; retain required public shims |
| `*_goal_mode` → `loopx/hosts/` with I3 compatibility | same | same |
| Publish `loopx-core` / `loopx-workspace` / meta `loopx` | package-smoke lane installs each on a clean runner | unpublish pre-release; `loopx` meta keeps old layout one release |
| Capability package moves | per capability, kernel-caller inventory resolved | revert one package |
| Shim removal | one minor release after the move; deprecation recorded in update notes | not applicable; requires migration by callers |

Rollback requires no data migration at any step because no canonical record
changes.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Top-level module count never grows | `tests/architecture/test_top_level_module_budget.py` | fails when `len(loopx/*.py) > max_top_level_modules` | Does not judge module quality |
| Old imports keep working after a move | import tests for every moved public name | pass with one `DeprecationWarning` | One minor release only |
| Kernel-only install without Node | package-smoke lane on a runner without Node: `pip install loopx-core && loopx --help && loopx status --format json` | exit 0 | Effectful commands excluded |
| Effectful command without Node fails typed | same runner: `loopx heartbeat-prompt ...` | `node_unavailable` diagnostic, exit non-zero | — |
| Absent capability package = off state | install `loopx-core` only; `loopx capability list` | capability reported unavailable, core commands unaffected | — |
| No kernel truth duplicated | import-boundary tests + review of `packages/*/src` for kernel-private imports | zero violations | Static imports only, as today |
| Meta-package parity | `pip install loopx` then existing full smoke | identical to pre-split smoke | — |

Deterministic conformance rows above are required for each milestone; no live
qualification or performance claim is made by this RFC.

## 10. Operational contract

- `loopx doctor` reports the installed distribution set and versions and flags
  mixed release trains.
- `loopx capability list` distinguishes "not installed" from "installed, not
  ready".
- Release notes name public import moves and each retained shim’s expiry release.
- No new daemon, storage or network surface is introduced.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Budget fixture and test at the current count; RFC index entry | this RFC accepted | budget test green; count pinned | delete test |
| M1 | `loopx/chat_*` → `loopx/chat/` with I3 compatibility; budget lowered by net reduction | M0 | Section 9 rows 1–2 | revert |
| M2 | `loopx/*_goal_mode` → `loopx/hosts/` with I3 compatibility | M0 | rows 1–2 | revert |
| M3 | `loopx-core` + `loopx-workspace` + meta `loopx` published as pre-release; Node as extra or bundle per D1 | M1, M2; D1 decided | rows 3–4, 7 | unpublish pre-release |
| M4 | First capability without kernel callers moved to `packages/` | M3; D2 inventory | rows 5–6 | revert package |
| M5 | Remaining eligible capabilities moved; public shims from M1/M2 removed after the documented compatibility window | M4 | full smoke identical | not applicable |

## 12. Open decisions

1. **D1 — Node delivery for `loopx-core`.** Owner: kernel maintainers. Options:
   (a) `loopx-core[runtime]` extra that documents Node as prerequisite; (b)
   bundle a pinned Node runtime as a platform wheel; (c) both, with (a) as
   default. Recommendation: (c). Evidence needed: wheel size and platform
   matrix from the package-smoke lane. Deadline: before M3.
2. **D2 — Which capabilities remain in core.** Owner: kernel + capability
   maintainers. Input: the 52 kernel-side import sites. Recommendation: keep
   only capabilities that the Turn driver, quota or heartbeat prompt require at
   runtime; everything else is a package. Deadline: before M4.
3. **D3 — Shim lifetime.** Owner: release owner. Options: one minor release
   (recommended) or two. Deadline: M1.
4. **D4 — Index placement.** Whether this RFC is listed under "Control-Plane
   Kernel, State, And Migration" or "Runtime, Capability, And Collaboration
   Integration". Recommendation: kernel section, since the budget test and
   `loopx-core` boundary are kernel-owned.

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

## Appendix D: Rejected or superseded alternatives

**Multi-repository split.** Rejected for this RFC because it violates I1 while
the TypeScript control-plane migration is in transaction cutover, multiplies CI
and release surfaces, and requires per-repository maintainers that do not yet
exist. Evidence that could reopen the decision: migration RFC Stage 4 complete;
a `packages/` distribution with two or more non-kernel maintainers and no
cross-package breaking change for three consecutive releases.

## Appendix E: Incident and review lessons

None yet.
