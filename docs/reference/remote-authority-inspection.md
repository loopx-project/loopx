# Remote authority inspection

`loopx` and standalone `loopx-core` include a read-only inspection command:

```bash
loopx remote-authority inspect --goal-id <goal-id> --agent-id <agent-id> --format json
```

It uses the selected registered Goal's existing `remote_authority` binding:
`enabled: true`, `endpoint`, `tenant_id`, `store_identity`, `actor_id`, and
`probe_todo_id`, plus optional `ca_file`. The selected agent must be registered
for that Goal and match `actor_id`. The command does not create or change this
binding. Other Goals and environment-only endpoint settings cannot enable it.

Supply the service credential through `LOOPX_AUTHORITY_CREDENTIAL` or, if unset
or empty, `LOOPX_REMOTE_AUTHORITY_CREDENTIAL`. Credentials are passed to the
private client process over stdin, not command arguments. `endpoint` must be a
credential-free HTTPS origin. An optional CA file must be an absolute path to a
readable regular file of 1–65536 bytes. Certificate verification stays enabled.

The command reads only the configured probe Todo through the existing task wire
contract. It makes one request, with a three-second network deadline and a
64 KiB response limit, without retries, redirects or writes. Local validation
failures make no request. The outer client process has a five-second deadline.

JSON output uses `loopx_remote_authority_inspection_v1` and contains `ok`,
`status`, `stage`, `reason_code`, a fixed `message`, `recommended_action`, and
`execution_admitted: false`. HTTP failures additionally include `http_status`.
Output never includes binding values, credentials, remote Todo content, raw
server messages or exception details. Omit `--format json` for readable output.

| Result | Meaning and next step |
| --- | --- |
| `remote_authority_read_verified` | The configured read was verified; exit code 0. Execution still requires the existing admission and lease workflow. |
| Configuration/runtime failure | Check Goal registration, selected agent, binding, credential, CA file or local Node.js installation as directed. |
| DNS/connection/TLS/timeout failure | Check hostname, network, server and certificate configuration. TLS verification is never bypassed. |
| `remote_authority_http` | Check the returned HTTP status and service/proxy configuration. An HTTP 401/403 alone does not establish the protocol's authentication or scope result. |
| `remote_authority_unauthenticated` / `remote_authority_scope_denied` | A correlated protocol response rejected authentication or scope; check with the service operator. |
| Protocol/read failure | Check client/service compatibility or the configured probe Todo. Unknown service reasons are reduced to a fixed safe diagnostic. |

Every unverified result exits 1. A missing Todo does not verify readiness. A
verified read does not claim a task, renew a lease, spend quota or enable remote
execution. The existing scheduler gateway and its `verified`/`unavailable`
private probe remain compatible; this command adds operator diagnostics only.

This client command does not install a remote service or qualify a production
deployment. Server-side authority remains in `loopx-project/loopx-cloud`.
