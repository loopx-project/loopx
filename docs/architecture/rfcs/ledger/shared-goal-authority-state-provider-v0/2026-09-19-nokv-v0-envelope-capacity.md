# NoKV v0 single-envelope capacity, measured

Non-normative for the shared Goal authority model: it changes no code and no
profile. It records what the current NoKV candidate layout can hold before it
fails closed, so that §7.2's bounded-head discussion and the bounded-layout
design in [#4727](https://github.com/loopx-project/loopx/issues/4727) argue from
a measurement instead of an estimate.

- **What was measured.** `NoKVAuthorityStore` keeps one JSON envelope per goal
  and republishes the whole retained journal on every commit; the envelope is
  capped at 16 MiB (`DEFAULT_MAX_ENVELOPE_BYTES`), after which `commitAuthority`
  returns `failed/authority_envelope_too_large` before any CAS. A probe outside
  the repository committed one transaction after another through the real
  store (`commitAuthority`, then `readReceipt` of the first and last operation
  and `loadAuthority`, sampled every 50 commits) until that refusal, once over
  an in-memory transport and once over the real JSON-lines helper against a
  single-node NoKV 0.11.1 owner. Every transaction carried a fixed projection
  plus about 0.6 KiB of events and receipts. The projection sizes were the
  production-scale history fixture as it is (21,858 bytes) and a 64 KiB
  synthetic one. Measured on LoopX `da6d79778` (2026-09-19, before the
  incarnation fence landed; the envelope layout is unchanged on `main`).
- **Where the envelope stops.** In memory the 21,858-byte projection reached
  the cap at commit 739 (final envelope 16,758,084 bytes, about 22.7 KB added per
  commit); the 64 KiB projection at commit 252 (16,762,707 bytes, about 66.7 KB
  per commit). Live, the 64 KiB profile stopped at the same commit 252 with
  16,762,708 bytes. The cap is therefore reached by retained projections, not
  by receipts: every committed row keeps its full projection.
- **What that means in days.** Against §7.2's minimum continuity load of 864
  commits per day, the 21,858-byte profile lasts about 0.86 days and the 64 KiB
  profile about 0.29 days; at 10,000 commits per day, 1.8 hours and 0.6 hours.
- **Latency grows with history.** Live, commit latency went from 341 ms at
  commit 2 to 3,460 ms at commit 251 (p50 1,340 ms, p95 3,139 ms over 251
  commits); `readReceipt` of the first operation from 66 ms to 1,307 ms;
  `loadAuthority` from 57 ms to 1,334 ms. Reading is O(history) because every
  read decodes and re-verifies the whole journal.
- **Traffic.** Reaching the cap live moved 2,128,646,475 bytes of writes and
  4,627,769,098 bytes of reads over 409.6 s for 252 commits; the 739-commit
  in-memory run accounted 6,208,337,638 bytes written and 12,526,179,676 read.
  Each commit reads the envelope twice and each read carries one
  `find_workspaces` identity check.
- **NoKV facts that bound the design space.** Re-publishing the same bytes
  under the same `operation_id`/`artifact_revision_id` is `applied` (idempotent
  replay); a create-only publish with fresh ids on an existing path is
  `conflict`; reusing ids with different bytes or a stale generation is refused
  in a way the helper can only report as `ambiguous`, so publication ids are
  terminal after the first attempt. Single objects of 4, 8, 16 and 24 MiB were
  accepted and read back through the same transport (24 MiB needs the
  transport's response limit raised above its 32 MiB default), so 16 MiB is a
  LoopX constant, not a NoKV limit.
- **What this does not establish.** No bounded layout exists yet; #4727 is a
  design, not a delivery. The measurement covers one owner, one workbench and
  one goal; it says nothing about restart or restore recovery, availability,
  HA, or retention policy, and it does not move any qualification hold. The
  reason code stays `authority_envelope_too_large`; whether the NoKV plane
  adopts Appendix C's `store_capacity_exhausted` is an owner decision recorded
  in #4727.
