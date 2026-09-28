# RFC: Human Attention Wishlist v0

- Status: Accepted
- Supersedes / closes: none
- Proposed by: LoopX maintainers
- Date: 2026-08-14
- Scope: a typed, non-blocking human-attention subtype plus a bounded agent
  authoring sidecar; no new capability, task store, authority grant, scheduler,
  or quota lane
- Source baseline: LoopX `4e4c03621`
- Tracking issue: [#3179](https://github.com/huangruiteng/loopx/issues/3179)
- Language note: the
  [Chinese version](./human-attention-wishlist-v0.zh-CN.md) and this English
  version are semantic mirrors. A difference between them is a defect.

---

## 0. Example

An agent completes and validates the selected product task. During the work it
notices a high-leverage opportunity: a short preference answer, an introduction
to a relevant maintainer, or review of one concrete assumption could improve a
later slice. The selected task did not require that human action, and the agent
already has independent work it may continue.

Today the agent has three poor choices:

1. turn the opportunity into a `user_gate`, which falsely blocks delivery;
2. record an ordinary `user_action`, which can create an immediate notification
   even though the request is optional; or
3. leave the observation in chat or discard it, so later turns cannot use it.

The desired behavior is a fourth choice:

```text
complete and validate the selected work
  -> optionally capture zero or one evidence-backed human wish
  -> write it as a non-blocking sidecar
  -> preserve selected work, quota, authority, and notification behavior
```

The user can review wishes later or see one piggybacked on an already-visible
material result. The existence of a wish never creates a standalone
notification and never stops the agent's main flow.

## 1. Problem

LoopX already distinguishes blocking `user_gate` todos from non-blocking
`user_action` todos and can continue an independent agent lane around a scoped
gate. It also asks heartbeat agents to retain high-value losing candidates.
What is missing is an exact authoring and projection contract for optional
human leverage discovered while normal work proceeds.

The current seams do not compose into that outcome:

- heartbeat guidance says to record a high-value candidate, but it does not
  define a wishlist write command or lifecycle;
- `todo_write_hint` exposes gate, user-action, and agent-todo templates, but no
  non-notifying optional-human template;
- an open `user_action` can enter the user notification channel even when it is
  non-blocking;
- the retired `todo suggest` command only emitted an advisory prompt; the
  retired `todo capture-followups` command wrote only agent work. Neither
  provided a durable human-wish route;
- the compact turn envelope carries required execution and writeback actions,
  but no signed optional sidecar hint.

The result is an avoidable production bias: agents either promote optional
value into a blocker, create noisy reminders, or forget it.

## 2. Decision

LoopX will model human-facing attention as three semantic kinds:

| Kind | Stored form | Blocks selected work | Grants authority | Default delivery |
| --- | --- | --- | --- | --- |
| `gate` | `task_class=user_gate` | Only when its explicit scope covers the action | Only through existing typed decision-scope receipts | Interrupt with a concrete ask |
| `request` | `task_class=user_action`, field absent or `request` | No | No | Existing non-blocking notice behavior |
| `wish` | `task_class=user_action`, `human_attention_kind=wish` | No | No | Piggyback or digest only |

`user_gate` remains the only user-todo class that may carry blocking scope or
consume decision authority. A wish is an agent-authored hypothesis about
optional human leverage, not a weak approval and not a deferred gate.

The initial implementation reuses the canonical user-todo store. It does not
add `user_wish` to `task_class`, create another task database, or introduce a
new built-in capability. The nearest owners remain:

- `control_plane/todos` for typed metadata, authoring, deduplication, and
  projection;
- `control_plane/work_items/interaction_contract` for notification and agent
  channel semantics;
- `control_plane/heartbeat` and `loopx-project` for the active model-facing
  authoring rule;
- `control_plane/quota/turn_envelope` for the compact signed sidecar hint.

## 3. Typed Contract

### 3.1 Stored fields

The smallest stored extension is:

```json
{
  "task_class": "user_action",
  "human_attention_kind": "wish",
  "wish_key": "ux:onboarding-preference",
  "bound_agent": "agent-id",
  "text": "If convenient, review the proposed onboarding default.",
  "evidence": "todo_1234 or PR #123"
}
```

Rules:

- `human_attention_kind` is a typed enum with `request|wish` on
  `task_class=user_action`. Its absence means `request` for backward
  compatibility.
- `wish_key` is a stable, public-safe deduplication key. It is required for a
  wish and has no authority semantics.
- Existing multi-agent user-todo binding applies: a wish must declare
  `bound_agent` or `goal_bound` where the current user-todo contract requires
  one.
- Existing `text`, `evidence`, `updated_at`, completion, supersede, and archive
  behavior provide content and lifecycle. v0 does not add a second status
  machine.
- `action_kind` remains an extensible domain token. Runtime code must not infer
  wish semantics from substrings in `text` or `action_kind`.

### 3.2 Illegal combinations

A wish must fail validation if it carries any of:

- `blocks_agent` or `global_gate`;
- `decision_scope` or `required_decision_scopes`;
- `decision_outcome`;
- `unblocks_todo_id`;
- a task class other than `user_action`.

Completing or accepting a wish does not consume an authority requirement. If
the requested follow-up later needs private access, production mutation,
publication, or another protected action, that exact action still needs a
normal `user_gate` and decision scope.

## 4. Authoring Surface

The first active call site is the shipped heartbeat path. Add a narrow helper
under the existing todo CLI, provisionally:

```bash
loopx todo capture-wishes \
  --goal-id <goal-id> \
  --agent-id <registered-agent> \
  --wish-key <public-safe-key> \
  --wish '<optional human leverage>' \
  --evidence '<public-safe pointer>'
```

The helper is a convenience writer over canonical user todos, not a new store.
It must:

- write `task_class=user_action human_attention_kind=wish`;
- bind the response continuation to the authoring agent unless an explicit
  goal-wide binding is supplied;
- require a compact public-safe evidence pointer;
- accept at most one newly recorded wish per material turn;
- update evidence for an existing open `wish_key` rather than append a
  duplicate;
- cap active wishes per agent and return a typed `max_items_exceeded` or
  `duplicate_updated` result;
- perform no quota spend and claim no delivery progress by itself.

The exact command shape is open to implementation review. The behavior above
is the contract. The retired `todo capture-followups` batch command is not an
extension point; a future implementation must use a typed wish-specific helper
or an explicit canonical `todo add` option that cannot silently change role or
task class.

## 5. Skill and Heartbeat Generation Rule

The generated heartbeat prompt and `loopx-project` skill should add one compact
rule after primary validation and before accountable refresh/spend:

> Primary work comes first. If this material turn revealed an evidence-backed
> opportunity where human input has comparative advantage but the selected
> action does not depend on it, optionally capture zero or one wish. Do not
> invent a wish to satisfy the protocol, interrupt the main flow to ask it, or
> convert a permission/runtime gap into a wish.

Qualifying examples include:

- a product preference whose answer can improve a later slice while the agent
  can safely use a documented default now;
- an optional introduction, review, or domain judgment with concrete expected
  value;
- a bounded evidence request that improves confidence but is not required for
  the selected work.

Non-qualifying examples include:

- credentials, private material access, destructive action, production
  mutation, publication, or an explicit repository review rule: these remain
  gates when the selected action needs them;
- runtime capability discovery or ordinary agent-repair work;
- an unranked idea with no evidence or expected value;
- work the agent can simply add to its own runnable backlog.

The rule is intentionally `0..1`, not `1`. Wishlist capture must not become a
new output quota or a reason for low-value prose.

## 6. Interaction and Notification Semantics

Wishes require a separate projection lane:

```json
{
  "user_todo_summary": {
    "wishlist_open_count": 1,
    "wishlist_items": [
      {
        "todo_id": "todo_wish_123",
        "wish_key": "ux:onboarding-preference",
        "text": "If convenient, review the proposed onboarding default."
      }
    ]
  }
}
```

They must be excluded from:

- `gate_open_items` and quota/interaction blocking or action-required counts;
- `user_channel.actions` and `user_channel.action_required`;
- the predicate that turns a non-blocking `user_action` into immediate
  `user_channel.notify=NOTIFY`;
- `needs_user_or_controller`, selected-todo ranking, work-lane obligation,
  quota allocation, and scheduler cadence.

The canonical todo-source lifecycle `open_count` may still include an open
wish so source completeness remains true. Consumers must use the separate
wishlist and blocking/action projections instead of treating that aggregate
lifecycle count as routing authority.

Delivery policy is `piggyback_or_digest`:

- when a turn already returns a material user-visible result, it may append at
  most one newly captured wish;
- a wish alone never changes `DONT_NOTIFY` to `NOTIFY`;
- status and full review packets may show the bounded wishlist lane;
- a later digest consumer may summarize wish deltas, but no recurring wishlist
  scheduler belongs in v0.

## 7. Compact Packet Contract

The full quota payload should extend `todo_write_hint` with the exact wishlist
writer template. The compact turn envelope should expose a distinct optional
sidecar instead of placing it in required `next_cli_actions`:

```json
{
  "writeback": {
    "optional_sidecars": {
      "wishlist_capture": {
        "allowed": true,
        "required": false,
        "max_new": 1,
        "timing": "after_primary_validation",
        "delivery": "piggyback_or_digest",
        "affects_execution": false
      }
    }
  }
}
```

This field is model-facing behavior. Adding it must version the turn-envelope
action-signature coverage and preserve full/compact semantic parity. An
unsigned prose field is not sufficient because hosts could silently omit it or
models could mistake it for a required action.

The sidecar is eligible only on a material turn whose primary action remains
the selected LoopX work. It is not a fallback when `should_run=false`, a
replacement for writeback/settlement, or a new effect that settles the turn.

## 8. Wish Response and Promotion

The user may ignore, complete, decline/supersede, or accept a wish through the
existing todo lifecycle. A later convenience command may atomically:

1. complete the exact wish;
2. create a concrete agent successor; and
3. preserve the wish id as lineage evidence.

That transition promotes work priority, not authority. Any successor requiring
a protected decision scope remains gated until the normal authority receipt
exists. v0 does not infer acceptance from chat text or a generic completed
`user_action`.

## 9. Public and Private Boundary

Wishlist generation must use the same public-safe todo boundary as existing
state:

- no credentials, raw logs, transcripts, private source bodies, local absolute
  paths, internal links, or private organizational context;
- evidence is a compact pointer or reusable public-safe summary;
- private opportunities remain in owner-approved ignored local state unless
  they can be generalized safely;
- a wish cannot authorize reading the private material it references.

The writer should reuse the existing follow-up safety scan and extend it only
with typed wish validation. It must not add a second prose denylist as routing
authority.

## 10. Smallest Useful Implementation Slice

Ship one cohesive behavior slice:

1. normalize and validate `human_attention_kind=request|wish` plus `wish_key`
   on user todos;
2. add one bounded, evidence-required todo writer with deduplication;
3. project `wishlist_items` separately and prove that wishes do not enter the
   user notification channel;
4. add the exact optional-capture rule to the generated heartbeat and
   `loopx-project` skill;
5. expose the signed compact optional-sidecar hint with a versioned action
   signature.

This slice has a real active caller: every eligible shipped heartbeat already
performs primary validation and todo writeback. It does not require a dashboard,
new capability, recurring scheduler, acceptance metric, or auto-promotion
workflow before it is useful.

## 11. Validation Criteria

The first implementation is acceptable when focused tests prove:

1. adding the same wish to an otherwise identical quota state leaves
   `should_run`, selected todo, `must_attempt`, delivery permission, spend
   policy, and scheduler action unchanged;
2. a wish never creates `user_channel.action_required=true` or changes
   `DONT_NOTIFY` to `NOTIFY`;
3. `user_gate` still takes precedence when an exact authority dependency exists;
4. writer validation rejects every illegal field combination and requires
   public-safe evidence;
5. repeated `wish_key` capture updates rather than duplicates, and the active
   cap is deterministic;
6. full quota and compact turn-envelope packets preserve the same optional
   sidecar semantics under the new action-signature coverage version;
7. the shipped full, compact, brief, and thin heartbeat prompts preserve the
   `0..1`, post-validation, non-interrupting rule;
8. one model-behavior scenario executes the selected primary work and may
   capture a qualifying wish without replacing or backtracking from that work;
9. public/private scans reject local paths, credentials, raw evidence, and
   private material in fixtures or docs.

Wishlist capture itself is no-spend. A material primary turn still settles and
spends through the existing causal writeback contract.

## 12. Alternatives Considered

### Add `user_wish` as a new task class

Rejected for v0. It would widen every task-class switch, CLI validator, state
projection, compatibility path, and external sink even though storage,
ownership, and lifecycle are already those of a non-blocking user action.

### Use ordinary `user_action` with an `action_kind` convention

Rejected. Current interaction behavior can notify every visible user action,
and substring or prose classification would make routing authority ambiguous.

### Keep wishes only in an advisory suggestion

Rejected. An advisory proposal requires later promotion, so it cannot preserve
a small opportunity discovered as a normal turn side effect. Retiring the
standalone suggestion command does not close this lifecycle gap.

### Write every opportunity as an agent todo

Rejected. Some opportunities specifically depend on human preference,
relationships, judgment, or optional evidence. Making them executable work
misstates ownership and can pollute the runnable frontier.

### Put wishlist generation only in prompt prose

Rejected. Without a typed writer, projection, and signed compact packet hint,
the behavior drifts across hosts and can silently become either notification
spam or forgotten chat context.

## 13. Follow-on Work After Evidence Exists

Only after the first slice produces real usage evidence should LoopX consider:

- a user preference or digest policy for wishlist visibility;
- accept/decline convenience commands and atomic agent-todo promotion;
- value/acceptance metrics based on typed lifecycle events;
- external projection sinks that render the existing wishlist lane.

These are not required for v0 and must not delay the non-blocking authoring
contract.

## 14. Open Questions

1. Should the helper be `todo capture-wishes`, or should canonical `todo add`
   accept an explicit human-attention kind?
2. Should v0 cap active wishes per agent, per goal, or both?
3. Should piggyback delivery be part of the initial slice, or should the first
   implementation expose wishes only through status/review packets?
4. Which public-safe lifecycle field should record an explicit user decline
   before a dedicated typed outcome exists?

## 15. Relationship to Existing Contracts

- [Decision Scope v0](../../reference/protocols/decision-scope-v0.md) remains
  the authority source for gates and proves why a wish cannot satisfy a
  protected action.
- [Interaction Pattern Catalog](../../concepts/interaction-pattern-catalog.md)
  defines scoped-gate fallback; wishlist capture extends the non-blocking side
  without changing IP-003 gate precedence.
- [Project agent todo contract](../../project-agent-todo-contract.md) remains
  the canonical todo ownership and lifecycle surface.
- [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) remains the turn
  result and settlement contract; wishlist capture is an optional sidecar, not
  a new result kind.
- [Model behavior qualification v0](../../reference/protocols/model-behavior-qualification-v0.md)
  owns the real-packet proof that the optional hint does not displace selected
  work or turn a non-blocking item into a gate.
