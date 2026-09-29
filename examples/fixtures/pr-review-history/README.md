# Reviews grounded in historical code

These public historical reviews exercise the shared publication/readback
validator and the opt-in review decision probes. They replace the invented
export example as the default body fixture. They are not new approvals,
merge permissions, or evidence that today's checkout passes historical tests.

| Review | Why retain it | Code boundary |
| --- | --- | --- |
| [#4854 request changes](https://github.com/loopx-project/loopx/pull/4854#pullrequestreview-5287932132) | A concrete retry counterexample overturns an earlier approval at the same head; the repair preserves useful replay assets while removing duplicate fixture authority. | `external_progress_review.py` drops an earlier typed claim when deduplicating Turns; `replan_semantics.ts` relies on that window to refuse replay. |
| [#4882 request changes](https://github.com/loopx-project/loopx/pull/4882#pullrequestreview-5277672546) | Passing receipt and lock tests did not exercise the actual writer. The review spells out an interleaving and asks for the correct owner to enforce freshness. | `checkpoint_context_io.py` holds projection/source locks, while `provider_update.py` commits canonical state before projection settlement. |
| [#4882 approval after repair](https://github.com/loopx-project/loopx/pull/4882#pullrequestreview-5288050653) | A positive control: accept a demonstrated local fix while disclosing uncertain append recovery and excluding PostgreSQL. | `checkpoint_authority.ts` and `checkpoint_commit.ts` keep comparison and append inside the File writer lock or SQLite transaction. |

The Markdown bodies preserve the public reviews. `../pr-review.body.md` is
the last review with only the exact head and English verdict replaced by
`HEAD_OID` / `VERDICT` for queue and parser tests; substitutions in those tests
are mechanical mutations, not endorsements of another head or verdict. Other
bodies only normalize trailing whitespace. `cases.json` records original
review URLs, exact commits, normalization and original response-body digests.
Code excerpts carry immutable source URLs, file paths and inclusive line
ranges. Each `lines` array is an exact contiguous slice, including newlines,
of the file at that commit. Inspect the linked full file for omitted context.

Selection is based on causal analysis, concrete symbols, negative cases and
bounded conclusions, not size or approval state. The long [#4683
review](https://github.com/loopx-project/loopx/pull/4683#pullrequestreview-5243962661)
still missed enabled-but-out-of-scope acceptance interference, later repaired
in #4989. Its length must not become a quality oracle. The acceptance-scope
negative/positive probes remain in `test_pr_review_behavior.py`.

Two different checks consume this corpus:

- Body tests read the historical Markdown to ensure detailed real reviews
  satisfy the format contract. `evidence_truth_verified` remains false.
- Opt-in model probes receive only `scenario`: actual source excerpts, the
  accepted outcome and explicitly historical observations. The review body,
  published conclusion, `expected_verdict` and `decisive_location` are withheld. The checkpoint
  before/after pair guards against blanket rejection of locks or SQLite.

The historical probes must also locate the decisive source range. A correct
rejection blaming the wrong owner fails this check. For #4854 the excerpts
include the Python window producer, novelty codec and TypeScript consumer;
omitting the codec would invite an unsupported claim that TypeScript should
recompute novelty. Review the saved explanation as well: a matching location
and verdict still cannot mechanically certify the reasoning.

Expected decisions come from the stated invariant and inspected source, not
automatically from a historical approval. For retry claims, an omitted earlier
claim cannot become new evidence. For checkpointing, every canonical writer
must share the fence through append. A fixed local boundary can be accepted
without pretending external files participate in SQLite rollback.

Run from the checkout root:

```sh
uv run --extra test python -m pytest tests/capabilities/test_pr_review_body.py -q
# Release qualification only; normal process-only credentials, no tools or writes.
LOOPX_REVIEW_LIVE_TEST=1 uv run --extra test python -m pytest \
  tests/capabilities/test_pr_review_behavior.py -k historical -q
```

The same release-only runner also contains paired synthetic frontend cases for
redundant confirmation, recent-completion ordering and settings target clarity.
Run the full `test_pr_review_behavior.py` suite without `-k historical` to include
them at release qualification. Routine PRs run offline checks with live calls
disabled. Positive twins prevent a blanket rejection of forms or confirmation.

Live results qualify reasoning over supplied evidence only. They do not
demonstrate autonomous repository investigation, rerun the old provider
concurrency tests, or establish an improvement over a baseline model. No
private Goal state or raw execution logs belong in these fixtures.
