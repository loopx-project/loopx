#!/usr/bin/env python3
"""Smoke-test the exploration result layer: topology log, Lark board sync, card.

Covers the durable public contracts:
- result events (node/edge/finding) fold into a bounded public-safe projection
  with topology tree, blocked reasons, findings, and Mermaid graph source;
- absolute local paths are rejected at record time and never reach generated
  Lark record values (shared_adapter_local_path_leak guard);
- feishu-sync is dry-run by default, upserts idempotently by record id on the
  second executed sync, and shared visibility redacts private links;
- the result card is transport-free content built from the same projection;
- the CLI surface works end to end against a temp registry/runtime root.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.explore import result_log  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.capabilities.explore.router_state import (  # noqa: E402
    advance_epoch,
    scope_family_key,
    initial_router_state,
    observe_epoch,
)
from loopx.capabilities.explore.todo_branch_plan import build_explore_todo_branch_plan  # noqa: E402
from loopx.capabilities.explore.worker_branch_plan import (  # noqa: E402
    build_explore_worker_branch_plan,
)
from loopx.extensions.lark.presentation import explore_results  # noqa: E402
from loopx.extensions.lark.presentation import explore_visual_readback  # noqa: E402
from loopx.extensions.lark.presentation import explore_visual_styles  # noqa: E402

# Both exploration planners are deny-by-default behind the per-goal
# goal_boundary.orchestration.explore_harness gate; every library-level plan
# call in this smoke opts in with full spawn capacity so the pre-gate planner
# contracts stay observable. The gate states themselves are covered by
# examples/explore-worker-plan-gate-smoke.py.
_EXPLORE_PLAN_OPT_IN = {
    "spawn_allowed": True,
    "max_children": 16,
    "explore_harness": {"enabled": True},
}
build_explore_todo_branch_plan = functools.partial(  # noqa: E305
    build_explore_todo_branch_plan, orchestration=_EXPLORE_PLAN_OPT_IN
)
build_explore_worker_branch_plan = functools.partial(
    build_explore_worker_branch_plan, orchestration=_EXPLORE_PLAN_OPT_IN
)


ABS_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/](?![\\/])|file://|/Users/|/home/")


def build_sample_events(goal_id: str) -> list[dict[str, object]]:
    events = [
        result_log.build_explore_node_event(
            goal_id=goal_id,
            title="SCADE peer tool landscape",
            node_id="node_root",
            node_kind="area",
            status="exploring",
            tags=["portfolio"],
            recorded_at="2026-07-06T01:00:00Z",
        ),
        result_log.build_explore_node_event(
            goal_id=goal_id,
            title="KCG code generator licensing",
            node_id="node_kcg",
            node_kind="question",
            status="open",
            parent_id="node_root",
            recorded_at="2026-07-06T01:05:00Z",
        ),
        # Same node id again: latest event wins in the projection.
        result_log.build_explore_node_event(
            goal_id=goal_id,
            title="KCG code generator licensing",
            node_id="node_kcg",
            node_kind="question",
            status="blocked",
            blocked_reason="vendor licence terms unclear",
            parent_id="node_root",
            tags=["priority"],
            recorded_at="2026-07-06T02:00:00Z",
        ),
        result_log.build_explore_edge_event(
            goal_id=goal_id,
            from_node="node_kcg",
            to_node="node_root",
            edge_type="subtopic_of",
            recorded_at="2026-07-06T01:06:00Z",
        ),
        result_log.build_explore_finding_event(
            goal_id=goal_id,
            title="Two open-source Lustre toolchains cover the KCG core use case",
            node_id="node_kcg",
            status="confirmed",
            confidence=0.8,
            evidence_refs=["ov:doc:lustre-survey"],
            summary="See https://internal.example.invalid/wiki/abc for the private survey doc",
            recorded_at="2026-07-06T02:10:00Z",
        ),
    ]
    return events


def check_result_log_contract() -> dict[str, object]:
    goal_id = "explore-smoke-goal"
    with tempfile.TemporaryDirectory(prefix="loopx-explore-smoke-") as tmp:
        runtime_root = Path(tmp) / "runtime"
        log_path = result_log.explore_result_log_path(runtime_root, goal_id)
        for event in build_sample_events(goal_id):
            appended = result_log.append_explore_result_event(log_path, event)
            assert appended["ok"] is True, appended
        events = result_log.load_explore_result_events(log_path, goal_id=goal_id)
        assert len(events) == 5, events

        projection = result_log.build_explore_result_projection(events, goal_id=goal_id)
        assert projection["schema_version"] == "loopx_explore_result_projection_v0", projection
        counts = projection["counts"]
        assert counts["node_count"] == 2, counts
        assert counts["edge_count"] == 1, counts
        assert counts["finding_count"] == 1, counts
        assert counts["nodes_by_status"]["blocked"] == 1, counts

        # Latest node event wins and keeps update lineage.
        kcg = next(node for node in projection["nodes"] if node["node_id"] == "node_kcg")
        assert kcg["status"] == "blocked", kcg
        assert kcg["blocked_reason"] == "vendor licence terms unclear", kcg
        assert kcg["update_count"] == 2, kcg
        assert kcg["finding_count"] == 1, kcg

        stuck = projection["stuck"]
        assert [node["node_id"] for node in stuck] == ["node_kcg"], stuck

        tree = projection["tree"]
        assert len(tree) == 1 and tree[0]["node_id"] == "node_root", tree
        assert tree[0]["children"][0]["node_id"] == "node_kcg", tree

        mermaid = projection["mermaid"]
        assert mermaid.startswith("flowchart TD"), mermaid
        assert "node_kcg" in mermaid and ":::blocked" in mermaid, mermaid
        assert "-->|subtopic_of|" in mermaid, mermaid

        focused = result_log.build_explore_graph_view(
            projection["nodes"],
            projection["edges"],
            statuses=["blocked"],
            tags=["priority"],
        )
        assert focused["filter"] == {
            "active": True,
            "statuses": ["blocked"],
            "tags": ["priority"],
            "include_ancestors": True,
            "semantics": "status_and_any_tag",
        }, focused
        assert [node["node_id"] for node in focused["nodes"]] == [
            "node_root",
            "node_kcg",
        ], focused
        assert focused["graph_counts"] == {
            "node_count": 2,
            "edge_count": 1,
            "matched_node_count": 1,
            "context_node_count": 1,
        }, focused

        leaf_only = result_log.build_explore_graph_view(
            projection["nodes"],
            projection["edges"],
            tags=["priority"],
            include_ancestors=False,
        )
        assert [node["node_id"] for node in leaf_only["nodes"]] == ["node_kcg"], leaf_only
        assert leaf_only["edges"] == [], leaf_only

    # Public-safety gates at record time.
    try:
        result_log.build_explore_finding_event(
            goal_id=goal_id,
            title="leaky finding",
            evidence_refs=["C:\\\\work\\\\private\\\\notes.md"],
        )
        raise AssertionError("absolute evidence ref must be rejected")
    except ValueError:
        pass
    try:
        result_log.build_explore_node_event(
            goal_id=goal_id,
            title="Review C:\\\\work\\\\private\\\\notes.md",
        )
        raise AssertionError("absolute path in public text must be rejected")
    except ValueError:
        pass
    try:
        result_log.build_explore_finding_event(
            goal_id=goal_id,
            title="leaky summary",
            summary="See file:///tmp/private-notes.md for details",
        )
        raise AssertionError("file URL in public text must be rejected")
    except ValueError:
        pass
    try:
        result_log.build_explore_node_event(goal_id=goal_id, title="stuck node", status="blocked")
        raise AssertionError("blocked node without blocked_reason must be rejected")
    except ValueError:
        pass
    return projection


def lark_list_fixture(records: list[tuple[str, str, str]]) -> dict[str, object]:
    fields = ["LoopX Goal ID", "LoopX Result ID", "Title"]
    return {
        "ok": True,
        "data": {
            "fields": fields,
            "data": [[goal, result, title] for goal, result, title in records],
            "record_id_list": [f"rec_fixture_{index}" for index in range(len(records))],
        },
    }


def lark_value_list_fixture(
    records: list[tuple[str, dict[str, object]]], *, has_more: bool = False
) -> dict[str, object]:
    fields = sorted({field for _, values in records for field in values})
    return {
        "ok": True,
        "data": {
            "fields": fields,
            "data": [[values.get(field) for field in fields] for _, values in records],
            "record_id_list": [record_id for record_id, _ in records],
            "has_more": has_more,
        },
    }


def check_lark_sync_contract() -> None:
    goal_id = "explore-smoke-goal"
    events = build_sample_events(goal_id)
    projection = result_log.build_explore_result_projection(events, goal_id=goal_id)
    config = explore_results.LarkExploreConfig(
        **{"base_" + "token": "SMOKE_BASE"},
        table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
    )

    with tempfile.TemporaryDirectory(prefix="loopx-explore-smoke-") as tmp:
        config_path = Path(tmp) / ".loopx" / "lark-explore.json"

        # Dry-run: plans upserts, runs nothing, and leaks no local paths.
        dry = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            execute=False,
        )
        assert dry["ok"] is True and dry["execute"] is False, dry
        assert dry["row_counts"] == {"nodes": 2, "edges": 1, "findings": 1}, dry
        assert all(not item["command"]["executed"] for item in dry["records"]), dry
        record_blob = json.dumps(dry["records"], ensure_ascii=False)
        assert str(REPO_ROOT) not in record_blob, record_blob
        assert tmp not in record_blob, record_blob
        assert not ABS_PATH_RE.search(record_blob), record_blob

        # Executed sync creates records and remembers record ids.
        upsert_calls: list[list[str]] = []
        upsert_attempts = 0
        created_records = 0
        fail_on_upsert_attempt = 2
        remote_records: dict[str, dict[str, dict[str, object]]] = {
            "tblN": {},
            "tblE": {},
            "tblF": {},
        }
        for index in range(205):
            remote_records["tblN"][f"rec_filler_{index}"] = {
                "LoopX Goal ID": goal_id,
                "LoopX Result ID": f"filler-{index}",
                "Title": f"filler {index}",
            }

        def arg_value(args: list[str], flag: str) -> str:
            return args[args.index(flag) + 1]

        def fake_runner(args: list[str], cwd: Path | None, timeout: float | None) -> dict[str, object]:
            nonlocal upsert_attempts, created_records
            if "+record-list" in args:
                table_id = arg_value(args, "--table-id")
                offset = int(arg_value(args, "--offset"))
                limit = int(arg_value(args, "--limit"))
                records = list(remote_records[table_id].items())
                page = records[offset : offset + limit]
                return {
                    "returncode": 0,
                    "stdout": json.dumps(lark_value_list_fixture(page, has_more=offset + len(page) < len(records))),
                    "stderr": "",
                    "timed_out": False,
                }
            if "+record-upsert" in args:
                upsert_attempts += 1
                upsert_calls.append(list(args))
                if upsert_attempts == fail_on_upsert_attempt:
                    return {
                        "returncode": 1,
                        "stdout": json.dumps({"ok": False, "error": "simulated interruption"}),
                        "stderr": "",
                        "timed_out": False,
                    }
                table_id = arg_value(args, "--table-id")
                values = json.loads(arg_value(args, "--json"))
                if "--record-id" in args:
                    record_id = arg_value(args, "--record-id")
                else:
                    created_records += 1
                    record_id = f"rec_new_{created_records}"
                remote_records[table_id][record_id] = values
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": True,
                            "data": {
                                "created": 1,
                                "record": {"record_id_list": [record_id]},
                            },
                        }
                    ),
                    "stderr": "",
                    "timed_out": False,
                }
            raise AssertionError(args)

        interrupted = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            execute=True,
            runner=fake_runner,
        )
        assert interrupted["ok"] is False, interrupted
        assert len(upsert_calls) == 2, upsert_calls
        stored = json.loads(config_path.read_text(encoding="utf-8"))
        assert len(stored["result_records"]) == 1, stored

        # Resume skips the delivered row and fills only the missing records.
        upsert_calls.clear()
        fail_on_upsert_attempt = -1
        first = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            execute=True,
            runner=fake_runner,
        )
        assert first["ok"] is True, first
        assert first["readback"]["verified"] is True, first
        assert first["readback"]["source"] == "post_write_scan", first
        assert first["readback"]["expected_result_count"] == 4, first
        assert first["readback"]["observed_result_count"] == 4, first
        assert len(upsert_calls) == 3, upsert_calls
        assert all("--record-id" not in call for call in upsert_calls), upsert_calls
        assert first["written_rows"] == 3 and first["skipped_rows"] == 1, first
        assert len([item for item in first["commands"] if "+record-list" in item["command"]]) == 8
        stored = json.loads(config_path.read_text(encoding="utf-8"))
        assert len(stored["result_records"]) == 4, stored

        # Second executed sync discovers all pages and performs no unchanged writes.
        stored_before_second = config_path.read_text(encoding="utf-8")
        upsert_calls.clear()
        second = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            execute=True,
            runner=fake_runner,
        )
        assert second["ok"] is True, second
        assert second["readback"]["verified"] is True, second
        assert second["readback"]["source"] == "initial_scan", second
        assert len([item for item in second["commands"] if "+record-list" in item["command"]]) == 4
        assert not upsert_calls, upsert_calls
        assert second["written_rows"] == 0 and second["skipped_rows"] == 4, second
        assert config_path.read_text(encoding="utf-8") == stored_before_second
        edge_record = next(item for item in second["records"] if item["table"] == "edges")
        assert edge_record["values"]["From Node Link"] == [{"id": "rec_new_2"}], edge_record
        assert edge_record["values"]["To Node Link"] == [{"id": "rec_new_1"}], edge_record

        # A remote single-row drift is repaired without rewriting the full graph.
        remote_records["tblN"]["rec_new_1"]["Title"] = "manually drifted title"
        third = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            execute=True,
            runner=fake_runner,
        )
        assert third["ok"] is True, third
        assert len(upsert_calls) == 1, upsert_calls
        assert "--record-id" in upsert_calls[0], upsert_calls
        assert third["written_rows"] == 1 and third["skipped_rows"] == 3, third

        # Shared visibility redacts private links in row values.
        shared = explore_results.sync_explore_results_to_lark(
            config,
            projection=projection,
            config_path=config_path,
            sink_visibility="shared",
            execute=False,
        )
        shared_blob = json.dumps(shared["records"], ensure_ascii=False)
        assert "internal.example.invalid" not in shared_blob, shared_blob
        assert "[private-link-redacted]" in shared_blob, shared_blob


def check_visual_marker_readback_retry_contract() -> None:
    marker = "LoopX delivery smoke-marker"
    config = explore_results.LarkExploreConfig(
        **{"base_" + "token": "SMOKE_BASE"},
        table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
    )
    calls = 0

    def settling_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "ok": False,
                        "error": {"code": 2890002, "message": "invalid arg"},
                    }
                ),
                "stderr": "",
                "timed_out": False,
            }
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {"ok": True, "data": {"nodes": [{"text": {"text": marker}}]}}
            ),
            "stderr": "",
            "timed_out": False,
        }

    settled = explore_visual_readback.readback_visual_delivery_marker(
        cli_bin=config.cli_bin,
        identity=config.identity,
        whiteboard_token="wb_public_fixture",
        marker=marker,
        runner=settling_runner,
        retry_delays=(0.0,),
    )
    assert settled["ok"] is True and settled["verified"] is True, settled
    assert settled["attempt_count"] == 2, settled
    assert settled["attempts"][0] == {
        "attempt": 1,
        "ok": False,
        "marker_observed": False,
        "error_code": 2890002,
        "timed_out": False,
        "retryable": True,
    }, settled
    assert settled["retryable"] is False, settled

    observed_timeouts: list[float | None] = []

    def timeout_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        observed_timeouts.append(timeout)
        raise subprocess.TimeoutExpired(args, timeout)

    timed_out = explore_visual_readback.readback_visual_delivery_marker(
        cli_bin=config.cli_bin,
        identity=config.identity,
        whiteboard_token="wb_timeout_fixture",
        marker=marker,
        runner=timeout_runner,
        retry_delays=(),
    )
    assert observed_timeouts == [
        explore_visual_readback.VISUAL_READBACK_COMMAND_TIMEOUT_SECONDS
    ], observed_timeouts
    assert timed_out["ok"] is False and timed_out["retryable"] is True, timed_out
    assert timed_out["host_wait_exhausted"] is True, timed_out
    assert timed_out["command"]["timed_out"] is True, timed_out

    stage_markers = {
        "wb_stage_1": "LoopX delivery stage-1",
        "wb_stage_2": "LoopX delivery stage-2",
    }
    stage_calls = {token: 0 for token in stage_markers}

    def batch_settling_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        token = args[args.index("--whiteboard-token") + 1]
        stage_calls[token] += 1
        marker = stage_markers[token] if stage_calls[token] > 1 else "old marker"
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {"ok": True, "data": {"nodes": [{"text": {"text": marker}}]}}
            ),
            "stderr": "",
            "timed_out": False,
        }

    stage_results = []
    stage_targets = []
    for token, marker in stage_markers.items():
        result = {
            "ok": False,
            "status": "publish_unverified",
            "published": False,
            "command": {"ok": True},
            "readback": {
                "ok": False,
                "performed": False,
                "verified": False,
                "source": "deferred_to_batch",
                "expected_marker": marker,
                "observed_marker": None,
                "retryable": True,
                "attempts": [],
            },
            "retryable": True,
        }
        stage_results.append(result)
        stage_targets.append((result, token))

    sleeps: list[float] = []
    original_sleep = explore_visual_readback.time.sleep
    explore_visual_readback.time.sleep = sleeps.append
    try:
        explore_visual_readback.settle_visual_stage_readbacks(
            cli_bin=config.cli_bin,
            identity=config.identity,
            stage_targets=stage_targets,
            runner=batch_settling_runner,
            retry_delays=(0.0,),
        )
    finally:
        explore_visual_readback.time.sleep = original_sleep
    assert sleeps == [0.0], sleeps
    assert stage_calls == {"wb_stage_1": 2, "wb_stage_2": 2}, stage_calls
    assert all(result["ok"] is True for result in stage_results), stage_results
    assert all(result["published"] is True for result in stage_results), stage_results
    assert all(
        result["readback"]["attempt_count"] == 2 for result in stage_results
    ), stage_results

    timeout_stage_calls: list[str] = []

    def batch_timeout_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        token = args[args.index("--whiteboard-token") + 1]
        timeout_stage_calls.append(token)
        if token == "wb_stage_2":
            raise subprocess.TimeoutExpired(args, timeout)
        expected_marker = stage_markers.get(
            token,
            f"LoopX delivery stage-{token.rsplit('_', 1)[-1]}",
        )
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": expected_marker}}]},
                }
            ),
            "stderr": "",
            "timed_out": False,
        }

    timed_stage_results = []
    timed_stage_targets = []
    for index in range(1, 4):
        token = f"wb_stage_{index}"
        expected_marker = stage_markers.get(token, f"LoopX delivery stage-{index}")
        result = {
            "ok": False,
            "status": "publish_unverified",
            "published": False,
            "command": {"ok": True},
            "readback": {
                "ok": False,
                "expected_marker": expected_marker,
                "retryable": True,
                "attempts": [],
            },
            "retryable": True,
        }
        timed_stage_results.append(result)
        timed_stage_targets.append((result, token))

    explore_visual_readback.settle_visual_stage_readbacks(
        cli_bin=config.cli_bin,
        identity=config.identity,
        stage_targets=timed_stage_targets,
        runner=batch_timeout_runner,
        retry_delays=(0.0,),
    )
    assert timeout_stage_calls == ["wb_stage_1", "wb_stage_2"], timeout_stage_calls
    assert timed_stage_results[0]["ok"] is True, timed_stage_results
    assert timed_stage_results[1]["readback"]["host_wait_exhausted"] is True
    assert timed_stage_results[2]["readback"]["source"] == (
        "batch_host_wait_exhausted"
    )

    summary = explore_visual_styles.summarize_explore_visual_sync(
        views={"canonical": {"ok": False, "retryable": True}},
        configured_roles=["canonical"],
        recommended_roles=["canonical"],
        execute=True,
    )
    assert summary["ok"] is False, summary
    assert summary["status"] == "publish_failed", summary
    assert summary["retryable"] is True, summary
    assert "canonical marker readback" in str(summary["required_action"]), summary


def check_lark_setup_and_card() -> None:
    goal_id = "explore-smoke-goal"
    projection = result_log.build_explore_result_projection(build_sample_events(goal_id), goal_id=goal_id)

    schema = explore_results.lark_explore_schema_payload()
    assert schema["schema_version"] == "loopx_lark_explore_result_board_v0", schema
    assert set(schema["tables"]) == {"nodes", "edges", "findings"}, schema
    edge_fields = schema["tables"]["edges"]["fields"]
    assert any(
        field.get("name") == "From Node Link" and field.get("type") == "link" and field.get("link_table") == "Nodes"
        for field in edge_fields
    ), edge_fields
    assert any(
        field.get("name") == "To Node Link" and field.get("type") == "link" and field.get("link_table") == "Nodes"
        for field in edge_fields
    ), edge_fields

    with tempfile.TemporaryDirectory(prefix="loopx-explore-smoke-") as tmp:
        config_path = Path(tmp) / ".loopx" / "lark-explore.json"
        dry = explore_results.setup_lark_explore_board(config_path=config_path, execute=False)
        assert dry["ok"] is True and not config_path.exists(), dry
        assert len(dry["commands"]) == 4, dry

        def fake_runner(args: list[str], cwd: Path | None, timeout: float | None) -> dict[str, object]:
            if "+base-create" in args:
                payload = {
                    "ok": True,
                    "data": {
                        "app_token": "SMOKE_BASE",
                        "base": {
                            "base_token": "SMOKE_BASE",
                            "url": "https://example.invalid/base/SMOKE_BASE",
                        },
                    },
                }
            elif "+table-create" in args:
                name = args[args.index("--name") + 1]
                payload = {"ok": True, "data": {"table_id": f"tbl{name}"}}
            else:
                raise AssertionError(args)
            return {
                "returncode": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
                "timed_out": False,
            }

        executed = explore_results.setup_lark_explore_board(config_path=config_path, execute=True, runner=fake_runner)
        assert executed["ok"] is True, executed
        assert executed["tables"] == {
            "nodes": "tblNodes",
            "edges": "tblEdges",
            "findings": "tblFindings",
        }, executed
        assert executed["board"]["base_url"] == "https://example.invalid/base/SMOKE_BASE", executed
        stored = json.loads(config_path.read_text(encoding="utf-8"))
        assert stored["board"]["tables"]["nodes"] == "tblNodes", stored
        assert stored["board"]["base_url"] == "https://example.invalid/base/SMOKE_BASE", stored

        config_path.write_bytes(b"\xef\xbb\xbf" + config_path.read_bytes())
        bom_payload = explore_results.read_lark_explore_local_config(config_path)
        assert bom_payload["ok"] is True, bom_payload
        assert bom_payload["board"]["base_token"] == "SMOKE_BASE", bom_payload

    card_payload = explore_results.build_explore_result_card(projection)
    assert card_payload["ok"] is True, card_payload
    assert card_payload["schema_version"] == "loopx_lark_explore_card_v0", card_payload
    markdown = card_payload["card_markdown"]
    assert "**Exploration map**: 2 nodes" in markdown, markdown
    assert "**Blocked**" in markdown and "vendor licence terms unclear" in markdown, markdown
    assert "[confirmed] Two open-source Lustre toolchains" in markdown, markdown
    card = card_payload["card"]
    assert card["header"]["title"]["content"].startswith("Exploration map:"), card
    assert card["elements"][0]["text"]["tag"] == "lark_md", card


def check_todo_branch_prediction_contract() -> None:
    goal_id = "explore-smoke-goal"
    projection = result_log.build_explore_result_projection(
        [
            result_log.build_explore_node_event(
                goal_id=goal_id,
                title="MATLAB Simulink code generation frontier",
                node_id="matlab_codegen_frontier",
                status="exploring",
                tags=["matlab", "codegen"],
            )
        ],
        goal_id=goal_id,
    )
    todos = [
        {
            "todo_id": "todo_primary",
            "index": 1,
            "status": "open",
            "text": "[P0] Validate MATLAB codegen topology mapping",
            "task_class": "advancement_task",
            "claimed_by": "codex-main-control",
            "required_write_scopes": ["loopx/capabilities/explore/**"],
        },
        {
            "todo_id": "todo_parallel",
            "index": 2,
            "status": "open",
            "text": "[P1] Add Simulink finding projection smoke",
            "task_class": "advancement_task",
            "required_write_scopes": ["examples/**"],
        },
        {
            "todo_id": "todo_conflict",
            "index": 3,
            "status": "open",
            "text": "[P1] Edit alternate topology predictor",
            "task_class": "advancement_task",
            "required_write_scopes": ["loopx/capabilities/explore/result_log.py"],
        },
        {
            "todo_id": "todo_other",
            "index": 4,
            "status": "open",
            "text": "[P0] Other agent owned branch",
            "task_class": "advancement_task",
            "claimed_by": "codex-side-agent",
        },
    ]
    plan = build_explore_todo_branch_plan(
        goal_id=goal_id,
        todos=todos,
        projection=projection,
        agent_id="codex-main-control",
        width=3,
    )
    assert plan["ok"] is True and plan["dry_run"] is True, plan
    assert plan["schema_version"] == "loopx_explore_todo_branch_plan_v0", plan
    assert plan["boundary"]["starts_agents"] is False, plan
    assert plan["scheduler"]["schema_version"] == "loopx_explore_speculative_scheduler_v0", plan
    assert plan["scheduler"]["strategy"] == "dspark_confidence_scheduled_prefix", plan
    assert set(plan["scheduler"]["ab_comparison"]) == {
        "baseline_serial",
        "dspark_selected",
    }, plan
    assert plan["ab_result"]["schema_version"] == "loopx_explore_branch_plan_ab_result_v0", plan
    assert plan["ab_result"]["baseline_serial_theta"] > 0, plan
    assert plan["ab_result"]["estimated_speedup_vs_baseline"] > 0, plan
    selected_ids = [item["todo_id"] for item in plan["selected_branches"]]
    assert selected_ids[:2] == ["todo_primary", "todo_parallel"], plan
    assert all("expected_evidence_units" in item for item in plan["selected_branches"]), plan
    rejected = {item["todo_id"]: item for item in plan["rejected_candidates"]}
    assert rejected["todo_conflict"]["selection_status"] == "rejected_hazard", plan
    assert rejected["todo_other"]["selection_status"] == "blocked_claimed_by_other", plan
    assert not any("task-lease" in command for command in plan["selected_branches"][0]["suggested_commands"]), plan

    worker_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=todos,
        projection=projection,
        agent_id="codex-main-control",
        worker_width=2,
        max_todos_per_branch=3,
    )
    assert worker_plan["ok"] is True and worker_plan["dry_run"] is True, worker_plan
    assert worker_plan["schema_version"] == "loopx_explore_worker_branch_plan_v0", worker_plan
    assert worker_plan["harness_compatibility"]["replaces_loopx_runtime"] is False, worker_plan
    assert worker_plan["boundary"]["starts_agents"] is False, worker_plan
    assert worker_plan["selected_worker_branch_count"] >= 1, worker_plan
    first_worker = worker_plan["selected_worker_branches"][0]
    assert len(first_worker["todo_bundle"]) >= 1, worker_plan
    assert first_worker["execution_contract"]["must_enter_loopx_harness"] is True, worker_plan
    assert worker_plan["ab_result"]["schema_version"] == "loopx_explore_worker_branch_plan_ab_result_v0", worker_plan
    assert worker_plan["ab_result"]["estimated_speedup_vs_baseline"] > 0, worker_plan
    assert any(event["event_type"] == "predicted" for event in worker_plan["accept_reject_trace"]), worker_plan

    adaptive_worker_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=todos,
        projection=projection,
        agent_id="codex-main-control",
        worker_width=6,
        harness_profile="adaptive-resilient",
    )
    assert adaptive_worker_plan["ok"] is True, adaptive_worker_plan
    assert adaptive_worker_plan["harness_profile"] == "adaptive-resilient", adaptive_worker_plan
    assert adaptive_worker_plan["branch_fill_policy"] == "value-first", adaptive_worker_plan
    assert adaptive_worker_plan["selected_worker_branch_count"] <= 6, adaptive_worker_plan
    assert adaptive_worker_plan["harness_compatibility"]["duration_guard_controlled_by_planner"] is False, (
        adaptive_worker_plan
    )
    assert adaptive_worker_plan["harness_compatibility"]["fixed_worker_count_controlled_by_planner"] is False, (
        adaptive_worker_plan
    )
    assert adaptive_worker_plan["harness_compatibility"]["forces_full_branch_fill"] is False, adaptive_worker_plan
    assert adaptive_worker_plan["max_todos_per_branch_explicit"] is False, adaptive_worker_plan
    assert adaptive_worker_plan["harness_compatibility"]["adaptive_todo_batching"] is True, adaptive_worker_plan
    profile = adaptive_worker_plan["worker_harness_profile"]
    assert profile["duration_guard"]["enabled"] is False, profile
    assert profile["concurrency_policy"]["fixed_worker_count"] is False, profile
    assert profile["concurrency_policy"]["does_not_force_requested_width"] is True, profile
    assert profile["retry_policy"]["enabled"] is True, profile
    assert profile["infra_cooldown"]["enabled"] is True, profile
    assert any(0 < len(branch["todo_bundle"]) < 8 for branch in adaptive_worker_plan["selected_worker_branches"]), (
        adaptive_worker_plan
    )


def _router_smoke_todo(index: int, *, family: str, priority: str = "P0") -> dict[str, object]:
    return {
        "todo_id": f"todo_router_{family}_{index}",
        "index": index,
        "status": "open",
        "text": f"[{priority}] Probe {family} facet {index}",
        "task_class": "advancement_task",
        "required_write_scopes": [f"artifacts/{family}/**"],
    }


def check_worker_lane_router_contract() -> None:
    goal_id = "explore-smoke-router"

    # 1. Width is no longer silently clamped to 8: 12 distinct-family todos at
    #    worker_width=10 under independent-lane admission saturate all 10 lanes.
    families = [f"fam{index:02d}" for index in range(12)]
    wide_todos = [_router_smoke_todo(index + 1, family=family) for index, family in enumerate(families)]
    wide_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=wide_todos,
        agent_id="codex-main-control",
        worker_width=10,
        harness_profile="adaptive-resilient",
    )
    assert wide_plan["worker_width"] == 10, wide_plan["worker_width"]
    assert wide_plan["scheduler_model"] == "independent_lane_admission", wide_plan["scheduler_model"]
    assert wide_plan["selected_worker_branch_count"] == 10, wide_plan["selected_worker_branch_count"]
    assert wide_plan["admission_audit"]["queue_exhausted"] is False, wide_plan["admission_audit"]
    outside = [
        branch
        for branch in wide_plan["rejected_worker_branches"]
        if branch.get("selection_status") == "outside_verification_budget"
    ]
    assert len(outside) == 2, [branch.get("branch_id") for branch in outside]

    # 2. Fewer todos than width: every idle lane is a queue-exhaustion, not a cap.
    narrow_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=wide_todos[:5],
        agent_id="codex-main-control",
        worker_width=10,
        harness_profile="adaptive-resilient",
    )
    assert narrow_plan["selected_worker_branch_count"] == 5, narrow_plan["selected_worker_branch_count"]
    assert narrow_plan["admission_audit"]["queue_exhausted"] is True, narrow_plan["admission_audit"]

    # 3. moe-router without router state still plans (router disabled but supported).
    bare_moe_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=wide_todos[:4],
        agent_id="codex-main-control",
        worker_width=4,
        harness_profile="moe-router",
    )
    assert bare_moe_plan["router"]["supported_by_profile"] is True, bare_moe_plan["router"]
    assert bare_moe_plan["router"]["enabled"] is False, bare_moe_plan["router"]
    assert bare_moe_plan["strategy"] == "independent_lane_worker_prediction", bare_moe_plan["strategy"]

    # 4. Aux-loss-free invariant: bias reorders routing but leaves value
    #    bookkeeping untouched. Two equal-score families; biasing the
    #    alphabetically-later one moves it first without changing evidence.
    fam_first, fam_second = "family_alpha", "family_beta"
    pair_todos = [
        _router_smoke_todo(1, family=fam_first),
        _router_smoke_todo(2, family=fam_second),
    ]
    biased_state = initial_router_state()
    biased_state["families"][scope_family_key(fam_second)] = {"bias": 0.4, "runs": 0}
    unbiased_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=pair_todos,
        agent_id="codex-main-control",
        worker_width=2,
        harness_profile="moe-router",
        router_state=initial_router_state(),
    )
    biased_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=pair_todos,
        agent_id="codex-main-control",
        worker_width=2,
        harness_profile="moe-router",
        router_state=biased_state,
    )
    assert unbiased_plan["router"]["enabled"] is True, unbiased_plan["router"]
    unbiased_order = [branch["affinity_key"] for branch in unbiased_plan["selected_worker_branches"]]
    biased_order = [branch["affinity_key"] for branch in biased_plan["selected_worker_branches"]]
    assert unbiased_order[0].endswith(fam_first), unbiased_order
    assert biased_order[0].endswith(fam_second), biased_order
    evidence_by_family = lambda plan: {  # noqa: E731
        branch["affinity_key"]: branch["expected_evidence_units"] for branch in plan["selected_worker_branches"]
    }
    assert evidence_by_family(unbiased_plan) == evidence_by_family(biased_plan), (
        evidence_by_family(unbiased_plan),
        evidence_by_family(biased_plan),
    )
    routed_branch = biased_plan["selected_worker_branches"][0]
    assert "router" in routed_branch and "routing_score" in routed_branch, routed_branch

    # 5. DSpark confident-prefix bundles: P0+P1 clear the calibrated threshold,
    #    P2 truncates into its own lane; a family observed rejecting everything
    #    collapses to single-todo bundles.
    bundle_todos = [
        _router_smoke_todo(1, family="family_gamma", priority="P0"),
        _router_smoke_todo(2, family="family_gamma", priority="P1"),
        _router_smoke_todo(3, family="family_gamma", priority="P2"),
    ]

    def _bundle_sizes(plan: dict[str, object]) -> list[int]:
        # Same-family lanes share a write-scope root, so the selection loop
        # keeps only the first as parallel-safe; bundle FORMATION is what is
        # under test here, so collect selected and rejected branches alike.
        branches = list(plan["selected_worker_branches"]) + [
            branch for branch in plan["rejected_worker_branches"] if branch.get("todo_bundle")
        ]
        return sorted(len(branch["todo_bundle"]) for branch in branches)

    bundle_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=bundle_todos,
        agent_id="codex-main-control",
        worker_width=3,
        harness_profile="moe-router",
        router_state=initial_router_state(),
    )
    assert _bundle_sizes(bundle_plan) == [1, 2], _bundle_sizes(bundle_plan)

    rejected_family_state = observe_epoch(
        initial_router_state(),
        epoch=1,
        probes=[
            {
                "family": scope_family_key("family_gamma"),
                "duration_minutes": 1.0,
                "observation_keys": [],
                "accepted": False,
                "retryable_infra_error": False,
            }
        ],
    )
    rejected_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=bundle_todos,
        agent_id="codex-main-control",
        worker_width=3,
        harness_profile="moe-router",
        router_state=rejected_family_state,
    )
    assert _bundle_sizes(rejected_plan) == [1, 1, 1], _bundle_sizes(rejected_plan)

    # 6. Opportunistic expansion: low novelty should not collapse a proven router
    #    to two lanes when the next candidates still have positive lane value.
    #    The expansion is bounded by value floors, not a blind fill rule.
    expansion_families = [f"expand_fam_{index:02d}" for index in range(10)]
    expansion_todos = [
        _router_smoke_todo(index + 1, family=family, priority="P1") for index, family in enumerate(expansion_families)
    ]
    expansion_state = initial_router_state()
    expansion_state["updated_epoch"] = 3
    expansion_state["totals"]["dispatches"] = 80
    for family in expansion_families:
        expansion_state["families"][scope_family_key(family)] = {
            "runs": 5,
            "value_rate_ema": 8.0,
            "duration_ema": 0.8,
            "accept_rate_ema": 1.0,
            "infra_ema": 0.0,
            "novelty_rate": 0.05,
            "last_run_epoch": 2,
            "bias": 0.0,
        }
    expansion_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=expansion_todos,
        agent_id="codex-main-control",
        worker_width=10,
        harness_profile="moe-router",
        router_state=expansion_state,
        load_profile={
            "parallel_wall_minutes": 8.0,
            "max_branch_minutes": 1.0,
            "branch_count": 10,
            "source": "smoke_high_interference_profile",
        },
    )
    expansion_audit = expansion_plan["admission_audit"]
    assert expansion_plan["selected_worker_branch_count"] >= 6, expansion_audit
    assert expansion_audit["opportunistic_admitted_count"] > 0, expansion_audit
    assert expansion_audit["core_lane_count"] < expansion_audit["admitted_lane_count"], expansion_audit
    assert expansion_audit["opportunistic_utilization_floor"] == 0.65, expansion_audit

    # 7. Router state lifecycle: the novelty ledger dedupes across epochs and
    #    coverage debt accrues bias for eligible-but-unrun families.
    fam_key = scope_family_key("family_alpha")
    state = observe_epoch(
        initial_router_state(),
        epoch=1,
        probes=[
            {
                "family": fam_key,
                "duration_minutes": 0.6,
                "observation_keys": ["obs_a", "obs_b"],
                "weighted_flags": {"flag_probe_ok": 2.0},
                "accepted": True,
                "retryable_infra_error": False,
            }
        ],
    )
    assert state["families"][fam_key]["novelty_rate"] == 1.0, state["families"][fam_key]
    state = observe_epoch(
        state,
        epoch=2,
        probes=[
            {
                "family": fam_key,
                "duration_minutes": 0.6,
                "observation_keys": ["obs_a", "obs_b"],
                "weighted_flags": {"flag_probe_ok": 2.0},
                "accepted": True,
                "retryable_infra_error": False,
            }
        ],
    )
    assert state["families"][fam_key]["novelty_rate"] == 0.0, state["families"][fam_key]
    idle_key = scope_family_key("family_beta")
    for epoch in range(3, 9):
        state = advance_epoch(state, epoch=epoch, eligible_families=[fam_key, idle_key])
    assert state["families"][idle_key]["bias"] > 0.2, state["families"][idle_key]
    assert state["families"][fam_key]["bias"] <= 0.0, state["families"][fam_key]

    # 8. Observed load profile calibrates admission instead of the 0.2 prior.
    calibrated_plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=wide_todos[:4],
        agent_id="codex-main-control",
        worker_width=4,
        harness_profile="moe-router",
        load_profile={
            "parallel_wall_minutes": 1.0,
            "max_branch_minutes": 1.0,
            "branch_count": 5,
        },
    )
    calibration = calibrated_plan["load_calibration"]
    assert calibration is not None and calibration["measured_load_factor"] == 0.0, calibration
    assert calibrated_plan["scheduler"]["load_factor"] < 0.2, calibrated_plan["scheduler"]["load_factor"]


def check_harness_domain_purity() -> None:
    """The exploration harness stack must stay software-agnostic.

    Domain vocabulary (target-software names, domain flag names, provider
    error codes) belongs to adapters and agent-authored data files, never to
    these modules. This gate is what keeps the harness reusable beyond any
    single system under exploration.
    """

    banned = (
        "simulink",
        "matlab",
        "mathworks",
        "stateflow",
        "sim_ok",
        "codegen_ok",
        "build_ok",
        "error 5001",
    )
    modules = (
        "loopx/capabilities/explore/harness_gate.py",
        "loopx/capabilities/explore/harness_runtime.py",
        "loopx/capabilities/explore/router_state.py",
        "loopx/capabilities/explore/speculative_scheduler.py",
        "loopx/capabilities/explore/todo_branch_plan.py",
        "loopx/capabilities/explore/worker_branch_plan.py",
        "loopx/cli_commands/explore.py",
    )
    for module in modules:
        lowered = (REPO_ROOT / module).read_text(encoding="utf-8").lower()
        hits = [token for token in banned if token in lowered]
        assert not hits, f"domain vocabulary leaked into {module}: {hits}"


def check_cli_surface() -> None:
    goal_id = "explore-smoke-cli"
    with tempfile.TemporaryDirectory(prefix="loopx-explore-smoke-") as tmp:
        registry = Path(tmp) / ".loopx" / "registry.json"
        runtime_root = Path(tmp) / "runtime"
        project = Path(tmp) / "project"
        state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
        (project / state_file).write_text(
            "---\n"
            "status: active\n"
            "updated_at: 2026-07-06T00:00:00+00:00\n"
            "---\n\n"
            "# Explore CLI Fixture\n\n"
            "## Agent Todo\n\n"
            "- [ ] [P0] Continue CLI topology experiment.\n"
            "  <!-- loopx:todo todo_id=todo_cli_primary status=open task_class=advancement_task claimed_by=codex-main-control required_write_scopes=loopx/capabilities/explore/** required_capabilities=resource_lane:long_pool -->\n"
            "- [ ] [P1] Add CLI branch prediction smoke.\n"
            "  <!-- loopx:todo todo_id=todo_cli_parallel status=open task_class=advancement_task required_write_scopes=examples/** required_capabilities=resource_lane:short_pool -->\n",
            encoding="utf-8",
        )
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-07-06T00:00:00+00:00",
                    "common_runtime_root": str(runtime_root),
                    "goals": [
                        {
                            "id": goal_id,
                            "domain": "explore-smoke",
                            "status": "active",
                            "state_file": state_file,
                            "repo": str(project),
                            "adapter": {
                                "kind": "explore_result_layer",
                                "status": "connected-read-only",
                            },
                            "quota": {"compute": 1.0, "window_hours": 24},
                            "spawn_policy": {
                                "allowed": True,
                                "max_children": 8,
                                "explore_harness": {"enabled": True},
                            },
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": [
                                    {
                                        "agent_id": "codex-main-control",
                                        "role": "primary",
                                    }
                                ],
                            },
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        import_fixture_authority(registry, goal_id, provider="sqlite")

        def run_cli(*extra_args: str) -> dict[str, object]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "loopx.cli",
                    "--format",
                    "json",
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime_root),
                    *extra_args,
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return json.loads(result.stdout)

        extension_install = run_cli(
            "extension",
            "install",
            "--bundled",
            "loopx-lark",
            "--execute",
        )
        assert extension_install["doctor"]["verified"] is True, extension_install

        node = run_cli(
            "explore",
            "node",
            "--goal-id",
            goal_id,
            "--title",
            "CLI topology root",
            "--node-id",
            "node_cli_root",
            "--status",
            "exploring",
            "--tag",
            "executive",
        )
        assert node["ok"] is True and node["result_id"] == "node_cli_root", node
        finding = run_cli(
            "explore",
            "finding",
            "--goal-id",
            goal_id,
            "--title",
            "CLI-visible finding",
            "--node",
            "node_cli_root",
            "--status",
            "tentative",
        )
        assert finding["ok"] is True, finding
        summary = run_cli("explore", "summary", "--goal-id", goal_id)
        assert summary["counts"]["node_count"] == 1, summary
        assert summary["counts"]["finding_count"] == 1, summary
        presentation = run_cli(
            "explore",
            "presentation",
            "--goal-id",
            goal_id,
        )
        assert presentation["presentation_mode"] == "canonical_only", presentation
        assert presentation["canonical"]["graph_counts"]["node_count"] == 1, presentation
        assert (
            presentation["canonical"]["source_revision"]
            == presentation["executive"]["source_revision"]
        ), presentation
        graph = run_cli("explore", "graph", "--goal-id", goal_id)
        assert str(graph["mermaid"]).startswith("flowchart TD"), graph
        focused_graph = run_cli(
            "explore",
            "graph",
            "--goal-id",
            goal_id,
            "--status",
            "exploring",
            "--tag",
            "executive",
        )
        assert focused_graph["filter"]["active"] is True, focused_graph
        assert focused_graph["graph_counts"]["matched_node_count"] == 1, focused_graph
        assert [item["node_id"] for item in focused_graph["nodes"]] == ["node_cli_root"], focused_graph
        branch_plan = run_cli(
            "explore",
            "todo-branch-plan",
            "--goal-id",
            goal_id,
            "--agent-id",
            "codex-main-control",
            "--width",
            "2",
        )
        assert branch_plan["ok"] is True and branch_plan["selected_count"] == 2, branch_plan
        assert branch_plan["enabled"] is True, branch_plan
        assert branch_plan["orchestration_gate"]["state"] == "commands_suggested", branch_plan
        assert branch_plan["boundary"]["claims_todos"] is False, branch_plan
        assert branch_plan["selected_branches"][0]["todo_id"] == "todo_cli_primary", branch_plan
        assert set(branch_plan["scheduler"]["ab_comparison"]) == {
            "baseline_serial",
            "dspark_selected",
        }, branch_plan
        assert branch_plan["ab_result"]["estimated_speedup_vs_baseline"] > 0, branch_plan
        resource_branch_plan = run_cli(
            "explore",
            "todo-branch-plan",
            "--goal-id",
            goal_id,
            "--agent-id",
            "codex-main-control",
            "--width",
            "2",
            "--resource-capacity",
            "long_pool=2",
            "--resource-usage",
            "long_pool=1",
            "--resource-capacity",
            "short_pool=3",
            "--resource-usage",
            "short_pool=2",
        )
        assert resource_branch_plan["resource_portfolio"]["selected_slot_count"] == 2, resource_branch_plan
        assert resource_branch_plan["resource_portfolio"]["remaining_slot_count"] == 0, resource_branch_plan
        worker_branch_plan = run_cli(
            "explore",
            "worker-branch-plan",
            "--goal-id",
            goal_id,
            "--agent-id",
            "codex-main-control",
            "--worker-width",
            "2",
            "--max-todos-per-branch",
            "2",
        )
        assert worker_branch_plan["ok"] is True, worker_branch_plan
        assert worker_branch_plan["schema_version"] == "loopx_explore_worker_branch_plan_v0", worker_branch_plan
        assert worker_branch_plan["enabled"] is True, worker_branch_plan
        assert worker_branch_plan["orchestration_gate"]["state"] == "commands_suggested", worker_branch_plan
        assert worker_branch_plan["harness_compatibility"]["uses_loopx_todo_projection"] is True, worker_branch_plan
        assert worker_branch_plan["boundary"]["claims_todos"] is False, worker_branch_plan
        assert worker_branch_plan["selected_worker_branch_count"] >= 1, worker_branch_plan
        assert (
            worker_branch_plan["selected_worker_branches"][0]["execution_contract"]["requires_quota_should_run"] is True
        ), worker_branch_plan
        adaptive_worker_branch_plan = run_cli(
            "explore",
            "worker-branch-plan",
            "--goal-id",
            goal_id,
            "--agent-id",
            "codex-main-control",
            "--harness-profile",
            "adaptive-resilient",
            "--worker-width",
            "5",
        )
        assert adaptive_worker_branch_plan["ok"] is True, adaptive_worker_branch_plan
        assert adaptive_worker_branch_plan["harness_profile"] == "adaptive-resilient", adaptive_worker_branch_plan
        assert adaptive_worker_branch_plan["branch_fill_policy"] == "value-first", adaptive_worker_branch_plan
        assert adaptive_worker_branch_plan["worker_harness_profile"]["duration_guard"]["enabled"] is False, (
            adaptive_worker_branch_plan
        )
        assert (
            adaptive_worker_branch_plan["harness_compatibility"]["fixed_worker_count_controlled_by_planner"] is False
        ), adaptive_worker_branch_plan
        assert adaptive_worker_branch_plan["harness_compatibility"]["forces_full_branch_fill"] is False, (
            adaptive_worker_branch_plan
        )
        assert adaptive_worker_branch_plan["max_todos_per_branch_explicit"] is False, adaptive_worker_branch_plan
        assert adaptive_worker_branch_plan["harness_compatibility"]["adaptive_todo_batching"] is True, (
            adaptive_worker_branch_plan
        )
        router_state_path = Path(tmp) / "router_state.json"
        router_state_path.write_text(
            json.dumps(initial_router_state(), indent=2) + "\n",
            encoding="utf-8",
        )
        load_profile_path = Path(tmp) / "load_profile.json"
        load_profile_path.write_text(
            json.dumps(
                {
                    "parallel_wall_minutes": 0.8,
                    "max_branch_minutes": 0.75,
                    "branch_count": 5,
                    "source": "smoke_observed_profile",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        moe_worker_branch_plan = run_cli(
            "explore",
            "worker-branch-plan",
            "--goal-id",
            goal_id,
            "--agent-id",
            "codex-main-control",
            "--harness-profile",
            "moe-router",
            "--worker-width",
            "5",
            "--router-state",
            str(router_state_path),
            "--load-profile",
            str(load_profile_path),
        )
        assert moe_worker_branch_plan["ok"] is True, moe_worker_branch_plan
        assert moe_worker_branch_plan["harness_profile"] == "moe-router", moe_worker_branch_plan
        assert moe_worker_branch_plan["router"]["enabled"] is True, moe_worker_branch_plan["router"]
        assert moe_worker_branch_plan["scheduler_model"] == "independent_lane_admission", moe_worker_branch_plan
        assert moe_worker_branch_plan["load_calibration"]["source"] == "smoke_observed_profile", moe_worker_branch_plan
        assert moe_worker_branch_plan["admission_audit"] is not None, moe_worker_branch_plan
        assert moe_worker_branch_plan["max_todos_per_branch_source"] == "confident_prefix_scheduler_safety_cap", (
            moe_worker_branch_plan
        )
        assert moe_worker_branch_plan["harness_compatibility"]["confidence_prefix_todo_batching"] is True, (
            moe_worker_branch_plan
        )
        sync = run_cli(
            "explore",
            "feishu-sync",
            "--goal-id",
            goal_id,
            "--config-path",
            str(Path(tmp) / ".loopx" / "lark-explore.json"),
            "--base-token",
            "SMOKE_BASE",
            "--table-id-nodes",
            "tblN",
            "--table-id-edges",
            "tblE",
            "--table-id-findings",
            "tblF",
        )
        assert sync["ok"] is True and sync["execute"] is False, sync
        assert tmp not in json.dumps(sync["records"], ensure_ascii=False), sync

        config_path = Path(tmp) / ".loopx" / "stored-lark-explore.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "loopx_lark_explore_local_config_v0",
                    "board": {
                        "base_token": "SMOKE_BASE",
                        "tables": {
                            "nodes": "tblN",
                            "edges": "tblE",
                            "findings": "tblF",
                        },
                        "cli_bin": "stored-lark-cli",
                        "identity": "user",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        configured_visual = run_cli(
            "explore",
            "feishu-visual-configure",
            "--config-path",
            str(config_path),
            "--whiteboard-token",
            "wb_public_fixture",
            "--docx-token",
            "doc_public_fixture",
            "--tag",
            "executive",
            "--projection-mode",
            "canonical_filtered",
            "--execute",
        )
        assert configured_visual["status"] == "configured", configured_visual
        stored_cli_sync = run_cli(
            "explore",
            "feishu-sync",
            "--goal-id",
            goal_id,
            "--config-path",
            str(config_path),
        )
        assert stored_cli_sync["ok"] is True, stored_cli_sync
        assert stored_cli_sync["commands"][0]["command"].startswith("stored-lark-cli "), stored_cli_sync
        assert stored_cli_sync["visual_sync"]["status"] == "would_publish", stored_cli_sync
        assert "whiteboard +update" in stored_cli_sync["visual_sync"]["command"]["command"], stored_cli_sync

        for role, mode in (
            ("canonical", "canonical_full"),
            ("executive", "executive_auto"),
        ):
            role_args = [
                "explore",
                "feishu-visual-configure",
                "--config-path",
                str(config_path),
                "--whiteboard-token",
                f"wb_{role}_fixture",
                "--view-role",
                role,
                "--projection-mode",
                mode,
            ]
            if role == "executive":
                role_args.extend(
                    ["--board-style", "semantic_lane_columns"]
                )
            role_args.append("--execute")
            role_config = run_cli(*role_args)
            assert role_config["status"] == "configured", role_config
            expected_style = (
                "semantic_lane_columns" if role == "executive" else "auto_flow"
            )
            assert role_config["visual_sink"]["board_style"] == expected_style, role_config
            expected_renderer = "stage_svg" if role == "executive" else "mermaid"
            assert role_config["visual_sink"]["renderer"] == expected_renderer, role_config

            updated_role_config = run_cli(
                "explore",
                "feishu-visual-configure",
                "--config-path",
                str(config_path),
                "--whiteboard-token",
                f"wb_{role}_updated_fixture",
                "--docx-token",
                f"doc_{role}_updated_fixture",
                "--stage-whiteboard-token",
                f"wb_{role}_stage_02_updated_fixture",
                "--view-role",
                role,
                "--projection-mode",
                mode,
                "--execute",
            )
            assert updated_role_config["visual_sink"]["board_style"] == expected_style, (
                updated_role_config
            )
            assert updated_role_config["visual_sink"]["renderer"] == expected_renderer, (
                updated_role_config
            )
        dual_config_sync = run_cli(
            "explore",
            "feishu-sync",
            "--goal-id",
            goal_id,
            "--config-path",
            str(config_path),
        )
        assert dual_config_sync["visual_sync"]["presentation_mode"] == "canonical_only", dual_config_sync
        assert dual_config_sync["visual_sync"]["views"]["canonical"]["status"] == "would_publish", (
            dual_config_sync
        )
        assert dual_config_sync["visual_sync"]["views"]["executive"]["status"] == "not_recommended", (
            dual_config_sync
        )

        # Error contract: unknown target without config fails with exit 1.
        error = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                "explore",
                "feishu-sync",
                "--goal-id",
                goal_id,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert error.returncode == 1, error.stdout
        error_payload = json.loads(error.stdout)
        assert error_payload["ok"] is False, error_payload
        assert "feishu-setup" in str(error_payload["error"]), error_payload


def main() -> int:
    check_result_log_contract()
    check_lark_sync_contract()
    check_visual_marker_readback_retry_contract()
    check_lark_setup_and_card()
    check_todo_branch_prediction_contract()
    check_worker_lane_router_contract()
    check_harness_domain_purity()
    check_cli_surface()
    print("explore result layer smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
