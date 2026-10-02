#!/usr/bin/env python3
"""Smoke-test the first-connect contract for `loopx connect`.

`connect` registers the goal and its active state. It must not project a
first-connect onboarding todo, user gate, candidate list, or connection
validation item into the goal, because those items competed with the caller's
own first delivery todo and could park an unattended goal on an operator gate.

This smoke guards that contract from the outside: it drives the real CLI,
reads the written active state, and checks the gate that the caller sees next.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERIC_GOAL_ID = "fresh-generic-connect"
DOMAIN_GOAL_ID = "fresh-domain-connect"
FIRST_TODO_ID = "fresh-first-delivery"
REMOVED_ONBOARDING_MARKERS = (
    "action_kind=onboarding_",
    "## Onboarding Control",
    "## Proposed Onboarding Candidates",
    "## Accept Candidate Commands",
)


def run_cli(registry: Path, runtime: Path, *args: str) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def initialize_project(root: Path, name: str) -> tuple[Path, Path]:
    project = root / name
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    readme = project / "README.md"
    readme.write_text("# Synthetic first-connect fixture\n", encoding="utf-8")
    (project / ".gitignore").write_text(".loopx/\n.codex/\n.local/\n", encoding="utf-8")
    return project, readme


def state_path(project: Path, goal_id: str) -> Path:
    return project / ".loopx" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"


def assert_no_first_connect_projection(state_text: str, *, label: str) -> None:
    for marker in REMOVED_ONBOARDING_MARKERS:
        assert marker not in state_text, (label, marker, state_text)
    user_section = state_text.split("## User Todo / Owner Review Reading Queue", 1)[1]
    user_section = user_section.split("## Agent Todo", 1)[0]
    assert "- [ ]" not in user_section, (label, user_section)
    agent_section = state_text.split("## Agent Todo", 1)[1].split("## Next Action", 1)[0]
    assert "- [ ]" not in agent_section, (label, agent_section)
    assert "Initial routing is owned by the connected domain adapter." in state_text, state_text


def connect_generic(root: Path, runtime: Path) -> tuple[Path, Path, str]:
    project, readme = initialize_project(root, "project")
    registry = project / ".loopx" / "registry.json"
    connected = run_cli(
        registry,
        runtime,
        "connect",
        "--project",
        str(project),
        "--goal-id",
        GENERIC_GOAL_ID,
        "--objective",
        "Validate the fresh connection contract.",
        "--domain",
        "engineering",
        "--goal-doc",
        str(readme),
        "--adapter-kind",
        "read_only_project_map_v0",
        "--adapter-status",
        "connected-read-only",
        "--no-global-sync",
    )
    assert connected["ok"] is True, connected
    for removed_field in (
        "onboarding_scan",
        "onboarding_agent_todo_candidates",
        "onboarding_acceptance_required",
        "autonomous_advance_choice_required",
        "heartbeat_opt_in_required",
        "host_loop_activation_required",
        "onboarding_todos_written",
        "onboarding_connection_validation",
        "accept_candidate_commands",
        "codex_app_heartbeat",
    ):
        assert removed_field not in connected, (removed_field, connected)
    state_text = state_path(project, GENERIC_GOAL_ID).read_text(encoding="utf-8")
    assert_no_first_connect_projection(state_text, label="generic")
    return project, registry, state_text


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-first-connect-") as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        project, registry, state_text = connect_generic(root, runtime)

        mapped = run_cli(
            registry,
            runtime,
            "read-only-map",
            "--goal-id",
            GENERIC_GOAL_ID,
            "--no-global-sync",
        )
        assert mapped["ok"] is True, mapped

        healthy_check = run_cli(registry, runtime, "check", "--scan-path", str(project / "README.md"))
        assert healthy_check["ok"] is True, healthy_check
        assert healthy_check["summary"]["warnings"] == 0, healthy_check

        # A fresh connection is immediately runnable: no operator gate and no
        # first-connect todo stands between the caller and its own work.
        ungated = run_cli(registry, runtime, "quota", "should-run", "--goal-id", GENERIC_GOAL_ID)
        assert ungated["should_run"] is True, ungated
        assert ungated["normal_delivery_allowed"] is True, ungated
        assert ungated["effective_action"] == "normal_run", ungated
        assert "user_gate" not in json.dumps(ungated.get("interaction_contract") or {}), ungated
        assert ungated.get("state_projection_gap") is None, ungated

        # The caller's own first delivery todo is what the gate selects, even
        # when the state file still carries the connection-time next action.
        added = run_cli(
            registry,
            runtime,
            "todo",
            "add",
            "--goal-id",
            GENERIC_GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            FIRST_TODO_ID,
            "--text",
            "[P0] Land the first bounded delivery segment.",
            "--task-class",
            "advancement_task",
            "--status",
            "open",
            "--execute",
        )
        assert added["ok"] is True, added
        gated = run_cli(registry, runtime, "quota", "should-run", "--goal-id", GENERIC_GOAL_ID)
        assert gated["should_run"] is True, gated
        assert gated["recommended_action"] == "[P0] Land the first bounded delivery segment.", gated
        selected_state = state_path(project, GENERIC_GOAL_ID).read_text(encoding="utf-8")
        assert "action_kind=onboarding_" not in selected_state, selected_state
        assert (selected_state.count("- [ ]")) == state_text.count("- [ ]") + 1, selected_state

        # Domain-owned adapters keep the same contract: no gate, no injected
        # first-connect work, and a clean state projection.
        domain_project, domain_readme = initialize_project(root, "domain-project")
        domain_registry = domain_project / ".loopx" / "registry.json"
        domain_connected = run_cli(
            domain_registry,
            runtime,
            "connect",
            "--project",
            str(domain_project),
            "--goal-id",
            DOMAIN_GOAL_ID,
            "--objective",
            "Validate domain-owned first connect.",
            "--domain",
            "engineering",
            "--goal-doc",
            str(domain_readme),
            "--adapter-kind",
            "domain_fixture_v0",
            "--adapter-status",
            "connected-read-only",
            "--no-global-sync",
        )
        assert domain_connected["ok"] is True, domain_connected
        domain_state_text = state_path(domain_project, DOMAIN_GOAL_ID).read_text(encoding="utf-8")
        assert_no_first_connect_projection(domain_state_text, label="domain")
        domain_check = run_cli(
            domain_registry,
            runtime,
            "check",
            "--scan-path",
            str(domain_readme),
        )
        assert domain_check["ok"] is True, domain_check
        assert domain_check["summary"]["warnings"] == 0, domain_check
        domain_goal = json.loads(domain_registry.read_text(encoding="utf-8"))["goals"][0]
        assert "connection_validation" not in domain_goal["adapter"], domain_goal

    print("first-connect-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
