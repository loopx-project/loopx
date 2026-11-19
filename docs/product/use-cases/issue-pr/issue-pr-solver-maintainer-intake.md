# Issue/PR Solver Maintainer Intake Packet

This packet helps a LoopX maintainer decide whether an external or partner
issue/PR solver should become a high-value proof anchor. The solver may live
outside LoopX. LoopX's job is to make the maintainer decision, boundary,
evidence, and showcase path explicit.

The packet is intentionally pre-execution. It turns an issue/PR solver
opportunity into reviewable LoopX state.

## When To Use It

Use `issue_pr_solver_maintainer_intake_v0` when:

- a maintainer is considering a repository, issue, or PR as a public proof
  anchor;
- a partner solver or host product may do the implementation work;
- LoopX needs to track owner routing, allowed actions, evidence, and
  graduation into a showcase;
- the opportunity is not yet a normal agent todo because fit, consent, or
  boundaries are unclear.

Do not use it for ordinary local bug fixes, private customer work, benchmark
tasks, production incidents, or any repository where the public/private boundary
is unclear.

## Packet Shape

```yaml
issue_pr_solver_maintainer_intake_v0:
  candidate:
    repo_handle: "owner/repo or public-safe alias"
    issue_or_pr_handle: "optional public issue or PR id"
    source_status: public | needs_review | private | forbidden
    freshness: fresh | stale | unknown
  repo_fit:
    task_type: bug | docs | test | small_feature | triage | unknown
    expected_user_value: low | medium | high
    reproduction_clarity: clear | partial | missing
    maintainer_interest: confirmed | likely | unknown | rejected
    anchor_reason: "Why this is worth considering as a proof path."
  allowed_actions:
    observe: true
    triage: true
    reproduce: false
    draft_plan: false
    prepare_patch: false
    open_pr: false
    post_comment: false
  owner_routing:
    maintainer_contact: "public-safe handle or channel label"
    route_owner: "human | registered_peer | partner_solver | unknown"
    review_required_before: ["patch", "public_comment", "showcase"]
  evidence_boundary:
    allowed_evidence:
      - issue handle
      - patch summary
      - CI status
      - maintainer review outcome
    forbidden_evidence:
      - unredacted runtime material
      - unpublished source context
      - sensitive local paths
  stop_conditions:
    - source boundary unclear
    - maintainer interest rejected
    - patch would require protected write scope
    - validation cannot be represented compactly
  showcase_consent:
    status: not_requested | requested | approved | rejected
    allowed_surface: none | anonymized_card | public_case | launch_material
```

The fields are deliberately small enough to show in a management card. A real
adapter may store more internal detail, but public LoopX state should keep only
compact handles, labels, and evidence pointers.

## Fit Checklist

A candidate is a good public anchor when most answers are positive:

- the issue or PR is public and stable enough to cite by handle;
- the task is small enough for a bounded solver attempt;
- the expected user value is easy to explain;
- reproduction or validation can be checked without private material;
- the maintainer or repo owner has a clear route for review;
- the result can produce a visible signal: merged PR, rejected patch with
  useful reason, accepted plan, CI result, or documented blocker;
- the case demonstrates LoopX management value, not only raw solver ability.

A candidate should stay a signal, not an anchor, when:

- it requires broad repository ownership or protected production action;
- it mainly tests model coding ability without long-running control-plane
  value;
- the owner route is missing;
- the only possible evidence would be unredacted runtime material, private
  traces, or private source material;
- showcase consent is unknown and the case cannot be anonymized safely.

## Allowed Action Ladder

Allowed actions should start narrow and be promoted explicitly:

| Level | Allowed action | Promotion evidence |
| --- | --- | --- |
| Observe | record public handle, labels, source status, and freshness | source boundary is clear |
| Triage | classify task type, user value, owner route, and first validation | maintainer fit is plausible |
| Reproduce | run or describe a compact validation surface | validation can stay public-safe |
| Draft plan | propose patch scope and risk without changing source | owner review path exists |
| Prepare patch | create local patch or branch in an approved workspace | write scope and review gate are approved |
| Open PR / comment | publish externally | explicit maintainer and publication gates are approved |
| Showcase | turn outcome into card or case | showcase consent is approved or anonymization is safe |

The default level is Observe. Each higher level must be visible as a gate,
todo update, or review event.

## LoopX Writeback

The intake can produce several normal LoopX objects:

- `signal_v0` for unselected opportunities;
- `anchor_v0` for selected proof paths;
- agent todo for triage, validation, or handoff;
- user todo for maintainer approval or consent;
- `review_event_v0` for accepted/rejected candidate decisions;
- `feedback_signal_v0` for owner corrections or route changes;
- showcase card only after consent and boundary checks.

LoopX should not treat every public issue as backlog. The maintainer chooses a
few anchors; everything else remains a searchable signal or is archived.

## Acceptance Criteria

A maintainer intake is successful when:

- the repo/issue/PR handle, source status, and freshness are explicit;
- allowed actions are visible and start at Observe by default;
- owner routing names who must review patch, comment, or showcase steps;
- evidence boundaries say what can and cannot be stored or shown;
- stop conditions are concrete enough for an agent to halt without guessing;
- showcase consent is separate from implementation success;
- selected candidates promote to normal LoopX todos, gates, or anchors instead
  of staying only in chat.

This keeps open-source PR-led growth aligned with the core LoopX promise:
choose high-value anchors, keep humans in control, and make solver work
reviewable by evidence rather than hype.
