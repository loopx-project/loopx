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
5. **Deterministic Receipt Construction (`AntiEntropyReceipt`)**:
   Runs deterministic hygiene oracles against the exact git diff and returns a
   per-run receipt recording verified retired symbols, findings, and the
   evidence provenance (`basis_hash`, `source_root`, `source_evidence`). The
   capability does not persist a receipt store; delivery receipts of record are
   owned by change-quality.

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
   - a task opts in only by declaring an explicit `retirement_basis` /
     `anti_entropy_basis` JSON object in a task-intent carrier (`text`, `note`,
     `description`, `instruction`, including inside `metadata` / `params`).
     Generic `target_paths` / `target_symbols` keys outside that reserved
     namespace are ordinary task metadata and never authorize retirement;
   - a Todo may also list `anti-entropy-governance` in `required_capabilities`;
     that is a task-scoped typed enable, not a Goal-wide switch. A Goal-level
     `available_capabilities` declaration is availability only and does not
     activate unrelated or later-added Todos in the same Goal;
   - the turn-context hook itself is composed unconditionally in the shared
     `attach_turn_context` owner and is called by both `turn run-once
     --execute` and the heartbeat `quota should-run` path; it decides
     off/applicable per task and injects nothing when the task has not opted in;
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

- The capability is default-off and task-scoped: a turn receives the retirement context only when its own task input declares a `retirement_basis` / `anti_entropy_basis` namespace or a task-scoped typed enable. The turn-context hook stays composed on the real turn and heartbeat paths; the CLI (verify/inspect) remains the shared verification entry.
- The selected Todo is the intent source. The hook reads the native work context and the Goal registry; it never infers targets from unrelated business fields and never reads the process working directory as source evidence.
- Delete-first requires target old logic to be cleanly decommissioned in the diff without re-introducing wrappers or zombie symbols.
- Symbol retirement certification requires an explicitly bound workspace; diff-only evidence can reject but never certifies.
- AST-based diff hygiene verifies exact code diffs without mutating repository code or performing speculative git writes.
- Target paths declared for decommissioning must be cleanly removed in the unified diff.
- Advisory findings highlight non-blocking hygiene opportunities (such as dead commented code) without causing false positive rejections of documentation or explanations.
- Delivery acceptance and receipts of record remain owned by the change-quality capability; this capability never writes delivery receipts.

## Known Limitations

- Static computed member keys are resolved as a bounded expression subset: string/template literals, legal whitespace/comments/line terminators between tokens, parentheses, constant string `+` concatenation, `String(...)`, `String.raw`, and simple TypeScript `as`/`satisfies`/non-null wrappers. Malformed static forms fail closed with AE004.
- Dynamic computed member keys (`api[dynamicKey]`, a dynamic concatenation/template operand, `Reflect.get`, `eval`) are not analyzed. Target-scoped detection needs import/alias provenance and remains a named follow-up; the tool must not claim an absence it cannot prove.
- Static destructuring property keys are analyzed as references: string/template/computed/unquoted keys in declarations, assignments, function/arrow parameters, for-of/in, catch, and nested patterns. A target key blocks retirement (AE002). Dynamic destructuring keys (`const { [key]: fn } = api`) and contexts the bounded classifier cannot place produce AE004 with an actionable next action; they are never treated as absence.
- Clear object-literal property keys are non-references and pass; a target key in an unclassifiable object/type context produces blocking AE004 rather than a silent pass. Class/type-only property positions can therefore be conservative false rejects.
- Reference extraction is token-kind conservative: identifier object-literal keys, regex-literal text, type-only positions, JSX text and same-named properties on unrelated receivers can be reported as references (false reject). This is the opposite direction from the retirement false pass and is tracked for a token-kind-aware parser.
- Regex-literal detection for escape normalization is heuristic; escape-like text inside a regex literal is treated as pattern text, not a reference.
- A canonical work-context Todo record without `todo_id` is assumed to be the selected task's own record (the canonical producer emits the goal-state record and the selected Todo record). Direct-caller payloads with multiple id-less records should provide `todo_id`.
- A malformed explicit `retirement_basis` value is inert and fails closed; it cannot activate partial policy and may shadow a later valid declaration in the same carrier.
- Python identifier identity is NFKC-normalized in the candidate gate and AST matching; fullwidth, ligature and combining-mark names are covered. Other non-ASCII normalization forms should use the same AST-backed identity path.
- Escaped definition names and escapes outside value-position member/identifier references are outside the reference model.
- The hand-rolled lexer is not a complete JavaScript/TypeScript parser; closing arbitrary expression syntax requires a real parser and remains a bounded follow-up.
