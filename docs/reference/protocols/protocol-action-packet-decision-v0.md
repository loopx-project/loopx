# Protocol Action Packet Migration Contract

## Release Boundary

[PR #4794](https://github.com/loopx-project/loopx/pull/4794) retires
`protocol_action_packet` from freshly constructed quota decisions. The cutover
is the first official LoopX release whose source contains this change; source
and prerelease builds containing it use the same behavior. Published v1.1.0
artifacts are not changed or silently backported. The release tag identifies
that source boundary; this PR does not create a release or choose its date.

The change applies to normal quota, paused quota, live required-read and
capability-intent projection, and unsettled-host recovery, including full JSON
and Markdown output. It is independent of the opt-in TurnEnvelope view. There
is no new runtime flag, replacement summary field, or second decision owner.
The existing Envelope and signature schema identifiers stay at v0: the packet
was already optional to their readers. New signatures omit its capsule witness;
they need not equal signatures of older packet-bearing outputs.

## Supported Consumers and Upgrade Order

The supported consumer set for this migration is explicit:

| Consumer | New output | Stored v0 input |
| --- | --- | --- |
| Bundled quota CLI and Markdown renderer | Read `interaction_contract`, `work_lane_contract`, `scheduler_hint` and exact CLI actions; no legacy summary line | Continue displaying the packet when a stored decision contains it |
| TypeScript Effect reader and Python bridge | `protocol_summary=null`; typed obligations and next effects remain authoritative | Keep the supplied summary as an observation, never as execution authority |
| Python/TypeScript TurnEnvelope builders/readers | No `contract_capsule.protocol_action_packet`; other signed dimensions remain | Preserve verified reconstruction, residue and opaque-summary fallback |
| Bundled host authority extraction | Verify the canonical document and hashes before accepting the action | Verify the stored document and hashes without rebuilding its source |

Upgrade readers before changing producers. LoopX v1.1.0 is the tested rollback
reader baseline and already accepts missing packets. A client that requires
this field or parses its summary must migrate to the typed contracts above
before adopting the new producer, or remain on v1.1.0 until adapted. Such a
client is not automatically covered by this migration's compatibility claim.
No compatibility promise is made for unidentified external clients, changed
private forks, or older executable releases that have not been qualified.

## Historical Format Support

Support is bounded by format rather than record age: valid
`protocol_action_packet_v0` observations and their existing v0 Envelope/capsule
representation remain readable while the v0 reader contract is supported.
There is no scheduled removal of these readers in this migration. A future
breaking protocol revision must separately declare its reader-removal and data
migration policy; this writer removal cannot authorize it. Storage retention
policies and execution-time identity/permission checks remain independent.

Keep `protocol_action_packet_fields()`, ordered summary reconstruction,
`verified_with_residue`, and `unverified_retain_summary`. Do not rewrite stored
records, regenerate their signatures, or replace an opaque historical summary
with today's action text. Missing/mismatched signatures and changed signed
fields still fail host admission. Successful historical readback does not
re-authorize a stale task or bypass fresh receipt, lease or capability checks.

The original field selection and ordered assembly are owned by the TypeScript
Turn reader. Python retains the compatibility API and shared user/primary-action
providers, including its original scalar, whitespace and codepoint conversion.
The ordinary builder carries those inputs in its existing coarse transaction;
packet-free inputs do not load or run the historical providers. Original fields
and compact-derived fields still remain distinct inputs to residue verification.
The Python API preserves original metadata values and insertion order; crossing
JSON must not turn a rendered `1.0` into `1`. Its standalone compatibility call
uses the same reader operation, not a second field-selection implementation.

## Qualification and Rollback

The committed historical fixture is produced by actual v1.1.0 source
`607c11d75`, with synthetic public-safe input. Its stored envelopes and signature
hashes are immutable test inputs: current readers must accept valid examples
and reject tampering without regenerating the expected signature. The ordinary
compatibility fixtures additionally cover absent, v0, opaque and residue forms,
quiet waits and user gates. These examples qualify the specified formats and
readers, not an unknown production archive.

The repeatable version check exercises eight fresh packet-free decisions
(normal, paused, operator gate and exhausted, each ordinary/settled) plus the
four frozen release records through actual v1.1.0 reader/host code. It checks
canonical source/envelope agreement, host admission and unchanged input records:

```bash
# Prepare this pinned checkout and its Node dependencies once; no live state is used.
git worktree add --detach ../loopx-v1.1.0 v1.1.0
npm ci --prefix ../loopx-v1.1.0 --ignore-scripts
uv run --extra test python scripts/verify_protocol_packet_migration.py --reader-checkout ../loopx-v1.1.0
uv run --extra test python -m pytest tests/control_plane/test_protocol_packet_history.py tests/control_plane/test_protocol_packet_retirement_cli.py
```

The verifier rejects a different or modified reader revision; it does not fetch
or install anything itself. Source/envelope canonical documents and host
admission must agree.
An installed wheel is checked for packaged TS/JSON resources, the real bridge,
normal/paused output and signed host admission. Real CLI replay and the combined
required-read/capability-intent path are regression tests in this PR.

For rollback, stop starting new host turns, retain all runtime records unchanged,
and switch producer and reader together to the tagged v1.1.0 baseline. That
producer emits the old packet again; the old reader continues to accept the
packet-free records covered above. This is rollback of this field migration,
not certification that v1.1.0 can undo unrelated protocol or storage changes.
Reverting the writer-removal commit is also possible after resolving intervening
code changes; it must not rewrite stored signatures or receipts.

## Operating Boundary

Routine quota/status/heartbeat routing stays deterministic and makes no model
or external-provider call to replace the summary. Cold summarizer experiments
remain explicit, isolated and sidecar-only; they must not persist raw stderr,
private session traces or credentials. No other legacy field is retired by
this contract. PR approval accepts this scoped migration; normal maintainer
merge/release controls remain, and #4447 has its own remaining obligations.
