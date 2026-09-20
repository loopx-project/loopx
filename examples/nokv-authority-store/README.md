# Stage 2A NoKV AuthorityStore candidate qualification (TEST ONLY)

This directory contains an explicit, write-producing single-node conformance
probe for the Stage 2A `NoKVAuthorityStore` candidate. It is **TEST ONLY**.
LoopX does not select NoKV by importing this code, and a successful run does
not connect a runtime shadow, run a multi-Agent canary, or flip an authority
source.

The integration priority remains:

1. the native, full NoKV CLI as the primary operator and production surface;
2. the NoKV Python SDK as the secondary programmable surface; and
3. optional sidecars only as adapters around those surfaces, never as the main
   authority API.

This probe intentionally exercises the current Python SDK bridge because it is
the available raw byte-CAS seam. It always starts this checkout's reviewed
`NoKVJsonLinesTransport` and `nokv_jsonl_helper.py`; it has no fake, skip, or
"unverified but successful" CLI path. The helper admits exactly NoKV SDK
`0.11.1` / Python API `1` whose `Client.publish_bytes` names
`expected_workspace_incarnation_id` and whose module exports
`WorkspaceIncarnationMismatch`; the successful report repeats both version
values. A `0.11.0` wheel is refused by the version pin (`invalid_config`), and a
wheel labelled `0.11.1` without that publication surface is refused as
`nokv_sdk_capability_mismatch` before any client is constructed.

## What it proves

Against one **already existing** NoKV workbench, the probe starts three
independent helper processes and verifies:

- the selected tenant/goal path is initially absent and can be created;
- a publication of the generation-1 envelope fenced on a stale workbench
  incarnation is refused typed (`store_identity_mismatch`) before any row or
  object exists: the stored generation stays 1 and the workbench identity is
  unchanged (every LoopX publication carries the incarnation it was read from);
- the stored path generation advances from 1 to 2 under exact generation CAS;
- after the generation-2 CAS lands, an injected response loss is reconciled
  from the durable authority envelope and operation receipt rather than from
  the lower-layer response;
- every successful CAS response is accepted only after a fresh read proves the
  exact transaction in the current workbench incarnation;
- two writes released together against generation 2 produce exactly one
  generation-3 winner and one typed conflict;
- the losing operation does not acquire a durable receipt; and
- a third, freshly opened transport reads the winning envelope, its complete
  three-entry history, the response-lost operation receipt, and the retained
  winner receipt.

If the SDK, helper, workbench, backend, CAS, or independent readback cannot be
proved, the process exits nonzero. The normal test suite uses deterministic
fakes only to test this sequence and does **not** count as live evidence.

The probe proves the expected-incarnation publication fence only as far as a
single live owner can show it: a stale fence is refused typed and leaves the
generation untouched. It does not restore or recreate the workbench (NoKV
exposes no client-side retire verb), so the incarnation rotation itself is
covered by NoKV's own executor tests, not by this probe. Success is still
accepted only after authoritative post-write readback in the current
incarnation: the fence removes the stale write, not the readback obligation.
The probe does not prove runtime shadow parity, a multi-Agent canary, authority
promotion, HA, failover, restart recovery, capacity, or performance, and it does
not create a workbench. A green run is Stage 2A single-node storage conformance
evidence only.

## Inputs

Use a current NoKV Python environment. Keep the client configuration in an
ignored local file; do not commit credentials. The helper accepts three routing
kinds and passes each to the matching `RoutingConfig` constructor of the
installed SDK: `etcd` and `static` (the 0.11.x release wheels) and `seeds`
(`{"kind": "seeds", "endpoints": ["IP:PORT", ...]}`, the NoKV
metadata-runtimes line, which names serving owners directly and drops the etcd
constructor). Static routing is valid for a single-node NoKV deployment; etcd
is not required by this probe. A routing kind the installed wheel cannot build
fails the open handshake with `nokv_sdk_capability_mismatch` before any client
is constructed, so a seeds configuration against a 0.11.1 wheel (or an etcd
configuration against a metadata-runtimes wheel) is reported as the wrong
wheel, not as an outage. The `ready` handshake echoes `nokv_protocol_schema`:
the SDK's `WORKSPACE_PROTOCOL_SCHEMA` when the wheel exports one, otherwise
`null` (the 0.11.x releases do not). The following shape is illustrative:

```json
{
  "root_id": "00000000000000000000000000000000",
  "routing": {
    "kind": "static",
    "endpoint": "127.0.0.1:7412",
    "logical_shard_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "object_namespace_id": "cccccccccccccccccccccccccccccccc",
    "placement_generation": 1,
    "owner_epoch": 1
  },
  "object_store": {
    "kind": "s3",
    "bucket": "qualification-bucket",
    "region": "us-east-1",
    "root": "/loopx-qualification",
    "endpoint": "http://127.0.0.1:9000",
    "access_key_id": "set-in-your-ignored-local-file",
    "secret_access_key": "set-in-your-ignored-local-file",
    "virtual_host_style": false,
    "skip_signature": false
  }
}
```

Configuration objects are exact-key contracts. An unknown top-level, routing,
or object-store key fails before an SDK routing config, object-store config, or
client is constructed. In particular, a misspelled explicit credential cannot
silently fall through to NoKV's ambient provider chain. Intentionally omitted
optional S3 credential fields retain the NoKV SDK's normal behavior.

Pass only the absolute path to the Python executable that resolves the qualified
NoKV SDK. The probe itself fixes the remaining argv to the interpreter isolation
flag `-I` followed by the reviewed helper in this checkout; callers cannot
supply a wrapper argument or an alternate helper path, and `PYTHONPATH`,
`PYTHONHOME`, or user site-packages cannot redirect the `nokv` import away from
that executable's own environment:

```text
/path/to/nokv-python-environment/bin/python
```

Choose a fresh tenant/goal pair for every run. The probe refuses to overwrite
an existing authority envelope and deliberately leaves its three-generation
test envelope behind for inspection. Use a disposable qualification namespace
or remove it later with the native NoKV CLI according to that environment's
retention policy.

## Run

From the LoopX repository root:

```bash
node --no-warnings --experimental-strip-types \
  examples/nokv-authority-store/live-qualification.ts \
  --execute-live \
  --config-json /path/to/ignored/nokv-client.json \
  --python-executable /path/to/nokv-python-environment/bin/python \
  --tenant-id qualification-tenant-20260902 \
  --goal-id qualification-goal-20260902-01 \
  --workbench existing-qualification-workbench
```

`--execute-live` is mandatory and is checked before any helper starts. Exit 0
means every listed live check passed. Any unavailable, failed, ambiguous,
unfenced, pre-existing, or unreadable state exits nonzero with a compact JSON
reason; provider stderr, endpoints, credentials, and raw SDK errors are not
copied into that result. A successful JSON report includes
`"qualification_scope":"stage_2a_single_node_store_conformance"`,
`"nokv_sdk_version":"0.11.1"`, and `"nokv_api_version":1`. The two version
fields are the helper's admission constants: the helper refuses to open a client
for any other SDK version or API version, so a successful report implies them,
but they are not values read back from the NoKV server. The report is Stage
2A/helper-admission evidence only, not runtime-shadow, canary, HA, or
production-readiness evidence.
