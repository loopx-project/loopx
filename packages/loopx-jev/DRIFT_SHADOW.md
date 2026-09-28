# Review task progress from explicitly selected files

[中文](DRIFT_SHADOW.zh-CN.md)

This experimental command captures real, explicitly scoped file changes around a
successful LoopX `refresh-state`, then evaluates them in a **separate consumer**.
By itself it reports historical observations only: the observer never corrects,
pauses, redirects, acknowledges, settles, or injects messages into an Agent. When a
Goal opts into the core [progress-review sentinel](../../loopx/capabilities/progress_review/README.md)
policy, the observer's typed receipts become visible in `loopx status`, and under
`assist` a run of consecutive completed drift receipts raises the **existing**
`autonomous_replan_obligation`; nothing else changes. No success-rate or
time-saving claim follows from passing the integration tests.

## Placement and supported journey

The commands live in the optional `loopx-jev-pilot` distribution. Its product
surfaces are the task-progress observation command and the comparison harness; no
ranking code or scheduler is registered. The core-side policy, receipt contract and
trigger live in the builtin `progress-review-sentinel` capability, which imports
nothing from this package. The source is explicitly `scoped_checkpoint_capture`,
not a claim to be a Decision Context provider. The [decision record](DESIGN_DECISIONS.md)
links the research history and evidence limitations.

The existing L1 `reliability-diagnostics` observer is a separate contract: its
no-egress/no-worker-influence receipt is not reused for model inference. Neither
that observer nor `state_refresh.py` is modified. Jev never runs inside a core
transaction or core write lock.

This is an explicit CLI installation: use the wrapper at the real refresh call
site and run the consumer separately. Ordinary `loopx refresh-state` and native
Codex/Claude sessions remain unchanged; there is no automatic host-hook installer
or Lark switch. The observer's own settings (model, egress, limits, off/shadow)
are local and bound to one Goal state directory; give each Goal its own config
file. Whether the core reads the resulting receipts is a separate per-goal
registry policy, `loopx configure-goal --progress-review-mode`, also editable in
the Dashboard and default off. The operator supplies the contract export, which is
not itself proof of canonical Goal acceptance or exclusive workspace ownership.

## What “scoped files” means

These are the exact repository-relative files supplied with `drift init --path`.
For a retry task, an operator might select `src/retry.py`,
`tests/test_retry.py` and `reports/retry_probe.json`. This is an observation-input
list, **not** a restriction on which files the working Agent may edit. The
collector does not discover relevant files or scan the entire repository.

| Material | How it enters the assessment |
| --- | --- |
| Goal and acceptance criteria | Operator-provided basis JSON |
| Files named by `--path` | Before/after contents and net changes; a not-yet-created file is allowed |
| Optional `evidence` references in the basis | Explicitly named regular files, such as a test or probe report |
| Other source, dependencies or conversation history | Not automatically read; its absence limits the judgment |

Paths are files, not directories or globs, with at most 32 selected files and
the byte bounds below. Selection stays fixed for the initialized observer; to
change it, explicitly initialize a new observer/budget and establish a new
baseline. Changes outside the list may still be valid Agent work. `no_delta`
means no change in the observed material, not no progress on the whole task.
Reading a test report does not run the test or independently certify its claim.

For example, selecting only a function's file may omit the helper it calls and
the test that exercises it. A judgment from that packet cannot certify the full
behavior. Include relevant tests, results and dependencies deliberately; if the
necessary material does not fit, do not present the partial packet as complete.

## Run it

Use Python 3.11+ and the Node runtime required by the LoopX checkout. This
scoped collector targets POSIX file handling on Linux/macOS; Windows capture is
not qualified and unsupported file primitives produce an unavailable observation.
The optional distribution is not part of the default LoopX wheel. Install from
the source root into a fresh environment, then run the no-key checks:

```bash
uv venv .venv-jev
uv pip install --python .venv-jev/bin/python -e '.[test]' -e packages/loopx-jev
.venv-jev/bin/python -m pytest packages/loopx-jev/tests/test_drift.py packages/loopx-jev/tests/test_drift_cli.py -q
.venv-jev/bin/loopx-jev drift --help
```

This implementation only observes task progress; other assessment directions
and ranking code are not provided. Use `loopx_jev_drift_config_v0` and `minimum_label_probability`; old
multi-direction pilot profiles are rejected rather than silently promoted.
A configured key alone does not activate shadow or allow egress. On failure
the existing Agent workflow continues; no independent Agent judge is launched.

