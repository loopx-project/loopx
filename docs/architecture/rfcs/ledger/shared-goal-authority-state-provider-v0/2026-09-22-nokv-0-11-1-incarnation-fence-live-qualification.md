# NoKV 0.11.1 incarnation fence, live qualification on a reproducible stack

Non-normative for the shared Goal authority model: it moves no qualification
hold and edits no RFC clause. It records the live re-measurement of the
publication fence that [#4774](https://github.com/loopx-project/loopx/pull/4774)
connected, on a stack anyone can bring up from one command, so that the hold
removal the RFC still lists has a citable record instead of a contributor's
scratch directory.

- **What the change was.** #4774 (approved head `9cffc78be`, squash-merged as
  `be7789fd95` on 2026-09-20) makes every `NoKVAuthorityStore` publication carry
  `expected_workspace_incarnation_id`, the incarnation the envelope was read
  under. NoKV 0.11.1 evaluates that fence atomically with `expected_generation`
  before any operation row, artifact revision or object exists and refuses a
  stale incarnation with the typed `WorkspaceIncarnationMismatch`, which the
  helper maps to `failed/store_identity_mismatch`; the helper pins SDK 0.11.1 and
  admits only a wheel that names the fence parameter and exports the typed
  refusal. The approving review kept the profile hold
  `atomic_workspace_incarnation_publication_fence` and Appendix A's wording
  because the maintainer had no live owner to rerun the write-producing probe.
- **What made the re-measurement reproducible.** NoKV-Lab/NoKV#518 adds
  `scripts/workbench/loopx_stage2a_stack.py`, which starts an isolated etcd
  member, a digest-pinned RustFS container (or a `moto` S3 server without
  Docker), provisions one owner, creates one workbench and writes the client
  configuration and environment file that `env:nokv_legacy` and
  `env:nokv_authority` read. This entry's stack: NoKV `590d3a4bdc` (the
  `v0.11.1` tag; owner built from that commit by the script's `--build`,
  SHA-256 `1c468a7b…`), the released `nokv==0.11.1` macOS arm64 wheel (SHA-256
  `e33f318e…`, checked against the release `SHA256SUMS`), etcd 3.7.1, LoopX
  `main` at `4bed6ed3d` with a clean tree, Node 22.22.3 (the runtime CI uses for
  the Stage 2C jobs). Measured 2026-09-22.
- **Positive rows.** `s0.nokv_live_matrix` and `s2a.nokv_live_qualification`
  pass. The qualification report carries 15 checks, all passed, including
  `stale_incarnation_fence_rejected` (a raw publication of the generation-1
  envelope fenced on a different incarnation is refused typed) and
  `stale_incarnation_fence_left_generation_unchanged` (generation stays 1 and
  the workbench identity is unchanged); final generation 3, SDK `0.11.1`, API
  `1`. The complete 23-row ladder on the same stack: 22 pass,
  `s2b.postgresql_conformance_live` unverified (no PostgreSQL configured),
  `s2c2.sustained_parity_soak` pending as declared; privacy scan 0 violations.
  The `moto` fallback stack passes `s2a` with the same 15 checks.
- **Negative pairing on the same stack.** With the released `nokv==0.11.0`
  wheel, `s0` still passes (the matrix does not pin the SDK) and `s2a` fails
  typed: the probe reports `nokv_transport_protocol_failed` because the helper
  refuses the wheel at admission, before any client is constructed; no
  publication reached the owner.
- **A macOS-only ladder condition, recorded because it cost a run.** Under
  macOS's default `TMPDIR` (a `/var/folders/…` symlink) all eleven `s2c2` rows
  fail with `shadow_management_state_invalid`: the TypeScript side digests the
  real path of the runtime root (`realpathSync`) while the Python side digests
  `os.path.abspath`, so the two `source_root_digest` values differ. With a
  symlink-free `TMPDIR` the rows pass under Node 22.22.3 and Node 26 alike;
  Linux CI has no such symlink and is unaffected. This is a LoopX condition,
  not a NoKV one, and this entry only records it.
- **What this does not establish.** The incarnation rotation itself is not
  exercised here: NoKV exposes no client-side retire or recreate verb, so the
  probe proves only that a stale fence is refused against the current
  incarnation; the rotation is covered by NoKV's executor tests in
  NoKV-Lab/NoKV#514. One owner, one node, one workbench: nothing about
  availability, failover, restart or restore recovery, capacity (see the
  2026-09-19 entry) or authenticated transport. The profile hold stays until
  the maintainer decides; the RFC header and Appendix A still say 0.11.0 while
  the helper, ladder and probe pin 0.11.1, an inconsistency this entry records
  and does not resolve.
