# Repository review experience

These Git-versioned public sources belong to `pull-request-review`. Each active
JSON file under `experiences/<owner>/<repository>/` uses the existing
`procedural_experience_contract_v0`; there is no new memory schema or writable
provider. Git history retains revisions. Replace or remove a source through a
reviewed PR to update or retire it; keep one active revision of a case.

The [PR #5944 case](pr-5944-review-frame.md) records the machine reviews and the
maintainer-directed model review separately. Its [distilled experience](loopx-project/loopx/pr-5944-v1.json)
contains applicability, observation, attribution, future behavior, limitations
and public evidence refs. Historical verdict labels stay in the case document,
which retrieval does not load. Self-repair points to this same source rather
than maintaining another copy of the lesson.

## Use in an enabled review Agent

Use the existing **Reward Memory experiment** editor in the Goal's Capability
Center to enable the registered review Agent and apply its versioned config.
The config must have `automation.automatic_recall=true` and an explicit
`pull_request_review.review` surface, assigned to its existing corpus ids with
a recall profile. Keep the normal provider preflight and exact readback.
See [Reward Memory configuration](../../reward_memory/README.md); no new toggle,
provider write or automatic import is added here.

Add this surface to the existing private config, substituting the owned corpus
id, and include the surface in that corpus and standing policy's `surface_ids`:

```json
{
  "surface_id": "pull_request_review.review",
  "adapter": "scoped_feedback",
  "corpus_ids": ["reviewed_experiences"],
  "ingest_corpus_id": "reviewed_experiences",
  "recall_profile": {
    "profile_id": "pr_review",
    "mode": "function_boundary",
    "max_queries": 1,
    "limit": 3
  }
}
```

Apply/read back through the existing Goal editor or CLI:

```sh
loopx configure-goal --goal-id GOAL \
  --reward-memory-config .loopx/config/reward-memory/experiment.json \
  --reward-memory-agent REVIEWER --execute
```

```sh
loopx reward-memory experiment-status --goal-id GOAL --agent-id REVIEWER --format json
loopx pr-review --goal-id GOAL --agent-id REVIEWER --repo loopx-project/loopx \
  --target-exact-head NUMBER@HEAD_OID --format json
```

An actionable review row may contain `repository_experience.decision` and
`repository_experience.guidance`. The built-in reader searches only the selected
repository's bundled files, validates the existing experience contract, and
checks exact file readback before delivering at most three matching experiences.
It composes the existing corpus, candidate, recall and TypeScript decision
owners. BM25 orders lexical matches; it does not decide applicability or the
verdict. The SDK `provider_call_count` counts this local reader invocation;
there is no external memory-provider call or write in this path.

The review skill reads the guidance before judging the current diff. Record
adoption, rejection or irrelevance with current-head evidence in the existing
`problem_context` judgment. `context_delivery_verified=true` proves packet
delivery only: `semantic_disposition=null`, `decision_consumption_complete=false`
and `utility_verified=false` remain until their separate evidence exists. The
ordinary five-block review still returns through the existing conversation;
this internal source reader adds no separate product screen or agent authority.

This is a disclosed opt-in behavior extension: qualified Agents with automatic
recall and this review surface receive matching repository advice. Disabled,
unbound, other-surface, stale-config, unqualified and different-repository calls
preserve the original packet. Inventory-only and merge-readiness rows receive
no new recall. Invalid or changing assets preserve empty guidance and never
become a user gate. Empty guidance and its decision remain visible in JSON and
Markdown; no failed retrieval is reported as adopted learning.

Disable with `automation.automatic_recall=false` and reapply the existing config,
or remove the review surface and reapply it. Clear the whole experiment with
`loopx configure-goal --goal-id GOAL --clear-reward-memory-config --execute`.
Downgrading removes this reader; there is no provider or persisted runtime state
to migrate. Repository publication, review/comment, push, merge and cross-Agent
writes retain their existing authorization boundaries.

## Next evidence

The current case is a curated experience and a working context-delivery path,
not a measured improvement in review quality. A later pilot must pin the same
review frame, artifacts, model and budget, blind held-out cases to old verdicts,
and retain disagreement, false blockers, cost and human attention. Verified
adoption and outcomes may then enter the existing
[utility-attribution RFC](../../../../docs/architecture/rfcs/post-outcome-memory-utility-attribution-v0.md).

[中文](README.zh-CN.md)
