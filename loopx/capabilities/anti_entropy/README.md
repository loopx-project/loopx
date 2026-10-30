# Anti-Entropy & Code Retirement Governance

Anti-Entropy Governance provides LoopX with an automated, deterministic
code-retirement and diff-hygiene verification contract. It is **default-off**
and designed to support agent-driven refactoring and feature decommissioning workflows.

## Overview

When autonomous agents perform refactoring or feature replacement tasks, they
frequently exhibit an "entropy growth" anti-pattern: layering yet another wrapper
or fallback branch without safely deleting superseded implementations. Over
long-horizon tasks, this results in accumulated ghost code, redundant complexity,
and bloated execution contexts.

The Anti-Entropy capability addresses this through:

1. **AST-Based Delete-First Enforcement (`verify_delete_first`)**:
   Uses Python's syntax tree parser to verify that declared retirement targets
   (functions and classes) are genuinely removed from the codebase rather than
   merely bypassed or wrapped. Symbol retirement is a derived fact about
   **complete post-change sources**, not about diff text alone.
2. **Zombie Symbol Prevention**:
   Detects if a symbol marked for retirement is reintroduced as a new definition
   in the diff, or is still referenced by active identifiers in the bound
   post-change sources (string literals, comments, and docstring examples are
   never counted as references).
3. **Target Paths Verification**:
   Ensures that declared legacy file paths slated for deletion are completely
   removed in the unified diff (`/dev/null`).
4. **Ghost Code Auditing (`audit_ghost_code`)**:
   Identifies commented-out code blocks left behind, surfacing them as advisory
   quality findings without blocking legitimate documentation or explanatory comments.
5. **Durable Receipt Generation (`AntiEntropyReceipt`)**:
   Runs deterministic hygiene oracles against the exact git diff and produces
   an immutable receipt recording verified retired symbols, findings, and the
   evidence provenance (`basis_hash`, `source_root`, `source_evidence`).

## Source Binding And Evidence Rules

Verification distinguishes diff-visible facts from workspace-derived facts:

- `--workspace <dir>` explicitly binds the directory holding the complete
  post-change sources. The bound workspace — never the caller's CWD — is the
  only disk source read, and its complete files are parsed with the AST
  (line-signature matching is limited to unparseable in-diff code fragments;
  documentation files never contribute symbol evidence).
- **AE004 (insufficient source evidence, blocking)**: without a bound
  workspace, or when a diff-touched source file is missing from the bound
  workspace, symbol retirement cannot be certified. A diff-only receipt may
  reject, but never certifies retirement, and `retired_symbols` stays empty.
  A path deleted in the diff but still present in the bound workspace is
  reported as a patch/workspace mismatch.
- **AE001/AE002 remain hard gates** in this command; **AE003 stays advisory**.
  `allow_compat_seam=true` turns a retained-but-authorized definition into an
  advisory finding, and it is never listed as retired.
- The receipt records which sources backed the verdict; a `diff_hash` alone
  does not prove anything about the task, the sources, or the policy.

## Single-Task Feedback Loop (Default-Off)

The capability ships disabled (`default_enabled: false`); enabling it for a
task is an explicit choice. When enabled, one closed loop per task is:

1. **Task context** — `anti_entropy_context_registration` is composed into
   the shared `loopx.capabilities.turn_context.attach_turn_context` owner and
   reads the Turn's own authoritative input, never a hand-built payload shape:

   - the selected Todo record carried by `turn_envelope.work_context.sources[]`
     (its `note` / instruction text, `required_capabilities`, and the full
     task body when the compact display text was truncated), plus
     `turn_envelope.action.selected_todo.text` / `action.recommended_action`;
   - a task opts in by declaring an explicit `retirement_basis` /
     `anti_entropy_basis` JSON object in that text. A Todo may also list
     `anti-entropy-governance` in `required_capabilities`, but only when the
     Goal already declares that capability available: otherwise routing treats
     the Todo as requiring an unavailable capability and the Turn stops before
     the hook, so the explicit task declaration is the primary path;
   - the post-change workspace is the Goal repository from the registry
     (`load_goal_from_registry`), never the process working directory.

   Disabled turns write no readback slot and inject nothing; an
   enabled-but-inapplicable turn only reads back `enabled/applicable=false`.
   Recovery turns (the payload carries the previous inspect outcome) get
   remediation guidance instead.