For an existing Goal, create an ignored local directory, copy
[`config.shadow.json`](examples/drift/config.shadow.json) and
[`basis.json`](examples/drift/basis.json), and replace the example Goal id,
objective and acceptance with the intended contract. Optional `evidence` refs
are regular files relative to the delivery workspace, for example an independently
produced test report. Do not put credentials in either file. The config starts
with `allow_egress: false`; set it to true only for approved source material.
Provision `TYPESAFE_API_KEY` in the **consumer process environment**.

The following placeholders refer to that Goal's existing local paths. Initialize
**before the work being observed**, in a dedicated delivery workspace. Each
`--path` is an exact relative file path (a not-yet-created file is allowed), not
a glob or directory. Include relevant tests and research artifacts, not just code.
Do not include the observer directory, mutable LoopX state, or credentials.

```bash
loopx-jev drift init --state-dir "$OBSERVER" --config "$CONFIG" \
  --workspace "$WORKSPACE" --basis "$BASIS" \
  --path src/retry.py --path tests/test_retry.py

# At the original refresh call site, preserve its existing arguments and bindings.
loopx-jev drift refresh --state-dir "$OBSERVER" --config "$CONFIG" -- \
  --registry "$REGISTRY" --runtime-root "$RUNTIME" refresh-state \
  --goal-id "$GOAL_ID" --format json

# Separate shell/process: one pass, or bounded polling while work continues.
loopx-jev drift drain --state-dir "$OBSERVER" --config "$CONFIG"
loopx-jev drift drain --state-dir "$OBSERVER" --config "$CONFIG" --watch-seconds 300
loopx-jev drift status --state-dir "$OBSERVER"

loopx-jev drift configure --state-dir "$OBSERVER" --mode off
loopx-jev drift configure --state-dir "$OBSERVER" --mode shadow
```

Do not omit required Agent/Todo/Turn/validation arguments from a managed refresh;
the wrapper grants no exception to those rules. It preserves original stdout and
exit code and writes a compact capture diagnostic to stderr. No config means
off: no observation files, key lookup or transport import. Dry runs bypass
capture. Capture failure after a successful write does not turn that write into
a failed command; it increments capture failures and invalidates the baseline.
Unknown or invalid output cannot be treated as a successful observation.

Use `configure` to disable/re-enable: its monotonic epoch revokes in-flight work
even if the config returns to identical bytes. Re-enable requires a new baseline
checkpoint before comparison. Directly editing config changes its content hash,
but an off/on edit restored between checks cannot be observed; use the command
for revocation. Changing the contract also resets the comparison baseline.

To uninstall, restore the original `loopx refresh-state` call, stop the consumer,
and uninstall `loopx-jev-pilot` from the selected environment. Retained local
evidence may be deleted according to operator policy; deleting request tombstones
and creating a new state directory is an explicit new experiment/budget, not
transparent continuation.

## Receipts for the core, questions and labels

`drift init --runtime-root <runtime>` binds the observer to the LoopX runtime.
Every queued event first writes a **pending** receipt (`not_evaluated`,
`pending_evaluation`), and the separate consumer replaces it with the evaluated
receipt at `<runtime>/goals/<goal-id>/progress-review/receipts/<event-id>.json`
(`progress_review_receipt_v0`). Without `--runtime-root`, results stay in the
private state directory only. `drift init` also prints the observer's
`contract_revision` (the sha256 of the basis file); the Goal policy must pin
that value before `assist` can raise anything.

The model receives only the operator basis fields `objective`, `acceptance`,
`non_goals`, `horizon`, `evidence` and `already_known`, plus the scoped material.
Goal identity, case names and study bookkeeping never enter a request; a test
pins that two bases differing only in `goal_id` produce byte-identical requests.

Each request asks two Choice questions (`relation`, `increment`) and three Noul
questions. `behavior_change` asks whether the delta changes observable runtime
behaviour. `serves_acceptance` and `evidence_increment` are asked about the
**change between the checkpoints**, not the after state as a whole: whether it
implements, verifies or is a prerequisite for a criterion the before checkpoint
did not already satisfy, and whether it adds verifiable evidence about a listed
criterion. The drift booleans are derived by rule `progress_review_signal_rule_v1`
with the label threshold `t`; the core recomputes them from the typed judgments
and rejects a receipt whose booleans disagree:

| Signal | Drift when | Not drift when | Otherwise |
| --- | --- | --- | --- |
| `noul` | `P(serves_acceptance) ≤ 1−t` and `P(evidence_increment) ≤ 1−t` | either probability `≥ t` | null |
| `choice` | `relation = off_goal` and `increment = no_new_evidence` | `on_goal`, `necessary_prerequisite` or `new_evidence` | null |

`behavior_change` is recorded but not gating: an unrelated behaviour change that
serves nothing is still drift, and documentation or a negative finding that adds
goal evidence is not. A Noul probability inside `(1−t, t)` is undecided; an
evaluation with no decided answer is `abstained`. Receipts for `abstained`,
`failed`, `not_evaluated` and `stale` events carry null signals.

