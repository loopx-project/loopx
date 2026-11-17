# Packaged single-session heartbeat comparison

This is a navigation summary of the measurement checkpoint for the [controller/executor RFC](../../controller-executor-unified-contract-v0.md#9-validation),
not acceptance of C1 or a default promotion, and does not claim full prose equivalence with the raw ledger.
The [Chinese mirror](2026-10-28-packaged-heartbeat.zh-CN.md) records the same scope and results.
Complete verbatim append-only historical chronology, counterexamples, and validation bounds remain preserved in [2026-10-28-packaged-heartbeat.raw-archive.md](2026-10-28-packaged-heartbeat.raw-archive.md) (`sha256: 52b9cb9e0139d5820ba5404bb71084e27045ddcf759e436a720cf728c6d07599`).

## Source, workload, and convergence scope

Compares original quota on main against the unmerged `#7430` C1 integration
candidates using isolated File and SQLite authorities and a synthetic six-outcome
(24-requirement, one open selected task) packaged CLI workload (`1,884`–`1,889`
source-matching wheel members; no global runtime install, live Goal state, or
paid model run). Lifecycle continuity, `PostCompact`/`SessionStart` invalidation,
same-capture recovery without new admission, tamper/early-spend refusal, and
once-only replay settlement are exercised on both backends.

Absorbed prerequisite slices on main: `#7520` (route owner), `#7457` (shared
`reference_context`), `#7557` (`quota/diagnostic_projection.ts`, merged
`4b2b97b7b7`), `#7528` (relocated-context overlay `759c16a9cf`), `#7547`
(staged-capture Goal owner), `#7549` (prompt whitespace), `#7575` (complete Goal
warm reuse, merged `a6283d923e`), `#7594`/`#7609`/`#7610` (material planner and
exact Todo-record reuse), `#7628` (current-capture recovery ordering, merged
`75034bfa99`), `#7633` (lossless-reference regression suite), and `#7643`
(source-preserving display edits).

## Packaged cohort progression

One uncontrolled matched cohort per checkpoint (`13/9/13` CLI calls for
cold/warm/recovery; invalidation adds two hook calls, `683–738 ms`; expanding
all five optional peer outcomes adds `13,940` content bytes, `17,620` stdout
bytes, and `10` CLI/logical requests per candidate phase; seven settlement/negative
calls keep `226` logical requests).

| Checkpoint (candidate / main) | File cold → warm → recovery stdout B (orig → cand) | SQLite cold → warm → recovery stdout B (orig → cand) | Outcome & open regressions |
| --- | --- | --- | --- |
| Initial `#7430` (`1199cbe0b8` / `843be49426`) | `94,662→96,082` / `58,077→60,649` / `89,807→91,144` | `94,776→96,208` / `58,185→60,771` / `89,911→91,262` | Warm latency & stdout regressed (`~6.6 KB` Todo lanes + `~3.9 KB` scope metadata). |
| Diagnostic owner `#7557` (`d33c1a19d9` / `843be49426`) | `95,004→89,875` / `58,401→61,183` / `90,113→84,903` | `95,114→89,997` / `58,509→61,301` / `90,217→85,017` | Cold/recovery `-5.3 KB`, warm `+2.8 KB`; every phase slower; v4 probe `27,934 > 13,000` FAIL. |
| Relocated overlay `#7528` (`167b31e5d0` / `843be49426`) | `94,432→89,286` / `57,861→60,624` / `89,603→84,374` | `94,550→89,414` / `57,971→60,746` / `89,709→84,492` | Synthetic 24-row delta `13,199→6,090 B` (wrapper `13,809→6,230`); warm `+2.8 KB` & v4 `27,934 > 13,000` still FAIL. |
| Complete Goal reuse `#7575` (`ebdce8a49b` / `843be49426`, incl. `7,952 B` Goal read) | `94,892→90,918` / `58,289→52,474` / `92,068→87,418` | `95,002→91,048` / `58,401→52,598` / `92,178→87,540` | Stdout improves only when counting quota's separate Goal read; v4 `27,934 > 13,000` FAIL. |
| Todo-lane `<lane>_ref` (`4604ee479` / `843be49426`) | `94,510→89,911` / `57,931→52,094` / `91,720→87,048` | `94,622→90,638` / `58,043→52,218` / `91,830→87,170` | Agent summary `-2,072 B` (`7.4%`), v4 oracle **still FAIL** at `25,862 > 13,000`. |

## Mainline integration and immutable reader identity

Integration on main `447c35ed6` and `ccf6aacc5` adopts the shared native
progressive-material planner, generic capture selectors, request-scoped
settlement, and current-request authorization without creating a second policy
or schema.

- **Reader seams repaired (`5a770d183`):** `context-read --select` continuations
  carry their selection digest through the CLI validator; historical replan
  commands bind the original captured complete input *before* expanding
  diagnostic references so immutable captures do not reject valid commands.
- **Process lane fence (`82f93ab76a`):** moved published-input installation into
  the staged-capture owner so the process fence covers the actual Turn executor
  and complies with the unchanged 90-statement limit (fixing an intermediate
  94-statement premerge failure and two busy-request failures at `fc4763098`).
- **Numeric fidelity & display edits (`7f35454b39`, `fd65514e7`, `dd600ac02`):**
  `1.0` vs `1`, `-0.0`, and unsafe integers caused **8/10** adapted `#7633`
  display/CLI counterexamples and **4/5** complete-composition failures at
  `c335aae894` even after `#7643` edit-plan adoption. Sending sparse numeric
  spellings to the TypeScript `diagnostic_projection.ts` / `turn_envelope.ts`
  owner and applying edit plans on original Python IO values resolved 12/15
  counterexamples (`31,469` / `87,469` / `279,469` → `1,243 B` response for
  identical contracts; `279,469 → 536,456 B` caller view when preserving
  distinct bodies). Existing JavaScript normalized hash inputs remain separate
  from immutable exact-byte capture digests.
- **Interrupted & failed runs retained:** 37-module cohort stopped at `1,364`
  pass / `3` fail (`281910a95` restored missing-task contract error); 16-module
  cohort stopped at `843` pass / `3` fail; long temporary namespace reproduced
  `2,015`-char hook recovery-text failures on both main and candidate; earlier
  `28` codec-fixture failures and `27` inherited broad-mypy errors are preserved.

## Signed complete material staging

Integration on main `b47bbc6c9` wires the real native `complete_turn` caller to
use the shared outcome-scope, progressive-material, and exact-record owners
after validating the complete decision, without editing or re-signing the
canonical capture.

- **Ordering & invariants:** scope edits apply before material edits using
  original source array positions so filtered peer offsets never corrupt
  original evidence pointers; integral floats vs integers are distinguished on
  the Python IO side prior to edits; unknown fields, unsupported obligations,
  and small materials keep full fallback; delivery-off remains inert.
- **Current-capture recovery (`#7628`):** native presentation publishes current
  original recovery and pending delivery before optional material planning (one
  extra journal publication, `2 → 3`), preventing stale predecessor recovery on
  exit/compaction.
- **Hook & lifecycle boundaries:** tests distinguish required-view coverage from
  partial prefixes and truncated inner pages (partial reads never establish a
  baseline); `outcome_context_projection_v0` stays bounded to optional staging
  until replaced; no default reading policy or budget ceiling was relaxed.

## Pass, fail, and open gates

- **PASS (deterministic source & isolated wheel checks):** exact-byte capture
  readback, 24-clause completeness, numeric-preserving carrier sharing, tamper
  and early-spend refusal, once-only replay settlement, and process-level lane
  fencing on isolated File/SQLite authorities (`144`–`333` Python, `104`–`163`
  TypeScript across respective checkpoint revisions; `1,885`–`1,889` matching
  wheel members).
- **FAIL / negative (unchanged):** v4 multi-Agent budget (`25,862 > 13,000`),
  warm ordinary-input growth (`~2.8 KB`) and expensive all-peer reads (`+10`
  calls), delayed-sharing prototype (`8K`–`256K` request bytes `30.5K→55.2K` up
  to `278.5K→1,047.2K`), and long-namespace `2,015`-char recovery failures.
- **UNTESTED / open:** installed App/model retention, real tokens/cache/billing,
  latency under controlled load, native Windows execution, matched single-session
  accepted outcomes & complete costs under RFC §9, remaining consumer/writer
  cutover, and historical-reader retirement.
