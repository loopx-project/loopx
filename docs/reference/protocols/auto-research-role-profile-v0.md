# auto_research_role_profile_v0

`auto_research_role_profile_v0` defines how a LoopX auto-research worker knows
who it is before it loads any role-specific playbook. It bridges three existing
surfaces:

- the shared LoopX control plane, which grants identity and authority;
- worker-local role playbooks, which define how to act inside a phase;
- repository or workspace `AGENTS.md`, which defines long-lived local rules.

The contract exists because Arbor and LoopX use skills differently. Arbor can
keep identity mostly inside a Coordinator / Executor topology, then load skills
as phase playbooks. LoopX is decentralized: several workers may run in separate
Codex sessions over one state graph, so identity must be explicit control-plane
state rather than something inferred from a skill name or pane title.

## Ownership Split

| Surface | Owns | Does not own |
| --- | --- | --- |
| LoopX control plane | `agent_id`, `role_id`, claim, capability token, phase, write boundary, gate state, and stop condition. | The detailed reasoning checklist for an implementation phase. |
| Worker-local role playbook | Commands, checklists, artifact schema reminders, review prompts, and phase-specific failure modes. | Authority to write, promote, merge, publish, or bypass gates. |
| `AGENTS.md` | Repository-local and workspace-local rules such as private boundary, PR hygiene, first-screen review, protected paths, and local launch policy. | Dynamic role assignment or current frontier selection. |
| Host launcher | Visible panes, environment variables, attach/stop controls, and takeover affordances. | Research truth, promotion decisions, or hidden scheduling authority. |

This split keeps playbooks useful without letting them become a second source of
identity. A worker can load the same `loopx-auto-research` playbook in different
roles, but the profile tells it which section applies and which writes are
allowed.

## Profile Shape

The profile is public-safe and small enough to embed in a launcher packet,
frontier item, bootstrap prompt, or future kernel API.

```json
{
  "schema_version": "auto_research_role_profile_v0",
  "goal_id": "loopx-auto-research-demo",
  "agent_id": "research-executor",
  "role_id": "research_executor",
  "display_name": "Research executor",
  "phase": "attempt_running",
  "capability_token": "research_executor",
  "todo_id": "todo_auto_research_demo_001",
  "hypothesis_id": "hyp_state_a2a_round",
  "allowed_actions": ["claim_attempt", "edit_allowed_scope", "run_dev_eval", "write_evidence"],
  "write_scope": ["solution.py", "experiments/**"],
  "protected_scope": ["task.py", "eval.py", "data/**"],
  "required_skill": "loopx-auto-research",
  "skill_section": "Research executor",
  "agents_overlay": ["workspace/AGENTS.md"],
  "stop_conditions": [
    "quota should-run returns false",
    "frontier is empty or claimed by another agent",
    "protected scope would be edited",
    "private material or credentials are required",
    "operator gate is projected"
  ],
  "handoff_outputs": [
    "research_evidence_event_v0",
    "branch_or_artifact_ref",
    "retry_or_retirement_rationale"
  ],
  "successor_todos": [
    {
      "after_action": "run_dev_eval",
      "when": "dev_supported_without_holdout",
      "target_agent_id": "research-executor",
      "target_role_id": "research_executor",
      "task_class": "advancement_task",
      "action_kind": "run_holdout_eval",
      "text": "Run held-out validation for the dev-supported hypothesis."
    }
  ],
  "continuation_policy": {
    "schema_version": "auto_research_continuation_policy_v0",
    "successor_source": "role_profile.successor_todos",
    "required_holdout_improvement_count": 2,
    "unmet_target_rule": "when the target is unmet and a successor condition is satisfied, create or link that successor",
    "no_followup_rule": "no-follow-up is valid only after target reached, projected blocker/gate, or evidence-backed retirement"
  }
}
```

Required fields:

- `goal_id`, `agent_id`, `role_id`, `phase`, and `todo_id` identify the current
  worker and work item.
- `capability_token` must match a capability in
  `auto_research_role_state_machine_v0`.
- `allowed_actions`, `write_scope`, `protected_scope`, and `stop_conditions`
  bound what the worker may do.
- `required_skill` and `skill_section` route the worker to the correct how-to
  instructions after identity is resolved.

Optional fields such as `hypothesis_id`, `agents_overlay`, `handoff_outputs`,
`successor_todos`, and `continuation_policy` make the profile more ergonomic but do not grant
authority. A successor todo declaration is a small role-local handoff rule: when
the worker completes `after_action` and the `when` condition is satisfied, the
pane-local tick may write the named LoopX todo for `target_agent_id`. It is not a
graph-wide planner, and it must still pass quota, todo metadata, and
public/private boundary checks before another agent can run it.
`continuation_policy` is the compact acceptance guard for no-follow-up: if the
target is not met and a role-declared successor condition is satisfied, the
worker must create or link that successor instead of closing the lane.

For failure continuation, the successor's title and `unblocks_todo_id` share
the failed evidence Todo from `research_failure_continuation_v0.source_todo_id`.
Several summaries of that lineage therefore create or reuse one identical
successor without rebinding its original parent. A historical successor may
retain its original summary parent when the complete canonical records prove
that summary belongs to the same failed evidence lineage; all other intent
fields still pass the native duplicate check. Each completed summary keeps
its own `successor_todo_ids` link to that successor. Other role handoffs still
bind the selected source Todo. These links grant no execution or claim rights;
the canonical owner continues to reject same-title requests with different
lineage or other intent. Historical-parent reuse binds create to the inspected
provider revision; a concurrent change refuses without rebinding and requires
a fresh read before retry.