2. **In-flight check** — agents call `anti_entropy_inspect` through the Python
   entry or `loopx anti-entropy inspect`. The turn context carries the tool
   descriptor and CLI route as data; LoopX exposes no capability-owned host
   tool registry, so the CLI remains the callable route. Blocking findings come
   back as **actionable feedback with per-rule next actions**, classified as
   progress facts, not final delivery failures; the CLI exits 0 whenever the
   check ran.
3. **Delivery verification** — final acceptance stays owned by the existing
   change-quality owner (`loopx change-quality prepare/record/verify`, exact
   diff scope and receipt invalidation). This capability supplies evidence and
   names the delivery route; it writes no receipt store and creates no
   parallel delivery authority.
4. **Post-writeback (named follow-up)** — typed post-writeback intent bound to
   the source receipt remains owned by the provider-neutral post-writeback
   capability-hooks RFC, including failure isolation and idempotence.

Model adoption of the loop is proven by actual runs, not by this protocol;
tests here cover the typed protocol and the real callable entries.

## Attribution & Acknowledgments

This capability adopts and operationalizes the core engineering principles of
**delete-first retirement**, **ghost code prevention**, and **minimality verification**
inspired by the **Aegis** engineering methodology.

## Usage

Inspect the capability and its registered protocols:

```bash
loopx capability show anti-entropy-governance --format json
```

In-flight inspection with actionable feedback (progress-fact semantics):

```bash
loopx anti-entropy inspect --goal-id <goal-id> --diff-file <diff.patch> --basis-json <basis.json> --workspace <sources> --format json
```

Delivery-gate verification (non-zero exit on rejection). `verify` requires an
explicit basis with at least one target, and certifies symbol retirement only
when `--workspace` is bound:

```bash
loopx anti-entropy verify --goal-id <goal-id> --diff-file <diff.patch> --basis-json <basis.json> --workspace <sources> --format json
```

`verify` without `--basis-json` (or with an empty target set) exits 2: an
undeclared retirement is an input error, not a passing verification.

Example `basis.json`:

```json
{
  "target_symbols": ["old_deprecated_calculator"],
  "target_paths": ["legacy/old_module.py"],
  "allow_compat_seam": false,
  "rationale": "Superseded by modern_calculator implementation"
}
```

A deterministic end-to-end demonstration on fixed fixtures lives in
`examples/anti_entropy/compare_arms.py`: it seeds a disposable git sandbox per
arm, applies the arm's fixed post-state, captures the real diff with git, runs
the case-study functional tests with pytest, and evaluates the oracle against
the sandbox workspace. It measures the oracle's semantics only — it launches
no model runs and measures no agent-runtime behavior.

## Boundaries

- The capability is default-off and task-scoped: turns receive the retirement context only when the selected task explicitly declares a retirement basis or enables the capability in its own canonical input; the CLI (verify/inspect) remains the shared verification entry.
- The selected Todo is the intent source. The hook reads the native work context and the Goal registry; it never infers targets from unrelated business fields and never reads the process working directory as source evidence.
- Delete-first requires target old logic to be cleanly decommissioned in the diff without re-introducing wrappers or zombie symbols.
- Symbol retirement certification requires an explicitly bound workspace; diff-only evidence can reject but never certifies.
- AST-based diff hygiene verifies exact code diffs without mutating repository code or performing speculative git writes.
- Target paths declared for decommissioning must be cleanly removed in the unified diff.
- Advisory findings highlight non-blocking hygiene opportunities (such as dead commented code) without causing false positive rejections of documentation or explanations.
- Delivery acceptance and receipts of record remain owned by the change-quality capability; this capability never writes delivery receipts.
