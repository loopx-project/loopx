# Ark governed Turn adapter

An optional host for one LoopX-governed work unit using Ark Managed Agents.
The cloud Agent chooses its model/tool steps. LoopX's existing Turn executor
owns admission, independent validation, canonical writeback and quota settlement.
This adapter does not activate a native Goal or add a second scheduler.

## Install and select

Install both packages from the same checkout containing the shared
`loopx.control_plane.turn_driver.host_candidate` contract:

```bash
uv pip install -e . -e packages/loopx-ark-turn
loopx-ark-turn --model "$ARK_MODEL_ID" --environment-id "$ARK_ENVIRONMENT_ID" \
  --state-dir "$ARK_TURN_STATE" --doctor
```

Use an existing owner-selected Ark Environment. `ARK_API_KEY` authenticates
requests; it never selects the model or grants a tool permission. Set
`ARK_BASE_URL` only for an explicitly chosen compatible endpoint. Keep
`ARK_TURN_STATE` private and outside the task workspace.

Select this command through the existing
`loopx turn run-once --host generic-cli --iteration-context fresh` interface,
with `--host-command-json` containing the adapter argv above without `--doctor`.
Supply the normal Goal, Agent, project, Todo and independent validation command.
The caller must use a registered LoopX Agent and current work admission. The
adapter's cloud Agent definition and session are execution resources, not new
LoopX identities.

The envelope action signature is an integrity/coherence hash, not standalone
cryptographic authentication. Invoke the adapter behind the trusted local Turn
executor; it is not a public remote authority endpoint.

The default managed host and existing `ark-managed-agent` native Goal profile
are unchanged. A fresh cloud session is required for each admitted iteration;
continuation comes from the existing LoopX driver, not an automation prompt.

## File execution profiles

For larger tool configurations, use `loopx-ark-turn --config "$ARK_PROFILE"`.
The JSON object uses `Config` fields: `model`, `environment_id`, absolute
`workspace` and `state_dir`, optional `mcp_command` / `tool_names` / `mcp_env`
arrays, and execution/tool timeout and call limits. Keep it outside member
workspaces. Credentials remain environment variables. File profiles and inline
configuration cannot be combined; `--doctor`, `--inspect-turn-key` and
`--cleanup-turn-key` work with either form. File and inline forms resolve to
the same receipt identity; profile changes cannot retarget an existing attempt.

This compacts the generic CLI invocation without raising its argv limit or the
eight-tool selection limit. The [local delegation interface](../../docs/reference/local-delegation.md)
uses these profiles for both main and nested coordinators.

## Local tools

Supply an operator-owned stdio MCP server with `--mcp-command-json` and an exact
`--tool` selection for each exposed tool. Discovery reads every page, rejects
duplicate names, and allows at most eight selected tools. One MCP process is
bound to one Turn; models cannot change its command, selected tools or identity.

The server receives host-bound `LOOPX_TURN_KEY`, `LOOPX_TURN_GOAL_ID`,
`LOOPX_TURN_AGENT_ID`, `LOOPX_TURN_TODO_ID` and `LOOPX_TURN_WORKSPACE`. Use these
facts to bind the existing collaboration/work services. Tool implementations
must enforce resource scope and authorization; a schema or a coordination role
does not grant authority. MCP servers run with their local OS permissions, not
inside the cloud sandbox. Only connect trusted, appropriately isolated servers.

The existing `python -m loopx.collaboration_mcp` server can be selected with its
operator-bound registry, runtime, Goal, Agent and workspace argv. Select only
the needed `read_context`, `assess_request`, `request_peer`, `return_result`,
and `consume_peer_result` tools. Those existing tools own semantic requests,
adoption and return; they do not launch workers. This adapter transports their
calls without creating another Inbox or adding manager-specific authority.

The MCP SDK supplies its platform default environment; additional variables are
opt-in by name using `--mcp-env`. `ARK_API_KEY` is never forwarded. This profile
qualifies text and structured-JSON results only. Unsupported content, oversized
results, unknown tools and exhausted tool-call limits fail explicitly.

Receiving an external tool request does not complete the work. The adapter
waits through `requires_action`, supplies the correctly correlated result, and
requires `end_turn` plus a typed candidate. Only the independent LoopX validator
can accept the resulting artifact.

## Lifecycle and readback

Cloud builtin tools are explicitly disabled by default; an omitted declaration
does not reliably disable provider defaults. To allow bash and other sandbox
tools, add `--sandbox-builtins` or `"sandbox_builtins": true` to the operator
profile. This mode permits temporary sandbox computation and file writes. It
does not classify shell commands by substring or claim that bash is read-only.

Before creating a Session, the adapter checks that the selected Environment is
cloud-hosted with no injected variables, startup script, package installation
or configured output storage. It applies a Session-only networking override:
`limited`, no allowed hosts, no MCP-server or package-manager exceptions, no
resource mounts and no Vault access. The frozen Session must read back those
restrictions before input is sent. The shared Environment is never changed.
Missing or weaker readback refuses the attempt. Trusted local custom MCP tools
retain their separate host permissions; this cloud policy does not isolate them.

