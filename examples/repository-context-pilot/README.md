# Repository-context pilot

First bounded experiment for [#4926](https://github.com/loopx-project/loopx/issues/4926).
It asks whether an optional repository-intelligence provider can supply useful,
checkable context to the existing issue-fix workflow. It does not register a new
capability, change default context or claim improved repair outcomes.

## Run

Use a source checkout prepared with `uv sync --extra test`. Node must support
TypeScript stripping; use the repository's supported Node version. Git, tar and
ripgrep must be on PATH.

```sh
mkdir -p .local
node --experimental-strip-types examples/repository-context-pilot/run.ts \
  --repository . \
  --revision 789694fa42972db17acd47aa2604b1907060028d \
  --out .local/repository-context-first-run \
  --prepare-rune \
  --loopx-python "$PWD/.venv/bin/python"
```

`--prepare-rune` explicitly clones the MIT-licensed Rune upstream at
[`4042a088`](https://github.com/thecolourfoundation/rune/tree/4042a088f1672d32110f38a5ec10c59717ea5412)
and installs its locked dependencies with scripts disabled in disposable
storage. Preparation time is included. Shared package caches are not cleared.
Alternatively, `--rune-source` uses an already prepared checkout of that exact
revision with no tracked edits; its acquisition and installation cost is then **unknown/excluded**.
Omitting both options runs only direct search and records Rune as disabled.

The source is exported from the exact commit. Dirty edits, untracked files,
existing provider indexes and symlinks are excluded; the live repository and
LoopX runtime are never given to Rune. Only the exported copy receives an index.
Source hashes are captured before provider startup; a provider cannot validate
its own replacement text as evidence for the pinned revision.
The MCP client calls `rune_agent` with `deep:false`; no model is configured or
called by this runner. The optional provider's dependencies remain outside
LoopX's installation. Temporary copies and the provider process are cleaned up.

Each run requires a new output directory and retains failed rows. Keep generated
reports owner-local; review them before sharing. Raw MCP responses, source
snippets, absolute source locations and server logs are not retained. The report
contains source-relative citations, file hashes, coverage, elapsed times and
provider/native-readback dispositions. `*.context.json` files use the existing
`issue_fix_repository_context_input_v0` contract; a normal read-only
`issue-fix workflow-plan` consumes them when `--loopx-python` is supplied.

Verified citation currency does not establish relevance or authority. Sources
stay advisory and cover only change-scope discovery; reproduction, validation
and any protected operation still require their own evidence and authorization.
The prototype is a CLI/artifact experiment, not an installed frontend provider.

## Frozen comparison

`queries.json` is declared before running either provider. Two historical public
issues have reference files derived from their independently reviewed fixes:
[#7817](https://github.com/loopx-project/loopx/pull/7817) and
[#7814](https://github.com/loopx-project/loopx/pull/7814). A third query names an
absent symbol. The pinned source predates those repairs.

Rune receives the issue question; direct search receives the declared literal
terms from the same report. These are different retrieval interfaces, not
matched model treatments. Direct results are file-sorted; Rune keeps its own
ranking. Both retain at most eight distinct files after source verification.
Reference-file precision/recall measures this declared set, not every potentially
useful file. Extra files can be relevant; they are reported as unexpected rather
than certified false positives. Unsupported-language misses and wrong answers
on the absent-symbol query remain in the report.

Costs include source export, optional provider preparation, MCP startup/indexing,
all query/verification attempts, native CLI readback and cleanup. Native-plan
time is separate from retrieval time. One process search and one MCP tool call
are not equivalent units of work. No model, repair, handoff, held-out trial or
long-horizon benefit is qualified by this small comparison.

## Design research and placement

| Source | Design considered | Decision for this pilot |
| --- | --- | --- |
| [Rune](https://github.com/thecolourfoundation/rune), MIT | Source-cited graph facts through MCP; JavaScript/TypeScript strongest, thinner Python coverage; optional deep calls use a model | Use only the explicit no-model MCP path, pin the source and independently reread citations |
| [Repomix](https://github.com/yamadashy/repomix), MIT | Repository packing, bounded output and security checks through CLI/MCP | A packed repository is a useful alternative; this pilot tests retrieval rather than adding a second repository packer |
| [Existing LoopX provider comparison](../context-provider-comparison.py) | Content-based relevance and explicit unavailable rows through Decision Context | Preserve that distinction; issue-fix already owns repository-context input, so no new Kernel or provider-selection owner |

No upstream implementation was copied. Rune and Repomix are independently
maintained optional tools, not new mandatory dependencies. Production adoption
would need provider lifecycle, applicable dependency/security qualification,
the existing frontend journey and real task-outcome evidence. Reuse #4926 for
that decision after this experiment; do not treat a successful MCP read as
acceptance of a full integration.

## Validation

### Observed source qualification

On macOS with Node 26.9.0, the declared commit exported 7,174 files
(182,435,840 archive bytes); Rune indexed 6,240 files. The instrumented run kept
the declared queries, eight-file limit and 4,096 estimated-token Rune budget.

| Query | Direct search | Rune | Retrieval and verification time, direct / Rune |
| --- | --- | --- | --- |
| Windows worktree | 0/1 reference files; 46 additional matches omitted by the declared cap | 0/1; eight insights dropped by Rune's budget before citations reached the client | 0.100 s / 21.201 s |
| Provider key shapes | 2/2 reference files | 0/2; eight insights dropped by Rune's budget | 0.220 s / 7.496 s |
| Absent symbol | Empty result | Five source-current files unrelated to the requested symbol | 0.081 s / 2.266 s |

Provider preparation took 23.740 s, startup/indexing 11.302 s, and shared source
export/hash capture 2.358 s. The complete run took 73.151 s, including all six
read-only native CLI observations and cleanup. Native context remained
`ungrounded` in every case; no reproduction or validation evidence was invented.
`failed:false` in this report means the observation pipeline completed, not that
retrieval quality passed. The Windows issue is a historical source query, not
native Windows execution qualification.

Two earlier attempts are retained separately: the first failed before retrieval
because the archive exceeded a 128 MiB in-memory subprocess buffer. Export now
uses a temporary archive file without raising that buffer limit. The second
completed the same queries but lacked provider-budget telemetry; the third added
that observation without changing retrieval settings or reference files. Results
are not pooled, and no failed attempt was replaced or omitted. Preparation was
measured again; the differing setup times are not a stable latency estimate.

This does not support default integration of Rune on this workload. The direct
control is also incomplete: file sorting plus the declared cap misses the
Windows owner, and this one-shot search is not a full agent investigation.
Budget-aware citation delivery, unknown-symbol abstention and task-level utility
need evidence before adoption. Do not increase the budget after the fact and
relabel this observation as a pass.

A separate `npm audit` of the same pinned Rune dependency tree reported nine
findings (one critical, three high, five moderate). Dependencies are disposable
and this runner uses local stdio; production applicability and remediation have
not been qualified. This is another limit on adoption, not a clean security bill.

```sh
node --experimental-strip-types --test examples/repository-context-pilot/pilot.test.ts
```

These tests cover source isolation, citation tampering/escapes, default-off and
unavailable behavior, independent coverage accounting and the real LoopX CLI.
They do not mock a successful Rune run or enter the active benchmark fleet.
Run the explicit experiment above to qualify the real provider separately.
