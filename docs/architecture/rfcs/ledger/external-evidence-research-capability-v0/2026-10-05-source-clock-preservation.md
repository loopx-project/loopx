# Source clock preservation in the deepresearch ledger (2026-10-05)

Recorded for [#5609](https://github.com/loopx-project/loopx/pull/5609). The RFC
body keeps only its pointer to the
[2026-10-02 method checkpoint](2026-10-02-public-github-method-delivery.md); this
later record lives here per the [ledger convention](../README.md).

[中文镜像](2026-10-05-source-clock-preservation.zh-CN.md)

New external-evidence ledger rows preserve the normalized receipt's `accessed_at`
and `publication_date` (including unknown publication). Their separate
`recorded_at` marks ledger insertion; inserting or replaying a capture does not
establish a fresh source read. Exact receipt replay does not rewrite existing
rows, including legacy rows. Ordinary `deepresearch add-source` retains its
existing local read clock.

This is clock preservation, not provider execution or publication attestation.
Canonical TypeScript receipt/admission validation remains the authority, and the
existing CLI and shared readback consume the same receipt; this persistence change
does not qualify provider mounting or a packaged frontend/Lark journey.

Passed: paired base/head real-CLI replay with historical access times, `Z` and
offset precision and null or known publication dates (19 fixtures on each side,
none preserving source clocks before, all 19 after); tampered-clock refusal and
recovery of the original receipt before admitting; read-only isolation; exact
replay of an existing row, including a legacy row, without mutation; refusal of an
unadmitted reference; two concurrent real CLI processes inserting the same claim
once; and an unchanged ordinary `add-source` row shape. Focused Python and
TypeScript suites, the semantic inventory smoke, the public boundary scan and
`git diff --check` pass.
