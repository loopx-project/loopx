#!/usr/bin/env python3
"""Smoke-test the per-goal opt-in gate for the explore planners.

The experimental planners (todo-branch-plan and worker-branch-plan) may only
complement LoopX, never act as a second control plane, so both are gated on
the registered goal's goal_boundary.orchestration contract:

- default (no explore_harness opt-in): explicit disabled packet, no lanes;
- enabled=true, spawn_allowed=false: read-only analysis with no suggested
  claim/lease commands anywhere in the packet;
- enabled=true, spawn_allowed=true: suggested commands, still dry-run only,
  with lane width capped by max_children rather than only the planner's own
  ceiling;
- the planner ceilings (MAX_WORKER_LANES / MAX_BRANCH_WIDTH) stay the outer
  bound even for generous max_children.

Also covers: the opt-in bit failing closed on non-boolean values; the
registered goal's spawn_policy being the ONLY honored source (a registry
project_asset.orchestration key is ignored, because the quota/status boundary
never reads it); the gate riding the existing quota projection
(compact_orchestration_policy passthrough, orchestration_policy_summary
suffix, quota goal_boundary); a goal-pinned explore_harness.profile
overriding the planner's requested profile; and the real CLI returning the
right packet for every gate state, including unregistered goals and an
invalid pinned profile.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "control_plane"))

from canonical_authority_fixture import initialize_canonical_authority  # noqa: E402
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection  # noqa: E402
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source  # noqa: E402
from loopx.control_plane.todos.todo_summary import canonical_todo_read_record, structured_todo_item  # noqa: E402

from loopx.capabilities.explore.todo_branch_plan import (  # noqa: E402
    MAX_BRANCH_WIDTH,
    build_explore_todo_branch_plan,
)
from loopx.capabilities.explore.worker_branch_plan import (  # noqa: E402
    MAX_WORKER_LANES,
    build_explore_worker_branch_plan,
    resolve_explore_harness_gate,
)
from loopx.cli_commands.explore import _goal_orchestration_boundary  # noqa: E402
from loopx.orchestration import (  # noqa: E402
    compact_orchestration_policy,
    orchestration_policy_summary,
)
from loopx.control_plane.quota.goal_boundary import goal_boundary as _quota_goal_boundary  # noqa: E402


GOAL_ID = "explore-gate-smoke"
AGENT_ID = "agent_main"


def _todo(index: int, *, family: str, priority: str = "P1") -> dict[str, object]:
    return {
        "todo_id": f"todo_gate_{family}_{index}",
        "index": index,
        "status": "open",
        "text": f"[{priority}] Probe {family} facet {index}",
        "task_class": "advancement_task",
        "required_write_scopes": [f"artifacts/{family}/**"],
    }


TODOS = [_todo(index + 1, family=f"fam{index:02d}") for index in range(6)]


def _plan(orchestration: dict[str, object] | None, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "goal_id": GOAL_ID,
        "todos": TODOS,
        "agent_id": AGENT_ID,
        "worker_width": 4,
        "orchestration": orchestration,
    }
    kwargs.update(overrides)
    return build_explore_worker_branch_plan(**kwargs)


def _assert_read_only_boundary(plan: dict[str, object]) -> None:
    boundary = plan["boundary"]
    for key in ("writes_state", "claims_todos", "acquires_leases", "starts_agents", "changes_quota"):
        assert boundary[key] is False, (key, boundary)


def check_default_off() -> None:
    """No opt-in (missing boundary, empty boundary, spawn-only boundary,
    explicit enabled=false, or a non-boolean enabled value) always yields the
    disabled packet: the opt-in bit fails closed."""

    for orchestration in (
        None,
        {},
        {"spawn_allowed": True, "max_children": 5},
        {"explore_harness": {"enabled": False}},
        # Strings never open the deny-by-default gate, whichever way a
        # hand-edited registry spells them.
        {"explore_harness": {"enabled": "false"}},
        {"spawn_allowed": True, "max_children": 5, "explore_harness": {"enabled": "true"}},
        {"explore_harness": {"enabled": 1}},
    ):
        plan = _plan(orchestration)
        assert plan["ok"] is True and plan["enabled"] is False, plan
        assert plan["schema_version"] == "loopx_explore_worker_branch_plan_v0", plan
        gate = plan["orchestration_gate"]
        assert gate["state"] == "disabled", gate
        assert gate["reason"] == "explore_harness_opt_in_required", gate
        assert plan["selected_worker_branch_count"] == 0, plan
        assert plan["selected_worker_branches"] == [], plan
        assert plan["rejected_worker_branches"] == [], plan
        contract = plan["required_contract"]["spawn_policy"]
        assert contract["explore_harness"]["enabled"] is True, contract
        assert "max_children" in contract, contract
        _assert_read_only_boundary(plan)


def check_analysis_only() -> None:
    """enabled=true without spawn permission ranks lanes but never emits a
    claim/lease command anywhere in the packet."""

    plan = _plan({"explore_harness": {"enabled": True}})
    assert plan["enabled"] is True, plan
    gate = plan["orchestration_gate"]
    assert gate["state"] == "analysis_only", gate
    assert gate["reason"] == "spawn_not_allowed_by_goal_boundary", gate
    assert plan["selected_worker_branch_count"] >= 1, plan
    for branch in [*plan["selected_worker_branches"], *plan["rejected_worker_branches"]]:
        assert not branch.get("suggested_commands"), branch
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "loopx todo claim" not in serialized, serialized
    assert "task-lease acquire" not in serialized, serialized
    _assert_read_only_boundary(plan)

    # spawn_allowed without child capacity is a contradiction, not a licence:
    # it degrades to analysis instead of silently granting one lane.
    contradictory = _plan({"spawn_allowed": True, "explore_harness": {"enabled": True}})
    gate = contradictory["orchestration_gate"]
    assert gate["state"] == "analysis_only", gate
    assert gate["reason"] == "spawn_allowed_without_child_capacity", gate


def check_commands_and_width_cap() -> None:
    """Full opt-in emits suggested commands and max_children caps the width."""

    plan = _plan(
        {"spawn_allowed": True, "max_children": 3, "explore_harness": {"enabled": True}},
        worker_width=10,
    )
    gate = plan["orchestration_gate"]
    assert gate["state"] == "commands_suggested", gate
    assert gate["reason"] == "goal_boundary_opt_in", gate
    assert plan["worker_width"] == 3, plan["worker_width"]
    assert plan["requested_worker_width"] == 10, plan
    assert gate["width_cap_source"] == "max_children", gate
    assert plan["selected_worker_branch_count"] >= 1, plan
    assert plan["selected_worker_branch_count"] <= 3, plan
    first = plan["selected_worker_branches"][0]
    assert any("loopx todo claim" in command for command in first["suggested_commands"]), first
    assert any("task-lease acquire" in command for command in first["suggested_commands"]), first
    # Suggested is still only suggested: the packet remains dry-run.
    assert plan["dry_run"] is True, plan
    assert plan["harness_compatibility"]["claim_and_lease_are_suggested_only"] is True, plan
    _assert_read_only_boundary(plan)

    wide = _plan(
        {"spawn_allowed": True, "max_children": 99, "explore_harness": {"enabled": True}},
        worker_width=99,
    )
    assert wide["worker_width"] == MAX_WORKER_LANES, wide["worker_width"]
    assert wide["orchestration_gate"]["width_cap_source"] == "max_worker_lanes", wide["orchestration_gate"]


def _todo_plan(orchestration: dict[str, object] | None, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "goal_id": GOAL_ID,
        "todos": TODOS,
        "agent_id": AGENT_ID,
        "width": 4,
        "orchestration": orchestration,
    }
    kwargs.update(overrides)
    return build_explore_todo_branch_plan(**kwargs)


def check_todo_branch_plan_gate() -> None:
    """todo-branch-plan sits behind the same per-goal gate as the worker planner."""

    disabled = _todo_plan(None)
    assert disabled["ok"] is True and disabled["enabled"] is False, disabled
    assert disabled["schema_version"] == "loopx_explore_todo_branch_plan_v0", disabled
    assert disabled["orchestration_gate"]["state"] == "disabled", disabled
    assert disabled["selected_count"] == 0 and disabled["selected_branches"] == [], disabled
    assert disabled["required_contract"]["spawn_policy"]["explore_harness"]["enabled"] is True, disabled
    _assert_read_only_boundary(disabled)

    analysis = _todo_plan({"explore_harness": {"enabled": True}})
    assert analysis["enabled"] is True, analysis
    gate = analysis["orchestration_gate"]
    assert gate["state"] == "analysis_only", gate
    assert analysis["selected_count"] >= 1, analysis
    serialized = json.dumps(analysis, ensure_ascii=False)
    assert "loopx todo claim" not in serialized, serialized
    assert "task-lease acquire" not in serialized, serialized
    _assert_read_only_boundary(analysis)

    capped = _todo_plan(
        {"spawn_allowed": True, "max_children": 2, "explore_harness": {"enabled": True}},
        width=5,
    )
    gate = capped["orchestration_gate"]
    assert gate["state"] == "commands_suggested", gate
    assert capped["issue_width"] == 2, capped["issue_width"]
    assert capped["requested_issue_width"] == 5, capped
    assert gate["width_cap_source"] == "max_children", gate
    first = capped["selected_branches"][0]
    assert any("loopx todo claim" in command for command in first["suggested_commands"]), first
    assert capped["dry_run"] is True, capped
    _assert_read_only_boundary(capped)

    wide = _todo_plan(
        {"spawn_allowed": True, "max_children": 99, "explore_harness": {"enabled": True}},
        width=99,
    )
    assert wide["issue_width"] == MAX_BRANCH_WIDTH, wide["issue_width"]
    assert wide["orchestration_gate"]["width_cap_source"] == "max_branch_width", wide["orchestration_gate"]


def check_profile_pin_and_boundary_precedence() -> None:
    plan = _plan(
        {
            "spawn_allowed": True,
            "max_children": 4,
            "explore_harness": {"enabled": True, "profile": "moe-router"},
        },
        harness_profile="generic",
    )
    gate = plan["orchestration_gate"]
    assert plan["harness_profile"] == "moe-router", plan["harness_profile"]
    assert gate["profile_source"] == "goal_boundary", gate
    assert gate["requested_profile"] == "generic", gate
    assert gate["effective_profile"] == "moe-router", gate

    unpinned = _plan(
        {"spawn_allowed": True, "max_children": 4, "explore_harness": {"enabled": True}},
        harness_profile="adaptive-resilient",
    )
    assert unpinned["harness_profile"] == "adaptive-resilient", unpinned["harness_profile"]
    assert unpinned["orchestration_gate"]["profile_source"] == "planner_request", unpinned

    # spawn_policy is the ONLY honored source: a raw registry
    # project_asset.orchestration key never reaches the live quota
    # goal_boundary projection (enrich_project_asset derives it from
    # spawn_policy), so the gate must not treat it as an authorization
    # surface either.
    registry = {
        "goals": [
            {
                "id": GOAL_ID,
                "spawn_policy": {"allowed": True, "max_children": 8},
                "project_asset": {
                    "orchestration": {
                        "spawn_allowed": True,
                        "max_children": 8,
                        "explore_harness": {"enabled": True},
                    }
                },
            }
        ]
    }
    boundary = _goal_orchestration_boundary(registry, goal_id=GOAL_ID)
    assert boundary == {"allowed": True, "max_children": 8}, boundary
    gate = resolve_explore_harness_gate(boundary, requested_width=4)
    assert gate["state"] == "disabled", gate
    unknown = resolve_explore_harness_gate(
        _goal_orchestration_boundary(registry, goal_id="never-registered"), requested_width=4
    )
    assert unknown["state"] == "disabled", unknown


def check_quota_boundary_projection() -> None:
    """Requirement 5: the gate rides the existing quota/status projection.

    The same spawn_policy that gates the planners must surface in
    compact_orchestration_policy, the orchestration summary line, and the
    quota goal_boundary packet, so `quota should-run` output and the planner
    gate can never disagree about the opt-in."""

    policy = {
        "allowed": True,
        "max_children": 3,
        "explore_harness": {"enabled": True, "profile": "moe-router"},
    }
    compact = compact_orchestration_policy(policy)
    assert compact["explore_harness"] == {"enabled": True, "profile": "moe-router"}, compact
    summary = orchestration_policy_summary(policy)
    assert "explore_harness=on(moe-router)" in summary, summary
    off_summary = orchestration_policy_summary(
        {"allowed": True, "max_children": 3, "explore_harness": {"enabled": False}}
    )
    assert "explore_harness=off" in off_summary, off_summary
    no_section_summary = orchestration_policy_summary({"allowed": True, "max_children": 3})
    assert "explore_harness" not in no_section_summary, no_section_summary

    boundary = _quota_goal_boundary({"spawn_policy": policy})
    assert boundary is not None, boundary
    assert boundary["orchestration"]["explore_harness"]["enabled"] is True, boundary
    assert boundary["orchestration"]["explore_harness"]["profile"] == "moe-router", boundary
    assert boundary["orchestration"]["max_children"] == 3, boundary


def check_cli_gate_states() -> None:
    """Every gate state through the real CLI: default-off, analysis-only,
    commands with a binding max_children cap plus goal-pinned profile,
    unregistered goal, invalid pinned profile, and the markdown gate line."""

    goals = {
        "gate-cli-off": None,
        "gate-cli-analysis": {"explore_harness": {"enabled": True}},
        "gate-cli-capped": {
            "allowed": True,
            "max_children": 2,
            "explore_harness": {"enabled": True, "profile": "moe-router"},
        },
        "gate-cli-badpin": {
            "allowed": True,
            "max_children": 2,
            "explore_harness": {"enabled": True, "profile": "not-a-profile"},
        },
    }
    with tempfile.TemporaryDirectory(prefix="loopx-explore-gate-smoke-") as tmp:
        registry = Path(tmp) / ".loopx" / "registry.json"
        runtime_root = Path(tmp) / "runtime"
        project = Path(tmp) / "project"
        goal_entries = []
        for goal_id, spawn_policy in goals.items():
            state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
            (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
            todo_slug = goal_id.replace("-", "_")
            (project / state_file).write_text(
                "---\n"
                "status: active\n"
                "updated_at: 2026-07-08T00:00:00+00:00\n"
                "---\n\n"
                f"# Explore Gate CLI Fixture {goal_id}\n\n"
                "## Agent Todo\n\n"
                "- [ ] [P0] Probe alpha lane.\n"
                f"  <!-- loopx:todo todo_id=todo_{todo_slug}_alpha status=open task_class=advancement_task required_write_scopes=artifacts/alpha/** -->\n"
                "- [ ] [P1] Probe beta lane.\n"
                f"  <!-- loopx:todo todo_id=todo_{todo_slug}_beta status=open task_class=advancement_task required_write_scopes=artifacts/beta/** -->\n"
                "- [ ] [P1] Probe gamma lane.\n"
                f"  <!-- loopx:todo todo_id=todo_{todo_slug}_gamma status=open task_class=advancement_task required_write_scopes=artifacts/gamma/** -->\n",
                encoding="utf-8",
            )
            entry = {
                "id": goal_id,
                "domain": "explore-gate-smoke",
                "status": "active",
                "state_file": state_file,
                "repo": str(project),
                "adapter": {
                    "kind": "explore_result_layer",
                    "status": "connected-read-only",
                },
            }
            if spawn_policy is not None:
                entry["spawn_policy"] = spawn_policy
            goal_entries.append(entry)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-07-08T00:00:00+00:00",
                    "common_runtime_root": str(runtime_root),
                    "goals": goal_entries,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        for entry in goal_entries:
            state_path = project / entry["state_file"]
            groups, _, _ = parse_todo_source(state_path.read_text(encoding="utf-8"))
            records = [canonical_todo_read_record(structured_todo_item(item, role=role,
                source_section=item["source_section"])) for role, rows in groups.items() for item in rows]
            initialize_canonical_authority(runtime_root, entry["id"], build_todo_runtime_shadow_projection(
                goal_id=entry["id"], todos=records, handoff_mode="soft_claim"), state_path=state_path)

        def run_cli(*extra_args: str, fmt: str = "json", check: bool = True):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "loopx.cli",
                    "--format",
                    fmt,
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime_root),
                    "explore",
                    *extra_args,
                ],
                cwd=REPO_ROOT,
                check=check,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if fmt == "json" and check:
                return json.loads(result.stdout)
            return result

        # 1. Default off: disabled packet for both planners.
        payload = run_cli(
            "worker-branch-plan", "--goal-id", "gate-cli-off",
            "--agent-id", AGENT_ID, "--worker-width", "4",
        )
        assert payload["ok"] is True and payload["enabled"] is False, payload
        assert payload["orchestration_gate"]["state"] == "disabled", payload
        assert payload["selected_worker_branches"] == [], payload
        assert payload["required_contract"]["spawn_policy"]["explore_harness"]["enabled"] is True, payload
        todo_payload = run_cli(
            "todo-branch-plan", "--goal-id", "gate-cli-off",
            "--agent-id", AGENT_ID, "--width", "2",
        )
        assert todo_payload["ok"] is True and todo_payload["enabled"] is False, todo_payload
        assert todo_payload["orchestration_gate"]["state"] == "disabled", todo_payload
        assert todo_payload["selected_branches"] == [], todo_payload

        # 2. Unregistered goal: still the explicit disabled packet, not a
        #    state-file resolution error.
        unregistered = run_cli(
            "worker-branch-plan", "--goal-id", "never-registered",
            "--agent-id", AGENT_ID, "--worker-width", "4",
        )
        assert unregistered["ok"] is True and unregistered["enabled"] is False, unregistered
        assert unregistered["orchestration_gate"]["state"] == "disabled", unregistered

        # 3. Analysis-only: enabled without spawn permission plans lanes but
        #    the raw CLI payload carries zero claim/lease command strings.
        analysis = run_cli(
            "worker-branch-plan", "--goal-id", "gate-cli-analysis",
            "--agent-id", AGENT_ID, "--worker-width", "3",
        )
        assert analysis["enabled"] is True, analysis
        assert analysis["orchestration_gate"]["state"] == "analysis_only", analysis
        assert analysis["selected_worker_branch_count"] >= 1, analysis
        serialized = json.dumps(analysis, ensure_ascii=False)
        assert "loopx todo claim" not in serialized, serialized
        assert "task-lease acquire" not in serialized, serialized

        # 4. Full opt-in via the registry "allowed" alias: max_children binds
        #    the requested width and the goal-pinned profile beats the CLI
        #    default (generic).
        capped = run_cli(
            "worker-branch-plan", "--goal-id", "gate-cli-capped",
            "--agent-id", AGENT_ID, "--worker-width", "5",
        )
        assert capped["orchestration_gate"]["state"] == "commands_suggested", capped
        assert capped["worker_width"] == 2 and capped["requested_worker_width"] == 5, capped
        assert capped["orchestration_gate"]["width_cap_source"] == "max_children", capped
        assert capped["harness_profile"] == "moe-router", capped
        assert capped["orchestration_gate"]["profile_source"] == "goal_boundary", capped
        assert any(
            "loopx todo claim" in command
            for branch in capped["selected_worker_branches"]
            for command in branch["suggested_commands"]
        ), capped
        capped_todo = run_cli(
            "todo-branch-plan", "--goal-id", "gate-cli-capped",
            "--agent-id", AGENT_ID, "--width", "5",
        )
        assert capped_todo["issue_width"] == 2 and capped_todo["requested_issue_width"] == 5, capped_todo
        assert capped_todo["orchestration_gate"]["width_cap_source"] == "max_children", capped_todo

        # 5. Invalid goal-pinned profile: clean error packet with exit 1.
        bad = run_cli(
            "worker-branch-plan", "--goal-id", "gate-cli-badpin",
            "--agent-id", AGENT_ID, "--worker-width", "2",
            check=False,
        )
        assert bad.returncode == 1, bad.stdout
        bad_payload = json.loads(bad.stdout)
        assert bad_payload["ok"] is False, bad_payload
        assert "profile" in str(bad_payload["error"]), bad_payload

        # 6. The human-facing markdown surface renders the gate audit line.
        markdown = run_cli(
            "worker-branch-plan", "--goal-id", "gate-cli-off",
            "--agent-id", AGENT_ID, "--worker-width", "4",
            fmt="markdown",
        )
        assert "- orchestration_gate: " in markdown.stdout, markdown.stdout
        assert "state=disabled" in markdown.stdout, markdown.stdout


def main() -> int:
    check_default_off()
    check_analysis_only()
    check_commands_and_width_cap()
    check_todo_branch_plan_gate()
    check_profile_pin_and_boundary_precedence()
    check_quota_boundary_projection()
    check_cli_gate_states()
    print("explore worker plan gate smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
