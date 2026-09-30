# Agent collaboration boundary

Any registered Agent can be a requester, worker or coordinator for a particular
exchange. These are relationships between requests, not privileged Agent types
or fixed hierarchy levels. A coordinator may request several peers; a receiver
may create another request using its received request as parent. Each conclusion
returns to its immediate requester. Only the original Chat request uses the
manager's conversation return adapter.

| Responsibility | Existing owner | This delivery |
| --- | --- | --- |
| Create/onboard an identity | `agent_onboarding.py`, `agent_registry.py`, registration CLI | Reuse registration; a request never creates an Agent |
| Connect a runtime and admit execution | Existing executor/binding, Turn and Goal/Todo/lease owners | Host explicitly binds Goal/Agent/workspace; requests do not launch workers or grant execution |
| Discover collaborators | `control_plane/agents/directory.py`, `agent-directory` | Existing same-Goal directory remains the discovery contract; registration is not presence |
| Validate semantic requests | `semantic_request.ts` | One typed validator for manager and peer callers |
| Retain requests, decisions and results | `inbox.py`, `peers.py` | Immutable identity, parent lineage, artifact versions, explicit result consumption |
| Classify pending receiver receipts | `inbox_receipts.ts` | One bounded read model for CLI/MCP callers; missing, unreadable and conflicting results remain distinct |
| Sandboxed Agent access | `loopx/collaboration_mcp.py` | Same tools and identity binding at every coordination level |
| Owner conversation and external audience | `capabilities/manager_context` | Intent extraction, ingress grants, Chat/Lark routing and display; no peer scheduling |

The existing `manager-context` capability lifecycle, `manager-inbox` CLI and
`.local/manager-context` record address are retained for compatibility. They do
not make the manager an execution authority. Old Python entrypoints re-export
the moved shared functions for current callers. New peer/MCP callers import this
neutral boundary directly. No parallel task database, manager-only Agent factory,
new capability registration or speculative workflow engine is introduced.

Parent lineage retains root semantic context without recursively copying the
entire ancestor transcript. Immediate request ids keep each return unambiguous;
brief authors must preserve decision-relevant intermediate constraints. A parent
reference proves which request was received, not authority inheritance. Local
peer forwarding of external-audience parent requests is rejected.

`inbox.py` adapts the existing private file stores; typed request validation stays
in TypeScript. This does not promote a canonical shared-authority backend. General
request amendment/cancellation, cross-Goal/host delegation, dynamic Agent creation
and lifecycle supervision remain owned by their existing roadmap contracts.
The nested-coordinator regression and the [managed delivery demo](../../../examples/collaboration-delivery/README.md)
exercise this boundary without imposing a maximum tree depth or a manager hop.

Pending request pagination also belongs to `inbox.py`. Its stateless cursor
binds the resolved runtime root, Goal and receiving Agent to a request-id
position. CLI and MCP use the same live 20-request pages. Invalid cursors and
unreadable entry directories fail before request read receipts are written.
No scan index, receipt migration or additional authority store is required.
See [receiver pagination](../../capabilities/manager_context/README.md#a-delegation-returns-automatically)
for restart and concurrent-arrival behavior.

An ACK and a result file's existence do not clear an owed return. The shared
typed read model checks request/Goal/Agent identity, exact Goal instance when
present, source, phase, nonempty bounded text and receiver disposition. A
damaged or conflicting receipt remains visible as `receipt_unavailable` with
warnings; observing it never rewrites the record or repeats receiver work.
Recover the original receipt before continuing. Valid terminal receipts keep
the existing pagination behavior. File observations travel in bounded batches
of at most 128 requests, split by encoded bytes within the existing bridge limit.

App readback retains the collaboration card when a reply or delivery record
cannot be read, displaying delivery as unverified. Restoring the original
record lets the existing pump return once to the original conversation; it does
not grant permission to reassign work or send to a different audience. Native
file/CLI and real HTTP tests qualify this recovery boundary. Model routing,
receiver adoption and live external-provider delivery require their own evidence.