## Resolution Order

Every visible auto-research worker should resolve its instructions in this
order:

1. Read the role profile from the launcher packet, frontier item, or bootstrap
   prompt.
2. Run `quota should-run --goal-id ... --agent-id ...` and confirm the selected
   todo still matches the profile.
3. Read the current role/state-machine contract for allowed transitions.
4. Load the required skill and only the section named by `skill_section`.
5. Read applicable `AGENTS.md` overlays for repository and workspace rules.
6. Act within `allowed_actions` and `write_scope`, then write only the listed
   handoff outputs and role-declared successor todos.

If any source conflicts, the worker fails closed in this order:

1. operator gate or private/security boundary;
2. control-plane quota, claim, capability, or write-scope mismatch;
3. repository `AGENTS.md` safety rule;
4. skill checklist disagreement;
5. launcher pane label or cosmetic prompt text.

## Auto-Research Role Profiles

The v0 demo should expose four logical always-on profiles. A host may render
fewer panes only when the merged duties are explicit in the profile and the
records still name the role that produced each transition.

| Role id | Skill section | Primary phase | Writes | Must stop when |
| --- | --- | --- | --- | --- |
| `research_curator` | Research curator | `contract_ready`, `promotion_gate` | `research_contract_v0`, owner gate todos, protected-boundary notes. | The next step would select a winner, run an experiment, or publish unsupported evidence. |
| `hypothesis_proposer` | Hypothesis proposer | `hypothesis_proposed`, `retired` | `research_hypothesis_v0`, successor todos, no-follow-up rationale. | Novelty requires the same source that inspired the idea, or negative evidence would be hidden. |
| `research_executor` | Research executor | `frontier_selected`, `attempt_running` | Branch refs, dev/holdout eval evidence, retry packets, role-declared successor todos. | Protected scope changes, promotion decisions, or private/raw artifacts are needed. |
| `evaluator_promoter` | Evaluator/promoter | `evidence_recorded`, `evaluated`, `promotion_gate` | Held-out validation evidence, evaluation summary, promotion/retirement candidates, gate todos. | Evidence is dev-only but would be presented as promoted, or held-out data is missing when required. |

Future roles such as gate steward, synthesis narrator, and frontier janitor are
split candidates, not required v0 panes. They should be introduced only when
evidence from the demo shows that a transition duty needs a separate owner.

## Worker-Local Playbook Strategy

Use one worker-local `loopx-auto-research` playbook that contains role sections:

- `Research curator`
- `Hypothesis proposer`
- `Research executor`
- `Evaluator/promoter`
- `Visible takeover and stop controls`

This keeps each visible worker aligned without exposing auto-research as a
global LoopX skill for ordinary project agents. Split into role-specific
worker playbooks only after a visible run shows repeated confusion that a single
role-routed playbook cannot prevent. The split decision should cite evidence
such as wrong section loading, unauthorized writes, hidden negative evidence, or
missed stop conditions.

The playbook body should not invent current work. It should tell the worker to
read the role prompt/profile context and run role-local quota/frontier commands
through the pane-local LoopX wrapper inside the Codex TUI. This mirrors Arbor's
useful "load a checklist at the exact phase" behavior while preserving LoopX's
decentralized identity model.

## Demo Launcher Implications

A visible tmux or terminal launcher should silently materialize the profile and
start one fresh interactive Codex CLI TUI per role:

```bash
export LOOPX_GOAL_ID=loopx-auto-research-demo
export LOOPX_AGENT_ID=research-executor
export LOOPX_ROLE_ID=research_executor
export LOOPX_ROLE_PROFILE_REF=auto_research_role_profile_v0
exec codex -c model_reasoning_effort=high -C "$LOOPX_PROJECT" "$ROLE_PROMPT"
```

The pane title is cosmetic. The profile and quota/frontier projection are the
authority, but raw profile/frontier JSON should stay in local artifacts or an
explicit machine channel. The launcher must keep attach, interrupt, and stop
commands visible so the user can take over without reading hidden logs.

## Acceptance Checks

An implementation satisfies this contract when:

- launcher and frontier packets expose `auto_research_role_profile_v0` for each
  visible worker without printing raw profile JSON on the first screen;
- each profile names `agent_id`, `role_id`, `phase`, `capability_token`,
  `allowed_actions`, `write_scope`, `protected_scope`, `required_skill`,
  `skill_section`, and `stop_conditions`;
- the default demo uses the four logical v0 role identities, with owner takeover
  as a visible control surface rather than a research role;
- the role-aware skill says identity comes from the profile and quota/frontier,
  not from the skill itself;
- the profile and skill make no-follow-up depend on evidence against the
  continuation target, not on one successful promotion candidate alone;
- `AGENTS.md` overlays can add stricter local rules but cannot expand a role's
  authority beyond the control-plane profile;
- validation proves no leader/coordinator pane is required for the demo to be
  understandable or interruptible.
