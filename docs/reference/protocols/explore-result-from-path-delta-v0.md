# Explicit Explore capture from a path delta

An enabled Explore Goal can attach a reusable result to ordinary `refresh-state`
writeback. Capture is optional: an ordinary vision packet or local validation
note does not enter the graph automatically. Explore uses its existing
capability hook, claimed Todo, current lease and committed writeback receipt.

For a result already expressed in this packet's top-level `path_delta`, put the
following explicit scope in its top-level `explore_result` field:

```json
{
  "schema_version": "goal_vision_replan_contract_v0",
  "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
  "path_delta": {
    "schema_version": "goal_path_delta_v0",
    "outcome": "continue",
    "prior_assumption": "A finite prefix might bound the tail.",
    "observed_reality": "A divergent tail shares the tested prefix.",
    "changed": ["Require a uniform estimate before transferring the result."],
    "evidence_refs": ["validation:counterexample-1"]
  },
  "explore_result": {
    "schema_version": "explore_result_from_path_delta_v0",
    "question": "Does a finite prefix establish the tail bound?",
    "applicability": "Finite prefix only; no uniform tail estimate.",
    "input_revision": "fixture-v1",
    "status": "refuted"
  }
}
```

Submit this file with `--agent-vision-json vision.json` on the Todo-bound
`refresh-state` command projected by `quota should-run`. The typed Goal owner
validates `path_delta`; the Explore owner copies `observed_reality`, renders
its outcome and every retained/changed/stopped item as the interpretation, and
copies its evidence identifiers unless the caller explicitly selects a subset.
It normalizes the result to the existing
`explore_result_attachment_v0` before the usual hook commits and links it.
No finding status is inferred from the route outcome. The caller must choose
`tentative`, `confirmed` or `refuted` from the evidence, within the declared scope.

For **first capture**, omit `node_id`. The typed Explore owner derives a stable
question id from the exact trimmed `question` and `applicability` strings
(SHA-256 of their JSON array, prefixed `question_`). The hook creates the question,
records the finding and links the claimed Todo in the same existing writeback
flow. No separate `explore node` call is required. This applies to both attachment
forms. Revision, observation and status do not affect question identity.

For later observations, reuse the returned question id and canonical scope.
Explicit ids remain supported and are never silently remapped; a blank, null or
malformed id is rejected with recovery instructions. Changed question or
applicability text without an id creates a distinct scoped question; this is
exact identity, not semantic deduplication or a lookup of legacy/manual ids.

首次记录新问题时省略 `node_id`，由 Explore 根据明确填写的问题和适用范围生成稳定标识，
随后通过原有 hook 写入发现并关联当前 Todo，无须先调用 `explore node`。
后续记录复用返回的标识和原始范围；输入版本、观察和结论仍须由调用者明确提供。
空值或冲突标识不会被静默修正，也不会覆盖已有问题。

Question identity and applicability remain stable across later observations.
When the selected Todo already links a stored question, the same schema also
accepts this shorter explicit reference:

```json
{
  "schema_version": "explore_result_from_path_delta_v0",
  "node_id": "<returned-question-id>",
  "input_revision": "fixture-v2",
  "status": "tentative"
}
```

Omit **both** `question` and `applicability` to reuse that question's complete
canonical title and scope. The hook reads the Goal's source registry/runtime
and the canonical Todo links, including File/SQLite authority. An unknown,
unlinked or non-question node fails before primary commit. Supplying only one
scope field or an explicit blank also fails; stored text never silently replaces
an explicit override. Full scope remains required for a new question. This
reduces repeated authoring; it does not initialize an empty graph or automatically
capture an ordinary path delta. The turn-context and settlement hooks project
`linked_question_attachment_template` alongside the first-capture template.

`input_revision` identifies the tested input, rather than a guessed source
revision. Evidence identifiers stay opaque; keep raw logs local. Neither capture
nor a successful transport proves model adoption, task completion or causal
utility.

When the delta includes local file pointers alongside opaque identifiers, add
optional `evidence_refs` to `explore_result`, for example
`["validation:counterexample-1"]`. Every selected identifier must occur in this
same delta and pass the existing opaque-reference validator. The selection must
be nonempty; unrelated refs and local file pointers are rejected. Without this
field, all delta refs are reused and must satisfy that validator. This explicit
selection neither removes refs from the ordinary vision nor invents provenance.

The complete `explore_result_attachment_v0` form remains supported inline or
through `--explore-result-json result.json`. It additionally supplies
`observation`, `interpretation` and `evidence_refs`. It is appropriate when the
reusable result differs from the current route delta. Two sources must normalize
to identical results; a conflict fails before primary commit.

## Execution evidence on the same input

Both attachment forms accept the following `execution_observation` value
when the candidate ran on the finding's declared `input_revision`.

```json
{
  "schema_version": "explore_execution_from_finding_v0",
  "candidate_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "planned_method_id": "bounded-probe",
  "executed_method_id": "bounded-probe",
  "change_reason": null,
  "elapsed_seconds": 0.125,
  "limit_seconds": 10,
  "search_iterations": 2,
  "stop_reason": "iteration_limit",
  "evidence_refs": "finding"
}
```

Use the actual candidate digest, method and measured resource values. Set
unknown measurements to null. The shorthand binds the required input revision
from this finding and rejects an explicit nested `input_revision`. Choose
`"finding"` only when all of the finding's validated evidence refs describe
the execution. Otherwise list its exact nonempty subset. With path-delta
capture this operates on the finding's selected refs, after excluding any local
file pointers. A released result still requires its `result_ref` and
matching `result_candidate_sha256`.

The Explore owner expands this input to the complete
`explore_execution_observation_v0` before the existing attribution checks
and durable write. That complete form still accepts explicit values and rejects
mismatches. Standalone observations, including `explore finding` and stored
records, use the complete form. The captured writeback plan and Explore
turn-context expose `optional_execution_observation_example` when relevant.

## Bounds and readback

A path-delta reference requires this same vision packet's top-level delta;
a previous stored vision, a nested delta or a separate result file alone cannot
supply it. Question allows 180 characters and applicability 200. Observation's
320-character limit matches the Goal observation budget. Interpretation allows
1200 characters and retains every legal route item as plain list text without
JSON escaping expansion. The combined finding, including its input
revision and applicability, fits within the 2000-character stored and next-turn
summary budget. This replaces the previous 300/300 attachment and 1200 summary
limits; the default three-result page and progressive reads are unchanged. No
condition or route item is silently truncated. Malformed or disabled capture
fails before primary commit. On an identity conflict the error names the
mismatched `node_kind`, `title` or `summary` without copying stored text. Read the
canonical question scope when adding to that question. If this is a distinct
scoped question, omit the conflicting id and supply full question/applicability;
do not overwrite the old node. The standalone `explore node` command defaults
to an `area`, which cannot serve as a capture question. Correct the input and
retry the same writeback; rejected attempts leave primary history, graph and
Todo links unchanged. A partial post-commit
link failure uses the existing exact-writeback replay recovery; replay does not
duplicate graph events.

Read back with `loopx explore turn-context --goal-id GOAL --agent-id AGENT`:
the finding's scope, input revision, observation, interpretation and Todo link
use the existing Explore context and frontend result view. Disable capture by
omitting `explore_result`; turn Explore off with the existing Goal configuration.
Neither activation nor this reference grants claims, leases, spawning, external
writes or access to evidence outside the caller's authority.
