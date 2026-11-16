# C1 context source-fidelity checkpoint

中文镜像：[同名检查点](2026-10-29-context-source-fidelity.zh-CN.md)。

C1 integrates main `b201da8bab305d053866aa0d4516b97ba4a4499e` and reuses normal
[PR #7661](https://github.com/loopx-project/loopx/pull/7661), source
`4bb530c0f7b22950ba17300dacdc5a23debedf12`. This is proposed source adoption,
not a merged, installed or model-qualified rollout. The separate journal repair
[#7657](https://github.com/loopx-project/loopx/pull/7657) remains a prerequisite
through its existing owner.

The generic context codec also normalized original numbers in cold delivery and
full recovery. Warm equality could omit a change between `1` and `1.0`, negative
zero, or distinct large integers. Seven of nine independent exact-number cases
fail on the unchanged baseline. Ordinary dictionary equality concealed part of
that failure.

The existing TypeScript codec now compares sparse original-number identity and
returns the existing source-edit format. Python applies those edits to original
values; it does not select sources, diff contexts or decide recovery. The same
source-identity helper serves C1 acceptance sharing and the codec. Full bodies,
primitive/grouped literals and reordered sources retain origin. Recovery rejects
normalized-equivalent damaged deltas against the supplied current capture.
Direct JavaScript callers retain detached results. No native hook, source
selection, authority, admission, settlement or historical digest is changed.

The independent slice passes 76 Python checks, including actual File/SQLite CLI
cold/warm/missing-baseline paths, and 90 TypeScript checks. Another 194 capture,
selection, reader and lifecycle regressions pass; 46 native-binary opt-in cases
remain skipped. Typecheck, Ruff, public scan, semantic advisory and standard local
premerge pass. C1 integration results are pinned on its PR; earlier cohorts stay
at their original revisions. This is reconstruction from the supplied source,
not proof that the old semantic hash distinguishes every numeric source.

Matched synthetic component costs cover five codec RPCs, not a complete model or
Host Turn:

| Body | Request parameter JSON, base → repair | Response result JSON, base → repair |
| --- | --- | --- |
| 8 KiB | 70,855 → 72,040 bytes | 41,277 → 5,183 bytes |
| 64 KiB | 524,616 → 525,801 bytes | 300,569 → 5,183 bytes |

Both larger samples retain a warm delta; model-visible growth is the restored
numeric distinctions. With no body, response metadata grows from 2,400 to 3,560
bytes. Sequential local latency samples move in both directions; memory and full
Host/model/human-attention costs remain unqualified. Request limits, original
quota comparison and the frozen 25,862 > 13,000 failure are unchanged.

Continue with numeric hash identity across all actual consumers and remaining
bridges before writer/caller cutover and historical-reader exit. Reuse this codec
and the journal owner rather than adding caller-specific number patchers. Keep
original-quota single-session accepted outcomes/full cost open; optional two-session
work remains separate.
