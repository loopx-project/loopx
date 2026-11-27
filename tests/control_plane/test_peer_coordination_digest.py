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
from loopx.control_plane.goals.peer_coordination_digest import peer_coordination_cursor_path
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

    # Live Turn #1 emits digest and commits cursor.
    code1, live1 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code1 == 0 and live1["ok"] is True
    assert_complete_turn_decision(live1)
    assert live1["peer_coordination_digest"] == digest
    assert cursor.is_file()

    # Live Turn #2 (unchanged canonical frontier) omits field completely (0 B).
    code2, live2 = _run_should_run(
        registry=paths["registry"], runtime=paths["runtime"], agent_id="agent-a"
    )
    assert code2 == 0 and live2["ok"] is True
    assert "peer_coordination_digest" not in live2

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
