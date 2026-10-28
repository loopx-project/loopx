#!/usr/bin/env python3
"""Smoke-test the public session-runtime read-only projection contract."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.session_runtime import (  # noqa: E402
    SESSION_RUNTIME_READONLY_PROJECTION_SCHEMA_VERSION,
    build_session_runtime_readonly_projection,
)
from loopx.presentation.renderers.status_markdown import render_status_markdown  # noqa: E402
from loopx.status import (  # noqa: E402
    build_attention_queue,
    build_status_runtime_summaries,
)


LOCAL_PATH_FIXTURE = "/" + "private" + "/tmp/raw-run.log"


def assert_no_raw_values(payload: dict[str, object]) -> None:
    text = json.dumps(payload, sort_keys=True)
    forbidden_values = (
        LOCAL_PATH_FIXTURE,
        "full transcript body",
        "credential-value",
        "secret-value",
    )
    leaked = [value for value in forbidden_values if value in text]
    assert not leaked, leaked


def test_operator_gate_first_screen() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="demo-goal",
        sessions=[
            {
                "session_id": "session-1",
                "created_at": "2026-01-01T00:00:00Z",
                "summary": "runtime finished preflight",
            }
        ],
        gates=[
            {
                "gate_id": "gate-1",
                "status": "pending",
                "actor": "operator",
                "question": "Approve read-only evidence import?",
                "blocking": True,
            }
        ],
        decision_results=[
            {
                "artifact_id": "decision-1",
                "recommended_action": "continue only after compact gate approval",
            }
        ],
    )
    assert (
        payload["schema_version"]
        == SESSION_RUNTIME_READONLY_PROJECTION_SCHEMA_VERSION
    ), payload
    assert payload["mode"] == "read_only", payload
    assert payload["boundary"]["runtime_writeback_allowed"] is False, payload
    assert payload["first_screen"]["waiting_on"] == "operator", payload
    assert payload["first_screen"]["user_action_required"] is True, payload
    assert payload["first_screen"]["agent_can_continue"] is False, payload
    assert (
        payload["first_screen"]["first_user_todo"]
        == "Approve read-only evidence import?"
    ), payload
    assert payload["first_screen"]["first_agent_todo"] is None, payload
    assert payload["work_lane_contract"]["lane"] == "user_gate", payload
    assert payload["attention_item"]["priority"] == "P0", payload
    assert_no_raw_values(payload)


def test_agent_advancement_first_screen() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="demo-goal",
        sessions=[
            {
                "session_id": "session-2",
                "created_at": "2026-01-01T00:01:00Z",
                "next_action": "write compact adapter fixture",
            }
        ],
        events=[
            {
                "event_id": "event-1",
                "kind": "validation",
                "status": "passed",
                "event_at": "2026-01-01T00:02:00Z",
                "validation_summary": "contract smoke passed",
            }
        ],
        outcomes=[
            {
                "outcome_id": "outcome-1",
                "kind": "outcome",
                "status": "validated",
                "created_at": "2026-01-01T00:03:00Z",
                "validation_summary": "projection is public-safe",
            }
        ],
        decision_results=[
            {
                "artifact_id": "decision-2",
                "recommended_action": "wire adapter into status projection",
            }
        ],
    )
    assert payload["first_screen"]["waiting_on"] == "agent", payload
    assert payload["first_screen"]["user_action_required"] is False, payload
    assert payload["first_screen"]["agent_can_continue"] is True, payload
    assert (
        payload["first_screen"]["first_agent_todo"]
        == "wire adapter into status projection"
    ), payload
    assert payload["first_screen"]["latest_validation"] == (
        "projection is public-safe"
    ), payload
    assert payload["work_lane_contract"]["lane"] == "advancement_task", payload
    assert payload["work_lane_contract"]["must_attempt_work"] is True, payload
    assert_no_raw_values(payload)


def test_raw_material_is_flagged_not_copied() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="demo-goal",
        sessions=[
            {
                "session_id": "session-3",
                "created_at": "2026-01-01T00:03:00Z",
                "next_action": "continue compact projection",
                "raw_transcript": "full transcript body",
                "credential_hint": "credential-value",
            }
        ],
        events=[
            {
                "event_id": "event-raw",
                "kind": "blocker",
                "status": "blocked",
                "summary": "source summary contained raw fields",
                "local_path": LOCAL_PATH_FIXTURE,
                "secret": "secret-value",
            }
        ],
    )
    assert payload["boundary"]["raw_transcript_copied"] is False, payload
    assert payload["boundary"]["raw_logs_copied"] is False, payload
    assert payload["boundary"]["credentials_copied"] is False, payload
    assert payload["boundary"]["raw_material_detected"] is True, payload
    assert payload["first_screen"]["agent_can_continue"] is False, payload
    assert payload["first_screen"]["recommended_action"] == (
        "provide compact summaries without raw material before projection"
    ), payload
    assert "raw_transcript" in payload["boundary"]["raw_material_key_names"], payload
    assert "credential_hint" in payload["boundary"]["raw_material_key_names"], payload
    assert "local_path" in payload["boundary"]["raw_material_key_names"], payload
    assert payload["boundary"]["raw_material_categories"] == [
        "credential",
        "local_path",
        "transcript",
    ], payload
    assert_no_raw_values(payload)


# Usage metrics and pointers whose words merely contain "token", "log", or
# "trace" are compact input, never raw material.
COMPACT_LOOKALIKE_KEYS = (
    "token_count",
    "tokens_used",
    "max_tokens",
    "input_tokens",
    "output_tokens",
    "trace_id",
    "login_at",
    "catalog_id",
    "dialog_id",
)
# Keys with no matching word at all are reported, not flagged.
UNCLASSIFIED_KEYS = ("logical_clock", "backlog", "changelog", "drawer")
# Raw material the substring rule used to miss: body text, credentials, local
# paths, and raw tool output in whole-word form.
RAW_MATERIAL_KEYS = (
    "messages",
    "content",
    "prompt",
    "api_key",
    "password",
    "body",
    "output_text",
    "file_path",
    "diff",
    "patch",
    "stdout_tail",
    "stderr_tail",
    "log_path",
    "transcript_path",
    "access_token",
    "auth_token",
    # Raw evidence outranks pointer-like suffixes unless the full key is an
    # explicit public-safe collision such as ``trace_id``.
    "secret_id",
    "password_id",
    "transcript_id",
    "raw_id",
    "stdout_id",
    "api_key_id",
    "access_token_ref",
    "tool_result_ref",
)


def test_word_level_classification_does_not_block_compact_keys() -> None:
    session = {
        "session_id": "session-4",
        "created_at": "2026-01-01T00:04:00Z",
        "next_action": "continue compact projection",
        **{key: 1 for key in COMPACT_LOOKALIKE_KEYS},
        **{key: "opaque" for key in UNCLASSIFIED_KEYS},
    }
    payload = build_session_runtime_readonly_projection(goal_id="demo-goal", sessions=[session])
    boundary = payload["boundary"]
    assert boundary["raw_material_detected"] is False, boundary
    assert boundary["raw_material_key_names"] == [], boundary
    assert boundary["unclassified_key_names"] == sorted(UNCLASSIFIED_KEYS), boundary
    assert payload["first_screen"]["agent_can_continue"] is True, payload
    assert payload["work_lane_contract"]["must_attempt_work"] is True, payload
    assert payload["first_screen"]["recommended_action"] == (
        "continue compact projection"
    ), payload


def test_word_level_classification_flags_every_raw_material_key() -> None:
    for key in RAW_MATERIAL_KEYS:
        payload = build_session_runtime_readonly_projection(
            goal_id="demo-goal",
            sessions=[
                {
                    "session_id": "session-5",
                    "next_action": "continue compact projection",
                    key: "raw-value-must-not-copy",
                }
            ],
        )
        boundary = payload["boundary"]
        assert boundary["raw_material_key_names"] == [key], (key, boundary)
        assert boundary["unclassified_key_names"] == [], (key, boundary)
        assert payload["first_screen"]["agent_can_continue"] is False, (key, payload)
        assert "raw-value-must-not-copy" not in json.dumps(payload), (key, payload)


def test_status_ingests_projection_first_screen() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-session-readonly-") as temporary:
        _assert_status_ingests_projection_first_screen(Path(temporary))


def _assert_status_ingests_projection_first_screen(root: Path) -> None:
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    runtime, state, registry = root / "runtime", root / "STATE.md", root / "registry.json"
    state.write_text("# Session Goal\n\n## Objective\nObserve compact session readback.\n\n## Agent Todo\n")
    goal = {"id": "demo-goal", "repo": str(root), "state_file": str(state)}
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [goal]}))
    import_fixture_authority(registry, "demo-goal", provider="sqlite")
    payload = build_session_runtime_readonly_projection(
        goal_id="demo-goal",
        sessions=[
            {
                "session_id": "session-status-1",
                "created_at": "2026-01-01T00:04:00Z",
                "next_action": "continue compact projection",
            }
        ],
        events=[
            {
                "event_id": "event-status-1",
                "kind": "validation",
                "status": "passed",
                "event_at": "2026-01-01T00:05:00Z",
                "validation_summary": "status ingest smoke passed",
            }
        ],
        decision_results=[
            {
                "artifact_id": "decision-status-1",
                "recommended_action": "show session projection in status",
            }
        ],
    )
    run = {
        "generated_at": "2026-01-01T00:06:00Z",
        "goal_id": "demo-goal",
        "classification": "session_runtime_projection_recorded",
        "json_exists": True,
        "markdown_exists": True,
        "session_runtime_readonly_projection": payload,
    }
    history = {
        "goal_count": 1,
        "run_count": 1,
        "goals": [
            {
                "id": "demo-goal",
                "status": "active",
                "registry_member": True,
                "adapter_kind": "session_runtime",
                "adapter_status": "connected-read-only",
                "latest_runs": [run],
            }
        ],
        "runs": [run],
    }
    queue = build_attention_queue(
        runtime_root=runtime, registry_path=registry,
        contract={"ok": True},
        history=history,
        global_registry={"ok": True, "findings": []},
    )
    item = queue["items"][0]
    assert item["source"] == "session_runtime_projection", item
    assert item["waiting_on"] == "codex", item
    assert item["recommended_action"] == "show session projection in status", item
    projection = item["project_asset"]["session_runtime_projection"]
    assert projection["first_screen"]["first_agent_todo"] == (
        "show session projection in status"
    ), projection
    assert projection["boundary"]["runtime_writeback_allowed"] is False, projection
    assert "source_refs" not in projection.get("source", {}), projection
    assert projection["source"]["source_ref_counts"]["sessions"] == 1, projection

    run_history = build_status_runtime_summaries(
        history=history,
        queue=queue,
        runtime_root=runtime,
        goal_id_filter=None,
        display_limit=10,
        todo_index_limit=10,
    )["run_history"]
    compact_run = run_history["goals"][0]["latest_runs"][0]
    assert compact_run["session_runtime_projection"]["mode"] == "read_only", compact_run
    markdown = render_status_markdown(
        {
            "ok": True,
            "registry": "fixture",
            "runtime_root": "fixture",
            "goal_count": 1,
            "run_count": 1,
            "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
            "global_registry": {"available": True, "ok": True, "summary": {"findings": 0, "high": 0, "action": 0, "info": 0}},
            "attention_queue": queue,
            "run_history": run_history,
        }
    )
    assert "session_runtime_projection" in markdown, markdown
    assert "session_runtime_agent_todo" in markdown, markdown
    assert_no_raw_values(item)
    assert_no_raw_values(run_history)


def main() -> int:
    test_operator_gate_first_screen()
    test_agent_advancement_first_screen()
    test_raw_material_is_flagged_not_copied()
    test_word_level_classification_does_not_block_compact_keys()
    test_word_level_classification_flags_every_raw_material_key()
    test_status_ingests_projection_first_screen()
    print("session-runtime-readonly-projection-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
