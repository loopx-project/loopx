# Lark personal follow-through: manager profile

[中文版](personal-follow-through.zh-CN.md) is the semantic mirror of this profile.

This is a proposed, default-off application of the accepted
[capable-manager RFC](../../../architecture/rfcs/capable-manager-semantic-handoff-v0.md).
It introduces no independent lifecycle, permission model, scheduler, or M1–M3
program. Its implementation and live qualification remain partial/open. The
[original proposal](../../../architecture/rfcs/personal-follow-through-v0.md)
is retained as a superseded discovery and evidence entrypoint.

## User outcome and scope

An owner promises a draft, receives a deadline correction, and asks for supporting
material. The manager retains one commitment, prepares authorized material, and
returns a sourced brief in the original private desktop conversation. The owner
can inspect, correct, dismiss and pause it. A prepared draft or sent message alone
does not prove that the personal commitment has been fulfilled.

The first profile covers one owner, a private local workspace, selected authorized
bot-visible Lark conversations and an existing or explicitly created private Goal.
Verify the owner's platform identity; never infer it from a display name. Show
bot-visible coverage. Group access implies no full-account/private-message access.
Email, DingTalk, external task synchronization, automatic external sends, cloud
24/7 operation and a connector marketplace are outside this profile.

## Shared owners and acceptance mapping

The manager RFC owns the common requirements below. Its acceptance IDs and
execution ledger remain canonical; this table supplies scenario fixtures without
creating a second acceptance authority. Passing a fixture here does not close an
entire manager acceptance row or milestone.

| Profile fixture | Existing normative owner | Acceptance / delivery relation |
| --- | --- | --- |
| Verified owner, selected sources, standing grant, pause and revoke before queued work | Manager §5.3; source provider permission boundary | A3; manager M1 effective authority and profile |
| Promise, correction, rejected interpretation, owner edit and restart | Manager §5.4/§5.11; canonical work-item owner | A5/A13/A20; manager M2 semantic continuation |
| Source replay, concurrent Todo edit, withdrawal and crash between proposal and Todo effect | Manager §5.2/§5.12; existing proposal and canonical receipt owners | A6/A7/A15; manager M2 identity, single writer and recovery |
| Authorized preparation creates a linked artifact without completing the human commitment | Manager §5.6/§5.11; User/Agent Todo and artifact owners | A13/A14; manager M2 obligations and artifacts |
| Executor/transport restart, private desktop return, equivalent authorized Lark/CLI readback | Manager §5.6/§5.7; App continuity and existing outbox | A8/A9/A10/A14/A20; manager M3 complete exchange |
| Recurring brief, sleep/offline catch-up, cost and attention measurement | Manager §5.11/§10; existing schedule, budget and retention owners | Repeat applicable A3/A7/A8/A10/A13 under the profile; evidence for manager M4 promotion |

Use [App continuity](../../../architecture/rfcs/app-conversation-and-async-inbox-v0.md)
for conversation delivery and [canonical Todo completion](../../../reference/canonical-todo-completion-update.md)
for work completion. The manager's §11 dependency plan determines delivery order;
a profile does not require unrelated provider promotion or repository-wide migration.
The former personal M1 read/review slice maps to manager M1/M2 and A10 visibility;
personal M2 preparation/return maps to manager M2/M3; personal M3 pilot contributes
to manager M4 qualification. Those personal milestone names are historical only.

## Lark interpretation and evidence

Compose `lark-event-inbox`, `manager-context`, `periodic-report` and existing Todo
operations. Provider id `loopx-lark` is bundled and optional; no new connector
capability is needed. Lark transport stays in the extension; domain-neutral
validation stays with typed work-item owners. The lead Agent interprets messages;
provider text and model output cannot authorize an effect.

A versioned personal-follow-through payload belongs in the existing action/proposal
lifecycle. Its scenario data is: source binding and opaque event/revision references,
verified responsible identity, candidate title, proposed operation, target Todo and
expected revision for amendments, evidence references, and an optional due date
with timezone and interpretation basis. Omission preserves existing values;
explicit clearing differs from omission. Invalid calendar dates and mismatched
identities cannot become mutations. Ambiguous responsibility or dates require
clarification; quoted promises, questions and other people's commitments must not
be promoted as the owner's commitment.

Interpretation may yield no follow-up, clarification, create, amend, proposed
completion or dismissal. These map to existing typed operations and proposal
transitions, not another status enum/database. Keep source → proposal → canonical
User Todo → linked Agent preparation → versioned artifact/evidence references.
User Todo represents the human commitment; Agent Todo represents bounded supporting
work. The completion owner requires an explicit owner decision or an already
authorized verifiable criterion. A commitment requiring another person's acceptance
needs that acceptance evidence. Cross-message semantic matching requires an explicit
existing-Todo match; transport deduplication alone cannot identify one commitment.

