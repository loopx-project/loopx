from __future__ import annotations

import json
import shlex

import pytest

from loopx.capabilities.explore.turn_context import (
    explore_turn_context,
    extend_turn_start_dispatch,
)
from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_node_event,
    build_explore_finding_event,
    explore_result_log_path,
)
from loopx.configure_goal import configure_goal
from loopx.chat_goal_configuration_api import _goal_capability_options
from loopx.control_plane.effect_runtime import (
    effect_runtime_result,
    EffectRuntimeRejected,
)
from loopx.explore_graph import compact_explore_graph_policy


def registry(tmp_path, *, graph=False, planning=False):
    path = tmp_path / "registry.json"
    (tmp_path / "goal.md").write_text("# Goal\n\n## Agent Todos\n")
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "research",
                        "status": "active",
                        "repo": str(tmp_path),
                        "state_file": "goal.md",
                        "coordination": {"registered_agents": ["worker"]},
                        "explore_graph": {"enabled": graph},
                        "spawn_policy": {
                            "allowed": False,
                            "explore_harness": {"enabled": planning},
                        },
                    }
                ],
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "mode,graph,planning",
    [("off", False, False), ("evidence", True, False), ("planning", True, True)],
)
def test_mode_preview_apply_and_readback(tmp_path, mode, graph, planning):
    path = registry(tmp_path)
    original = path.read_bytes()
    options = _goal_capability_options(
        "explore_harness", {"mode": mode, "profile": "generic"}
    )
    configure_goal(registry_path=path, goal_id="research", execute=False, **options)
    assert path.read_bytes() == original
    result = configure_goal(
        registry_path=path, goal_id="research", execute=True, **options
    )
    assert result["ok"]
    goal = json.loads(path.read_text())["goals"][0]
    assert goal["explore_graph"]["enabled"] is graph
    assert goal["spawn_policy"]["explore_harness"]["enabled"] is planning
    assert goal["spawn_policy"]["allowed"] is False
    catalog = configure_goal(registry_path=path, goal_id="research", execute=False)[
        "configuration_catalog"
    ]
    features = {f["feature_id"]: f for f in catalog["features"]}
    assert "explore_graph" not in features
    assert features["explore_harness"]["current"]["mode"] == mode


def test_legacy_planning_implies_evidence_and_graph_disable_is_actionable(tmp_path):
    path = registry(tmp_path, planning=True)
    assert compact_explore_graph_policy({"enabled": False}, {"enabled": True}) == {
        "enabled": True
    }
    original = path.read_bytes()
    with pytest.raises(ValueError, match="planning requires its evidence graph"):
        configure_goal(
            registry_path=path,
            goal_id="research",
            explore_graph_enabled=False,
            execute=True,
        )
    assert path.read_bytes() == original
    configure_goal(
        registry_path=path,
        goal_id="research",
        explore_harness_enabled=False,
        execute=True,
    )
    goal = json.loads(path.read_text())["goals"][0]
    assert goal["explore_graph"]["enabled"] is True
    assert goal["spawn_policy"]["explore_harness"]["enabled"] is False


@pytest.mark.parametrize(
    "changes", [{"mode": "unknown"}, {"mode": "off", "planning_enabled": True}]
)
def test_invalid_mode_plan_is_rejected(changes):
    with pytest.raises(EffectRuntimeRejected):
        effect_runtime_result(
            "explore.configuration.plan", {"current": {}, "changes": changes}
        )


def test_disabled_hook_keeps_packet_and_does_not_read_evidence(tmp_path, monkeypatch):
    path = registry(tmp_path)
    original = {"registered_count": 2, "required_reads": [{"command": "existing"}]}

    def unexpected(*args, **kwargs):
        pytest.fail("disabled Explore read evidence")

    monkeypatch.setattr(
        "loopx.capabilities.explore.turn_context.load_explore_result_events", unexpected
    )
    assert (
        extend_turn_start_dispatch(
            original,
            registry_path=path,
            runtime_root=tmp_path / "runtime",
            goal_id="research",
            agent_id="worker",
        )
        is original
    )
    assert (
        explore_turn_context(
            registry_path=path,
            runtime_root=tmp_path / "runtime",
            goal_id="research",
            agent_id="worker",
        )["graph"]
        is None
    )


