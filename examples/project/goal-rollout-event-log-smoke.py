#!/usr/bin/env python3
"""Validate public-safe LoopX rollout event logging."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.rollout_event_log import (  # noqa: E402
    build_rollout_event,
    append_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
    summarize_rollout_events,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402


def assert_public_safe_text(text: str) -> None:
    forbidden = (
        "/" + "Users/",
        "/" + "root/",
        "/" + "private/",
        "trajectory" + ".json",
        "Auth" + "orization:",
        "loopx-" + "ecs",
        "115." + "190.",
    )
    for marker in forbidden:
        assert marker not in text, marker


def assert_boundary(payload: dict) -> None:
    boundary = payload["boundary"]
    assert boundary["raw_task_text_recorded"] is False, payload
    assert boundary["raw_logs_recorded"] is False, payload
    assert boundary["raw_trajectory_recorded"] is False, payload
    assert boundary["raw_session_transcript_recorded"] is False, payload
    assert boundary["credential_values_recorded"] is False, payload
    assert boundary["absolute_paths_recorded"] is False, payload


def run_script(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "goal_rollout_event_log.py"), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    assert_public_safe_text(result.stdout)
    return json.loads(result.stdout)


def run_loopx_cli(*args: str, allow_failure: bool = False) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 and not allow_failure:
        raise AssertionError(
            f"loopx.cli failed rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    payload = json.loads(result.stdout)
    assert_public_safe_text(
        json.dumps(payload.get("rollout_event") or {}, ensure_ascii=True, sort_keys=True)
    )
    return payload


def write_smoke_project(tmp_root: Path) -> tuple[Path, Path, Path, str]:
    runtime_root = tmp_root / "runtime"
    project = tmp_root / "project"
    project.mkdir(parents=True)
    goal_id = "rollout-log-smoke"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        """---
status: smoke
updated_at: 2026-06-21T00:00:00+08:00
---

# Rollout Log Smoke

## Next Action

- [P0] Exercise automatic rollout logging.

## Agent Todo

- [ ] [P0] Claim the smoke todo.
  <!-- loopx:todo todo_id=todo_auto_rollout status=open task_class=advancement_task action_kind=smoke -->
