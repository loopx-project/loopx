from __future__ import annotations

from typing import Any

GOAL_START_SCHEMA_VERSION = "loopx_goal_start_command_v0"

# Shared Agent guidance, not a quality classifier or an admission gate.
_FIRST_DELIVERY_GUIDANCE = (
    "Use the strongest feasible approach supported by current knowledge for the "
    "first deliverable, including its necessary implementation and validation as "
    "a coherent slice. Do not choose a deliberately weak placeholder merely to "
    "obtain earlier feedback or declare progress. Compare first-result quality, "
    "latency and full cost; neither immediate feedback nor indefinite preparation "
    "is the objective. Useful diagnostic probes remain allowed under the existing "
    "acceptance, feedback and authority limits; identify their purpose and do not "
    "substitute them for the requested deliverable."
)


def goal_planner_contract(*, fine_grained: bool = False) -> dict[str, Any]:
    planner = {
        "required_before_todo_write": True,
        "default_profile": "open_ended_product_direction",
        "profile_selection": (
            "Use open_ended_product_direction when the user's goal is a broad, "
            "fuzzy product direction or new initiative. Use clear_bounded_problem "
            "when the target is a concrete task with a clear success condition. "
            "In both cases, let the model produce a real ordered plan before writes."
        ),
        "profiles": {
            "open_ended_product_direction": {
                "suggested_items_min": 2,
                "suggested_items_max": 5,
                "intent": (
                    "turn an ambiguous product direction into public-safe, ranked "
                    "todo options before execution"
                ),
            },
            "clear_bounded_problem": {
                "item_count_policy": "planner_sized",
                "may_reuse_current_todo_when_it_already_represents_the_plan": True,
                "intent": (
                    "make the approach explicit with enough concise ordered todos, "
                    "without arbitrary caps or management-only filler"
                ),
            },
        },
        "allowed_priorities": ["P0", "P1", "P2"],
        "default_role": "agent",
        "default_task_class": "advancement_task",
        "required_fields": ["priority", "text", "task_class", "action_kind"],
        "public_safe_only": True,
        "budget_policy": "minimum sufficient plan; no fixed-count filler",
        "first_delivery_guidance": _FIRST_DELIVERY_GUIDANCE,
    }
    if fine_grained:
        planner["fine_grained_plan_horizon"] = (
            "write one current runnable checkpoint; keep later options as evidence-linked "
            "planning notes until the existing replan path qualifies the successor"
        )
        planner["maximum_runnable_todos_written_ahead"] = 1
    return planner


def build_goal_start_contract(
    *,
    goal_text: str | None,
    selected_capability_route: dict[str, Any] | None,
    connected: bool,
    agent_type: str,
    issue_fix_commands: dict[str, str],
    fine_grained: bool,
) -> dict[str, Any]:
    contract = {
        "schema_version": GOAL_START_SCHEMA_VERSION,
        "behavior_authority": (
            "ordered_steps + goal_start_contract; skill passes raw arguments after host "
            "detection, executes packet"
        ),
        "slash_syntax": "/loopx <goal text>",
        "goal_text": goal_text,
        "selected_capability_route": selected_capability_route,
        "explicit_invocation_confirms_project_local_state_writes": True,
        "connect_if_needed": True,
        "bootstrap_policy": "create project-local LoopX state only when no matching registry goal exists",
        "planner": goal_planner_contract(fine_grained=fine_grained),
        "priority_ordering": {
            "bucket_order": ["P0", "P1", "P2"],
            "same_priority_tie_breaker": "planner_order_then_todo_write_order",
            "prompt_constraint": "priority then exact write order",
            "storage_contract": "Todo index is same-priority rank; no extra field",
        },
        "execution_invariants": (
            "identity: fresh public-safe agent after verified/active; explicit takeover; "
            "bind/readback before Todo; no peer inference; unknown: "
            "loopx agent-onboard --list-agent-types | route: selected_capability_route only; "
            "never infer from text/URLs; #/activation+#/stop_conditions; review/pasteable "
            "gates | Todo/writeback: Agent advancement_task; User owner/private; business Todo "
            "before work; current evidence + next Todo; refresh/quota; chat not durable | "
            "returned typed quota_guard; one bounded validation+writeback or exact blocker; "
            "optional need/preview/explicit apply"
        ),
        "activation": {
            "after_write": ["refresh-state", "host_loop_activation", "quota should-run"],
            "host_loop_required_after_todo_writeback": True,
            "agent_type": agent_type,
            "agent_type_discovery": "loopx agent-onboard --list-agent-types",
            "host_surfaces": {
                "codex-app": "Codex App heartbeat automation",
                "trae_app": "Trae App heartbeat automation",
                "codex-cli": "visible Codex CLI `/goal <task_body>`",
                "claude-code": "Claude Code native `/loop` after `/loopx <task>` arms LoopX",
                "opencode": "OpenCode `loopx_goal_activate`",
                "pi": "Pi `loopx_goal_activate`",
                "gemini-cli": "agent-driven Gemini CLI loop; every turn enters through quota should-run",
                "cursor-agent": "agent-driven cursor-agent loop; every turn enters through quota should-run",
                "zcode": "agent-driven ZCode loop; turns enter through quota should-run",
                "agy": "agy native /goal loop with schedule wakes; quota entry advisory",
                "kiro-cli": "Kiro CLI native /goal loop; quota entry advisory",
                "deepseek-harness-native": "DeepSeek Harness same-session plugin driver; every turn enters through quota should-run",
                "ark-managed-agent": "one-shot Goal",
                "manual": "external scheduler or manual quota/status loop",
                "other-agent": "custom host loop driver using the returned task body and quota guard",
            },
            "missing_host_loop_policy": (
                "Do not claim autonomous setup complete from registry/quota identity alone. "
                "If the host cannot mutate its loop surface, report the exact pasteable gate."
            ),
            "low_cost_recheck_policy": "missing/unknown/stale/type-changed only",
            "begin_automation_when_quota_allows": True,
            "spend_quota_after_writeback": True,
        },
        "domain_route_hints": {
            "issue_fix_workflow": {
                "when": (
                    "selected_capability_route.capability_id is explicitly "
                    "set to issue-fix"
                ),
                "preview_command": issue_fix_commands[
                    "issue_fix_workflow_plan_template"
                ],
                "decision_command": issue_fix_commands[
                    "issue_fix_feasibility_template"
                ],
                "post_pr_reviewer_request_command": issue_fix_commands[
                    "issue_fix_reviewer_request_template"
                ],
                "post_pr_monitor_command": issue_fix_commands[
                    "issue_fix_pr_lifecycle_template"
                ],
                "writeback": (
                    "persist candidate preflight; only proceed enters feasibility and writes "
                    "its successor; keep private/external/publish/destructive/production gates; "
                    "after PR creation verify reviewer-request, then monitor one PR state bucket"
                ),
            }
        },
        "connected_at_preview_time": connected,
        "stop_conditions": [
            "private material requested before a public-safe todo can be written",
            "credentials or secrets are required",
            "destructive git or production operation would be needed",
            "the host cannot execute shell/CLI/tool calls or persist LoopX state",
            "the host cannot activate or expose the required host loop and no concrete pasteable gate can be shown",
        ],
    }
    if fine_grained:
        contract["turn_mode"] = "fine_grained"
        contract["fine_grained"] = {
            "todo_granularity": "small_checkpoint",
            "turn_work_budget": "coherent_slice",
            "turn_boundary": "settle_after_coherent_slice",
            "replan": "direction_change_or_bounded_chain",
            "checkpoint_accounting": "advancement_only",
        }
    return contract


