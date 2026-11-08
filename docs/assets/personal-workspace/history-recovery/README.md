# Original history recovery after repeated transport loss

Ordinary task: open the existing bound conversation and read its full reply.
Previously, a lost later page discarded every verified page. Automatic retry
started from page zero, repeating already completed work. The same reader now
keeps a browser-memory checkpoint and continues from the failed cursor under
the existing retry schedule. No extra user action, new timeout or server-side
state is introduced. Reload and explicit refresh remain fresh reads.

The immutable base `4a4fe1495b89eac3af9bd1f956183748c2944909` and the changed
reader were challenged with transport interruptions on offsets 200 and 400.
The base requested `0, 200, 0, 200, 400, 0, 200, 400`; the changed reader requested
`0, 200, 200, 400, 400`. Both eventually read the same complete source; only
the changed reader avoids the repeated prefix. The regression belongs to
`npm run smoke:bound-conversation` in `apps/presentation/dashboard`.

These captures use the built Chat bundle and the production HTTP/SQLite/Core
binding owner in a disposable synthetic runtime. Its 464 original rows include
a 20,000-character body. The packaged UI recovered automatically through both
interruptions, displayed the complete body once, retained the draft, and kept
Send disabled until the complete history was verified. Independent store
readback remained identical, with no message/Session creation or model Turns.
The focused HTTP smoke also challenges source revocation, permission denial,
changed source identity and inconsistent prefix digest.

- `unavailable.png` (1440×1000): incomplete history is unavailable, with the
  retained draft, an existing retry action and disabled Send.
- `complete.png` (1440×1000): automatic recovery exposes the original tail and
  enables Send. The long body is checked in the DOM; this tail viewport alone
  does not show the full body.
- `narrow.png` (390×844): the same recovered conversation and draft fit without
  horizontal overflow. No layout or wording change is claimed.

All imagery is synthetic and contains no live conversation or credentials.
PNG pixels and metadata were inspected. This qualifies the packaged browser
reader, not the installed native App, live IM transport, server latency or
business completion. Current source authorization and every page's frozen
prefix/identity checks remain mandatory; checkpoints do not grant authority.

The server also selects the current page's immutable result keys before reading
answer bodies and delivery sidecars. This matters when an unmatched legacy row
requires the existing exhaustive route fallback: off-page results no longer
need to be deserialized. The fallback still examines every candidate route;
Session/source checks, current status validation and unreadable-state handling
remain unchanged. No delivery state or authorization is cached between reads.

A controlled production HTTP/SQLite/Core fixture contains 404 messages, 96
request routes and 768 complete 20,000-character results. In alternating
base/head/head/base runs (seven uninstrumented samples each), the first page's
result-file reads fell from 1,536 to 80. Per-run median HTTP latency was
0.17–0.26 seconds on the base and 0.04–0.05 seconds after the change. Both arms
returned the identical complete history and left the original source unchanged.
These local synthetic measurements are neither a production p95 nor evidence
that other service contention is fixed. The durable HTTP and fallback
regressions live in `tests/test_manager_context_roundtrip.py`; they also retain
late verification changes, malformed state and the original full long body.