""",
        encoding="utf-8",
    )
    registry_path = tmp_root / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "smoke",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["codex-main-control"],
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime_root, project, goal_id


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-rollout-event-log-") as tmp:
        tmp_root = Path(tmp)
        runtime_root = tmp_root / "runtime"
        goal_id = "loopx-meta"
        log_path = rollout_event_log_path(runtime_root, goal_id)
        identity_log_path = tmp_root / "rollout-event-identity.jsonl"
        first_occurrence = build_rollout_event(
            goal_id=goal_id,
            event_kind="validation",
            status="passed",
            summary="Focused validation passed.",
            recorded_at="2026-07-12T00:00:00Z",
        )
        append_rollout_event(identity_log_path, first_occurrence)
        replayed = append_rollout_event(identity_log_path, dict(first_occurrence))
        assert replayed == first_occurrence, replayed
        assert len(load_rollout_events(identity_log_path)) == 1
        later_occurrence = build_rollout_event(
            goal_id=goal_id,
            event_kind="validation",
            status="passed",
            summary="Focused validation passed.",
            recorded_at="2026-07-12T00:01:00Z",
        )
        assert later_occurrence["event_id"] != first_occurrence["event_id"]
        append_rollout_event(identity_log_path, later_occurrence)
        assert len(load_rollout_events(identity_log_path)) == 2
        conflicting = dict(first_occurrence)
        conflicting["summary"] = "Conflicting content must not reuse an event id."
        try:
            append_rollout_event(identity_log_path, conflicting)
        except ValueError as exc:
            assert "conflicting rollout event_id" in str(exc), exc
        else:
            raise AssertionError("conflicting rollout event_id was appended")
        assert len(load_rollout_events(identity_log_path)) == 2

        event = build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            agent_id="codex-main-control",
            todo_id="todo_406bb256efd8",
            lane_id="main-control",
            agent_role="primary",
            gate_id="gate_owner_scope",
            decision_id="todo_7aadc0a2387a",
            from_state="waiting",
            to_state="eligible",
            caused_by="quota_should_run",
            source_event_id="event_previous_public",
            blocks=["todo_blocked_by_gate"],
            unblocks=["todo_406bb256efd8"],
            handoff_to="codex-product-capability",
            commit_ref="abcdef1",
            pr_ref="huangruiteng/loopx#551",
            revert_of="event_superseded_public",
            status="eligible",
            summary="Quota allowed one bounded rollout event-log slice.",
            artifact_refs=["benchmark/deepswe/README.md"],
            details={"open_agent_todo_count": 2},
        )
        append_rollout_event(log_path, event)
        assert event["lane"] == {
            "lane_id": "main-control",
            "agent_role": "primary",
        }, event
        assert event["state_transition"] == {
            "from_state": "waiting",
            "to_state": "eligible",
        }, event
        assert event["causality"] == {
            "caused_by": "quota_should_run",
            "source_event_id": "event_previous_public",
            "gate_id": "gate_owner_scope",
            "decision_id": "todo_7aadc0a2387a",
            "blocks": ["todo_blocked_by_gate"],
            "unblocks": ["todo_406bb256efd8"],
        }, event
        assert event["handoff"] == {
            "to_agent_id": "codex-product-capability",
        }, event
        assert event["code_refs"] == {
            "commit_ref": "abcdef1",
            "pr_ref": "huangruiteng/loopx#551",
            "revert_of": "event_superseded_public",
        }, event

        result_event = run_script(
            "append",
            "--goal-id",
            goal_id,
            "--runtime-root",
            str(runtime_root),
            "--event-kind",
            "compact_case_result",
            "--agent-id",
            "codex-main-control",
            "--todo-id",
            "todo_406bb256efd8",
            "--case-id",
            "build-cython-ext",
            "--status",
            "precise_blocker",
            "--lane-id",
            "product-capability",
            "--agent-role",
            "side-agent",
            "--from-state",
            "open",
            "--to-state",
            "blocked",
            "--caused-by",
            "validation",
            "--gate-id",
            "gate_public_safe_boundary",
            "--commit-ref",
            "bcdef12",
            "--pr-ref",
            "huangruiteng/loopx#552",
            "--summary",
            "Compact case result reduced to public-safe failure attribution.",
            "--artifact-ref",
            "benchmark/deepswe/README.md",
        )
        assert result_event["event_kind"] == "compact_case_result", result_event
        assert result_event["lane"]["lane_id"] == "product-capability", result_event
        assert result_event["state_transition"] == {
            "from_state": "open",
            "to_state": "blocked",
        }, result_event
        assert result_event["causality"]["caused_by"] == "validation", result_event
        assert result_event["causality"]["gate_id"] == (
            "gate_public_safe_boundary"
        ), result_event
        assert result_event["code_refs"] == {
            "commit_ref": "bcdef12",
            "pr_ref": "huangruiteng/loopx#552",
        }, result_event
        assert_boundary(result_event)

        session_root = Path(tmp) / "sessions"
        session_root.mkdir()
        (session_root / "rollout.jsonl").write_text(
            '{"raw":"this transcript body must not be read"}\n',
            encoding="utf-8",
        )
        session_event = run_script(
            "observe-codex-sessions",
            "--goal-id",
            goal_id,
            "--runtime-root",
            str(runtime_root),
            "--session-root",
            str(session_root),
            "--agent-id",
            "codex-main-control",
        )
        assert session_event["event_kind"] == "codex_session_observed", session_event
        assert session_event["private_source"] == {
            "kind": "codex_sessions_jsonl",
            "raw_values_recorded": False,
            "count": 1,
        }, session_event
        assert_boundary(session_event)

        events = load_rollout_events(log_path)
        summary = summarize_rollout_events(events, limit=5)
        assert summary["event_count"] == 3, summary
        assert summary["counts_by_kind"]["quota_should_run"] == 1, summary
        assert summary["counts_by_kind"]["compact_case_result"] == 1, summary
        assert summary["counts_by_kind"]["codex_session_observed"] == 1, summary
        latest_view = summary["recent_events"][0]
        assert latest_view["lane"]["lane_id"] == "main-control", latest_view
        assert latest_view["state_transition"]["to_state"] == "eligible", latest_view
        assert latest_view["causality"]["gate_id"] == "gate_owner_scope", latest_view
        assert latest_view["code_refs"]["pr_ref"] == "huangruiteng/loopx#551", latest_view
        assert latest_view["handoff"]["to_agent_id"] == (
            "codex-product-capability"
        ), latest_view
        assert_boundary(summary)
        rendered_summary = run_script(
            "summarize",
            "--goal-id",
            goal_id,
            "--runtime-root",
            str(runtime_root),
            "--limit",
            "3",
            "--pretty",
        )
        assert rendered_summary["event_count"] == 3, rendered_summary

        try:
            build_rollout_event(
                goal_id=goal_id,
                event_kind="validation",
                artifact_refs=["/" + "Users/bytedance/private-result.json"],
            )
        except ValueError:
            pass
        else:
            raise AssertionError("absolute artifact refs must be rejected")

        try:
            build_rollout_event(
                goal_id=goal_id,
                event_kind="validation",
                details={"raw_" + "trajectory_path": "trajectory" + ".json"},
            )
        except ValueError:
            pass
        else:
            raise AssertionError("raw/private detail keys must be rejected")

        assert_public_safe_text(log_path.read_text(encoding="utf-8"))

        for provider in ("file", "sqlite"):
            assert_cli_events(tmp_root / "auto" / provider, provider)


def assert_cli_events(root: Path, provider: str) -> None:
    registry_path, cli_runtime_root, project, cli_goal_id = write_smoke_project(root)
    claim_args = (
        "--registry", str(registry_path), "--runtime-root", str(cli_runtime_root),
        "--format", "json", "todo", "claim", "--goal-id", cli_goal_id,
        "--todo-id", "todo_auto_rollout", "--claimed-by", "codex-main-control",
    )
    state = project / "ACTIVE_GOAL_STATE.md"
    before_state, before_registry = state.read_bytes(), registry_path.read_bytes()
    refused = run_loopx_cli(*claim_args, allow_failure=True)
    assert refused["ok"] is False, refused
    assert refused["error_code"] == "canonical_authority_required", refused
    assert state.read_bytes() == before_state
    assert registry_path.read_bytes() == before_registry
    import_fixture_authority(registry_path, cli_goal_id, provider=provider)
    events_before = load_rollout_events(rollout_event_log_path(cli_runtime_root, cli_goal_id))
    claim_payload = run_loopx_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(cli_runtime_root),
        "--format",
        "json",
        "todo",
        "claim",
        "--goal-id",
        cli_goal_id,
        "--todo-id",
        "todo_auto_rollout",
        "--claimed-by",
        "codex-main-control",
    )
    assert claim_payload["rollout_event"]["event_kind"] == "todo_claim", claim_payload
    readback = run_loopx_cli(
        "--registry", str(registry_path), "--runtime-root", str(cli_runtime_root),
        "--format", "json", "todo", "list", "--goal-id", cli_goal_id,
        "--todo-id", "todo_auto_rollout",
    )
    assert readback["authority_read"]["source_authority"] == f"{provider}_v0", readback
    assert readback["authority_read"]["legacy_fallback_used"] is False, readback
    assert readback["todo"]["claimed_by"] == "codex-main-control", readback

    refresh_payload = run_loopx_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(cli_runtime_root),
        "--format",
        "json",
        "refresh-state",
        "--goal-id",
        cli_goal_id,
        "--classification",
        "automatic_rollout_logging_smoke",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--agent-id",
        "codex-main-control",
        "--agent-lane",
        "smoke",
        "--no-global-sync",
    )
    assert refresh_payload["rollout_event"]["event_kind"] == "refresh_state", refresh_payload

    should_run_payload = run_loopx_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(cli_runtime_root),
        "--format",
        "json",
        "quota",
        "should-run",
        "--goal-id",
        cli_goal_id,
        "--agent-id",
        "codex-main-control",
        "--limit",
        "1",
        allow_failure=True,
    )
    assert should_run_payload["rollout_event"]["event_kind"] == (
        "quota_should_run"
    ), should_run_payload

    auto_events = load_rollout_events(rollout_event_log_path(cli_runtime_root, cli_goal_id))
    assert auto_events[:len(events_before)] == events_before
    auto_kinds = [event["event_kind"] for event in auto_events[len(events_before):]]
    assert auto_kinds == [
        "todo_claim",
        "refresh_state",
        "quota_should_run",
    ], auto_kinds
    latest_event = auto_events[-1]
    assert_boundary(latest_event)
    auto_log_text = rollout_event_log_path(cli_runtime_root, cli_goal_id).read_text(
        encoding="utf-8"
    )
    assert_public_safe_text(auto_log_text)

if __name__ == "__main__":
    main()
