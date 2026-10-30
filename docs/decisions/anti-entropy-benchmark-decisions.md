# Architecture Decision Record: Anti-Entropy Governance Benchmark & Evaluation Harness

- **Status**: Accepted (Design Grill Complete)
- **Date**: 2026-10-29
- **Scope**: Out-of-band evaluation harness for quantifying anti-entropy (Aegis delete-first) impact
- **Target Constraint**: 100% zero-intrusion to LoopX core control plane (`loopx/control_plane/`), default-off opt-in capability

---

## 1. Context & Problem Statement

When autonomous coding agents execute refactoring, feature replacement, or protocol migration tasks, they suffer from systematic "entropy growth":
1. **Fallback/Wrapper Addiction**: Instead of cleanly deleting old classes/functions, agents keep them as backwards-compatibility shims or wrap them inside new logic.
2. **Ghost Code Residue**: Agents frequently comment out superseded blocks (`# old_logic()`) rather than deleting them.
3. **Zombie Reintroduction**: When tests fail, agents often recreate the deprecated symbol in the newly modified files.

LoopX possesses an existing deterministic AST-based anti-entropy engine (`loopx/capabilities/anti_entropy/`). The core question was:
- Should test/benchmark scenarios overlay a full real-world development task?
- How should the anti-entropy capability be injected into commands to measure the delta between having vs. not having anti-entropy governance?
- What task scenario is most appropriate to evaluate this without compromising LoopX core stability?

---

## 2. Decision Points & Trade-Off Analysis

### Decision 1: Loading Mechanism & Interception Timing
- **Considered Alternatives**:
  - *Option 1*: Pure prompt guidance (soft constraint). Zero tooling overhead, but models consistently regress under multi-file pressure.
  - *Option 2*: Post-hoc terminal gate only. Verifies diff at submission; fails late when the agent has already exhausted budget, leading to blind rollback loops.
  - *Option 3 (Selected, partially delivered)*: **Two-Tier Closed Loop (Execution Tool + Submission Gate)**.
    - Delivered now: the execution tier as an explicitly callable entry (`anti_entropy_inspect` tool descriptor plus `loopx anti-entropy inspect`) returning actionable progress-fact feedback, and the gate tier as a named delivery route — final blocking stays owned by the existing change-quality `prepare/record/verify` receipt, not a parallel authority.
    - Not yet proven: that models actually adopt the tool and prefer deletion in long-horizon runs. That claim requires actual recorded runs and is intentionally not asserted by this ADR or the comparator.
- **Trade-off**: Increases tool-call rounds and slight token overhead per run, in exchange for deterministic first-pass cleanliness and eliminating expensive blind rollbacks.

---

### Decision 2: Zero-Intrusion Packaging & Retirement Basis Injection
- **Considered Alternatives**:
  - *Option 1*: Hardwire anti-entropy check into `loopx/control_plane/turn_driver/`. Causes direct intrusion into generic mainline scheduling, violating domain-neutrality.
  - *Option 2*: Dynamic runtime AST inference without declaration. Prone to false positives when agents refactor internal helper functions.
  - *Option 3 (Selected)*: **Declarative Sidecar / Task-Envelope Hook Pattern**.
    - The task specification explicitly provides an `anti_entropy_basis` (target symbols and target paths to retire).
    - Delivered now: the typed turn-context projection (`build_anti_entropy_turn_context`) a host can inject when the capability is enabled for the task, and the explicit CLI/tool entries agents call.
    - Named follow-up (not delivered here): a runner that conditionally injects the inspect tool into a host agent envelope and appends the verify command to a test command. Owner: the host integration that owns agent envelopes; post-writeback typed intent stays with the provider-neutral post-writeback capability-hooks RFC, including failure isolation and idempotence.
    - `loopx/control_plane/` remains completely unmodified (0-line diff).
- **Trade-off**: Requires test cases to declare their target retirement list in advance, rather than inferring intent magically.

