from __future__ import annotations

import json
import shlex
import subprocess
import sys

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
from loopx.todos import add_goal_todo


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


@pytest.mark.parametrize("command", ["turn-context", "summary", "worker-branch-plan", "graph"])
@pytest.mark.parametrize("damage", ["truncated", "foreign-goal", "invalid-status"])
def test_decision_reads_reject_incomplete_evidence_and_recover(tmp_path, command, damage):
    path = registry(tmp_path, graph=True, planning=True)
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    node = build_explore_node_event(
        goal_id="research", agent_id="worker", node_id="route",
        title="Candidate route", status="open",
    )
    finding = build_explore_finding_event(
        goal_id="research", agent_id="worker", node_id="route",
        finding_id="counterexample", title="Counterexample", status="refuted",
        summary="Applies only to the tested inputs.",
    )
    append_explore_result_event(log, node)
    append_explore_result_event(log, finding)
    healthy = log.read_bytes()
    if damage == "truncated":
        damaged = json.dumps(node) + '\n{"event_kind":'
    else:
        altered = {**finding, **({"goal_id": "foreign"} if damage == "foreign-goal"
                                else {"status": "invented"})}
        damaged = json.dumps(node) + "\n" + json.dumps(altered) + "\n"
    log.write_text(damaged)
    args = [sys.executable, "-m", "loopx.cli", "--registry", str(path),
            "--runtime-root", str(root), "--format", "json", "explore", command,
            "--goal-id", "research"]
    if command in {"turn-context", "worker-branch-plan"}:
        args += ["--agent-id", "worker"]
    output = tmp_path / "graph.json"
    if command == "graph":
        output.write_text("previous export")
        args += ["--graph-format", "json", "--out", str(output)]
    rejected = subprocess.run(args, capture_output=True, text=True)
    assert rejected.returncode != 0, rejected.stdout
    packet = json.loads(rejected.stdout)
    assert packet["ok"] is False and "Explore result" in packet["error"]
    assert log.read_text() == damaged
    if command == "graph":
        assert output.read_text() == "previous export"
    log.write_bytes(healthy)
    recovered = subprocess.run(args, capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stdout
    assert json.loads(recovered.stdout)["ok"] is True
    assert log.read_bytes() == healthy


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
        "loopx.capabilities.explore.turn_context.load_explore_result_events_strict", unexpected
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


def test_hook_cli_preserves_linked_evidence_beyond_recent_window(tmp_path):
    path = registry(tmp_path, planning=True)
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    add_goal_todo(
        registry_path=path, runtime_root_arg=str(root), goal_id="research",
        role="agent", text="Test the route under changed input conditions",
        task_class="advancement_task", claimed_by="worker",
        explore_result_node_refs=["route-old", "route-missing"],
    )
    for i in range(8):
        node_id = "route-old" if i == 0 else f"route-{i}"
        append_explore_result_event(log, build_explore_node_event(
            goal_id="research", node_id=node_id, title=f"Hypothesis {i}",
            status="resolved" if i == 0 else "exploring",
        ))
        event = build_explore_finding_event(
            goal_id="research", node_id=node_id, title=f"Result {i}",
            status="refuted" if i == 0 else "confirmed",
            recorded_at=f"2026-01-01T00:00:{i:02d}+00:00",
        )
        # Deterministic ordering: unrelated newer results crowd the recent view.
        append_explore_result_event(log, event)
    hook = extend_turn_start_dispatch(
        {}, registry_path=path, runtime_root=root, goal_id="research", agent_id="worker"
    )
    command = shlex.split(hook["required_reads"][0]["command"])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *command[1:]],
        check=True, capture_output=True, text=True,
    )
    packet = json.loads(result.stdout)
    assert "Result 0" not in str(packet["graph"]["recent_findings"])
    branch = packet["harness"]["selected_branches"][0]
    audit = branch["typed_evidence_audit"]
    assert audit["findings"][0]["finding"] == "Result 0"
    assert audit["findings"][0]["status"] == "refuted"
    assert "linked_finding_refuted" in audit["hazards"]
    assert audit["unknown_node_refs"] == ["route-missing"]
    assert audit["score_delta"] == 0
    assert branch["todo_id"]
    assert len(packet["harness"]["frontier"]) == 3
    assert packet["harness"]["omitted_frontier"] == 4
    assert packet["harness"]["orchestration_gate"]["state"] == "analysis_only"
    assert not packet["boundary"]["starts_agents"]
    for p, content in before.items():
        assert p.read_bytes() == content

    # Reopening the same hypothesis updates the next read; old refutation remains
    # scoped evidence and does not make the candidate ineligible.
    append_explore_result_event(log, build_explore_node_event(
        goal_id="research", node_id="route-old", title="Changed conditions",
        status="exploring",
    ))
    reopened = explore_turn_context(
        registry_path=path, runtime_root=root, goal_id="research", agent_id="worker"
    )["harness"]["selected_branches"][0]
    assert reopened["todo_id"] == branch["todo_id"]
    assert reopened["typed_evidence_audit"]["score_delta"] == 0
    assert reopened["typed_evidence_audit"]["nodes"][0]["status"] == "exploring"
    assert reopened["typed_evidence_audit"]["findings"][0]["status"] == "refuted"


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


