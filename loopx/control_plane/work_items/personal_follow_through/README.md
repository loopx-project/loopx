# Personal follow-through: experimental TypeScript CLI

This opt-in source-checkout workflow reads one selected Lark conversation,
proposes personal commitments, applies individually reviewed changes to canonical
User Todos, and reads those Todos back. It is a partial implementation of
[the proposed personal follow-through RFC](https://github.com/loopx-project/loopx/pull/5384),
not the completed desktop M1 journey. Nothing is enabled by installation.

## Ownership and supported boundary

The workflow composes existing work-item and canonical coordination owners.
The bundled optional provider is `loopx-lark`; its read adapter lives beside the
existing Lark extension. There is no new capability, parallel Todo database,
background scheduler, or Python bridge. The CLI and domain logic run on Node.js;
source access invokes the installed `lark-cli` executable without a shell.

This initial admission profile requires an **existing** private canonical local
store and a project-local object registry (`schema_version: "0.1"`) with:

- an absolute `common_runtime_root` matching the configured runtime;
- one matching Goal with an exact `ginst_` instance identity;
- explicit `activation_state: "active"`, or the existing activation object with
  explicit active state;
- no registered Agents in Goal, coordination, or spawn-policy registration.

The registry, profile, and review files must be owner-only regular files (0600),
and the runtime must be an owner-only directory (0700). The CLI refuses missing
stores, strict registry envelopes, unstamped Goals, shared/registered-Agent Goals,
and unsupported provider configurations. Do not rewrite an active registry to
bypass these restrictions. Extending registry admission belongs to the existing
registry owner and needs parity validation first. PostgreSQL is not qualified.

The Lark owner id is an operator-supplied open id, not an identity inferred from a
display name. Verify it in the selected tenant before enabling the profile. Only
bot-visible text messages are supported; attachments/cards require manual review.
A window is bounded to seven days, four pages, and 200 messages. This does not
provide full-account or private-message coverage.

## Run from a source checkout

Use Node.js satisfying the repository engine requirement and `npm ci`. Configure
an existing authenticated `lark-cli` profile separately. Keep the model API key in
an environment variable; do not put it in the profile or source tree. The endpoint
must support the JSON chat-completions request used by `model.ts`.

Create a private profile outside the source tree; substitute actual identifiers,
paths and endpoint. The following values are placeholders, not working credentials:

```json
{
  "schema_version": "personal_follow_through_config_v0",
  "enabled": false,
  "runtime_root": "/absolute/private/runtime",
  "registry_path": "/absolute/private/registry.json",
  "goal_id": "personal-work",
  "goal_instance_id": "ginst_00000000000000000000000000000001",
  "owner_id": "ou_verified_owner",
  "binding": "selected-conversation",
  "chat_id": "oc_selected_conversation",
  "lark_profile": "personal",
  "model": {
    "endpoint": "https://model.example.invalid/v1/chat/completions",
    "name": "configured-model",
    "key_env": "PERSONAL_MODEL_KEY"
  }
}
```

Set `enabled` to true only after checking the selected source, owner, Goal and
model endpoint. `--allow-model` explicitly allows sending the captured text and
open User Todo context to that configured endpoint. Review files contain private
source text. Keep them private and delete them when no longer needed.

```sh
npm run personal-follow-through -- prepare --config "$PROFILE" \
  --start "$START_ISO" --end "$END_ISO" --allow-model --output "$REVIEW"
npm run personal-follow-through -- inspect --config "$PROFILE" \
  --packet "$REVIEW" --index 0
npm run personal-follow-through -- apply --config "$PROFILE" \
  --packet "$REVIEW" --index 0 --approve-digest "$REVIEWED_DIGEST"
npm run personal-follow-through -- brief --config "$PROFILE"
```

Read the complete inspected packet before supplying its digest. This digest binds
an explicit CLI operation; it is not a signed approval token or protection against
other processes already running as the OS owner. The CLI creates review files
exclusively and refuses to overwrite an existing path.

After one item changes the canonical revision, refresh the next item, inspect its
new packet, and approve its new digest. Refresh rereads the source and current Todo
state without calling the model again:

```sh
npm run personal-follow-through -- refresh --config "$PROFILE" \
  --packet "$REVIEW" --index 1 --output "$NEXT_REVIEW"
npm run personal-follow-through -- inspect --config "$PROFILE" --packet "$NEXT_REVIEW"
npm run personal-follow-through -- apply --config "$PROFILE" \
  --packet "$NEXT_REVIEW" --approve-digest "$NEW_REVIEWED_DIGEST"
```

Exact retries use canonical receipts. Changed source text or registry/configuration
invalidates old packets. Re-prepare after those changes; review the new result.
Edits to the same source-id set cannot silently create a second Todo. Semantic
matching across different source-id sets still depends on model proposals and
owner review. Amendments preserve omitted deadline fields; explicit null means a
reviewed deadline removal. Deadline metadata is in the Todo note, with append-only
correction entries; it does not install reminders or populate a scheduler field.

`brief` reads persisted User Todos and labels source freshness `not_checked`.
The CLI cannot mark work complete, send a message, or execute a delegated task.
Set `enabled: false` to disable provider/model access and further mutations. Prior
Todos remain in the canonical store. Changes are checked between external calls
and before commits; already-sent network requests cannot be recalled. Registry
witnesses reuse the existing consistency check, not a distributed transaction.

## Validation and remaining work

```sh
node --no-warnings --experimental-sqlite --experimental-strip-types \
  --test tests/control_plane_ts/personal_follow_through.test.ts
```

The process-level test invokes the real Node CLI, real HTTP transport, and a
real disposable FileAuthorityStore. Its PATH contains a scripted Lark executable;
the model server is scripted too. It covers review, create, replay, multi-item
refresh, source withdrawal, deadline correction, disabling, and registry rejection.
This proves the local process/storage journey without Python. It does **not** prove
live Lark payload compatibility, real-model extraction quality, or installed UI.

Desktop settings, persistent ChatActionStore proposal lifecycle, dismiss/manual
correction UX, desktop/private return delivery, live account qualification and
semantic evaluation remain open in the RFC. Review files here are transient CLI
handoff artifacts; they are not a second durable proposal service. The existing
desktop/Lark Python owners are unchanged and have not acquired this workflow.
The next integration owner is personal-workspace/work-items with the Lark extension;
that slice must adopt the accepted typed conversation contract and validate the
packaged desktop journey. No M1 completion or shipping claim follows from these
CLI tests.