@pytest.mark.parametrize("planning", [False, True])
def test_enabled_hook_and_read_preserve_evidence_and_authority(tmp_path, planning):
    path = registry(tmp_path, graph=True, planning=planning)
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    for i in range(5):
        append_explore_result_event(
            log,
            build_explore_node_event(
                goal_id="research",
                title=f"Route {i}",
                node_id=f"route-{i}",
                status="dead_end" if i == 4 else "exploring",
            ),
        )
    append_explore_result_event(
        log,
        build_explore_finding_event(
            goal_id="research",
            title="Route falsified by controlled experiment",
            node_id="route-4",
            status="refuted",
        ),
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    packet = extend_turn_start_dispatch(
        {}, registry_path=path, runtime_root=root, goal_id="research", agent_id="worker"
    )
    assert packet["registered_count"] == 1
    read = packet["required_reads"][0]
    assert read["ordering"] == "before_work"
    assert "turn-context" in shlex.split(read["command"])
    result = explore_turn_context(
        registry_path=path, runtime_root=root, goal_id="research", agent_id="worker"
    )
    assert result["graph_enabled"] and result["harness_enabled"] is planning
    assert len(result["graph"]["recent_nodes"]) == 3
    assert result["graph"]["omitted_nodes"] == 2
    assert result["graph"]["recent_findings"][0]["status"] == "refuted"
    assert result["graph"]["recent_findings"][0]["title"] == (
        "Route falsified by controlled experiment"
    )
    if planning:
        assert result["harness"]["orchestration_gate"]["state"] == "analysis_only"
    else:
        assert result["harness"] is None
    assert not result["boundary"]["starts_agents"]
    for p, content in before.items():
        assert p.read_bytes() == content
    with pytest.raises(ValueError, match="not registered"):
        explore_turn_context(
            registry_path=path,
            runtime_root=root,
            goal_id="research",
            agent_id="stranger",
        )


def test_public_goal_editor_retains_registered_profiles(tmp_path):
    from loopx.capabilities.configuration_inspection import project_goal_configuration

    path = registry(tmp_path)
    raw = configure_goal(registry_path=path, goal_id="research", execute=False)
    projected = project_goal_configuration(raw)
    catalog = projected["capability_catalog"]["capabilities"]
    harness = next(c for c in catalog if c["capability_id"] == "explore_harness")
    profile = next(
        f for f in harness["configuration_editor"]["fields"] if f["key"] == "profile"
    )
    assert "generic" in profile["options"]
    assert "adaptive-resilient" in profile["options"]


def test_legacy_planning_does_not_grant_existing_sink_publication(tmp_path):
    from loopx.capabilities.explore.activation import (
        sync_explore_graph_after_material_refresh,
    )

    path = registry(tmp_path, planning=True)
    observed = []

    def syncer(**kwargs):
        observed.append(kwargs["external_sink_delivery_authorized"])
        return {"ok": True, "status": "not_configured"}

    sync_explore_graph_after_material_refresh(
        registry_path=path, goal_id="research", syncer=syncer
    )
    assert observed == [False]


def test_real_quota_packet_exposes_replayable_read_without_admitting_unhealthy_goal(
    tmp_path,
):
    import subprocess
    import sys

    path = registry(tmp_path, graph=True)
    prefix = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(path),
    ]
    packet_run = subprocess.run(
        prefix
        + [
            "quota",
            "should-run",
            "--goal-id",
            "research",
            "--agent-id",
            "worker",
            "--turn-instance-id",
            "fixture-turn",
        ],
        capture_output=True,
        text=True,
    )
    packet = json.loads(packet_run.stdout)
    assert packet["ok"] is False  # This fixture deliberately has no healthy adapter.
    required = next(
        r for r in packet["required_reads"] if r["kind"] == "explore_turn_context"
    )
    read = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *shlex.split(required["command"])[1:]],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(read.stdout)
    assert result["graph_enabled"] is True
    assert result["harness"] is None


@pytest.mark.parametrize("authorized", [True, False])
def test_disabled_activation_preserves_caller_authorization_observation(tmp_path, authorized):
    from loopx.capabilities.explore.activation import sync_explore_graph_after_material_refresh

    path = registry(tmp_path)

    def unexpected(**kwargs):
        pytest.fail("disabled activation invoked a sink")

    result = sync_explore_graph_after_material_refresh(
        registry_path=path, goal_id="research", syncer=unexpected,
        external_sink_delivery_authorized=authorized,
    )
    assert result["status"] == "disabled"
    assert result["external_sink_delivery_authorized"] is authorized
    assert result["delivery_postcondition"]["required"] is False


def test_real_cli_recent_context_includes_revisited_old_node(tmp_path):
    import subprocess
    import sys

    path = registry(tmp_path, graph=True)
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    for i in range(5):
        append_explore_result_event(log, build_explore_node_event(
            goal_id="research", title=f"Route {i}", node_id=f"route-{i}",
            status="exploring", recorded_at=f"2026-01-0{i + 1}T00:00:00Z",
        ))
    append_explore_result_event(log, build_explore_node_event(
        goal_id="research", title="Route 0", node_id="route-0", status="blocked",
        blocked_reason="Required observation is unavailable",
        recorded_at="2026-02-01T00:00:00Z",
    ))
    before = log.read_bytes()
    prefix = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(path),
              "--runtime-root", str(root), "explore"]
    result = subprocess.run(prefix + ["turn-context", "--goal-id", "research", "--agent-id", "worker"],
                            capture_output=True, text=True, check=True)
    graph = json.loads(result.stdout)["graph"]
    assert [row["node_id"] for row in graph["recent_nodes"]] == ["route-0", "route-4", "route-3"]
    assert graph["recent_nodes"][0]["blocked_reason"] == "Required observation is unavailable"
    assert graph["omitted_nodes"] == 2
    summary = subprocess.run(prefix + ["summary", "--goal-id", "research"],
                             capture_output=True, text=True, check=True)
    canonical = json.loads(summary.stdout)
    assert canonical["ok"] is True
    assert [row["node_id"] for row in canonical["nodes"]] == [f"route-{i}" for i in range(5)]
    assert log.read_bytes() == before
