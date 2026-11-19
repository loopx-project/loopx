from __future__ import annotations

import argparse
import json
from pathlib import Path

from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_node_event,
    explore_result_log_path,
    load_explore_result_events,
)
from loopx.cli_commands.explore import handle_explore_command
from loopx.control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from loopx.extensions.runtime import default_extension_state_file, install_extension


GOAL_ID = "split-runtime-fixture"
ROOT = Path(__file__).resolve().parents[2]


def _activate_lark(runtime_root: Path) -> None:
    install_extension(
        ROOT / "loopx" / "extensions" / "lark" / "extension.toml",
        state_file=default_extension_state_file(runtime_root),
        execute=True,
    )


def _write_registry(
    path: Path,
    *,
    runtime_root: str,
    source_registry: Path | None = None,
    global_registry: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    goal = {"id": GOAL_ID, "status": "active", "repo": str(path.parent.parent)}
    if source_registry is not None:
        goal["source_registry"] = str(source_registry)
    payload = {
        "schema_version": 1,
        "common_runtime_root": runtime_root,
        "goals": [goal],
    }
    if global_registry:
        payload["registry_role"] = "global-local"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _append_node(runtime_root: Path, *, node_id: str, title: str) -> None:
    append_explore_result_event(
        explore_result_log_path(runtime_root, GOAL_ID),
        build_explore_node_event(
            goal_id=GOAL_ID,
            node_id=node_id,
            title=title,
            tags=["stage:4"],
        ),
    )


def _sync_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="explore",
        explore_command="feishu-sync",
        config_path=str(config_path),
        goal_id=GOAL_ID,
        finding_limit=200,
        mermaid_node_limit=60,
        base_token=None,
        table_id_nodes=None,
        table_id_edges=None,
        table_id_findings=None,
        cli_bin=None,
        identity="user",
        sink_visibility="owner-only",
        execute=False,
    )