def test_real_quota_delivers_replayable_context_without_admitting_unhealthy_goal(
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
    channel = packet["interaction_contract"]["agent_channel"]
    assert packet["should_run"] is False
    assert channel["delivery_allowed"] is False
    assert "required_reads" not in packet
    source = next(
        r for r in channel["work_context"]["sources"]
        if r["kind"] == "explore_turn_context"
    )
    assert not any(r.get("kind") == "explore_turn_context" for r in channel["required_reads"])
    read = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *shlex.split(source["command"])[1:]],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(read.stdout)
    # The CLI adds its route receipt; the registered reader returns the same
    # canonical context without transport diagnostics.
    assert result.pop("source_runtime_route")["routed_to_source_registry"] is False
    assert result == source["content"]
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


def test_linked_evidence_hazards_survive_detail_caps(tmp_path):
    from loopx.capabilities.explore.result_log import build_explore_edge_event
    from loopx.capabilities.explore.todo_evidence import build_todo_typed_evidence_audit

    path = registry(tmp_path, planning=True)
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    todo = {"explore_result_node_refs": ["route"]}
    add_goal_todo(
        registry_path=path, runtime_root_arg=str(root), goal_id="research",
        role="agent", text="Test changed conditions", task_class="advancement_task",
        claimed_by="worker", **todo,
    )
    append_explore_result_event(log, build_explore_node_event(
        goal_id="research", node_id="route", title="Route", status="exploring",
    ))
    for i in range(25):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="research", node_id="route", title=f"Result {i}",
            status="refuted" if i == 0 else "confirmed",
            recorded_at=f"2026-01-01T00:00:{i:02d}+00:00",
        ))
        append_explore_result_event(log, build_explore_node_event(
            goal_id="research", node_id=f"input-{i}", title=f"Input {i}",
        ))
        append_explore_result_event(log, build_explore_edge_event(
            goal_id="research", from_node=f"input-{i}", to_node="route",
            edge_type="refutes" if i == 0 else "supports",
            recorded_at=f"2026-01-01T00:00:{i:02d}+00:00",
        ))
    hook = extend_turn_start_dispatch(
        {}, registry_path=path, runtime_root=root, goal_id="research", agent_id="worker"
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    command = shlex.split(hook["required_reads"][0]["command"])
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *command[1:]],
        check=True, capture_output=True, text=True,
    )
    packet = json.loads(result.stdout)
    audit = packet["harness"]["selected_branches"][0]["typed_evidence_audit"]
    assert "linked_finding_refuted" in audit["hazards"]
    assert "refute_edge_present" in audit["hazards"]
    assert len(audit["findings"]) == len(audit["relevant_edges"]) == 3
    assert audit["omitted_audit_findings"] == audit["omitted_audit_edges"] == 22
    assert audit["score_delta"] == 0
    assert packet["harness"]["orchestration_gate"]["state"] == "analysis_only"
    assert {p: p.read_bytes() for p in before} == before

    # Audit classifications must not depend on the detail ordering or its cap.
    projection = {
        "nodes": [{"node_id": "route"}],
        "findings": [
            {"node_id": "route", "status": "confirmed"} for _ in range(24)
        ] + [{"node_id": "route", "status": "refuted"}],
        "edges": [
            {"to_node": "route", "edge_type": "supports"} for _ in range(24)
        ] + [{"to_node": "route", "edge_type": "refutes"}],
    }
    full = build_todo_typed_evidence_audit(todo, projection)
    assert full["status_counts"]["findings"] == {"confirmed": 24, "refuted": 1}
    assert full["status_counts"]["edges"] == {"refutes": 1, "supports": 24}
    assert len(full["findings"]) == len(full["relevant_edges"]) == 24
    assert full["hazards"] == ["linked_finding_refuted", "refute_edge_present"]
    assert build_todo_typed_evidence_audit({}, projection) is None