The core reads receipts only when the Goal's registry policy says so, and
`assist` additionally requires the pin:

```bash
loopx configure-goal --goal-id <goal-id> --progress-review-mode shadow --execute
loopx configure-goal --goal-id <goal-id> --progress-review-mode assist \
  --progress-review-signal noul --progress-review-drift-threshold 2 \
  --progress-review-contract-revision <sha256 printed by drift init> --execute
```

Receipts bound to any other revision are stale history and are never counted.
A receipt found by `turn_instance_id` must name the same nonempty Agent and
exact Todo, including matching absence for unbound work. Missing identity is
not a wildcard. Only absent Turn identity permits the unique
`(generated_at, agent_id, todo_id)` fallback; malformed/conflicting Turn claims
cannot fall back. Conflicts in receipts or run retries are unattributable,
and anonymous ACKs cannot discharge another Agent's obligation. These stricter
assist rules also apply to historical receipts; see the
[identity contract](../../loopx/capabilities/progress_review/README.md#receipts). A transition whose receipt
is pending, failed, abstained, stale, undecided, mismatched, missing or bound to
another revision is unevaluated: it never counts as drift, it breaks a streak
that has not yet formed, and it neither extends nor dissolves an obligation
that has. Only an acknowledged replan or a newer completed on-goal verdict ends
an obligation. The pin is manual: when the newest receipt is bound to a
revision other than the pinned one, `loopx status` reports
`rebind_hint: newer_receipts_under_unpinned_revision`. A receipt's `sequence` is
this observer state's local counter and restarts at zero when `drift init`
creates a new state; the core orders receipts by the run's `generated_at`, then
`recorded_at`, so a re-initialised observer never reads as older than the state
it replaced. Under `assist` the obligation carries every typed claim made while
it formed; replaying one of them, or renaming identifiers over their evidence
ids, is not an acknowledgement.

`assist` changes the Agent's work contract: it raises a `required` obligation
with a stop condition and an acknowledgement requirement. The obligation binds
the evaluated window's typed progress observation as its baseline, so the
existing writeback rejects an acknowledgement that repeats that observation or
only swaps evidence ids under the same hypothesis; it fires only when such an
observation exists. It grants no pause,
gate or acceptance authority, but it is not a passive recommendation. The
observer's own `off/shadow` switch controls provider calls and egress; the
Goal's `off/shadow/assist` policy controls what the core does with receipts
that already exist. Turning the observer off does not retract written receipts;
clearing the Goal policy does.

`drift label --state-dir <dir> --event-id <id> --truth drift|on_goal|unknown`
records a private human label; `drift status` then reports a confusion table per
signal. Labels never leave the private directory or enter a receipt.

`sentinel compare --matrix … --responses … --output …` replays the committed
16-sequence matrix under `tests/fixtures/sentinel/` against recorded provider
answers and reports, per sequence, when the typed repeat fuse would fire, when
each signal first flags drift, when `assist` would raise the obligation, and every
false flag. `--live` records fresh answers instead; the committed
`expected_summary.json` pins what the last live run produced.

## Evidence, deduplication and results

- Snapshot comparison covers net committed, staged and unstaged **working-file**
  changes between checkpoints, plus explicitly named untracked files and optional
  evidence files. Git is read only. Index-only changes that leave working files
  identical are `index_only_change_unknown`; no model guess is made.
- Model input includes both checkpoints' scoped file contents, including unchanged
  files, alongside the delta. A probe or new test often cannot be interpreted from
  changed lines alone. The overall request-byte limit still applies: reject an
  oversized packet, never silently remove required context. Equal patches against
  different surrounding source are distinct evidence identities.
- Two reads check stability and the post-refresh read checks it again. This is
  not an atomic filesystem snapshot or an authorship proof. Use a single-writer
  worktree. Unobserved edits restored between reads and out-of-scope work remain
  limitations; diff-only evidence cannot establish whole-task progress.
- Baseline reset, no delta, duplicate event and duplicate evidence do not call
  the model. Goal/Agent/Todo/Turn identity deduplicates checkpoint supplements;
  unbound refreshes use the durable record digest. Identical delta under the same
  contract is not another independent observation. Explicit sequence numbers
  preserve order independently of JSON key sorting.
- Queued evidence is immutable historical input. Subsequent workspace work does
  not invalidate it; contract/config epoch changes or changed/deleted source
  records do. Results never acquire authority over the current task.
- `on_goal`, `necessary_prerequisite`, `off_goal`, `unknown` and the separate
  evidence-increment labels remain distinct. Necessary tests, research, negative
  findings and documentation may advance a Goal without changing runtime behavior.
  `no_new_evidence` alone is not a drift verdict. No consecutive-suspicion fuse
  or automatic escalation is implemented.
- Missing key, egress denial, stale inputs, transport failure and abstention remain
  separate outcomes. Requests are never automatically retried. A durable request
  reservation survives deleted result detail; an unresolved send is not reissued.
  Consumer crashes can reuse a saved provider response without another request.

The fixed bounds are 32 named files, 32 KiB combined file text, 32 KiB optional
evidence text, 32 KiB delta, 16 pending observations and 256 event identities.
Oversize/binary/symlink input is rejected, not silently truncated into a verdict.
The initialized request budget (default 20, at most 100) cannot be increased by
editing the config. Full queues reset the baseline and visibly count a failed
capture; they do not silently stretch one observation over missed rounds.

State directories use private permissions; JSON files are mode 0600. The current
baseline and pending jobs contain raw scoped material and must remain local and
ignored. Completed jobs discard raw deltas; compact results and request
tombstones remain. The reused credential-pattern filter is defense in depth,
not a guarantee that arbitrary source is safe to export. Review the scope.

`status` lists statuses, judgments, capture failures and nanosecond client timings:
pre-capture, original command, capture before final state write, assessment,
transport and worker phases when available. Parent timings include child timings;
do not sum them. Cache timings are marked separately. These measurements do not
identify server-only inference time or time saved by the Agent.

## Recorded differential and what remains unproven

The frozen matrix holds 9 drift sequences (6 purely cosmetic from round 1, 3 that
drift only after genuine work) and 7 real upstream commits labelled on-goal. Every
round self-reports `advanced` with a fresh hypothesis id. The committed live
recording uses question set `scoped-progress-sentinel-v2` (`jev-1.13.0`, 35
rounds, 34 completed, 1 failed closed):

| | Typed repeat fuse | `noul` receipts | `choice` receipts |
| --- | --- | --- | --- |
| Drift sequences flagged at their gold round | 0/9, invisible by construction | 9/9 | 5/9 |
| Reaching the `assist` obligation (threshold 2) | 0/9 | 9/9 | 2/9 |
| Real on-goal commits falsely flagged | 0/7 | 0/6 evaluated | 0/6 evaluated |
| Real on-goal commits with no verdict (failed closed) | — | 1/7 | 1/7 |
| Premature flags inside mixed sequences | 0 | 0 | 0 |

The six purely cosmetic sequences, including an 18 KB rename sweep, were flagged
at round 1 and would raise the obligation at round 2. The three mixed sequences
were flagged at exactly their drift round (3) and would raise the obligation at
round 4: cosmetic churn after a landed implementation now scores
`serves_acceptance` 0.06–0.13, where the earlier wording scored it 0.62–0.90. The
executed negative probe (`serves_acceptance` 0.15, `evidence_increment` 0.85) and
the necessary failing test (`serves_acceptance` 0.73) stay unflagged because the
rule protects goal evidence, not because they change behaviour. A second
independent live run reproduced every first-flag round, obligation round and
false-flag count on all 16 sequences with 35/35 completed. The committed
recording's one failed round is a real on-goal commit
(`fix_manager_refused_read_argument`): it produced no verdict, so it is neither
a false flag nor a confirmed pass, and "0/7" would overstate the evidence. Client-measured
assessment latency across the two v2 runs was 1.45–1.49 s median and 2.9 s at
the 95th percentile, against 0.74–0.81 s median in the earlier recordings; the
difference is network and provider time, not the question set. Median input
tokens were 1890.

What changed since the earlier recording, and why it is not yet held-out
evidence: the first question set asked `serves_acceptance` about the file as a
whole and gated `noul` on behaviour change; it flagged 6/9 and missed every
post-implementation churn round. Its requests also carried a `goal_id` derived
from the case name, which could have hinted the label. The v2 wording and rule
were written after seeing those misses on these same constructed sequences, so
the constructed cases no longer count as held-out for the wording. The seven
real commits were not used to tune anything. One of 35 answers was rejected by
the strict decoder because its selected label was not the argmax of its
probabilities; that round fails closed and is neither drift nor a false flag.

Injected answers in the tests prove plumbing, not model quality. Gold labels for
constructed cases come from their author; real commits are labelled on-goal by
having merged upstream. Before intervention, label held-out multi-round Goals with
`drift label`, compare first-flag rounds against the fuse and an independent Agent
judge on the same material, and measure false alarms, lead time, review effort
and full overhead. Escalation, pause and automatic correction remain outside this
slice; whether obeying the obligation reduces wasted work is not measured here.