def build_goal_start_prompt(
    *,
    goal_text: str | None,
    goal_id: str,
    agent_id: str | None,
    fine_grained: bool,
) -> str:
    goal_clause = (
        f"Goal text: {goal_text}"
        if goal_text
        else "Goal text: use the text after `/loopx`; if it is empty, handle bare `/loopx` instead."
    )
    agent_clause = f" Use agent id `{agent_id}` for quota/claim commands." if agent_id else ""
    todo_rule = (
        "plan the broader direction as evidence-linked notes, but write exactly one "
        "current independently verifiable Agent advancement_task Todo; do not write a runnable "
        "successor ahead. Use `[P0]`/`[P1]`/`[P2]`, no `--priority`; User Todo only "
        "for owner/private gates"
        if fine_grained
        else "broad goal 2-5 public-safe items, bounded goal minimum sufficient plan; "
        "`[P0]`/`[P1]`/`[P2]`, no `--priority`, preserve order, prefer Agent "
        "`advancement_task`, User Todo only for owner/private gates. Write one business "
        "Todo before work"
    )
    fine_rule = (
        "\n8. Fine-grained mode: the current Todo must be an independently verifiable checkpoint. "
        "Split independent decisions before work. Scope-bounded work may complete "
        "one or more causally related Agent advancement Todos in the same turn: after each "
        "completion inspect its fresh evidence before creating or claiming the next Todo, "
        "and settle only once after the work. Use the existing replan obligation/ACK path "
        "when evidence changes direction or the bounded-chain review becomes due; never "
        "prewrite a long runnable chain. Protocol/setup and capability re-entry steps stay "
        "inline in the guided transaction, are not Todos, and do not count as advancement "
        "checkpoints or turn settlement."
        if fine_grained
        else ""
    )
    return f"""Plan; returned `ordered_steps` + `goal_start_contract` are authoritative.

{goal_clause}
Goal id: {goal_id}.{agent_clause}

Rules:
1. Identity: use verified binding/active contract; a stable unbound host gets a fresh public-safe agent. Existing lanes require explicit takeover plus bind/readback before Todo; never infer peers from Todo/worktree/arguments. Unknown host: `loopx agent-onboard --list-agent-types`.
2. Capability: only `selected_capability_route`; run entry/admission and its later `capability show`; never infer from text/URLs. Capability state owns facts; generic Todos schedule.
3. Todos: {todo_rule}. {_FIRST_DELIVERY_GUIDANCE}
4. Writeback: current Todo evidence + next executable Todo, then `loopx refresh-state --goal-id {goal_id}` and quota readback. Chat/model summaries are not durable state.
5. Host loop: after Todo write, activate missing/unknown/stale/type-changed Codex App heartbeat automation, CLI/TraeX `/goal`, Claude `/loop`, OpenCode bridge, Ark one-shot, or custom gate. Else surface the exact pasteable gate; never claim autonomy.
6. Run the returned typed `quota_guard`; finish one bounded segment with validation + LoopX writeback or an exact blocker. Setup/planning/claim is not delivery.
7. Optional features: need, preview, explicit apply. Respect private data, credentials, destructive git, production authority, and review rules.{fine_rule}
"""
