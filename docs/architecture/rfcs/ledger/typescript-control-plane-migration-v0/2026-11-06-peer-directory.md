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

Qualification is pending until recorded against the final integrated candidate:

- Fixed-time baseline/head comparison through the actual command, preserving
  old fields, ordering, diagnostics and exit status, with envelope additions
  checked separately.
- File and SQLite first/last pages, later-page members, unauthorized claimants,
  explicit runtime roots and unchanged business state.
- Stale, unreadable, incomplete, missing and replayed source evidence;
  independent mutations must detect page-only membership and refreshed old
  source timestamps.
- Clean wheel and sdist execution without borrowing the source checkout.
- Matched whole-CLI cold/warm p50/p95, runtime request counts and output sizes;
  type checking, native/Python regressions and required premerge checks.

This checklist is not a passed-test claim. Preserve failures and unmeasured
boundaries when adding results; earlier CLI optimization measurements do not
qualify this candidate. This checkpoint does not close T4 or change release
qualification.

## Bridge exit and rollback

Delete the remaining Python adapter when actual directory callers and necessary
fact providers can call the native owner directly. Until then it only supplies
facts, public safety and compatibility transport. No new store, dual-write or
persisted migration is introduced; rollback restores the previous code package.