def test_global_goal_route_uses_source_registry_relative_runtime(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    global_registry = tmp_path / "shared" / "registry.global.json"
    _write_registry(source_registry, runtime_root=".loopx/runtime")
    _write_registry(
        global_registry,
        runtime_root=str(tmp_path / "shared"),
        source_registry=source_registry,
        global_registry=True,
    )

    route = resolve_goal_source_runtime_route(
        registry_path=global_registry,
        goal_id=GOAL_ID,
    )

    assert route["status"] == "source_registry"
    assert route["routed_to_source_registry"] is True
    assert Path(route["source_runtime_root"]) == project / ".loopx" / "runtime"


def test_relative_runtime_belongs_to_registry_even_when_goal_repo_differs(tmp_path: Path) -> None:
    for relative_registry in ("registry.json", ".loopx/registry.json"):
        owner = tmp_path / relative_registry.replace("/", "-")
        registry = owner / relative_registry
        _write_registry(registry, runtime_root="runtime")
        payload = json.loads(registry.read_text())
        payload["goals"][0]["repo"] = str(tmp_path / "separate-goal-checkout")
        registry.write_text(json.dumps(payload))
        route = resolve_goal_source_runtime_route(registry_path=registry, goal_id=GOAL_ID)
        assert Path(route["source_runtime_root"]) == owner / "runtime"
        assert route["status"] == "project_registry"


def test_feishu_sync_uses_source_rows_and_visual_marker(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    source_runtime = project / ".loopx" / "runtime"
    shared_runtime = tmp_path / "shared"
    global_registry = shared_runtime / "registry.global.json"
    _write_registry(source_registry, runtime_root=".loopx/runtime")
    _write_registry(
        global_registry,
        runtime_root=str(shared_runtime),
        source_registry=source_registry,
        global_registry=True,
    )
    _append_node(shared_runtime, node_id="node_shared_stale", title="Shared stale row")
    _append_node(source_runtime, node_id="node_source_one", title="Source row one")
    _append_node(source_runtime, node_id="node_source_two", title="Source row two")

    config_path = tmp_path / "lark-explore.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_lark_explore_local_config_v0",
                "board": {
                    "base_token": "BASE_FIXTURE",
                    "tables": {
                        "nodes": "tblNodes",
                        "edges": "tblEdges",
                        "findings": "tblFindings",
                    },
                    "cli_bin": "lark-cli",
                    "identity": "user",
                },
                "visual_sink": {
                    "whiteboard_token": "wb_fixture",
                    "view_role": "canonical",
                    "projection_mode": "canonical_full",
                    "board_style": "auto_flow",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured: list[dict[str, object]] = []
    _activate_lark(shared_runtime)

    result = handle_explore_command(
        _sync_args(config_path),
        registry_path=global_registry,
        runtime_root_arg=None,
        print_payload=lambda payload, _fmt, _renderer: captured.append(payload),
        output_format=lambda _args: "json",
    )

    assert result == 0
    payload = captured[0]
    assert payload["row_counts"] == {"nodes": 2, "edges": 0, "findings": 0}
    assert "node_source_one" in json.dumps(payload["records"])
    assert "node_shared_stale" not in json.dumps(payload["records"])
    assert payload["source_runtime_route"]["routed_to_source_registry"] is True
    visual = payload["visual_sync"]
    assert visual["graph_counts"]["node_count"] == 2
    assert visual["source_revision"].startswith("events-2-")
    assert visual["readback"]["expected_marker"].startswith("LoopX delivery ")


def test_global_node_write_appends_only_to_source_runtime(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    source_runtime = project / ".loopx" / "runtime"
    shared_runtime = tmp_path / "shared"
    global_registry = shared_runtime / "registry.global.json"
    _write_registry(source_registry, runtime_root=".loopx/runtime")
    _write_registry(
        global_registry,
        runtime_root=str(shared_runtime),
        source_registry=source_registry,
        global_registry=True,
    )
    captured: list[dict[str, object]] = []

    result = handle_explore_command(
        argparse.Namespace(
            command="explore",
            explore_command="node",
            goal_id=GOAL_ID,
            title="Canonical source write",
            node_id="node_source_write",
            node_kind="artifact",
            status="resolved",
            summary=None,
            blocked_reason=None,
            parent_id=None,
            agent_id="fixture-agent",
            run_id=None,
            evidence_ref=[],
            tag=[],
            supersedes=None,
        ),
        registry_path=global_registry,
        runtime_root_arg=None,
        print_payload=lambda payload, _fmt, _renderer: captured.append(payload),
        output_format=lambda _args: "json",
    )

    assert result == 0
    assert captured[0]["source_runtime_route"]["routed_to_source_registry"] is True
    assert (
        len(
            load_explore_result_events(
                explore_result_log_path(source_runtime, GOAL_ID), goal_id=GOAL_ID
            )
        )
        == 1
    )
    assert (
        load_explore_result_events(
            explore_result_log_path(shared_runtime, GOAL_ID), goal_id=GOAL_ID
        )
        == []
    )


def test_explicit_runtime_override_remains_authoritative(tmp_path: Path) -> None:
    source_registry = tmp_path / "project" / ".loopx" / "registry.json"
    global_registry = tmp_path / "shared" / "registry.global.json"
    override = tmp_path / "diagnostic-runtime"
    _write_registry(source_registry, runtime_root=".loopx/runtime")
    _write_registry(
        global_registry,
        runtime_root=str(tmp_path / "shared"),
        source_registry=source_registry,
        global_registry=True,
    )

    route = resolve_goal_source_runtime_route(
        registry_path=global_registry,
        goal_id=GOAL_ID,
        runtime_root_override=str(override),
    )

    assert route["status"] == "explicit_override"
    assert route["routed_to_source_registry"] is False
    assert Path(route["source_runtime_root"]) == override


def test_missing_source_registry_fails_closed(tmp_path: Path) -> None:
    global_registry = tmp_path / "shared" / "registry.global.json"
    _write_registry(
        global_registry,
        runtime_root=str(tmp_path / "shared"),
        source_registry=tmp_path / "missing" / "registry.json",
        global_registry=True,
    )

    try:
        resolve_goal_source_runtime_route(
            registry_path=global_registry,
            goal_id=GOAL_ID,
        )
    except ValueError as exc:
        assert "refusing to use the shared runtime as source" in str(exc)
    else:
        raise AssertionError("missing source_registry must fail closed")
