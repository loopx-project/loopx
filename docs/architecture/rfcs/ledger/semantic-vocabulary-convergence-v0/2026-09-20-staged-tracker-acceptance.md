# Staged tracker acceptance and conditional field retirement

Current readback: [registered source treatment](2026-10-12-registered-source-treatment.md). The observations below retain their original baseline; they are not current PR status.


Status: **proposal**, not a completed migration or approval record. Baseline:
`36134771355f05c9bcc5657ce739bb86426383dc`. Delivery status and the ordered
work plan belong to [#4447](https://github.com/loopx-project/loopx/issues/4447);
[discussion #4738](https://github.com/loopx-project/loopx/discussions/4738)
provides the slice specifications and corrections. Section 11 carries the
proposed M3 contract change; this ledger is its evidence record.

The previous tracker required deletion of a first field as proof of usefulness.
That confuses a means with an outcome. At this baseline,
`protocol_action_packet` still has live writers and compatibility consumers;
[#4794](https://github.com/loopx-project/loopx/pull/4794) now implements the
writer retirement but is not merged into this baseline. Its semantic-field
projection remains necessary for Envelope/signature compatibility. A low
syntactic reader count proves neither that the field is redundant nor that
historical readers can be removed. A derived compatibility projection can be
retained while duplicated construction or independent decision authority is
eliminated.

The proposed tracker stages are:

1. Verify the shipped guard boundaries and the selected production-path
   simplifications on an integrated tree. Fresh packet construction must not be
   duplicated: when the PR-05 migration is adopted, new quota/live/paused/recovery
   outputs carry no packet while the historical v0 reader and signature projection
   remain; without that migration, the retained field is rendered at most once at
   the final live stage. Settled work must not be reconstructed or spent again,
   unadmitted selection must not acquire settlement capability, and inbox source
   precedence must remain intact. Preserve legitimate workspace repair and
   independent capability effects. Local branch tests do not certify the
   integrated result.
2. Close sources for the 26 registered vocabularies without pretending every
   value originates in an internal producer. On the baseline, six runtime
   producer entries plus one compatibility-only entry account for the reported
   F1/F2 7/26; 19 cross-runtime vocabularies remain outside that verification.
   Trace settlement/receipt, workspace/Todo, then the remaining bounded owners.
   Internal production needs a real witness; external input needs decoder
   acceptance/rejection evidence; compatibility needs retained scope and exit
   conditions. A local-only classification needs proof it does not cross the
   boundary. Labels alone, or renaming unknown entries, earn no evidence credit.
3. Review the same final revision, record passed/failed/untested boundaries and
   close only after the revised acceptance is approved and its evidence is met.
   Exact protocol formats and signatures stay protected. Whole-program analysis,
   all-field deletion, F6-wide persistence proof and unregistered vocabulary
   governance are outside this bounded delivery tracker.

This does not change F1/F2 formulas or enforcement domains, add a source schema,
lower budgets, or declare the 19 entries verified. Source classification and
production verification must remain separate claims. Implementing any new
source-evidence contract needs its own review and negative cases; the already
merged settlement-binding witness is one pilot, not the whole second stage.

M3 remains available when an actual consumer/authority simplification justifies
it and a versioned compatibility plan exists. Until then, retain the field and
its checks. Do not keep the tracker open solely to reach zero field names, and
do not close it by relabeling missing source evidence as documentation work.