---

### Decision 3: Evaluation Benchmark Scenario (Testbed Task) Selection
- **Considered Alternatives**:
  - *Option 1*: In-repo dogfooding on live LoopX modules. High risk of breaking the developer active branch during failed baseline runs; violates mainline safety.
  - *Option 2*: External SWE-bench Docker benchmark. Requires heavyweight containers and environment setups; feedback loop too slow for local iterative testing.
  - *Option 3 (Selected)*: **Self-Contained Realistic Engineering Playground in `examples/anti_entropy/`**.
    - A multi-module Python service (protocol parser + streaming receiver + client caller + unit test suite).
    - Real task: Migrate all callers from `LegacyProtocolParser` to `StreamMessageParser` and physically delete `legacy_parser.py`.
    - The comparator is a deterministic, fixed-input sandbox demonstration: per arm it seeds a disposable git sandbox, applies the fixed post-state, captures the real diff with git, executes the case-study functional tests with pytest, and evaluates the oracle against the bound sandbox workspace. It measures oracle semantics only — it launches no model runs and measures no agent-runtime behavior, so its output must not be read as an agent-effectiveness result.
- **Trade-off**: Tests on a standalone engineering playground rather than mutating production LoopX code directly.

---

## 3. First-Principles Minimality Justification (Aegis Integration)

1. **Change Necessity**: Reuses the existing `loopx/capabilities/anti_entropy/` AST engine without creating redundant verification abstractions.
2. **Fallback Ceiling**: Refuses to add fallback adapters; enforces physical deletion of deprecated symbols (`/dev/null` diff + AST symbol absence).
3. **Delete-First & Anti-Entropy**: The evaluation suite directly measures net negative lines of code, absence of zombie wrappers, and 0 ghost code blocks.
4. **Mainline Purity**: All new harness scripts live in `examples/anti_entropy/` and `docs/decisions/`. No files under `loopx/control_plane/` are touched.

---

## 4. Delivered Boundary (This PR)

- **Delivered**: default-off capability with explicit typed entries — `loopx anti-entropy verify` (delivery-gate semantics, exit 1 on rejection), `loopx anti-entropy inspect` (in-flight progress-fact feedback, exit 0), the `anti_entropy_inspect` tool descriptor registered in the capability catalog, the `build_anti_entropy_turn_context` projection, and the single-task feedback cycle classifying findings as actionable progress facts. Symbol retirement certification requires an explicitly bound workspace (`--workspace`); diff-only receipts may reject but never certify, and evidence provenance (`basis_hash`, `source_root`, `source_evidence`) is recorded on every receipt.
- **Delivery authority**: unchanged and owned by change-quality (`prepare/record/verify`, exact diff scope, receipt invalidation). This capability supplies evidence and names the delivery route; it writes no delivery receipt.
- **Not claimed**: model adoption of the loop in real agent runs, measured entropy reduction on live workloads, or any comparator-derived effectiveness number. Those require the recorded-run evaluation program (Decision 3) executed for real, and the envelope-injection runner from Decision 2.
- **Source binding invariants**: complete post-change sources are AST-parsed; documentation files never contribute symbol evidence; string literals, comments, and docstring examples are never counted as references or definitions; a missing bound source is evidence-insufficient (AE004), never silently skipped.

---

## 5. Impacted System Components & Retired Paths

- **Impacted Components**:
  - `docs/decisions/anti-entropy-benchmark-decisions.md`: Canonical ADR record.
  - `examples/anti_entropy/case_study/`: Standalone realistic multi-file testbed.
  - `examples/anti_entropy/compare_arms.py`: Out-of-band evaluation comparator for measuring differential.
- **Retired / Disallowed Paths**:
  - Disallowed: Modifying `loopx/control_plane/turn_driver/executor.py` for benchmark gates.
  - Disallowed: Ad-hoc heuristic AST diff guesswork without a declared basis.
