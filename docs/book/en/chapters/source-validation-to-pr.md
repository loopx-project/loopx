# From focused validation to a Pull Request

A control-plane PR is not strong because it runs many tests. It is strong when its evidence covers the
protocol chain it changes. One large smoke can miss a semantic error; one unit test can miss drift in
projection, scheduling, or writeback.

This chapter turns the decision-scope repair into a public evidence packet:

```text
independent invariant
  -> focused deterministic proof
  -> real public path
  -> risk-based cross-surface checks
  -> public/private scan
  -> reviewable commits and PR
```

The goal is not to reproduce maintainer-local automation. It is to let a reviewer judge the change from
protocols, invariants, and receipts.

## What you should learn

After this chapter, you should be able to:

- choose unit, contract, smoke, replay, canary, and model checks from protocol risk;
- distinguish a semantic oracle, characterization fixture, integration receipt, and release evidence;
- classify failure as product failure, infrastructure failure, manual hold, or deferred gap;
- deliver a Git and PR change without local state, private evidence, or unrelated refactoring;
- explain which contract changed and how it was proved instead of listing modified functions;
- recognize which changes require a maintainer or owner decision.

## Build the evidence matrix first

Do not begin by running every repository command. Map each risk to evidence:

| Risk | Independent oracle | Nearest proof | Cross-layer proof | Forbidden outcome |
| --- | --- | --- | --- | --- |
| Missing scope grants authority | `decision_scope_v0` | Decision table | Source-to-quota replay | Protected action runs |
| Missing scope becomes global | Explicit global-scope invariant | Negative test | Agent-frontier smoke | Independent work freezes |
| Lower-level flags override repair | Final interaction-contract authority | Precedence test | Scheduler replay | Host runs a stale action |
| Retry duplicates repair writeback | Write-correctness contract | Idempotency test | Interrupted writeback smoke | Duplicate event or spend |
| New field expands the hot path | Output contract | Shape or budget check | Actual CLI diff | Agent loses the next action |

If a validation command does not address a named risk, it may be convention rather than evidence for this
PR.

## Six evidence layers