Use the existing ingestion/effect receipt contract before advancing ACK/cursor.
Source edits change the interpretation basis; replay reconciles original receipts.
A newer owner edit is not overwritten by an old proposal. Deleted/unavailable
sources invalidate affected pending proposals and remain visible as unavailable
provenance for existing facts. These are Lark fixtures for A5/A7/A15, not a second
recovery protocol. For each rollout, distinguish captured, proposed, applied,
prepared, returned and verified evidence through their actual owners.

## Activation, privacy and implementation placement

Keep the profile off until the selected source/window, verified owner, private
return destination, permitted local effects, cadence, budget and retention are
visible in existing settings. Initially review proposed commitment changes; reuse
any already granted effect scope under manager §5.3 rather than introducing repeat
approval for permitted reading or draft preparation. Source authorization never
implies sending, assigning other people, or modifying external task systems.

Keep credentials with the provider owner and source bodies in private storage.
An owner-private brief requires a separately scoped report profile; the public-safe
periodic-report preset and shared projections must not receive private source text.
Off-state must preserve existing Chat/Todo/capture/schedule/report behavior and
produce no model calls or notifications. Disconnect stops new dependent work via
the existing revoke/dispatch boundary; retain created Todos and dedupe receipts.
Deletion and retention are separate owner-visible choices. No automatic historical
import; bounded catch-up starts at an explicit cursor/window. Reuse existing
outbox, retention and scheduler recovery without a profile-specific daemon.

The complete profile uses TypeScript/TSX and Node.js, including provider adapter,
semantic validation, canonical integration, report composition and tests. No new
Python modules or subprocess bridges. Follow the
[TS migration RFC](../../../architecture/rfcs/typescript-control-plane-migration-v0.md):
characterize and migrate the smallest required cohesive Python-only owner and its
active consumers, preserving canonical authority and rollback. Unrelated language
rewrites are outside scope. Profile qualification includes the packaged journey
with Python unavailable; isolated Node unit tests do not satisfy that requirement.

## Scenario evaluation and promotion evidence

Freeze at least 100 synthetic or consented messages with at least 30 actionable
commitments, labeled independently of model output. Include owner/other-person
promises, quotes, negation, ambiguity, timezones, edits and deletions. Report
proposal precision (correct proposed items / all proposed items) and recall
(correctly found commitments / all labeled commitments) separately. Proposed
profile targets are at least 95% precision and 90% recall, zero wrong-owner
automatic mutations and zero unauthorized effects. These are unmeasured release
targets, not delivered performance or replacements for manager acceptance.

Exercise one independently specified sequence through the packaged frontend,
Lark and CLI: connect → promise → review → canonical Todo → changed date → owner
correction → authorized preparation → restart → same-conversation artifact return
→ disconnect. Verify state and receipts, inject response loss/crash/concurrency,
and include revoked grants, injected source instructions, wrong audience, expired
credentials, budget exhaustion and network/sleep gaps. An ACK, mock trajectory,
backend test or prepared draft cannot pass the real installed journey.

After applicable correctness gates, run a seven-day owner-authorized pilot. Review
all proposed/accepted items and independently sample source messages for misses.
Record source window, denominators, duplicate rate, deadline accuracy, verified
preparation outcomes, cost, and daily review/correction minutes against a comparable
manual baseline. Promote through the manager's existing M4 decision only with
correctness and reduced total review effort; small samples remain exploratory.
Publish aggregate redacted evidence only. Source identity/platform, retention,
model/labels and proposed thresholds must be settled before qualification;
post-failure threshold changes require a recorded decision and fresh held-out data.

## Evidence and remaining integration

[PR #5385](https://github.com/loopx-project/loopx/pull/5385) at the recorded
`ad478f4` head is an experimental CLI prerequisite. Its tests use scripted Lark
and model responses with real local processes/storage. It does not qualify live
identity/source/model compatibility, durable proposal adoption, desktop return,
packaged execution or the pilot. Historical counts and test limits remain in the
[original execution ledger](../../../architecture/rfcs/ledger/personal-follow-through-v0/).
Current integration evidence belongs in the
[manager execution ledger](../../../architecture/rfcs/ledger/capable-manager-semantic-handoff-v0/).
The manager/work-item, personal-workspace and Lark extension owners must complete
these existing acceptance obligations; this document allocates no live Todo and
closes no acceptance row. Disabling the profile remains the rollback, preserving
canonical work and receipts through their owners.
