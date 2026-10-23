# Protocol Action Packet Retirement Contract

## Boundary and consumer upgrade

[PR #4794](https://github.com/loopx-project/loopx/pull/4794) stopped emitting
`protocol_action_packet` in fresh full quota decisions, including normal,
paused, live required-read/capability-intent and unsettled-host recovery paths.
[PR #7161](https://github.com/loopx-project/loopx/pull/7161) ends the retained
reader compatibility. This is an intentional breaking input-format change,
not a new projection flag. Source builds containing this change reject the old
format; the first official release containing it establishes the release
boundary. This proposal neither publishes nor installs that release.

| Consumer | Current supported input | Retired input |
| --- | --- | --- |
| Quota CLI, full/compact projection and Markdown | Typed interaction, lane and scheduler contracts; exact actions | Reject any top-level `protocol_action_packet` before optional projection/fallback |
| TypeScript Effect reader and Python bridge | Same typed quota contracts; quota `protocol_summary=null` | Reject old packet before interpreting execution facts |
| TurnEnvelope builder, signature reader and capture binding | Packet-free decision/capsule and existing signature dimensions | Reject old packet or capsule witness before conversion, hashing or rebinding |
| Bundled Host authority extraction | Verify modern canonical document and original hashes | Reject old witness; never drop it to make a signature pass |

Presence is decisive, including an empty object, null or malformed value. The
error is `unsupported format: protocol_action_packet` and directs the caller
to obtain a fresh typed quota decision. There is no automatic legacy fallback,
owner question, new admission gate or silent migration. Clients still emitting
this field must upgrade to the existing typed contracts before using these
readers. Unidentified external clients are not implicitly qualified.

This retires a specific optional format, not all v0 protocols. Packet-free
TurnEnvelope v0 records keep their original signature interpretation. Identity,
permissions, leases, required reads, settlement/replay and exactly-once effects
retain their own checks. The shared Effect observation field `protocol_summary`
stays because the distinct turn-result family uses it; quota always yields null.

## Removed machinery and retained history

Ordered legacy field selection/rendering, reconstruction, transport inputs,
residue and opaque-summary fallback are removed from production. No historical
packet, witness, receipt or signature is rewritten, stripped or re-signed.
Keeping original history does not require accepting it as current execution
input. Historical artifacts can be inspected with a compatible isolated older
reader; that does not authorize replay against live current state.

The former v1.1.0 acceptance verifier is retired with its compatibility promise.
The frozen synthetic v1.1.0 records from source `607c11d75` remain byte-for-byte
unchanged: tests now require explicit rejection of the packet-bearing records
and exact original signature/Host acceptance for the packet-free record.
Historical test results remain evidence of the earlier supported contract,
not current reader qualification.

## Qualification and recovery

```bash
uv run --extra test python -m pytest tests/control_plane/test_protocol_packet_history.py tests/control_plane/test_protocol_packet_retirement_cli.py tests/test_turn_envelope.py
node --no-warnings --experimental-sqlite --experimental-strip-types --test tests/control_plane_ts/protocol_action_packet_compat.test.ts tests/control_plane_ts/turn_envelope.test.ts
```

Qualification covers the real Python-to-TypeScript bridge, CLI projection and
Markdown, signed Host extraction, capture binding and modern replay. Missing,
mismatched or forged signatures and changed action/spend dimensions still fail;
input objects and the frozen fixture remain unchanged. Real File/SQLite CLI and
installed disposable wheel checks qualify their tested boundaries, not a live
installation or complete private archive.

On unsupported input, preserve the original artifact and reacquire a current
quota decision through the normal entrypoint. Do not remove the retired field
or recompute old signatures. Code rollback requires a matching previously
qualified producer/reader pair and independent storage compatibility checks;
this format contract does not certify a whole-store downgrade to v1.1.0.

## Operating boundary

Quota/status/heartbeat routing remains deterministic, without a model or
external-provider call to replace the summary. Historical cold-path comparison
examples are synthetic experiments, not supported execution readers or current
quota-size evidence. No other legacy format is removed. Maintainer review,
merge and release authority remain separate from this source proposal.
