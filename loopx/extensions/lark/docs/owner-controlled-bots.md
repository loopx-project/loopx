# Owner-controlled assistant and steward Bots

An operator can plan two Lark applications: an assistant for ordinary project
conversations, and a steward for explicitly delegated long-term coordination.
They may share LoopX Core mechanics, but each application needs its own verified
identity, audience and workspace grants. This is a deployment and development
handoff; it does not certify the unfinished assistant DM journey.

## Readiness boundary

| Surface | Current evidence | Remaining qualification |
| --- | --- | --- |
| Existing group/Agent Topic and manager connections | Native provider routing, Inbox, Chat Session and verified replies | Installed application and host journey |
| Independent conversations on one App listener | Bounded transient dispatch with source/shared-Session ordering | Live slow-request and recovery journey |
| Ordinary project conversation and private Bot DM | Product requirement in the conversation RFC | Shared Core context, authenticated onboarding and installed continuity |
| Busy-conversation admission, progress, media and permission callbacks | Existing Core/host pieces and provider presentation | End-to-end behavior for the selected host; no adapter-wide parity claim |
| Two operator-owned applications | Separate profile/identity configuration is the intended boundary | Both Apps together, exact identity isolation and one consumer owner per App |

See [realtime readiness](realtime-conversation-readiness.md) for what the current
transport repair proves. Starting a listener is not evidence that the remaining
rows pass.

## Install and establish ownership

1. Read the target machine's existing installation report. Choose one LoopX
   installation owner using the [install guide](../../../../docs/guides/installing-loopx.md).
   For unreleased development, use a clean source checkout and qualify the exact
   candidate before installing it. Record both source and installed revisions;
   a public PR or passing source test does not identify the installed package.
2. Verify that the operator controls the tenant, developer console, application
   administration and publication scope. A personal login alone does not prove
   application ownership. Registration, login, consent and publication use that
   operator's own account.
3. Create separate profiles, for example `personal-assistant` and
   `personal-steward`. The existing App setup invokes
   `lark-cli config init --new --name PROFILE --brand feishu --lang zh_cn`;
   inspect the selected CLI's help and the existing settings wizard first. This
   is an App registration action, not a read-only diagnostic.
4. Keep credentials in provider-private local storage. Verify each profile's
   actual App identity and authorized user independently. Provider user ids can
   be App-scoped; do not copy an old user id or grant into a different App.
5. Review permissions against the enabled journey. The current
   [recommended scope bundle](../bot_scopes.py) includes group management and
   document comments as well as conversation features. It is not evidence of a
   minimal assistant-only permission set. Verify message events, readback and
   any enabled card/media operations against the actual provider version.
6. Start with an owner-only audience and an explicit workspace. One listener
   owner per App consumes `im.message.receive_v1`; do not run a bridge and native
   listener concurrently for that same App. Two different Apps still need an
   installed isolation check. Record the service owner and restart procedure.

Keep configuration facts, transcripts, SDK/session databases and verification
URLs private. Moving to new Apps does not require importing a previous machine's
registry, Goals, Todos, credentials or model-session history.

## Recipient purpose and shared implementation

| Recipient | Default purpose | Access boundary |
| --- | --- | --- |
| Assistant | Ordinary authorized project chat; explicitly selected existing Agent when available | Selected workspace and conversation audience |
| Steward | Persistent commitments, decomposition, coordination and acceptance | Explicitly granted portfolio; an empty installation has no inherited commitments |

The assistant must not borrow the global manager objective or create a hidden
Goal to bypass the current Goal/manager Chat context restriction. Implement the
authorized project-conversation context through the existing typed Core owner
in the [conversation RFC](../../../../docs/architecture/rfcs/app-conversation-and-async-inbox-v0.md).
Reuse Chat Session/Turn, admission, operation receipts, progress and host
execution. Provider transport must not add a parallel scheduler, approval store,
task ledger or model runner.

Separate durable admission from long model execution to improve a busy
conversation. A receipt must truthfully distinguish received, accepted, queued,
running and terminal result. Recipient changes affect subsequent input; accepted
work returns to its original Session and audience. A stop or permission answer
must resolve one exact request/operation and preserve existing host policy.

## Installed acceptance and handoff record

Exercise both Apps with synthetic content on the intended desktop and phone:

- First message and follow-up preserve the intended Session; unauthorized users
  and cross-App bindings fail closed.
- A blocked assistant request does not block an independent steward request;
  another assistant input receives bounded, truthful queue/steering feedback.
- Progress stays provisional. Image/file input reaches the selected host or
  reports unsupported. A permission callback binds the approving user, exact
  operation and expiry; it never silently elevates policy.
- Exact stop, duplicate events, reconnect and lost reply readback do not restart
  a model request, replay an effect or dispatch cancelled waiting input.
- Restart and login recovery use the same verified profiles and listener owner.
  Record rollback to a previously qualified package without reusing old grants.

For each case record `passed`, `failed` or `not_run`, the source/installed
revision, actual host capability, evidence category and next repair. Keep raw
provider evidence private. The
[steward operational contract](../../../../docs/architecture/rfcs/capable-manager-semantic-handoff-v0.md#10-operational-contract)
owns the broader acceptance. Deployment handoff, product qualification and
optional state inheritance are separate completion facts. A new deployment
does not automatically retire a service on a different App or machine.
