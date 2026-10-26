# Original project conversations as collaboration recipients

This source proposal connects an exact registered requester to an existing
private Codex project conversation. Its Goal stays null; the same Session,
executor, native thread and FIFO process both human and peer input. It extends
the existing collaboration and native Chat owners, with no new capability,
queue, database or external-message permission.

## Enable and inspect

Open the original project conversation in the App and choose **Collaboration
requests / 协作请求**. Select the original source request, inspect its purpose,
then choose **Allow this source / 允许此来源**. The grant identifies the source
Goal lifetime, requesting Agent, parent request and verified original recipient.
An existing workspace grant or `all_registered` delivery scope does not enroll
this recipient kind.

The panel shows received work, its queue state, current brief and revision,
independent receiver decision and returned conclusions. Expand a request for
constraints, actual input references, acceptance and return requirements.
“Conclusion awaiting requester” means a result for the current revision exists; “Read by requester” means
the original requester read that result. Neither proves requester adoption.
Peer input is labelled **Collaborator**, and its visible message is the request
purpose; execution instructions remain outside the human transcript.

Choose **Revoke / 撤销** to disable that source-to-recipient grant. Retained
grants remain revocable when the original connection is unavailable. Restore
the original App, operator, project and executor connection before retrying
discovery or execution. Revocation stops new dispatch; it does not erase history
or roll back already executed tools.

## Requester entrypoints

The requester must use the exact `source_session_v1` profile on the same
coordination runtime as the intended local Chat host. Legacy requester profiles,
group conversations and other executors are unavailable in this slice. They are
not silently migrated or represented as synthetic Goals.

The existing manager inbox entry exposes the local Chat host:

```sh
loopx manager-inbox --goal-id source-goal --agent-id requester \
  original-conversation --conversation-operation discover \
  --conversation-file discover.json --chat-url http://127.0.0.1:8767
```

`discover.json` contains `{"parent_request_id":"<exact parent request id>"}`.
Use the returned `recipient` object unchanged. A request file contains
`parent_request_id`, `recipient`, a stable `operation_id` and the existing
`collaboration_brief_v0` object. The brief names purpose, context, constraints,
relative input references (and available digests), observable acceptance and
return requirements. `--conversation-operation request` publishes it.
The CLI supplies requester identity and runtime location; caller-authored
requester or runtime fields are rejected.

The existing collaboration MCP server exposes
`original_conversation_collaboration(operation, parameters, chat_url)` for the
same discover, request and control operations. The HTTP transport accepts only
an intended local loopback service, checks its exact runtime and does not follow
redirects or proxies. This is a trusted local boundary, not remote authentication.

## Execution, correction and recovery

The versioned `collaboration_original_conversation_recipient_v0` address binds
the original binding and Session to a digest of its verified provider, operator,
audience, project, executor and native-thread identity. Session IDs and workspace
paths alone grant no authority. Multiple live Sessions claiming the same native
thread make the recipient unavailable.

Publication persists the immutable operation, exact original return route and
shared request record before admitting a peer Turn to the original FIFO.
Same-operation retries recover the same request and its current revision's Turn. Changed recipient,
source or content under that identity rejects; use the existing revision control
for a correction. Grant, source lineage, cancellation, recipient identity and
revision are rechecked before native model input and before result publication.
Receiving context never expands the receiver's workspace or tool permissions.

The receiver reads the current brief and independently returns
`adopt`, `defer`, `reject` or `no_change`, a reason and an evidence-backed
conclusion. The per-Turn response schema also binds the answer to the supplied
request revision and digest. Resuming an existing native thread requires neither
new dynamic tools nor Goal execution mode.

For `--conversation-operation control`, the file contains `recipient`,
`request_id`, `control` (`inspect`, `revise` or `cancel`) and
`parameters`. Inspect returns the current control view. Revision and cancellation
use the existing stable operation ID and expected revision/digest fields;
revision supplies the corrected brief and reason. Each accepted revision enters
the same Session FIFO once, including corrections after an earlier answer was
read. It has its own revision/digest-bound Turn; superseded Turns cannot start
new model input or publish a current result. Original revision-one Turn keys remain
readable. Cancellation uses the existing interrupt owner for only this request's outstanding Turns. A late or
cancelled answer cannot become a current result. The panel shows the current
revision's queue/result state and retains earlier conclusions as history.

Publication/enqueue response loss is recovered by retrying the same operation.
If correction persistence succeeds but queue admission fails, retry that same
correction operation or original publication to recover the current revision.
Actual structured model output is saved before result IO, so a lost return write
can recover that revision's assessment and conclusion without invoking the model again.
The original failed Turn remains failed; the panel separately reports a recovered
result. A failure before saved model output remains visible and cannot be
silently rerun as a new Turn under the same identity.

Read the result through the requester's existing manager inbox and use
`acknowledge-return` / MCP `consume_peer_result` to record consumption after
checking it. The saved requester route is verified at dispatch, source control,
requester read and consumption. Results do not automatically go to the
recipient conversation's external audience.

## Qualification boundary

Isolated File/SQLite integration uses the real source, binding, request, FIFO,
HTTP, revision, result and requester readback owners. The model transport and
provider identity observations are synthetic. Compatibility checks preserve
ordinary human input and registered recipients. Packaged desktop/narrow browser
inspection covers discovery, explicit grants, mixed queue/result states, long
content, unavailable readback and revocation.
[Packaged views and their observed boundary](media/original-project-collaboration/README.md)
retain the desktop and narrow evidence.

These checks qualify the proposed local path. They do not establish installed
provider delivery, real model quality, natural receiver adoption, requester
benefit or long-horizon cost improvement. Those remain separate acceptance
before a release or completion claim.
