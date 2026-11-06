# Peer directory projection and inherited source evidence

## Outcome and ownership

This bounded T3 projection migration removes Python's second directory decision
owner. The public `agent-directory` caller keeps its command, handler and
`build_peer_agent_directory()` signature. One `agent.directory.project` request
owns membership, work fields, route publication caps, counts, cursor,
limitations and page-local rollup, and invokes `sealProjectionEnvelope` directly
in the same TypeScript process.

Python retains source collection, public-output safety checks and request/result
compatibility. Its `_work_block()`, `_rollup()` and replaced directory assembly
branches are retired, with no semantic fallback on bridge failure. The existing
management projection remains the owner of Todo selection and management state;
the existing binding owner remains the route interpreter. Neither is copied
into the directory projector.

## Compatibility and authority

Membership uses the complete registered set, including Agents outside the
24-row display page. Unregistered historical claimants remain diagnostic rows,
not members. `omitted_row_count` keeps its whole-directory definition on later
and empty pages. Rollup category counts cover the returned page before its
eight-item display cap. Route counts include withheld candidates even though
at most three publishable candidates are shown. Unavailable, empty and partial
execution facts retain their distinct meanings, as do false, null and absent
fields.

The only intended public additions are `projection_envelope` and necessary
source-warning presentation. The envelope inherits upstream source observations
rather than replacing them with directory assembly time. Missing evidence stays
unknown; source incompleteness is distinct from display pagination. This does
not create a presence provider, resolve an exact host route, confer execution
permission or alter claims, leases, persistence or provider defaults. See the
[directory contract](../../../../reference/protocols/peer-agent-directory-and-observation-v0.md#directory-projection-ownership-and-source-evidence).

## Cost and acceptance boundary

The fixed implementation baseline is `faab5f5c291c5359216a0f944fe63fcfbaacdedc`.
The previous directory assembly was local Python work; this change adds one
coarse-grained runtime request to that stage. It does **not** make the entire
CLI a one-request operation. Rule ownership and retained source evidence are
the intended benefit; moving code alone is not evidence of a speedup or better
whole-Goal outcomes.

## Measured candidate qualification

Candidate `9d91c5bc178a0acddbbab3276b404b9d0ceada3f` was checked against the
fixed baseline above. The results below qualify this bounded synthetic fixture,
not a universal performance guarantee or completion of T4:

- Native TypeScript tests: 6,129 passed, 77 skipped; focused Python tests:
  33 passed. Randomized comparison covered 160 parity cases. Both independent
  mutations (page-only membership and refreshing old observation timestamps)
  were caught.
- Eight actual CLI baseline/head pairs covered File and SQLite first page,
  last page, cursor past the end and unregistered caller, including a registered
  caller beyond the first page. Existing fields, order, diagnostics and exit
  status matched; the new envelope was checked separately. Registry, Goal,
  canonical authority and fence data stayed unchanged. Initial SQLite reads
  created empty WAL/SHM sidecars, so this is not a zero-filesystem-write claim.
- Fresh wheel and sdist environments passed 16 installed console commands
  across both providers and the four cases, outside the checkout with installed
  import provenance and matching runtime-source hashes. Minimum-Node and
  Windows qualification were not performed.
- Premerge exited 0 with an inherited advisory. Skips and the advisory remain
  limitations, not additional passed checks.

### Whole-command cost

Linux, Python 3.12.14 and Node 24.22.1 measurements used identical synthetic
28-Agent canonical File/SQLite fixtures with an empty work set, explicit runtime
roots and usage telemetry disabled. The final alternating baseline/head run
contained 120 command executions: 20 warm and 10 cold samples per provider and
version. Warm means a new Python process using a running TS server; cold means
a new Python process and isolated runtime directory without a TS server. OS
filesystem caches were not cleared. Times include the complete subprocess;
p95 uses nearest rank on these small samples.

| Provider/runtime | Baseline p50 / p95 (ms) | Candidate p50 / p95 (ms) |
| --- | --- | --- |
| File warm | 348.85 / 397.95 | 348.14 / 380.69 |
| File cold | 949.17 / 986.70 | 950.08 / 1026.45 |
| SQLite warm | 342.77 / 396.21 | 338.94 / 373.52 |
| SQLite cold | 947.77 / 1020.42 | 953.57 / 1078.73 |

Every measured command went from five to six runtime requests. First-page
stdout grew from 9,330 to 11,384 UTF-8 bytes (+2,054; 22.0%). Cold p95 rose
39.75 ms (4.0%) for File and 58.31 ms (5.7%) for SQLite; lower warm samples
do not establish a speedup or statistical significance. This records the
additional crossing and envelope cost, not a latency or release qualification.
Earlier CLI optimization results do not qualify this candidate.

The first paired attempt reused long-idle runtime directories and hit a SQLite
transport failure in the unchanged `explore.configuration.goal` call before
directory execution (exit 1). The underlying cause is not established. That
attempt's results remain separate negative evidence. A complete rerun with fresh
isolated runtime directories passed all 120 measurements with unchanged fixture
hashes; it was not a selective retry of the failing sample. Long-idle runtime
reliability remains unqualified. This checkpoint does not close T4 or change
release qualification.

## Bridge exit and rollback

Delete the remaining Python adapter when actual directory callers and necessary
fact providers can call the native owner directly. Until then it only supplies
facts, public safety and compatibility transport. No new store, dual-write or
persisted migration is introduced; rollback restores the previous code package.
