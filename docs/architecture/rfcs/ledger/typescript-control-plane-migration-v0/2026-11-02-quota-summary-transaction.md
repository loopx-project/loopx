# Quota summary transaction cutover (2026-11-02)

The existing `todo.quota_planning.project` request now returns the complete
user or project-asset quota summary. TypeScript owns source selection, lane
composition, counts, limits, warnings and display projection in process.
Python gathers compatibility facts and validates and dereferences the response.
Source counts, metadata, selected Vision proofs, closure overlays and warning
fields retain their original Python values through owner-selected references;
planning and display rows retain their existing JSON normalization.

The pre-cutover complete-output fixtures remain the parity guard. For both
summary variants, every matrix path previously using 1–5 crossings now uses
one; non-dict input stays at zero and owner refusal stays at one without fallback.
No handler, authority or persisted format is added. The planning-only Python
codec remains for direct planning consumers; remove it only after their last
caller migrates. Payload/status compaction and source helpers retain other
callers. No facade exit or latency improvement is claimed.

The earlier Phase 1 transport-loss finding remains valid for plain JSON;
source references resolve that blocker without changing the pinned outputs.
This source-checkout slice does not qualify installed or long-horizon benefit.
Rollback is the code diff; no state migration is needed.
