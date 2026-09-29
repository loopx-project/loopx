# OpenViking project peer provider

LoopX includes a thin, opt-in OpenViking provider for project-scoped semantic
preferences. One canonical project maps to one reserved OpenViking peer. Git
worktrees and fresh clones of the same `origin` therefore share a memory scope,
while another repository resolves to a different scope.

This adapter does not implement memory extraction, ranking, update, or
supersede semantics. OpenViking owns those behaviors. LoopX only derives the
project peer, performs bounded `find` calls, and returns the existing semantic
preference provider protocol for a function-owned application and receipt.

The separate [explicit Agent preference lifecycle](../README.md#explicit-agent-preferences)
uses a narrower Goal/Agent scope and supports immediate current-state correction.
This project-peer recall adapter is not yet a replacement for that lifecycle.
See the [memory-service qualification requirements](../README.md#memory-service-providers-and-the-next-integration-boundary)
before adding Agent-scoped writes, retirement or provider migration; optional
recall activation does not export the local explicit-preference journal.

## Scope contract

- Identity comes from the normalized Git `origin`, never the checkout path.
- A non-Git project must provide a stable `--loopx-project-id`.
- Recall targets the exact project peer by default.
- Each `find` binds the OpenViking request actor to the derived project peer.
- User-global memory is available only through `--include-global-fallback`.
- The default budget is one `find`; explicit global fallback needs at least two.
- Only concrete preference nodes under the selected target are returned.
- OpenViking failures remain subject to the outer surface's `fail_open` or
  `fail_closed` policy.

Inspect the local scope without contacting OpenViking:

```bash
loopx semantic-preference openviking-provider \
  --project . \
  --user-space default \
  --describe-scope
```

The output contains the peer id and target URIs, but not the repository URL or
local checkout path. It also contains a bounded corpus inventory. The project
peer preference corpus is primary; user-global preferences appear only when
the caller explicitly enables global fallback.

An OpenViking agent integration can use the returned `peer_id` when adding a
user message to a session. For an isolated native write, create that session
with self memory disabled, peer memory enabled, and the desired memory types
allowed. Both the message peer and the request actor must be the same derived
project peer. A message `peer_id` alone identifies the speaker; it does not
authorize an extractor running as a different actor to update that peer.
OpenViking then owns extraction, update, and supersede semantics inside the
selected corpus.

Do not treat a completed extraction task as sufficient write evidence. A
maintenance closure for this provider requires all of the following:

1. The task reports the expected add or update count and memory diff.
2. Pending embedding or indexing work reaches zero without errors.
3. A direct L2 read returns the new semantic content.
4. A scoped `find` through the same project-peer provider recalls that content.

If a trigger does not require a semantic change, emit a compact
`no_write_rationale` maintenance receipt instead. Never persist raw memory in
the receipt.

## Repository template versus semantic preference

For PR descriptions, the repository's current
`.github/PULL_REQUEST_TEMPLATE.md` is the authoritative hard structure. Read it
from the working revision when building the artifact. OpenViking stores only
soft semantic preferences for how to fill that structure, such as reviewer
language, useful detail, and risk-based validation. Do not copy the template
body into OpenViking: doing so would create a stale second source of truth.

When the repository template changes, assess the project-peer preference
corpus because its interpretation may need to change. When explicit user
feedback changes the prose preference, update that corpus through OpenViking's
native extractor and complete the four-step readback above.

## Local-private hook config

First activate the bundled provider. This command registers the preinstalled
entrypoint only after a read-only `ov status` doctor succeeds; it does not
install or configure OpenViking:

```bash
loopx extension install \
  --bundled openviking-semantic-preference \
  --execute \
  --format json
```

Keep the hook config ignored and untracked. OpenViking service configuration
remains local:

```json
{
  "schema_version": "semantic_preference_hook_config_v0",
  "enabled": true,
  "provider": {
    "id": "openviking_semantic_preference",
    "extension_id": "openviking-semantic-preference",
    "args": [
      "--project",
      ".",
      "--user-space",
      "default",
      "--max-find-calls",
      "1"
    ]
  },
  "surfaces": {
    "issue_fix.pr_description": {
      "query": "PR description structure and validation preferences",
      "limit": 3,
      "failure_policy": "fail_open"
    }
  }
}
```

Expose `ov` on `PATH` and keep OpenViking's normal local configuration ready
before activation. `loopx extension doctor openviking-semantic-preference
--execute` repeats the read-only `ov status` probe. Hook `args` may still carry
project-scoping options; they do not alter the manifest-owned doctor.

`loopx semantic-preference openviking-provider` remains a lazy delegating
compatibility alias: ordinary LoopX CLI startup does not import the provider,
and the alias loads it only when invoked. New integrations should use the
extension activation and `extension_id` binding so enable, disable, upgrade,
rollback, API compatibility, permission, and doctor state remain inspectable in
one lifecycle.

The consuming function remains the final application boundary. For Issue Fix,
`build_issue_fix_pr_description()` owns one recall, fail-open preservation,
preference attribution, the compact application receipt, and propagation of
the provider's corpus inventory and maintenance guidance. It does not perform
an automatic write or add a second provider call.