The official
[Testing and Quality](https://github.com/huangruiteng/loopx/blob/main/docs/development/testing-and-quality.md)
guide defines the current quality system. For external contributors, organize it around the job.

### 1. Unit and contract tests

Use these for pure rules, schemas, transitions, and illegal-state rejection.

The decision-scope case directly tests:

```text
matching scope -> operator Gate
unrelated scope -> independent frontier
notice only -> authority remains unmet
ambiguous scope -> typed repair
explicit global scope -> global Gate
```

This layer is the closest proof of an invariant. It does not prove that CLI, projection, and scheduler are
connected correctly.

### 2. Focused deterministic smoke

Exercise one shipped public path:

```text
public-safe source fixture
  -> real projection
  -> real quota decision
  -> interaction contract
```

A durable smoke protects shipped behavior, a reusable contract, or a named regression. It should not
assert every incidental builder field or retain raw logs, live project state, or dated research packets.

### 3. Public-safe decision replay

Replay exposes:

```text
source facts
  + independently reviewed invariant
  -> expected decision and forbidden outcomes
```

It then runs the real product path again.

Replay is not a snapshot. A snapshot may preserve current output. The replay expectation comes from the
protocol, not the implementation under test.

### 4. Risk-based canary

Canary selects a minimal cross-surface set from the Git diff:

```bash
loopx canary premerge --from-git-diff
```

It can catch a repaired scope policy that still breaks scheduler behavior, output budgets, or another
consumer. It cannot replace the focused regression because it may not name this exact failure.

### 5. Full-public smoke fleet

The broad public suite is appropriate on `main`, on a schedule, or by explicit manual request:

```bash
loopx canary smoke-suite --suite full-public --jobs 4 --timeout-seconds 120
```

It provides broad coverage and fleet health. It should not block every ordinary PR synchronously.

### 6. Model behavior and release qualification

Use a real model only for questions deterministic checks cannot fully answer, such as whether an Agent
interprets a compressed default packet correctly.

Decision-scope precedence is deterministic, so the model layer is normally `not_applicable`. Asking a
model to judge scope coverage is expensive and weakens a clear contract.

Release qualification binds evidence to an exact commit, tree, version, and clean state. A contributor PR
can provide code-level evidence; it cannot claim that a release or production deployment is complete.

## Prove semantics before implementation

Keep the validation order:

```text
Is the intended rule correct?
  -> Does the pure implementation conform?
  -> Does the shipped path preserve it?
  -> Do adjacent surfaces remain compatible?
```

The dangerous order is:

```text
run current code
  -> save output
  -> assert output never changes
```

That is characterization. If output contradicts the protocol, refreshing a golden file turns a bug into a
test contract.

### What an independent oracle contains

At minimum:

- source facts;
- authority owner;
- allowed outcome;
- forbidden outcome;
- irrelevant mutations;
- freshness and revision conditions.

For the Gate repair:

```text
Authority owner:
  valid decision-scope relation and its lifecycle writer

Allowed:
  typed repair before protected delivery

Forbidden:
  approval, implicit global block, or hidden Gate

Irrelevant mutations:
  wording, unrelated Agent Gates, unrelated backlog size

Freshness:
  recompute the decision from the current source revision
```

A reviewer can approve the oracle before it becomes test code.

## Test counterexamples, not only the happy path

For each rule, design:

### Positive case

The rule triggers under its legal conditions.

### Suppression case

The rule does not trigger when a higher-priority owner or a safe frontier exists.

### Illegal state

Missing fields, conflicts, duplicates, or stale revisions fail closed or select repair.

### Metamorphic case

Changing irrelevant input preserves the outcome:

```text
add an unrelated Gate
change user-facing prose
increase other-Agent backlog
reorder projection rows
```

None may turn ambiguous scope into authority.

### Retry and interruption

Interrupt between prepare, Host result, validation, writeback, or spend. Recovery must not duplicate the
effect or account twice.

These dimensions protect the protocol better than ten full JSON snapshots.

## Test doubles must obey the real contract

A fake Host, fake clock, or in-memory store can reduce test cost. It must not invent product semantics.

Review a fake for:

1. defaults that match the real adapter;
2. separation among observation, housekeeping, and meaningful effects;
3. distinct denied, timeout, nonzero, and malformed-result paths;
4. recorded idempotency and proposal identity;
5. final receipt assertions rather than only “the call occurred.”

Creating or deleting a test file may be setup, not material progress. Only a protocol-declared effect and
an independently validated postcondition can form a delivery receipt.

If the fake violates the real contract, repair test infrastructure before diagnosing a product regression.

## Choose local validation commands by risk

The official fast baseline includes:

```bash
python -m pip install -e ".[test]"
python -m ruff check tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
python -m mypy
python examples/control_plane/cli-output-budget-regression-smoke.py
python -m pytest -q
git diff --check
```

During development, start closer to the change:

```text
changed decision rule
  -> related unit or contract test
  -> one real-path focused smoke
  -> affected output, compile, lint, or type check
  -> diff-selected canary
```

The current repository, Issue, and quality catalog own exact smoke names. This book does not maintain a
second command inventory.

### Documentation and protocol PRs

For public documentation:

```bash
git diff --check
loopx check --scan-path <changed-doc-or-directory>
```

If a protocol document changes shipped behavior, run its relevant contract and smoke. “Only Markdown”
does not imply zero behavior risk.

### Python rule PRs

Consider:

- lint, type, and compile checks for touched modules;
- the pure decision table;
- a focused public smoke;
- CLI output budgets if the hot path changes;
- `loopx canary premerge --from-git-diff`;
- a public/private scan.

### Host, writeback, and scheduler PRs

Also cover:

- fake Host or clock;
- interrupted-phase replay;
- no-effect and no-spend paths;
- idempotency and revision conflict;
- scheduler acknowledgement and reset identity;
- denied capability and authority.

One successful happy path is not enough evidence for an external effect.

## Interpret results accurately

Validation outcomes are not only `true` or `false`:

| Outcome | Meaning | How to report it |
| --- | --- | --- |
| `pass` | The named check is satisfied | Command, scope, and result |
| `blocking_failure` | An invariant is violated | Do not mark the PR ready |
| `infra_failure` | The environment produced no product conclusion | Report runner/provider failure, not product failure |
| `manual_hold` | Automated evidence is insufficient and an owner must decide | State the exact question and owner |
| `advisory` | A risk signal does not block the current slice | Explain why the minimal path remains safe |
| `deferred_gap` | A useful layer is not built yet | Keep a visible owner and successor |
| `not_applicable` | The layer does not fit this semantic risk | Provide a stable reason |

A provider outage does not prove Gate policy is wrong. A passing unit test does not override a deterministic
canary failure.

## Keep Git changes reviewable

For nontrivial work:

1. start from the latest default branch in a clean branch or worktree;
2. confirm the worktree contains no unrelated task;
3. modify only files needed for one protocol result;
4. classify every path before staging;
5. stage explicit pathspecs rather than `git add .`.

Inspect:

```bash
git status --short --branch
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

LoopX repositories can contain product code, public fixtures, local runtime state, and generated evidence.
Git hygiene is part of the public/private contract.

## Classify changed paths before staging

| Category | Example | Action |
| --- | --- | --- |
| Product code | Protocol policy, writer, projection | Commit when required by this PR |
| Public docs | Protocol, contributor guide | Commit when it explains current behavior |
| Durable validation | Contract test, public-safe smoke | Commit when it protects the rule |
| Local/private state | `.loopx/`, `.loopx/goals/`, live state | Never commit |
| Generated/raw evidence | Logs, transcripts, verifier tails | Never commit |
| Unrelated artifact | Another experiment or formatter churn | Keep outside the PR |

Scan candidate paths for:

- credentials, tokens, or secrets;
- machine-absolute paths;
- private Issues, documents, or internal links;
- raw benchmark tasks, trajectories, or verifier output;
- real active Goal or Todo state;
- generated logs and screenshots.

A public fixture keeps only the minimal synthetic facts needed to reproduce the state machine.

## Split commits for reviewer reasoning

Split by the review task, not mechanically by file type.

A small rule repair can be one cohesive commit:

```text
repair ambiguous decision-scope routing
  - policy correction
  - focused contract and replay
  - protocol clarification only if needed
```

If the work also includes a behavior-preserving move:

```text
commit 1: characterize existing protocol behavior
commit 2: move one cohesive rule family without behavior change
commit 3: change the rule and add negative/replay evidence
```

Do not mix formatter churn, unrelated renames, another Extension, and the Gate repair.

Name the outcome:

```text
fix(control-plane): repair ambiguous decision scopes
```

not:

```text
update quota helpers
```

## Make the PR description a protocol evidence packet

Use this structure.

### Problem

Describe the reader-visible or state-machine failure, not a filename.

### Protocol and invariant

Name the authoritative contract and allowed or forbidden outcomes.

### Change

State which source, projection, decision, effect, or writeback layer changed. State what did not change.

### Validation

List evidence by risk:

- unit or contract;
- focused smoke or replay;
- output and boundary checks;
- canary;
- unrun or `not_applicable` layers with reasons.

### Compatibility and recovery

Describe public fields, migration, retry, rollback, manual hold, or release impact.

### Public boundary

Confirm that no local state, private evidence, credentials, raw session, or machine path is included.

A reviewer should be able to answer:

```text
What contract changed?
Why is the new decision correct?
Which consumers were checked?
What remains owner-held?
```

## Link public work, not local runtime state

Nontrivial contributions should link
the [Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)
or a GitHub Issue:

- state the intended slice before a large change;
- keep scope close to the claimed task;
- obtain design or owner feedback before changing public schema, scoring, permissions, release, or
  production behavior;
- post concrete blockers and attempted validation;
- do not duplicate `Maintainer-owned` live work.

An Issue is a public collaboration boundary. It is not a place to paste project-local Goal state.

## Stop for an owner decision when required

More tests cannot replace authority. Stop when a change would:

- remove or rename a public JSON or schema field;
- migrate canonical state storage;
- change permissions, production effects, or credential boundaries;
- change benchmark scoring, task semantics, submission, or leaderboard behavior;
- remove authority fields from the default Agent packet;
- depend on private sources or maintainer-owned live evidence;
- require a release or merge-owner decision;
- change a public first viewport, hero, or primary CTA that needs presentation review.

These are decision Gates, not missing tests.

## Treat review feedback as protocol validation

Do not implement every comment mechanically. Ask:

1. Does the feedback identify an invariant, implementation, readability, or scope issue?
2. Does the suggestion agree with current contracts and evidence?
3. Will it affect other consumers, migration, or validation?
4. Does it require another counterexample rather than only a code edit?
5. Does the PR description or documentation need recomposition?

If review exposes protocol ambiguity, agree on semantics first. Do not make two contradictory tests pass.

## Two real community contribution cases

This section and its Chinese counterpart are semantic mirrors. A material difference in case facts,
conclusions, link targets, or risk boundaries is a documentation defect.

<!-- community-casebook:small-pr:start -->
<!-- community-casebook:small-pr-problem -->

### Case one: turn a CLI inconsistency into a complete small PR

[PR #3540](https://github.com/huangruiteng/loopx/pull/3540)
started from a real first-run and deep-use observation: `loopx --format json doctor` worked, while the more
intuitive `loopx doctor --format json` failed during argparse processing. The contributor reused the
existing subcommand format contract instead of creating a second renderer.

<!-- community-casebook:small-pr-scope -->

The final change covered only parser wiring, the doctor handler, and one end-to-end CLI regression.
Validation covered:

- the new subcommand argument position;
- the existing global argument position;
- precedence when both forms appear;
- fail-closed rejection of an invalid format before diagnostics run.

<!-- community-casebook:small-pr-lesson -->

The value of this case is not that it changed only a few lines. It completed the whole chain:

```text
real user friction
  -> existing public contract
  -> correct owner
  -> positive, compatibility, and negative validation
  -> independently reviewable result
```

A small PR is not a lower-standard PR. It keeps one user outcome easier to validate and reverse.
<!-- community-casebook:small-pr:end -->

<!-- community-casebook:review-repair:start -->
<!-- community-casebook:review-repair-problem -->

### Case two: review high-risk state writes with counterexamples

[PR #3529](https://github.com/huangruiteng/loopx/pull/3529)
implemented a provider-neutral Stage 2 slice for shared-goal coordination: the aggregate head, file
provider, and `claim_work` executor. The first revision already had broad positive coverage, but review
still produced stronger illegal states: a partial write reported as `applied`, a corrupt receipt escaping
as an untyped exception, an oversized TTL crossing the typed boundary, a lock implementation that could
not import on Windows, and persisted naive timestamps or bool-as-int generations.

<!-- community-casebook:review-repair-response -->

The author did not add string checks for each example. The repair strengthened the shared trust boundary:

- durable writes use write-all, fsync, and atomic replace, returning `ambiguous` when the outcome cannot be
  proven;
- persisted receipts, timestamps, and generations pass typed validation before the executor consumes them;
- locking reuses the repository's existing cross-platform owner;
- the RFC, negative tests, and CI describe the same machine-enforced obligation.

<!-- community-casebook:review-repair-lesson -->

This case shows that review is not style feedback after implementation. A high-value review supplies a
concrete counterexample that breaks the intended invariant and asks for the repair at the real owner. The
author then adds negative evidence proving that the same class of illegal state cannot re-enter the
semantic core. The final `APPROVE` applies only to the revalidated exact head.
<!-- community-casebook:review-repair:end -->

## Distinguish merge, release, deployment, and observation

A merged PR does not prove a release, deployment, or every external Host update. Follow-up may include:

- full-public smoke on `main`;
- release qualification;
- packaged-install verification;
- Host or plugin compatibility;
- documentation-site deployment;
- fresh external readback.

Report these states separately:

```text
merged
released
deployed
observed in the target environment
```

Do not promote an earlier state into a later claim.

## Checklist

Before opening the PR, confirm:

- [ ] Every named risk has an independent oracle and forbidden outcome.
- [ ] Unit, smoke, replay, canary, and model layers were selected by risk rather than volume.
- [ ] Characterization is not the correctness authority.
- [ ] Fakes, fixtures, and snapshots do not invent product semantics.
- [ ] Validation failures are classified accurately.
- [ ] Every changed path is classified and staged explicitly.
- [ ] `.loopx/`, `.loopx/goals/`, live state, credentials, private links, raw logs, and machine paths are absent.
- [ ] Commits and PR text are organized around protocol results, not function lists.
- [ ] Compatibility, recovery, unverified items, and owner Gates are explicit.
- [ ] The PR links public work and does not duplicate maintainer-owned execution.
- [ ] “Merged,” “released,” “deployed,” and “observed” remain distinct.

When a risk surface requires choosing among deterministic tests, decision replay, canaries, model-behavior
validation, or release gates, continue to
[Control-Plane Course Lesson 10](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/).
The course provides combined risk cases; this chapter keeps the delivery path from local evidence to a
public PR.

You have now completed one Control-Plane path within developer contributions: select an owner from the
contribution map, trace one protocol chain, change one rule, and deliver independent evidence in a PR.
Capability, Provider, Host or Runner, projection and documentation, fixture, and Extension work have
different nearest validation surfaces, but reuse the same principle: identify the protocol and authority
owner first, then make the evidence cover the real delivery boundary.

You do not need to memorize every LoopX function. You need to know where a fact comes from, who may change
it, which invariant protects the decision, and which receipt lets the next turn continue. When the
contribution needs independent versioning, optional installation, or a separate lifecycle, continue to
[Extension placement](./08-extension-placement.md) rather than packaging every contribution as an
Extension.
