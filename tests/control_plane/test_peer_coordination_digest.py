"""End-to-end File & SQLite regression tests for M1 Turn-envelope peer_coordination_digest."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from canonical_authority_fixture import (
    advance_canonical_fixture_authority,
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.peer_coordination_digest_source import (
    commit_displayed_peer_coordination_cursor,
    peer_coordination_cursor_path,
    read_peer_coordination_cursor,
    read_peer_coordination_cursor_state,
)
from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
from loopx.control_plane.todos.todo_summary import canonical_todo_read_record, structured_todo_item


GOAL_ID = "goal-m1-digest"


def _canonical_record(**fields: object) -> dict[str, object]:
    raw = {
        "role": "agent",
        "source_section": "Agent Todo",
        "archive_state": "active",
        "done": False,
        **fields,
    }
    return canonical_todo_read_record(
        structured_todo_item(
            raw,
            role="agent",
            source_section="Agent Todo",
            archive_state="active",
            text_limit=None,
        )
    )


def _write_m1_fixture(
    root: Path,
    *,
    agents: tuple[str, ...],
    todos: list[dict[str, object]],
    provider: str,
    coordination_extra: dict[str, Any] | None = None,
) -> dict[str, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-11-27T05:00:00+00:00\n---\n\n# M1 Fixture\n\n## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    coordination: dict[str, Any] = {
        "agent_model": "peer_v1",
        "registered_agents": list(agents),
        "agent_profiles": {
            agent_id: {
                "schema_version": "agent_profile_v1",
                "profile_role": "fixture",
                "scope": "public test",
            }
            for agent_id in agents
        },
        "write_scope": ["loopx/**"],
    }
    if coordination_extra:
        coordination.update(coordination_extra)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "control_plane",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": coordination,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos)
    initialize_canonical_authority(
        runtime, GOAL_ID, projection, state_path=state_file, provider=provider
    )
    return {
        "project": project,
        "runtime": runtime,
        "registry": registry_path,
        "state_file": state_file,
    }


def _run_should_run(
    *,
    registry: Path,
    runtime: Path,
    agent_id: str,
    extra_args: tuple[str, ...] = (),
) -> tuple[int, dict[str, Any]]:
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        agent_id,
        "--context-delivery",
        "off",
        *extra_args,
    ]
    buf = io.StringIO()
    saved_thread = os.environ.pop("CODEX_THREAD_ID", None)
    try:
        with contextlib.redirect_stdout(buf):
            code = cli_main(argv)
    finally:
        if saved_thread is not None:
            os.environ["CODEX_THREAD_ID"] = saved_thread
    return code, json.loads(buf.getvalue())


def _sample_multi_agent_todos(*, include_second_verify: bool = False) -> list[dict[str, object]]:
    todos = [
        _canonical_record(
            todo_id="todo_a_work",
            text="[P0] Verify PR slice on turn envelope",
            status="open",
            task_class="advancement_task",
            action_kind="verify",
            task_domain="control_plane",
            task_repository="https://github.com/loopx-project/loopx.git",
            target_key="turn-envelope",
            required_write_scopes=["loopx/control_plane/quota/"],
            claimed_by="agent-a",
            priority="P0",
            index=1,
        ),
        _canonical_record(
            todo_id="todo_b_collide",
            text="[P0] Refactor turn envelope allocation",
            status="open",
            task_class="advancement_task",
            action_kind="refactor",
            task_domain="control_plane",
            task_repository="git:github.com/loopx-project/loopx",
            target_key="turn-envelope",
            required_write_scopes=["loopx/control_plane/quota/turn_envelope.ts"],
            completion_validation_required=True,
            claimed_by="agent-b",
            priority="P0",
            index=2,
        ),
        _canonical_record(
            todo_id="todo_b_unleased",
            text="[P0] Fix unleased scheduler corner",
            status="open",
            task_class="advancement_task",
            action_kind="refactor",
            task_domain="control_plane",
            task_repository="git:github.com/loopx-project/loopx",
            target_key="scheduler",
            required_write_scopes=["loopx/control_plane/scheduler/"],
            claimed_by="agent-b",
            priority="P0",
            index=3,
        ),
        _canonical_record(
            todo_id="todo_b_synergy",
            text="[P2] Polish docs table",
            status="open",
            task_class="advancement_task",
            action_kind="docs",
            task_domain="control_plane",
            task_repository="git:github.com/loopx-project/loopx",
            target_key="docs",
            required_write_scopes=["docs/architecture/"],
            claimed_by="agent-b",
            priority="P2",
            index=4,
        ),
    ]
    if include_second_verify:
        todos.append(
            _canonical_record(
                todo_id="todo_b_verify_new",
                text="[P1] Newly queued peer verification request",
                status="open",
                task_class="advancement_task",
                action_kind="refactor",
                task_domain="other_domain",
                task_repository="git:github.com/loopx-project/loopx",
                target_key="fresh-target",
                completion_validation_required=True,
                claimed_by="agent-b",
                priority="P1",
                index=5,
            )
        )
    return todos


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_solo_goal_omits_digest_and_skips_cursor_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a",),
        todos=[_sample_multi_agent_todos()[0]],
        provider=provider,
    )
    code, payload = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code == 0 and payload["ok"] is True
    assert "peer_coordination_digest" not in payload
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )
    assert not cursor.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_automatic_turn_digest_cursor_lifecycle_and_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    # Preview (--dry-run) shows the digest without creating or advancing cursor.
    code, dry_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--dry-run",),
    )
    assert code == 0 and dry_payload["ok"] is True
    assert_complete_turn_decision(dry_payload)
    assert not cursor.exists()
    digest = dry_payload["peer_coordination_digest"]
    assert digest["schema"] == "peer_digest_v0"
    assert digest["read_only"] is True
    assert digest["grants_authority"] is False
    kinds = {item["kind"] for item in digest["items"]}
    assert kinds <= {"collision", "verify"}
    assert "unleased" not in kinds and "synergy" not in kinds
    wire_bytes = len(',"peer_coordination_digest":'.encode("utf-8")) + len(
        json.dumps(digest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    assert wire_bytes <= 300

    # Live Turn #1 emits collision digest (with 1 omitted verify opp) and records only Turn 1's ID.
    code1, live1 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code1 == 0 and live1["ok"] is True
    assert_complete_turn_decision(live1)
    assert live1["peer_coordination_digest"] == digest
    assert digest.get("omitted") == 1
    assert cursor.is_file()
    cursor_after_t1 = json.loads(cursor.read_text(encoding="utf-8"))
    assert len(cursor_after_t1["seen_opportunity_ids"]) == 1

    # Live Turn #2 surfaces the previously omitted verify opportunity without losing it.
    code2, live2 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code2 == 0 and live2["ok"] is True
    digest2 = live2["peer_coordination_digest"]
    assert [item["kind"] for item in digest2["items"]] == ["verify"]
    assert "omitted" not in digest2
    cursor_after_t2 = json.loads(cursor.read_text(encoding="utf-8"))
    assert len(cursor_after_t2["seen_opportunity_ids"]) == 2

    # Live Turn #3 (unchanged canonical frontier, all ops seen) omits field completely (0 B).
    code2_quiet, live2_quiet = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code2_quiet == 0 and live2_quiet["ok"] is True
    assert "peer_coordination_digest" not in live2_quiet

    # Newly added peer verification opportunity immediately surfaces ONLY the new item.
    updated_projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=_sample_multi_agent_todos(include_second_verify=True)
    )
    advance_canonical_fixture_authority(
        paths["runtime"], GOAL_ID, updated_projection, operation_id="op_add_second_verify"
    )
    code3, live3 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code3 == 0 and live3["ok"] is True
    digest3 = live3["peer_coordination_digest"]
    assert [item["todo"] for item in digest3["items"]] == ["todo_b_verify_new"]

    # When peer opportunities clear, live Turn omits digest and prunes cursor cleanly.
    cleared_projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=[_sample_multi_agent_todos()[0]]
    )
    advance_canonical_fixture_authority(
        paths["runtime"], GOAL_ID, cleared_projection, operation_id="op_clear_peer_opps"
    )
    code4, live4 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code4 == 0 and live4["ok"] is True
    assert "peer_coordination_digest" not in live4
    cursor_data = json.loads(cursor.read_text(encoding="utf-8"))
    assert cursor_data["seen_opportunity_ids"] == []


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_idle_specialist_discovers_peer_opportunities_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b", "agent-idle"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    code, payload = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-idle"
    )
    assert code == 0 and payload["ok"] is True
    digest = payload["peer_coordination_digest"]
    kinds = {item["kind"] for item in digest["items"]}
    assert kinds <= {"verify", "unleased"}
    assert len(kinds) >= 1


@pytest.mark.parametrize("mode", ["goal_config", "env_flag"])
def test_explicit_off_escape_hatch_suppresses_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    extra = {"peer_coordination_digest": "off"} if mode == "goal_config" else None
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider="file",
        coordination_extra=extra,
    )
    if mode == "env_flag":
        monkeypatch.setenv("LOOPX_PEER_COORDINATION_DIGEST", "off")
    code, payload = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code == 0 and payload["ok"] is True
    assert "peer_coordination_digest" not in payload


def test_commit_displayed_cursor_records_only_displayed_and_active_seen(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    active = ["opp:1", "opp:2", "opp:3"]
    assert (
        commit_displayed_peer_coordination_cursor(
            runtime,
            goal_id=GOAL_ID,
            agent_id="agent-a",
            active_opportunity_ids=active,
            displayed_opportunity_ids=["opp:1"],
        )
        is True
    )
    assert read_peer_coordination_cursor(runtime, goal_id=GOAL_ID, agent_id="agent-a") == [
        "opp:1"
    ]

    # When opp:2 becomes inactive while opp:1 was previously seen and opp:3 is displayed next,
    # opp:2 is pruned and opp:1 + opp:3 remain recorded.
    commit_displayed_peer_coordination_cursor(
        runtime,
        goal_id=GOAL_ID,
        agent_id="agent-a",
        active_opportunity_ids=["opp:1", "opp:3", "opp:4"],
        displayed_opportunity_ids=["opp:3"],
    )
    assert read_peer_coordination_cursor(runtime, goal_id=GOAL_ID, agent_id="agent-a") == [
        "opp:1",
        "opp:3",
    ]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_dry_run_non_mutating_and_omitted_opportunities_surface_across_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    todos: list[dict[str, object]] = [
        _canonical_record(
            todo_id="todo_a_verifier",
            text="[P0] Verify peer slices cleanly",
            status="open",
            task_class="advancement_task",
            action_kind="verify",
            task_domain="control_plane",
            task_repository="https://github.com/loopx-project/loopx.git",
            target_key="peer-target",
            required_write_scopes=["loopx/control_plane/goals/"],
            claimed_by="agent-a",
            priority="P0",
            index=1,
        )
    ]
    long_todo_ids = [
        f"todo_overflow_{idx}_" + ("x" * 12)
        for idx in range(3)
    ]
    for offset, todo_id in enumerate(long_todo_ids, start=2):
        todos.append(
            _canonical_record(
                todo_id=todo_id,
                text=f"[P0] Peer slice {offset} needing review",
                status="open",
                task_class="advancement_task",
                action_kind="refactor",
                task_domain="control_plane",
                task_repository="git:github.com/loopx-project/loopx",
                target_key=f"module-{offset}",
                required_write_scopes=[f"loopx/control_plane/mod_{offset}/"],
                completion_validation_required=True,
                claimed_by="agent-b",
                priority="P0",
                index=offset,
            )
        )

    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=todos,
        provider=provider,
    )
    cursor_path = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    # Preview (--dry-run) may display the digest without creating or advancing the cursor.
    dry_code, dry_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--dry-run",),
    )
    assert dry_code == 0 and dry_payload["ok"] is True
    assert "peer_coordination_digest" in dry_payload
    assert not cursor_path.exists()

    seen_todo_ids: list[str] = []
    omitted_seen_on_first_turn = False
    for turn in range(6):
        code, payload = _run_should_run(
            registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
        )
        assert code == 0 and payload["ok"] is True
        digest = payload.get("peer_coordination_digest")
        if digest is None:
            break
        serialized_bytes = len(
            json.dumps(digest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        assert serialized_bytes <= 300
        if turn == 0:
            omitted_seen_on_first_turn = int(digest.get("omitted", 0)) >= 1
        turn_todos = [str(item["todo"]) for item in digest["items"]]
        assert set(turn_todos).isdisjoint(set(seen_todo_ids))
        seen_todo_ids.extend(turn_todos)
        recorded = read_peer_coordination_cursor(
            paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
        )
        assert len(recorded) == len(seen_todo_ids)

    assert omitted_seen_on_first_turn
    assert set(long_todo_ids).issubset(set(seen_todo_ids))

    quiet_code, quiet_payload = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert quiet_code == 0 and quiet_payload["ok"] is True
    assert "peer_coordination_digest" not in quiet_payload


def _run_quota_cli(
    *,
    registry: Path,
    runtime: Path,
    subcommand: str,
    extra_args: tuple[str, ...] = (),
) -> tuple[int, dict[str, Any]]:
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "quota",
        subcommand,
        *extra_args,
    ]
    buf = io.StringIO()
    saved_thread = os.environ.pop("CODEX_THREAD_ID", None)
    try:
        with contextlib.redirect_stdout(buf):
            code = cli_main(argv)
    finally:
        if saved_thread is not None:
            os.environ["CODEX_THREAD_ID"] = saved_thread
    return code, json.loads(buf.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_capture_failure_preserves_cursor_and_same_turn_or_next_turn_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    from loopx.control_plane.turn_driver import decision_capture

    with monkeypatch.context() as patch:
        def _fail_capture(*_args: Any, **_kwargs: Any) -> str:
            raise OSError("disk full")

        patch.setattr(decision_capture, "write_decision_capture", _fail_capture)
        fail_code, fail_payload = _run_should_run(
            registry=paths["registry"],
            runtime=paths["runtime"],
            agent_id="agent-a",
            extra_args=(
                "--turn-instance-id",
                "turn-cap-fail",
                "--decision-output-dir",
                str(tmp_path / "cap-fail"),
            ),
        )
    assert fail_code == 1 and fail_payload["ok"] is False
    assert fail_payload["error_code"] == "quota_decision_transport_failed"
    assert not cursor.exists()

    # Same Turn retries once capture path succeeds -> delivers collision and commits ONLY collision.
    retry_code, retry_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=(
            "--turn-instance-id",
            "turn-cap-fail",
            "--decision-output-dir",
            str(tmp_path / "cap-retry"),
        ),
    )
    assert retry_code == 0 and retry_payload["ok"] is True
    retry_digest = retry_payload["peer_coordination_digest"]
    assert [item["kind"] for item in retry_digest["items"]] == ["collision"]
    assert retry_digest.get("omitted") == 1
    seen_after_retry, turns_after_retry = read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )
    assert seen_after_retry == [retry_digest["items"][0]["id"]]
    assert turns_after_retry == ["turn-cap-fail"]

    # Replaying the exact same Turn ID again stays idempotent (0 B, does not consume verify).
    replay_code, replay_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=(
            "--turn-instance-id",
            "turn-cap-fail",
            "--decision-output-dir",
            str(tmp_path / "cap-replay"),
        ),
    )
    assert replay_code == 0 and replay_payload["ok"] is True
    assert "peer_coordination_digest" not in replay_payload
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == (seen_after_retry, turns_after_retry)

    # Next normal Turn surfaces the previously omitted verify opportunity.
    next_code, next_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--turn-instance-id", "turn-next"),
    )
    assert next_code == 0 and next_payload["ok"] is True
    next_digest = next_payload["peer_coordination_digest"]
    assert [item["kind"] for item in next_digest["items"]] == ["verify"]
    seen_after_next, turns_after_next = read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )
    assert len(seen_after_next) == 2
    assert turns_after_next == ["turn-cap-fail", "turn-next"]

    quiet_code2, quiet_payload2 = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--turn-instance-id", "turn-quiet"),
    )
    assert quiet_code2 == 0 and quiet_payload2["ok"] is True
    assert "peer_coordination_digest" not in quiet_payload2


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_delivery_failure_after_capture_preserves_cursor_and_context_read_recovers_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    from loopx.cli_commands import quota_result_delivery
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError

    with monkeypatch.context() as patch:
        def _fail_handoff(*_args: Any, **_kwargs: Any) -> Any:
            raise QuotaDecisionTransportError("simulated handoff transport failure")

        patch.setattr(quota_result_delivery, "handoff_decision", _fail_handoff)
        fail_code, fail_payload = _run_should_run(
            registry=paths["registry"],
            runtime=paths["runtime"],
            agent_id="agent-a",
            extra_args=(
                "--turn-instance-id",
                "turn-handoff-fail",
                "--decision-output-dir",
                str(tmp_path / "cap-handoff"),
            ),
        )
    assert fail_code == 1 and fail_payload["ok"] is False
    assert fail_payload["error_code"] == "quota_decision_transport_failed"
    assert not cursor.exists()
    captured_ref = fail_payload["captured_decision"]

    # Reading an unrelated pointer does NOT advance the peer digest cursor.
    hb_code, hb_payload = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--select",
            "/heartbeat_receipt",
        ),
    )
    assert hb_code == 0 and hb_payload["ok"] is True and hb_payload["eof"] is True
    assert not cursor.exists()

    # Full same-decision recovery read delivers collision digest and commits ONLY collision + original Turn.
    read1_code, read1_payload = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--page-bytes",
            "65536",
            "--max-page-output-bytes",
            "65536",
        ),
    )
    assert read1_code == 0 and read1_payload["ok"] is True and read1_payload["eof"] is True
    recovered_doc = json.loads(read1_payload["content"])
    collision_id = recovered_doc["peer_coordination_digest"]["items"][0]["id"]
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([collision_id], ["turn-handoff-fail"])

    # Repeated readback of the same saved decision is idempotent (same bytes, no extra advancement).
    read2_code, read2_payload = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--page-bytes",
            "65536",
            "--max-page-output-bytes",
            "65536",
        ),
    )
    assert read2_code == 0 and read2_payload["ok"] is True
    assert read2_payload["content"] == read1_payload["content"]
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([collision_id], ["turn-handoff-fail"])

    # Subsequent normal Turn surfaces omitted verify item.
    next_code, next_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--turn-instance-id", "turn-after-recovery"),
    )
    assert next_code == 0 and next_payload["ok"] is True
    assert [item["kind"] for item in next_payload["peer_coordination_digest"]["items"]] == ["verify"]
    seen_after_next, turns_after_next = read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )
    assert len(seen_after_next) == 2
    assert turns_after_next == ["turn-handoff-fail", "turn-after-recovery"]

    # Late repeated readback on the old capture stays idempotent and never regresses cursor.
    late_code, late_payload = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--select",
            "/peer_coordination_digest",
        ),
    )
    assert late_code == 0 and late_payload["ok"] is True
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == (seen_after_next, turns_after_next)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_prepared_capture_reference_defers_cursor_until_selected_read_and_stdout_failure_preserves_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    cap_dir = tmp_path / "prepared-cap"
    prep_code, prep_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=(
            "--turn-instance-id",
            "turn-prep",
            "--decision-output-dir",
            str(cap_dir),
            "--prepared-capture-reference",
        ),
    )
    assert prep_code == 0 and prep_payload["ok"] is True
    assert "peer_coordination_digest" not in prep_payload
    assert not cursor.exists()

    sel_code, sel_payload = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            str(cap_dir / "decision.json"),
            "--capture-sha256",
            prep_payload["capture_sha256"],
            "--select",
            "/peer_coordination_digest",
        ),
    )
    assert sel_code == 0 and sel_payload["ok"] is True and sel_payload["eof"] is True
    selections = json.loads(sel_payload["content"])["selections"]
    collision_id = selections[0]["value"]["items"][0]["id"]
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([collision_id], ["turn-prep"])

    # Stdout emission error never advances cursor either.
    from loopx import cli_runtime

    with monkeypatch.context() as patch:
        def _fail_print(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("broken pipe")

        patch.setattr(cli_runtime, "print_payload", _fail_print)
        with pytest.raises(OSError, match="broken pipe"):
            cli_main(
                [
                    "--registry",
                    str(paths["registry"]),
                    "--runtime-root",
                    str(paths["runtime"]),
                    "--format",
                    "json",
                    "quota",
                    "should-run",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    "agent-a",
                    "--turn-instance-id",
                    "turn-stdout-fail",
                    "--context-delivery",
                    "off",
                ]
            )
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([collision_id], ["turn-prep"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_buffered_stdout_flush_failure_preserves_cursor_and_next_turn_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    import sys

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    class _BufferedBrokenPipeStdout:
        def __init__(self) -> None:
            self.buffered: list[str] = []
            self.delivered = bytearray()

        def write(self, data: str) -> int:
            self.buffered.append(data)
            return len(data)

        def flush(self) -> None:
            raise BrokenPipeError("broken pipe on stdout flush")

    broken_stdout = _BufferedBrokenPipeStdout()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", broken_stdout)
        with pytest.raises(BrokenPipeError, match="broken pipe on stdout flush"):
            cli_main(
                [
                    "--registry",
                    str(paths["registry"]),
                    "--runtime-root",
                    str(paths["runtime"]),
                    "--format",
                    "json",
                    "quota",
                    "should-run",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    "agent-a",
                    "--turn-instance-id",
                    "turn-buf-fail",
                    "--context-delivery",
                    "off",
                ]
            )
    assert len(broken_stdout.delivered) == 0
    assert not cursor.exists()

    # Subsequent live Turn must still surface the unseen collision opportunity.
    next_code, next_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--turn-instance-id", "turn-buf-next"),
    )
    assert next_code == 0 and next_payload["ok"] is True
    next_digest = next_payload["peer_coordination_digest"]
    assert [item["kind"] for item in next_digest["items"]] == ["collision"]
    assert next_digest.get("omitted") == 1
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([next_digest["items"][0]["id"]], ["turn-buf-next"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_tail_only_raw_and_selected_context_read_preserve_cursor_until_full_digest_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_m1_fixture(
        tmp_path,
        agents=("agent-a", "agent-b"),
        todos=_sample_multi_agent_todos(),
        provider=provider,
    )
    cursor = peer_coordination_cursor_path(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    )

    from loopx.cli_commands import quota_result_delivery
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError

    with monkeypatch.context() as patch:
        def _fail_handoff(*_args: Any, **_kwargs: Any) -> Any:
            raise QuotaDecisionTransportError("simulated handoff transport failure")

        patch.setattr(quota_result_delivery, "handoff_decision", _fail_handoff)
        fail_code, fail_payload = _run_should_run(
            registry=paths["registry"],
            runtime=paths["runtime"],
            agent_id="agent-a",
            extra_args=(
                "--turn-instance-id",
                "turn-tail-fail",
                "--decision-output-dir",
                str(tmp_path / "cap-tail"),
            ),
        )
    assert fail_code == 1 and fail_payload["error_code"] == "quota_decision_transport_failed"
    assert not cursor.exists()
    captured_ref = fail_payload["captured_decision"]
    raw_len = len(Path(captured_ref["path"]).read_bytes())

    # 1. Bare capture tail-only page (1 byte, eof=True) must NOT advance cursor.
    raw_tail_code, raw_tail = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--offset",
            str(raw_len - 1),
        ),
    )
    assert raw_tail_code == 0 and raw_tail["ok"] is True and raw_tail["eof"] is True
    assert len(raw_tail["content"].encode("utf-8")) == 1
    assert not cursor.exists()

    # 2. Explicit selection head chunk truncated before digest end (eof=False) must NOT advance cursor.
    sel_head_code, sel_head = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--select",
            "/peer_coordination_digest",
            "--page-bytes",
            "256",
            "--offset",
            "0",
        ),
    )
    assert sel_head_code == 0 and sel_head["ok"] is True and sel_head["eof"] is False
    assert not cursor.exists()

    # 3. Jumping directly to the tail page of the explicit selection (1 byte, eof=True) must NOT advance cursor.
    sel_total = int(sel_head["total_bytes"])
    sel_tail_code, sel_tail = _run_quota_cli(
        registry=paths["registry"],
        runtime=paths["runtime"],
        subcommand="context-read",
        extra_args=(
            "--capture-file",
            captured_ref["path"],
            "--capture-sha256",
            captured_ref["sha256"],
            "--select",
            "/peer_coordination_digest",
            "--selection-sha256",
            str(sel_head["selection_sha256"]),
            "--offset",
            str(sel_total - 1),
        ),
    )
    assert sel_tail_code == 0 and sel_tail["ok"] is True and sel_tail["eof"] is True
    assert len(sel_tail["content"].encode("utf-8")) == 1
    assert not cursor.exists()

    # 4. Subsequent normal live Turn must STILL see the collision item.
    next_code, next_payload = _run_should_run(
        registry=paths["registry"],
        runtime=paths["runtime"],
        agent_id="agent-a",
        extra_args=("--turn-instance-id", "turn-after-tail"),
    )
    assert next_code == 0 and next_payload["ok"] is True
    next_digest = next_payload["peer_coordination_digest"]
    assert [item["kind"] for item in next_digest["items"]] == ["collision"]
    assert next_digest.get("omitted") == 1
    assert read_peer_coordination_cursor_state(
        paths["runtime"], goal_id=GOAL_ID, agent_id="agent-a"
    ) == ([next_digest["items"][0]["id"]], ["turn-after-tail"])