Runtime builtin calls require the selected root thread, an observed `allow`
permission and correlated results before a candidate can be returned. Receipts
retain tool names and input/result hashes, not raw commands or outputs. Calls
before the input ACK, undeclared builtin calls and conflicting/orphan results
refuse the attempt and retire its known resources. The combined call limit is
an observation ceiling for cloud tools: they may already have executed before
the adapter sees the event. The execution deadline and cleanup still apply.

The input ACK must identify the root thread, and every candidate message must
explicitly match it. A custom tool call or a builtin call/result invalidates
earlier candidate text. Completion requires a new candidate after the latest
tool outcome; an error may be followed by a fresh `repair_required` candidate.
Tool failure alone neither validates an earlier result nor forbids repair.

`--doctor` reports configuration only and always leaves
`sandbox_enforcement_verified` false. SDK wire fixtures qualify this adapter's
requests and refusals, not live sandbox enforcement. Before enabling this mode
in production, qualify the provider's actual no-egress, no-secret/no-mount
behavior and cleanup with an authorized disposable cloud attempt. Configuration
readback alone cannot establish that all builtin tools have no external effect.

Each attempt creates its own cloud Agent definition and session using the
selected model and exact tool declarations, then deletes both with readback.
The frozen session snapshot must match the model/tool selection and contain no
unexpected skills, remote MCP servers or provider multiagent topology before
input is sent. Historical events are paginated through the public API and
processed only after the input ACK; pre-input idle events cannot finish work.
It never changes or deletes the configured Environment. Private host receipts
retain resource identities, tool-effect status, bounded candidate and provider
usage for reconciliation. Raw model thinking/events and credentials are not
stored. Provider usage is separate from LoopX quota; unavailable usage stays
unknown, and rejected work can still cost tokens.

Receipts from before `sandbox_tools_v2` remain inspectable and cleanable
with their original options, but their candidates cannot be replayed or their
sessions resumed as newly qualified work. Changing builtin mode binds a different
configuration; it cannot retarget or rerun an existing Turn key. Keep stopped
attempts and their unknown usage instead of resetting them to retry input.

Mutating provider requests are not automatically retried. A duplicate exact
request may reuse a completed candidate after resource cleanup; a conflicting
request or incomplete prior attempt cannot silently start another model run.
An interrupted running attempt with a confirmed original input and no uncertain
local effect can resume observation of that exact cloud session. It does not
send the input again, repeat acknowledged tool effects, or reset the execution
deadline. Terminal text is persisted before candidate conversion. While the
host is absent, cloud computation can continue until it needs a local tool;
that call waits for reconnection. An interrupted executing/sending tool, lost
creation/input response or changed tool schema cannot be treated as a safe
restart. Preserve those receipts for reconciliation. This does not install
fleet supervision or provide distributed authority.

With the same model, environment, workspace, state directory and tool options
as the original invocation, append one of:

```bash
loopx-ark-turn <same-options> --inspect-turn-key "$TURN_KEY"
loopx-ark-turn <same-options> --cleanup-turn-key "$TURN_KEY"
```

Inspection is local and credential-free. Cleanup retries deletion of known
attempt-owned resources without launching a model or repeating a local tool.
It returns success only after absence is confirmed. A lost create response
leaves `unknown_creation=reconcile_required`: use the private receipt's exact
resource label to inspect the provider account and resolve ownership manually.
An explicit creation rejection (HTTP 400, 401, 403, 404 or 422) records only
the phase and status code, then retires already acknowledged resources. It
does not rewind the attempt or retry creation. Timeout, conflict, rate-limit,
server errors and lost responses remain uncertain; earlier unknown receipts
are not reclassified. Raw provider error bodies are not stored. A rejected
configuration does not qualify sandbox enforcement or backend compatibility.
The adapter cannot safely adopt an unknown resource or declare it absent.
Keep that attempt blocked; do not delete arbitrary matching resources or edit
the receipt to make replay look successful. A completed candidate remains
subject to the outer Turn's independent validator on replay.

To disable, remove the explicit host-command selection and stop any running
adapter before restoring the previous profile. Retain private receipts until
owned cloud resources are confirmed absent. Uninstall with
`uv pip uninstall loopx-ark-turn`; this does not uninstall LoopX or alter work
history. Do not run native Goal and outer Turn drivers for the same binding.

## Public provider references

- [Ark SDK 0.8.0](https://github.com/volcengine/ark-runtime-python/blob/5c0c78acd8570f20b9be615906cf556d45e9ada5/README.md)
- [Agent / Environment / Session example](https://github.com/volcengine/ark-runtime-python/blob/5c0c78acd8570f20b9be615906cf556d45e9ada5/examples/volc/sessions_loop.py)
- [Custom tool and MCP boundaries](https://github.com/volcengine/ark-runtime-python/blob/5c0c78acd8570f20b9be615906cf556d45e9ada5/examples/self_hosted_mcp_worker/README.md)

Only these public contracts and LoopX source define the integration. Native
Goal evaluation, live steering, cross-host leases and provider promotion are
outside this profile's qualification.

For a runnable multi-Agent example with actual dependent artifacts, see the
[synthetic research team](../../examples/managed-research-team/README.md).
