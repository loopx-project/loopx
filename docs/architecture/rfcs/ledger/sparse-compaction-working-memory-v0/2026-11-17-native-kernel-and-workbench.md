# Native kernel and Workbench experimental profile

- **RFC:** [Sparse compaction working memory v0](../../sparse-compaction-working-memory-v0.md), §5.5.
- **Scope:** authorized experimental kernel implementation and a device switch;
  AI-selected schema, limits and integration remain reviewable implementation choices.
- **Baseline:** main `b9f2e2a516b58e62cbb0da6978625388322b5a90`;
  [PR #8872](https://github.com/loopx-project/loopx/pull/8872), not installed or promoted by this record.
- **Mirror:** [中文](2026-11-17-native-kernel-and-workbench.zh-CN.md).

## Delivered boundary

The existing Turn-envelope dispatcher calls the TypeScript memory selector.
A private Python IO adapter reuses write-once captures, exact readers and the
native journal lock after ordinary lifecycle handling. Pre/PostCompact deduplicate
one source/view; SessionStart(compact) validates it before additive recovery.
Native lifecycle, Progressive/Delta selection, original source bytes and pending
reads retain their owners. The new versioned schema is derived context data;
the registered machine namespace extends configuration, not work authority.
No capability/provider id or semantic generation service is added.

Workbench exposes an independent default-off device switch through existing
configuration preview/apply/readback. Disablement removes the namespace for older
full-settings editors and retains immutable references. The operator reference
consumes the canonical desktop off/on and narrow-screen guide images; generated
execution logs and other captures remain outside Git.

## Verification and limits

- Pure TypeScript tests preserve exact failure/unverified distinctions, pointer-only
  long facts, source/scope revisions and matching canonical Todo detail.
- Private-capture and actual hook-CLI tests cover duplicate hooks, exact reader
  expansion, journal/source immutability, absent/off and context-delivery-off,
  unsupported profile, child/foreign session, corrupt locator, changed/missing
  source, unsafe permissions and symlink rejection. Existing configuration/API
  inventory tests include the independent machine-only namespace.
- The real native `0.167.0-alpha.3.2` app-server runs with an isolated home and
  synthetic loopback inference. Two tests (off/on) drive automatic compaction
  and inspect all four actual model submissions. Enabled recovery contains the
  complete short JSON structure and exact negative/unfinished note; off retains
  ordinary behavior. Native-managed policy remains in force; only exact fixture
  hooks receive fixture trust. This is one qualified host/version and one
  compaction per treatment, not a task-effectiveness experiment or installed-user acceptance.
- The packaged Workbench browser smoke uses the real Chat backend and an isolated
  empty registry. It checks enablement, reload, a genuinely rejected stale CAS
  request, unchanged confirmed state, disablement/removal, Chinese readback and no
  work dispatch from preference saves. Desktop 1440×1000 and narrow 430×932
  views retain the switch and have no horizontal page overflow. The populated
  capability list includes other machine and mixed-scope entries; no real Goal
  or user data is used. The existing narrow selector occupies space above the
  editor; the setting is still visible in the first viewport.

## Bounded mechanism cost

One synthetic selected Todo plus 400 repeated supporting sentences produced an
18,205-byte original capture and an 844-byte memory artifact. Seven same-source
preparations measured 7.48–11.52 ms (median 7.81 ms); one restore measured 6.96 ms.
Ordinary recovery was 3,169 bytes; the memory added 1,297 bytes. No additional
model call occurred. These are local mechanism measurements with generated local
references, not representative workloads, token counts, a latency distribution
or total-cost savings. Restoration remains additive.

## Remaining acceptance

This supplies bounded A2/A3/A4/A6/A8/A9/A10 evidence; it does not close the full
matrix or M1–M3. Conversation-only semantic knowledge, repeated/restarted
compactions across a declared cleanup lifetime, broader host/version coverage,
Lark/CLI settings journeys and complete-cost/outcome benefit remain unqualified.
No source deletion, default activation, paid run, installation or merge follows
from this record. The next outcome experiment must freeze B0/T1 conditions and
acceptance before execution; absence of benefit can keep the feature disabled.