@pytest.mark.parametrize("counts,omitted", [(None, 1), ({"confirmed": 25}, 22)])
def test_turn_context_audit_count_compatibility(counts, omitted):
    audit = {"findings": [{"status": "confirmed"}] * 4}
    if counts is not None:
        audit["status_counts"] = {"findings": counts}
    packet = effect_runtime_result("explore.turn_context", {
        "goal_id": "research", "agent_id": "worker", "route": ["loopx"],
        "harness_gate": {"enabled": True}, "graph_enabled": False,
        "projection": {}, "plan": {"selected_branches": [{"typed_evidence_audit": audit}]},
    })
    result = packet["harness"]["selected_branches"][0]["typed_evidence_audit"]
    assert result["omitted_audit_findings"] == omitted
    assert len(result["findings"]) == 3


def test_turn_context_preserves_wait_conditions_and_total_omissions():
    from loopx.capabilities.explore.todo_branch_plan import build_explore_todo_branch_plan

    plan = build_explore_todo_branch_plan(
        goal_id="research", agent_id="worker", width=3,
        orchestration={"explore_harness": {"enabled": True}},
        todos=[{
            "todo_id": f"todo_waiting_{index}", "text": "Use the prepared artifact",
            "status": "open", "task_class": "advancement_task", "priority": "P0",
            "resume_when": "todo_done:todo_prepare", "resume_ready": False,
        } for index in range(12)],
    )
    packet = effect_runtime_result("explore.turn_context", {
        "goal_id": "research", "agent_id": "worker", "route": ["loopx"],
        "harness_gate": {"enabled": True}, "graph_enabled": False,
        "projection": {}, "plan": plan,
    })
    context = packet["harness"]
    assert context["selected_branches"] == []
    assert len(context["rejected_candidates"]) == 3
    assert context["omitted_rejected_candidates"] == 9
    for row in context["rejected_candidates"]:
        assert row["actionable_open"] is False
        assert row["resume_ready"] is False
        assert row["resume_when"] == "todo_done:todo_prepare"
    assert "todo-branch-plan" in context["plan_command"]


@pytest.mark.parametrize("kind", ["findings", "edges"])
def test_turn_context_rejects_negative_evidence_count(kind):
    with pytest.raises(EffectRuntimeRejected, match=f"Explore {kind} count must be nonnegative") as rejected:
        effect_runtime_result("explore.turn_context", {
            "goal_id": "research", "agent_id": "worker", "route": ["loopx"],
            "harness_gate": {"enabled": True}, "graph_enabled": False,
            "projection": {}, "plan": {"selected_branches": [{
                "typed_evidence_audit": {"status_counts": {kind: {"unknown": -1}}},
            }]},
        })
    assert rejected.value.error_kind == "request_rejected"
    assert rejected.value.diagnostic_code == "invalid_request"


def test_many_durable_refs_keep_bounded_audit_and_full_cold_read(tmp_path):
    path = registry(tmp_path, planning=True)
    refs = [f"unknown-{i}" for i in range(40)]
    add_goal_todo(registry_path=path, goal_id="research", role="agent",
                  text="Inspect unresolved evidence", claimed_by="worker",
                  explore_result_node_refs=refs)
    packet = explore_turn_context(registry_path=path, runtime_root=tmp_path / "runtime",
                                  goal_id="research", agent_id="worker")
    audit = packet["harness"]["selected_branches"][0]["typed_evidence_audit"]
    for field in ("requested_node_refs", "unknown_node_refs"):
        assert audit[field] == refs[:8]
        assert audit[f"omitted_{field}"] == 32
    assert "unknown_result_node_ref" in audit["hazards"]
    # Follow the actual full-audit command carried by the compact packet.
    command = packet["harness"]["plan_command"]
    result = subprocess.run([sys.executable, "-m", "loopx.cli", *command[1:]],
                            capture_output=True, text=True, check=True)
    full = json.loads(result.stdout)["selected_branches"][0]["typed_evidence_audit"]
    assert full["requested_node_refs"] == full["unknown_node_refs"] == refs
    assert full["score_delta"] == 0
