# RFC: Provider-side authorization at effect acceptance (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Design only; no runtime integration or qualified provider
- **Authors / owners:** LoopX capability, security, and provider maintainers
- **Created:** 2026-09-28
- **Last normative revision:** 2026-09-28
- **Implementation baseline:** `6643f3670e76498b9600638523844876fbfe9b3a`
- **Related contracts:** [overall roadmap](loopx-overall-roadmap-v0.md),
  [effect interpreter](agent-loop-effect-interpreter-v0.md),
  [shared authority](shared-goal-authority-state-provider-v0.md),
  [Goal instance identity](goal-instance-identity-and-orphan-recovery-v0.md),
  [capability portfolio](goal-scoped-capability-portfolio-v0.md), and
  [human-confirmed operations](human-confirmed-domain-operations-v0.md)
- **Language mirror:** [中文版](provider-effect-acceptance-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1-10 are the durable design and acceptance contract. Section 11 is
the normative delivery plan. Section 12 contains unresolved decisions.
Appendices are non-normative. The Chinese document is the semantic mirror of
this document, and both versions must change together.

---

## 1. Decision summary

LoopX will define an opt-in provider-effect acceptance contract for protected
external effects. The contract closes one specific gap: a LoopX authorization
check before dispatch does not prove that the effect owner still allowed the
operation when it committed the effect.

A conforming provider checks its current provider-visible policy, orders that
check against revocation, reserves the effect and authorization identities,
commits the effect or a factual no-change result, and stores an immutable
decision receipt at one provider-owned linearization point. Exact replay returns
the same public-safe projection of that historical receipt. Changed identity
reuse conflicts.

LoopX keeps its existing responsibilities. It owns Goal and Todo authority,
admission, the governed dispatch journal, settlement ordering, quota, and later
state transitions. The provider owns the external effect and its factual
receipt. Provider records do not enter `AuthorityStore`, and the provider
receipt does not grant a LoopX transition.

The contract is absent by default. Existing providers and operations keep their
current behavior. A future operation that requires strict provider acceptance
must fail before dispatch when its selected provider lacks a qualified
declaration for the required guarantees. An explicitly owner-authorized weaker
operation may dispatch once under a no-blind-retry rule and retain
`outcome_unknown`.

This RFC does not add runtime code, enable a provider, create a distributed
transaction, or claim exactly-once effects across independent systems.

## 2. Problem and invariants

The current governed path persists an invocation before dispatch and passes one
stable `effect_id` to the provider. It can resume LoopX writeback and quota
settlement without repeating completed local phases. If a third-party provider
accepts a request and its response is lost, however, the local journal cannot
prove whether the provider checked current authorization, consumed the identity,
or committed the effect exactly once.

Consider this order:

```text
T0  LoopX admits a protected operation
T1  LoopX or a gateway checks policy
T2  the request leaves LoopX
T3  policy is revoked
T4  the provider accepts the request
T5  the provider commits the effect
T6  the response arrives or is lost
```

A check at `T1` narrows the interval but does not determine the policy state at
`T4` or `T5`. A gateway has the same gap when it consumes local authorization
and then calls another effect owner.

### Invariants

- The final effect owner decides provider acceptance.
- LoopX Goal, Todo, lease, gate, quota, and settlement owners remain unchanged.
- Exact `GoalRef` binds an authorization to one Goal lifetime.
- Sender identity, authorization freshness, one-use consumption, retry
  idempotency, and effect commitment remain separate facts.
- A provider recomputes request and payload digests from the values it will
  apply.
- Revocation and first acceptance have one observable provider order.
- A response loss never authorizes a new effect identity.
- Exact replay returns history before current policy is evaluated for a new
  effect.
- An authenticated policy denial is a terminal, replayable decision.
- An unauthenticated or sender-invalid request cannot consume or reserve a
  trusted identity.
- Expiry never makes an old effect or authorization identity reusable.
- An unsupported strict operation fails before dispatch without silent
  downgrade.
- Unknown stays unknown when provider readback cannot prove an outcome.

## 3. Scope and non-goals

### In scope

- a provider-neutral requirement and guarantee vocabulary;
- exact Goal lifetime, effect, authorization, audience, action, target,
  request, payload, policy, sender, and provider-epoch binding;
- provider-side revocation ordering and one-use authorization;
- atomic effect or no-change commitment with an immutable decision receipt;
- exact replay, query, conflict, rejection, compaction, and retirement rules;
- explicit strict, no-blind-retry, and unsupported behavior;
- a controlled-provider conformance plan and bounded qualification claims.

### Non-goals

- moving external effects or provider policy into `AuthorityStore`;
- making Goal enablement or capability registration a security token;
- a universal token format, credential store, policy language, or key service;
- a distributed transaction between LoopX and external providers;
- automatic fallback to another provider;
- retroactive cancellation of a committed effect;
- automatic compensation, provider promotion, or default behavior changes;
- claiming support from an undocumented header or observed provider behavior.

## 4. Current-system and standards contract

At the implementation baseline:

- command-specific TypeScript reducers own coordination lifecycle and settlement
  reductions;
- `AuthorityStore` atomically commits LoopX coordination state, events, and
  receipts, but it is a storage contract rather than an external-effect owner;
- `CoordinationCommandReceipt` recovers a historical LoopX command by operation
  identity and request digest;
- File and SQLite stores can verify an identical historical operation without
  rewinding the current authority head;
- the Effect Program binds ordered settlement steps to one `effect_id`;
- Python capability admission selects one enabled, doctor-ready provider and
  validates a Goal capability binding that currently contains `goal_id` but no
  Goal instance;
- Python governed capability execution journals the request, dispatches the
  provider, invokes TypeScript result reduction, and resumes settlement;
- the governed result path validates a returned
  `loopx_external_effect_receipt_v0`;
- provider-effect ambiguity remains owned by the provider or effect ledger.

A historical coordination receipt does not grant current execution authority.
Likewise, a provider effect receipt reports a provider fact but cannot complete
a Todo or authorize another LoopX state transition.

The standards used by this design define separate properties:

| Mechanism | What it establishes | Missing property |
| --- | --- | --- |
| [OAuth token introspection](https://www.rfc-editor.org/rfc/rfc7662.html#section-4) | Token state at the authorization server's observation point | Provider policy state at effect commit; cached responses can remain stale |
| [JWT `aud`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.3), [`exp`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.4), and [`jti`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.7) | Audience, time bound, and an identifier | Atomic one-use consumption |
| [DPoP](https://www.rfc-editor.org/rfc/rfc9449.html) | Proof that the sender holds a bound key for request metadata | Current business policy and request-body binding by default |
| [HTTP idempotency](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2) | Repeat semantics defined by a server contract | Authorization freshness |
| [PATCH and `If-Match`](https://www.rfc-editor.org/rfc/rfc5789.html#section-2) | Atomic patch application and conditional resource revision where supported | Provider policy, sender identity, and one-use authorization |

No one row supplies provider-side authorization at effect commit.

## 5. Proposed architecture

### 5.1 Caller's view

The operation owner states its requirement. The executor derives the binding,
persists the governed intent, and owns recovery.

```ts
const outcome = await executeProtectedEffect({
  requirement: {
    kind: "strict_provider_acceptance_v0",
    sender_binding: "proof_of_possession",
    minimum_receipt_retention_seconds: 604800,
  },
  goal_ref,
  effect_id,
  provider,
  request: { action: "report.publish", target, payload },
  authorization,
});

if (outcome.kind !== "terminal" || outcome.projection.decision === "rejected") {
  return holdWithoutLoopXWriteback(outcome);
}
```

Three representative callers share this interface:

- A publisher commits a provider-owned object and its decision receipt in one
  operation.
- A remote job service treats durable job creation as the protected effect.
  Later job success is a separate result.
- A legacy API uses `explicit_no_blind_retry_v0` only under an exact owner
  authorization. Response loss leads to readback or `outcome_unknown`, never an
  automatic resend.

### 5.2 Ownership and authority

```text
LoopX typed owners
  -> obtain a current source-owned Goal admission witness
  -> resolve an independent provider qualification receipt
  -> admit one exact Goal/effect request
  -> persist governed dispatch intent
  -> send a bounded authorization presentation

provider effect owner
  -> authenticate presentation and sender
  -> order current provider policy against revocation
  -> reserve identities
  -> commit effect or no-change plus decision receipt
  -> return or query the same historical result

LoopX typed owners
  -> validate and journal the provider projection
  -> commit writeback
  -> spend quota
  -> admit any later Goal or Todo transition
```

The provider receives `ExactGoalRef` as immutable binding data. It does not
receive Goal write authority. A gateway satisfies this contract only when it
owns the final effect state, shares the same atomic log, or receives a compatible
downstream decision receipt.

An exact Goal identity is not an authorization grant. Before journaling or
dispatch, LoopX must obtain a current admission witness from the source project
registry and bind its digest into the authorization. Missing, stale, or
unavailable source state fails closed.

### 5.3 Requirement and guarantee declarations

An operation requirement and a provider guarantee are separate. The operation
cannot weaken its requirement based on the selected provider.

```ts
type ProviderAcceptanceRequirement =
  | { readonly kind: "off" }
  | {
      readonly kind: "strict_provider_acceptance_v0";
      readonly sender_binding:
        | "proof_of_possession"
        | "authenticated_channel"
        | "not_required";
      readonly minimum_receipt_retention_seconds: number;
    }
  | {
      readonly kind: "explicit_no_blind_retry_v0";
      readonly owner_authorization_ref: string;
      readonly readback: "required" | "when_available";
    };

type ProviderAcceptanceGuarantees = Readonly<{
  provider_identity_epoch: string;
  policy_ordering: "provider_atomic" | "not_supported";
  single_use: "provider_atomic" | "not_supported";
  request_binding: "canonical_request_and_payload" | "not_supported";
  sender_binding:
    | "proof_of_possession"
    | "authenticated_channel"
    | "not_supported";
  recovery:
    | "exact_replay_and_query"
    | "query_only"
    | "none";
  full_receipt_retention_seconds: number;
}>;

type ProviderAcceptanceQualificationReceipt = Readonly<{
  schema_version: "provider_acceptance_qualification_receipt_v0";
  qualification_id: string;
  qualification_revision: string;
  status: "qualified" | "revoked";
  provider: Readonly<{
    provider_id: string;
    implementation_id: string;
    implementation_version: string;
    deployment_identity_sha256: `sha256:${string}`;
    audience: string;
    provider_identity_epoch: string;
  }>;
  guarantees: ProviderAcceptanceGuarantees;
  evidence_sha256: `sha256:${string}`;
  not_before: string;
  expires_at: string;
  previous_receipt_sha256: `sha256:${string}` | null;
  receipt_sha256: `sha256:${string}`;
}>;
```

Strict admission requires provider-atomic policy ordering and single-use
consumption, canonical request and payload binding, queryable exact replay, and
sufficient receipt retention. It also requires the requested sender guarantee.
The provider declaration is evidence input, not qualification. A LoopX-owned
qualification owner issues the current receipt after it checks the named
implementation, version, deployment, audience, identity epoch, guarantees, and
evidence. Strict admission requires the latest unexpired `qualified` receipt and
binds its digest with the provider revision and integration-profile digest.
Provider readiness cannot create this receipt.

### 5.4 Core types

The types below describe domain semantics. Adapters keep credentials, proof
bytes, transport objects, raw provider payloads, and storage rows private.

```ts
type ProviderOrder = Readonly<{
  ordering_domain_sha256: `sha256:${string}`;
  provider_identity_epoch: string;
  sequence: `uint:${string}`;
}>;

type ProviderSecurityContextBinding = Readonly<{
  tenant_sha256: `sha256:${string}`;
  issuer_sha256: `sha256:${string}`;
  subject_or_client_sha256: `sha256:${string}`;
  sender:
    | {
        mechanism: "proof_of_possession" | "authenticated_channel";
        principal_sha256: `sha256:${string}`;
      }
    | { mechanism: "not_required"; principal_sha256: null };
  context_sha256: `sha256:${string}`;
}>;

type ProviderIdentityScope = Readonly<{
  provider_id: string;
  audience: string;
  provider_identity_epoch: string;
  security_context_sha256: `sha256:${string}`;
}>;

type ProviderEffectKey = ProviderIdentityScope & Readonly<{
  effect_id: string;
}>;

type ProviderAuthorizationKey = ProviderIdentityScope & Readonly<{
  authorization_id: string;
}>;

type ProviderEffectQuery = Readonly<{
  effect_key: ProviderEffectKey;
  authorization_key: ProviderAuthorizationKey;
  binding_sha256: `sha256:${string}`;
  query_sha256: `sha256:${string}`;
}>;

type ProviderEffectBinding = Readonly<{
  schema_version: "provider_effect_binding_v0";
  effect_key: ProviderEffectKey;
  authorization_key: ProviderAuthorizationKey;
  goal_ref: Readonly<{
    goal_id: string;
    goal_instance_id: string;
  }>;
  goal_admission: Readonly<{
    source_authority_sha256: `sha256:${string}`;
    source_revision: string;
    goal_ref_sha256: `sha256:${string}`;
    receipt_sha256: `sha256:${string}`;
  }>;
  action: string;
  target_sha256: `sha256:${string}`;
  canonical_request_sha256: `sha256:${string}`;
  payload_sha256: `sha256:${string}`;
  policy_basis_sha256: `sha256:${string}`;
  security_context: ProviderSecurityContextBinding;
  validity: Readonly<{
    not_before: string;
    expires_at: string;
  }>;
  acceptance: Readonly<{
    requirement: "strict_provider_acceptance_v0";
    sender_binding:
      | "proof_of_possession"
      | "authenticated_channel"
      | "not_required";
    minimum_full_receipt_retention_seconds: number;
    required_guarantees_sha256: `sha256:${string}`;
    qualification_receipt_sha256: `sha256:${string}`;
  }>;
  provider_profile_sha256: `sha256:${string}`;
  binding_sha256: `sha256:${string}`;
}>;

type ProviderPolicyOrderReceipt = Readonly<{
  schema_version: "provider_policy_order_receipt_v0";
  policy_event: "activated" | "revoked" | "superseded";
  policy_basis_sha256: `sha256:${string}`;
  scope_sha256: `sha256:${string}`;
  provider_order: ProviderOrder;
  receipt_sha256: `sha256:${string}`;
}>;

type ProviderEffectDecisionReceipt =
  | Readonly<{
      schema_version: "provider_effect_decision_receipt_v0";
      decision: "committed" | "no_change";
      authorization_disposition: "consumed";
      binding: ProviderEffectBinding;
      provider_order: ProviderOrder;
      policy_order_receipt_sha256: `sha256:${string}`;
      decided_at: string;
      external_object_ref_sha256: `sha256:${string}`;
      evidence_sha256: `sha256:${string}`;
      receipt_sha256: `sha256:${string}`;
    }>
  | Readonly<{
      schema_version: "provider_effect_decision_receipt_v0";
      decision: "rejected";
      authorization_disposition: "terminally_denied";
      reason:
        | "not_yet_valid"
        | "expired"
        | "revoked"
        | "policy_basis_stale"
        | "policy_denied";
      policy_reason_code: string | null;
      binding: ProviderEffectBinding;
      provider_order: ProviderOrder;
      policy_order_receipt_sha256: `sha256:${string}`;
      decided_at: string;
      receipt_sha256: `sha256:${string}`;
    }>;

type ProviderEffectDecisionProjection = Readonly<{
  schema_version: "provider_effect_decision_projection_v0";
  provider_ref_sha256: `sha256:${string}`;
  provider_identity_epoch_sha256: `sha256:${string}`;
  effect_id_sha256: `sha256:${string}`;
  authorization_id_sha256: `sha256:${string}`;
  goal_ref_sha256: `sha256:${string}`;
  security_context_sha256: `sha256:${string}`;
  binding_sha256: `sha256:${string}`;
  decision: ProviderEffectDecisionReceipt["decision"];
  reason:
    | "not_yet_valid"
    | "expired"
    | "revoked"
    | "policy_basis_stale"
    | "policy_denied"
    | null;
  policy_reason_code: string | null;
  provider_order: Readonly<{
    ordering_domain_sha256: `sha256:${string}`;
    provider_identity_epoch_sha256: `sha256:${string}`;
    sequence: `uint:${string}`;
  }>;
  policy_order_receipt_sha256: `sha256:${string}`;
  external_object_ref_sha256: `sha256:${string}` | null;
  evidence_sha256: `sha256:${string}` | null;
  receipt_sha256: `sha256:${string}`;
}>;

type ProviderAcceptResult =
  | {
      readonly kind: "terminal";
      readonly replay: "new" | "exact";
      readonly projection: ProviderEffectDecisionProjection;
    }
  | {
      readonly kind: "presentation_rejected";
      readonly reason:
        | "malformed"
        | "untrusted_issuer"
        | "binding_mismatch"
        | "sender_authentication_failed";
    }
  | {
      readonly kind: "conflict";
      readonly reason:
        | "effect_identity_reused"
        | "authorization_identity_reused";
    }
  | {
      readonly kind: "epoch_rejected";
      readonly presented_epoch: string;
      readonly current_epoch: string;
      readonly retirement_receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "outcome_unknown";
      readonly recovery: "query_or_exact_replay";
    };

type ProviderQueryResult =
  | {
      readonly kind: "found";
      readonly projection: ProviderEffectDecisionProjection;
    }
  | {
      readonly kind: "tombstone";
      readonly query_sha256: `sha256:${string}`;
      readonly decision: ProviderEffectDecisionReceipt["decision"];
      readonly receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "absent";
      readonly query_sha256: `sha256:${string}`;
      readonly observed_order: ProviderOrder;
      readonly absence_receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "epoch_retired";
      readonly presented_epoch: string;
      readonly current_epoch: string;
      readonly retirement_receipt_sha256: `sha256:${string}`;
    }
  | { readonly kind: "unavailable"; readonly reason_code: string };

interface ProviderEffectAcceptance<Request> {
  accept(
    binding: ProviderEffectBinding,
    request: Request,
    authorization_presentation: Uint8Array,
  ): Promise<ProviderAcceptResult>;

  query(
    query: ProviderEffectQuery,
    query_authentication: Uint8Array,
  ): Promise<ProviderQueryResult>;
}
```

`canonical_request_sha256` covers the provider's versioned canonical request.
`target_sha256` and `payload_sha256` cover the exact target and effect payload.
The provider derives the security context from authenticated tenant, issuer,
subject or client, and sender facts. It compares those values with the binding.
Both the effect key and the authorization key include the provider, audience,
identity epoch, and security-context digest.
`binding_sha256` covers every binding field except itself in schema order. Each
`receipt_sha256` covers every field in that receipt except itself. The provider
computes provider-owned digests rather than trusting caller-supplied values.
`query_sha256` covers both identity keys and the binding digest.

`accept` hides authorization parsing, sender authentication, digest
recomputation, policy lookup, identity reservation, effect commit, and receipt
persistence. `query` authenticates the tenant, issuer, subject or client, and a
recovery principal before it reads history. It is the response-loss recovery
boundary. Public methods do not expose separate check, consume, apply, and
record stages.

`ProviderEffectDecisionReceipt` stays provider-private because it embeds the
full binding. Both `accept` and `query` return
`ProviderEffectDecisionProjection`, and LoopX journals only that projection.
The projection uses a fixed allowlist of digests, opaque reason codes, and
ordering values. It cannot contain raw targets, provider object references,
identities, policy documents, or authorization presentations.

Query authentication can use a current recovery credential. It must match the
original tenant, issuer, and subject or client. A different sender principal
needs an explicit provider recovery delegation bound to `query_sha256`. Query
authentication does not need to reuse an expired authorization presentation.
It permits a historical read only and cannot authorize a new effect.

`ProviderOrder.sequence` is a canonical unsigned decimal integer prefixed with
`uint:`. Two positions are comparable only when their ordering-domain digest and
identity epoch match. The provider writes policy changes, acceptance decisions,
and epoch transitions to that order. A `ProviderPolicyOrderReceipt` makes the
policy event used by a decision independently inspectable.

### 5.5 Acceptance transaction

The provider performs these steps:

1. Parse the request and authenticate the authorization presentation, issuer,
   tenant, subject or client, transport, and required sender mechanism.
2. Derive the trusted security context. Reject a mismatch with the expected
   tenant, issuer, subject or client, or sender digests.
3. Recompute the target, canonical request, payload, profile, requirement, and
   complete binding digests from the values that the provider would apply.
4. Enter the provider's serialization boundary. Verify that the presented
   identity epoch is current and that the effect and authorization scopes are
   identical, then read both identity indexes.
5. Return an exact historical result when both identities and the complete
   binding match. Return a conflict when either identity was bound differently.
6. For a new trusted identity, compare the validity interval and policy basis
   with current provider-visible policy in the same order as revocation.
7. If policy denies the operation, reserve both identities and store a terminal
   rejected receipt. Use `policy_denied` with a bounded opaque code for an
   authenticated denial outside the other named reasons.
8. If policy allows the operation, reserve both identities, consume the
   authorization, commit the effect or factual no-change result, and store the
   receipt in one transaction or equivalent atomic log append.

Malformed, untrusted, binding-mismatched, or sender-invalid presentations fail
before identity reservation. They cannot let an attacker consume a trusted
authorization. A trusted policy denial is terminal because a later policy
change requires a new authorization identity.

Exact replay occurs after caller and presentation authentication but before
current policy and validity checks for a new effect. Replay reads history. It
does not authorize another effect, and a later revocation cannot erase the
original result.

LoopX verifies the source-owned Goal admission and the independent provider
qualification before it journals or dispatches strict work. The provider binds
those receipt digests but does not become their authority. The provider also
checks that the requested guarantee set matches the contract it can enforce.

### 5.6 Revocation and state transitions

```text
unseen trusted authorization
  -> committed       accept ordered first; effect and receipt commit together
  -> no_change       accept ordered first; factual no-change and receipt commit
  -> rejected        not yet valid, expired, stale, revoked, or policy denied
  -> no record       failure before the provider transaction commits

committed | no_change | rejected
  -> same result     exact replay
  -> conflict        changed effect or authorization binding
  -> tombstone       full receipt compacted after its declared horizon

retired identity epoch
  -> epoch_rejected  acceptance cannot use an old epoch
  -> epoch_retired   query returns no deleted operation metadata
```

Revocation and acceptance share the provider's ordering domain. If revocation
orders first, the effect is rejected. If acceptance orders first, its receipt
remains a committed historical fact and revocation affects future
authorizations. This rule orders concurrency. It does not claim a zero-width
race or instant propagation from a separate policy authority.

A LoopX-local revocation has no provider-ordering meaning until the provider
accepts the corresponding policy update. Provider adapters must report the last
provider-visible policy basis rather than relabel local intent as remote fact.
The decision and policy-order receipts carry comparable positions. A revoked
decision cites the exact revocation receipt that ordered before it.

### 5.7 Replay, recovery, and retention

Recovery follows the last proven boundary:

| Proven boundary | Allowed recovery |
| --- | --- |
| No governed journal | No dispatch is attributed to this attempt |
| Journaled intent, no provider result | Authenticate and query the same effect and authorization keys; exact retry is allowed only after a linearizable `absent` witness plus current Goal, qualification, and operation admission |
| Provider terminal decision, local response lost | Return the same projection and receipt digest; do not repeat the effect |
| Provider projection journaled, writeback incomplete | Resume LoopX writeback only |
| Writeback committed, quota incomplete | Resume quota settlement only |
| Provider query unavailable | Keep `outcome_unknown`; do not mint another identity |

The provider creates an `absent` witness only after it verifies the current
identity epoch and reads both identity indexes at one linearizable position.
An old epoch never returns `absent`. It returns `epoch_retired`, and acceptance
returns `epoch_rejected`.

The provider keeps the full receipt for at least the operation's declared
settlement, replay, reconciliation, and audit horizon. Declared exact replay
cannot outlive full-receipt retention.

After that horizon, the provider may compact the receipt to a tombstone that
retains the effect key, authorization identity, binding digest, terminal
decision, receipt digest, and provider identity epoch. A provider may delete
per-operation tombstones only after it durably retires the whole identity epoch
and rejects every request or query from that epoch. After deletion, an old-epoch
query returns only `epoch_retired`; it cannot claim deleted operation metadata.
Expiry alone never releases an identity.

The current and retired epoch registry must not roll back with an effect-store
snapshot. A provider can meet this rule with a separate monotonic store or an
equivalent append-only recovery boundary. If restore cannot prove the latest
epoch, the provider remains unavailable.

### 5.8 Compatibility modes

| Requirement | Provider guarantees | Behavior |
| --- | --- | --- |
| Missing or `off` | Any existing profile | Preserve current governed behavior |
| Strict | Every required guarantee present and pinned | Use provider acceptance |
| Strict | Any guarantee absent, stale, or unqualified | Fail before dispatch |
| Explicit no-blind-retry | Exact owner authorization and declared readback | Persist intent, dispatch once, then query |
| Explicit no-blind-retry | Readback unavailable after dispatch | Retain `outcome_unknown`; no resend |

An existing `loopx_external_effect_receipt_v0` retains its current meaning and
never satisfies strict acceptance. A future receipt may add a compact reference
to `provider_effect_decision_receipt_v0`. Historical receipts do not gain a
stronger interpretation.

## 6. Alternatives and design choices

- **Put provider acceptance in `AuthorityStore`.** Rejected. It would mix LoopX
  coordination with provider effect truth and still could not commit the remote
  effect atomically.
- **Expose check, consume, apply, and record calls.** Rejected. This temporal
  interface makes callers coordinate the same crash and revocation windows.
- **Use a short-lived sender-bound token plus an idempotency key.** Rejected as
  sufficient proof. These mechanisms constrain different risks but do not
  establish current provider policy, atomic consumption, and effect commitment.
- **Consume at a gateway.** Rejected unless the gateway owns the final effect,
  shares its atomic log, or receives an equivalent downstream receipt.
- **Use a universal distributed transaction.** Rejected. Ordinary providers
  cannot participate, and the coordinator would enlarge the authority and
  availability boundary.
- **Treat every denial as consuming.** Rejected before authentication because
  an attacker could burn another caller's identity. Trusted policy denial is
  terminal and replayable.
- **Delete tombstones after a fixed duration.** Rejected without an identity
  epoch fence because an old request could become new again.

## 7. Safety, privacy, and compatibility

- Authorization presentations, sender proofs, credentials, raw payloads, and
  private policy documents stay in provider-private storage and transport.
- The full provider receipt is private. LoopX persists only the fixed
  `ProviderEffectDecisionProjection` allowlist.
- Every projected reason code is a profile-approved token of at most 64 ASCII
  characters. Provider messages and stack traces remain private.
- The provider authenticates the tenant, issuer, subject or client, audience,
  and required sender. A caller-supplied policy basis or authorization ID is
  not authority.
- Providers isolate tenant, issuer, subject or client, sender, audience, and
  identity epoch. Acceptance and query cannot cross those boundaries.
- Strict admission reads the current LoopX-owned qualification state. Provider
  readiness, a profile field, or an older qualification revision cannot replace
  that state.
- Strict admission reads a current source-owned Goal admission witness. An
  exact `GoalRef` or a cached capability binding cannot replace that witness.
- Provider restore or rollback must preserve current policy plus receipt and
  tombstone history. Epoch advance uses the non-rollbackable epoch registry.
- Clock rollback fails closed. Each profile declares clock-skew handling and
  time-source assumptions.
- Key rotation preserves validation of retained receipts or records an
  immutable verification-key reference.
- A strict profile is qualified per implementation, version, deployment
  boundary, and audience. A profile name alone proves nothing.
- Feature-off behavior remains unchanged. Strict operations never fall back.
- Existing providers remain available only under their current contracts unless
  an operation owner explicitly requires this contract.

## 8. Migration and rollback

This RFC changes no persisted format or runtime behavior.

Future integration is additive:

1. Add provider-neutral types, an independent qualification owner, and a
   controlled provider outside production composition.
2. Add source-owned Goal admission, optional operation requirements, and pinned
   provider guarantees.
3. Add a versioned public projection beside the existing effect receipt.
4. Opt in one simulated protected operation.
5. Qualify each real provider and trust boundary independently.

Before enabling a strict operation, freeze its operation schema, canonical
encoding, recovery horizon, provider identity epoch, and rollback procedure.
Mixed versions reject unknown strict requirements before dispatch.

Rollback stops new strict admission and retains all decision receipts,
tombstones, and unresolved outcomes. It never converts strict work to a weaker
mode, deletes unknown operations, or retries under a fresh identity.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary and exclusions |
| --- | --- | --- | --- |
| Exact binding | Change each Goal, Goal-admission, effect, authorization, tenant, issuer, subject or client, audience, action, target, request, payload, policy, sender, requirement, qualification, profile, and epoch field | Presentation rejection or conflict; original result unchanged | Does not validate business usefulness |
| Independent qualification | Mark a profile ready without a current qualification, then revoke or expire the current qualification | Strict work fails before dispatch | Qualification owner remains separate from the provider |
| Goal provenance | Reuse an exact Goal value with a stale, missing, or unavailable source witness | Strict work fails before journaling or dispatch | `GoalRef` remains binding data, not authority |
| One-use authorization | Race the same authorization across distinct effects | One terminal decision reserves the authorization; no second effect | Controlled provider only |
| Exact replay | Concurrent identical calls and historical replay after later operations | Same receipt and one effect | Historical result grants no current LoopX authority |
| Crash atomicity | Stop before and after every provider transaction boundary | No half-consume, half-effect, or receipt-only state | Process crash, not power loss |
| Lost response | Commit, drop response, restart, query, and exact replay | Same receipt; zero recovery effects | Requires query availability |
| Authoritative absence | Query a current epoch before first acceptance, then race acceptance with retry | An ordered `absent` witness or the terminal receipt determines recovery | Plain `missing` is not sufficient |
| Revoke first | Commit revocation before acceptance | Terminal rejection and zero effect | Provider-visible revocation only |
| Accept first | Commit acceptance before revocation | One effect and immutable earlier order | No retroactive cancellation |
| Concurrent revoke | Release both operations at a fixed barrier | Comparable policy and decision receipts explain one stored order | No zero-width race claim |
| Security-context isolation | Reuse valid identifiers across tenants, issuers, subjects or clients, and senders; query with each context | No cross-context conflict, read, or receipt disclosure | Does not prove device integrity |
| Sender isolation | Use a valid authorization with the wrong required sender mechanism or principal | Non-consuming presentation rejection | Authenticated channel is not proof of possession |
| Trusted denial replay | Retry a not-yet-valid, expired, revoked, stale-basis, or ordinary policy-denied authorization after policy changes | Same rejected receipt and bounded reason | A fresh authorization may form a new operation |
| Retention | Compact a receipt, restore a backup, retire an epoch, delete tombstones, and replay an old request | Tombstone or `epoch_retired` blocks a new effect; old epoch never returns `absent` | Horizon is profile-specific |
| Restore fence | Restore the effect store behind the durable epoch registry | Provider remains unavailable or rejects the old epoch | Does not qualify power-loss durability |
| Strict compatibility | Pair strict work with stale, weaker, or unsupported profiles | Typed failure before dispatch | Existing off behavior unchanged |
| Weaker ambiguity | Lose a response from a no-blind-retry provider | Readback or terminal `outcome_unknown`; one dispatch | No exactly-once claim |
| Privacy | Scan fixtures, projections, and docs | No credentials, proof bytes, raw payloads, identities, targets, provider object references, private links, or local paths | Private provider receipts stay outside LoopX journals |

The first executable evidence uses a real SQLite file, separate processes,
deterministic clocks, and transaction barriers. Its verdict qualifies only the
controlled implementation and the tested process-crash boundaries. It does not
qualify independent trust domains, network partitions, power-loss durability,
third-party providers, production scale, or promotion.

## 10. Operational contract

Operators must be able to inspect:

- the operation requirement, qualification state, and pinned provider
  guarantees;
- digests for the provider, identity epoch, Goal admission, security context,
  effect ID, and authorization ID;
- binding, policy-order, and receipt digests;
- terminal decision and provider-order reference;
- full-receipt and tombstone retention horizons;
- whether readback is available;
- `unsupported`, `conflict`, `outcome_unknown`, and provider-unavailable reasons;
- the last provider-visible policy basis without private policy content.

Recovery uses one identity and one provider boundary. An operator must not clear
a tombstone, change an effect ID, or select another provider to make an unknown
operation proceed. Credential revocation blocks new authentication but does not
erase a committed effect or its recovery obligation.

Backup and restore procedures preserve decision records and identity epochs.
If a provider cannot prove that restored state contains the latest receipt and
tombstone history, it stops accepting work. Recovery may advance and publish a
new epoch only through the non-rollbackable epoch registry. Requests from an old
epoch remain rejected, and their queries return `epoch_retired`.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | This bilingual RFC, index, and roadmap mapping | Current ownership audit and public standards review | Documentation governance and public/private scans pass | Revert documents; runtime unchanged |
| M1 | Provider-neutral contract, qualification receipts, controlled SQLite provider, and deterministic conformance suite outside production composition | M0 accepted; canonical encoding and retention decisions resolved | Section 9 controlled matrix, mutation cases, and process-crash recovery pass | Remove isolated fixture and types; no production state |
| M2 | Source-owned Goal admission, optional requirement and guarantee declarations, public receipt projection, and one simulated governed consumer | M1; exact Goal lifetime and receipt-version integration approved | Strict pre-dispatch failure, off parity, lost-response recovery, privacy scan, and full settlement pass | Disable opt-in; retain receipts and unknown outcomes |
| M3 | One independently deployed provider and policy authority | M2; authenticated issuer, audience, sender, tenant, restore, and rotation profiles | Independent-trust-domain revocation, network, restart, backup, and upgrade qualification | Stop new admission; preserve query and reconciliation |
| M4 | One named production provider and protected operation | M3 plus provider-specific operational approval | Real entrypoint, provider receipt, recovery, privacy, and rollback evidence | Disable only the named operation; never downgrade it silently |

No milestone promotes another provider by analogy.

## 12. Open decisions

### 12.1 Canonical request encoding

Capability and provider maintainers own this decision. The options are JSON
Canonicalization Scheme, a versioned binary encoding, or a versioned
provider-native encoding. Use one versioned encoder per operation rather than
one universal encoding. Cross-language golden vectors and mutation tests must
prove that semantically relevant changes alter the digest. This decision blocks
M1.

### 12.2 Authorization presentation

Security and provider maintainers own this decision. V0 can standardize one wire
format or only the resolved semantics. Keep JWT, COSE, SSH signatures, mutual
TLS, and authenticated channels behind adapters. Negative issuer, audience,
tenant, subject or client, sender, expiry, and replay vectors must produce the
same domain results across adapters. This decision blocks M1.

### 12.3 Qualification owner

Capability and security maintainers own this decision. The options are a
dedicated qualification registry or a typed record under an existing
independent owner. Use a dedicated typed owner unless an existing owner already
has the same lifecycle and revocation rules. Evidence must show that provider
registration, enablement, and readiness cannot create or reactivate
qualification. It must also show that qualification revocation blocks new
strict admission. This decision blocks M1.

### 12.4 Sender requirement

Each operation owner selects proof of possession, an authenticated channel, or
no sender binding. Require proof of possession for high-risk cross-host effects.
Use an authenticated channel only when the qualification binds the deployment
and channel principal. Cross-principal replay and mechanism-downgrade tests must
pass. This decision blocks M2 for the simulated operation and M3 for an
independent deployment.

### 12.5 Retention horizon

The governed execution owner selects either one global minimum or an
operation-specific minimum. Use the operation requirement as the minimum and
allow the provider to retain full receipts longer. Crash, restore, reconciliation,
and audit tests must cover the declared interval and the transition to
tombstones. This decision blocks M1.

### 12.6 Policy replication

The provider owner selects co-located policy, ordered push replication, or
ordered pull replication. Keep the transport provider-specific, but require a
`ProviderPolicyOrderReceipt` in the acceptance ordering domain. Concurrent
revoke, stale replica, restore, and rollback tests must prove the recorded
order. This decision blocks M3.

### 12.7 First simulated consumer

Capability maintainers select durable job creation or publication in a
controlled store. Use durable job creation because its provider-owned commit
boundary and later job outcome are separate facts. The fixture must expose
effect count, decision history, query, and deterministic crash barriers. This
decision blocks M2.

## Appendix A: Public references

- [RFC 7519: JSON Web Token](https://www.rfc-editor.org/rfc/rfc7519.html)
- [RFC 7662: OAuth 2.0 Token Introspection](https://www.rfc-editor.org/rfc/rfc7662.html)
- [RFC 8693: OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html)
- [RFC 8705: OAuth 2.0 Mutual-TLS client authentication](https://www.rfc-editor.org/rfc/rfc8705.html)
- [RFC 8785: JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
- [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [RFC 9449: OAuth 2.0 Demonstrating Proof of Possession](https://www.rfc-editor.org/rfc/rfc9449.html)
- [GitHub REST API endpoints for Gists](https://docs.github.com/en/rest/gists/gists?apiVersion=2022-11-28)

GitHub's public Gist create and update contracts are useful examples of an
external provider without this acceptance contract. The documented operations
do not define custom one-use authorization consumption, an operation-result
query, or `Idempotency-Key` semantics. This is a statement about the public
contract, not GitHub's internal implementation.
