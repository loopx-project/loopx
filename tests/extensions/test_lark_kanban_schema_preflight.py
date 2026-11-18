from __future__ import annotations

import json
from pathlib import Path

from loopx.extensions.lark.presentation.kanban import (
    LarkKanbanConfig,
    lark_kanban_doctor,
    lark_kanban_schema_payload,
    read_lark_kanban_local_config,
    save_lark_kanban_board_config,
    sync_loopx_projection_to_lark_kanban,
    sync_loopx_todos_to_lark_kanban,
    write_lark_kanban_local_config,
)


def _result(payload: object) -> dict[str, object]:
    return {
        "returncode": 0,
        "stdout": json.dumps(payload),
        "stderr": "",
        "timed_out": False,
    }


def _stale_schema_runner(
    calls: list[list[str]],
):
    def run(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        calls.append(args)
        if args == ["lark-cli", "--version"]:
            return _result({"version": "1.0.56"})
        if args == ["lark-cli", "auth", "status"]:
            return _result(
                {"ok": True, "identities": {"user": {"available": True}}}
            )
        if args[-1:] == ["--help"]:
            return _result({"ok": True})
        if "+field-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "fields": [
                            {"id": "fld_task", "name": "Task", "type": "text"},
                            {
                                "id": "fld_status",
                                "name": "Status",
                                "type": "select",
                            },
                        ]
                    },
                }
            )
        return _result({"ok": True})

    return run


def test_doctor_reports_stale_schema_with_migration_command(tmp_path: Path) -> None:
    config_path = tmp_path / "lark-kanban.json"
    save_lark_kanban_board_config(
        config_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        cli_bin="lark-cli",
        identity="user",
    )
    calls: list[list[str]] = []

    payload = lark_kanban_doctor(
        config_path=config_path,
        runner=_stale_schema_runner(calls),
    )

    assert payload["ok"] is False
    assert payload["schema_check"]["status"] == "stale"
    assert "Work Item Type" in payload["schema_check"]["missing_fields"]
    assert payload["schema_check"]["remediation_command"] == (
        "loopx lark-kanban setup --execute"
    )
    assert any("setup --execute" in issue["message"] for issue in payload["issues"])
    assert sum("+field-list" in args for args in calls) == 1


def test_execute_sync_stops_before_writes_when_schema_is_stale() -> None:
    calls: list[list[str]] = []
    config = LarkKanbanConfig(
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        cli_bin="lark-cli",
        identity="user",
    )

    payload = sync_loopx_projection_to_lark_kanban(
        config,
        projection={
            "schema_version": "goal_channel_projection_v0",
            "goal_id": "goal_public_fixture",
            "source_id": "source-public-fixture",
            "agent_todos": [
                {
                    "todo_id": "todo_public_fixture",
                    "title": "Repair the public fixture",
                    "status": "open",
                    "task_class": "advancement_task",
                }
            ],
        },
        execute=True,
        runner=_stale_schema_runner(calls),
    )

    assert payload["ok"] is False
    assert payload["schema_check"]["status"] == "stale"
    assert "setup --execute" in payload["error"]
    assert payload["records"] == []
    assert len(calls) == 1
    assert "+field-list" in calls[0]
    assert not any("+record-upsert" in args for args in calls)


def test_complete_remote_list_replaces_stale_local_record_id(tmp_path: Path) -> None:
    config_path = tmp_path / "lark-kanban.json"
    config = LarkKanbanConfig(
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        cli_bin="lark-cli",
        identity="user",
    )
    save_lark_kanban_board_config(
        config_path,
        base_token=config.base_token,
        table_id=config.table_id,
        cli_bin=config.cli_bin,
        identity=config.identity,
    )
    local = read_lark_kanban_local_config(config_path)
    record_key = (
        "goal_public_fixture:"
        "projection:source-public-fixture:agent_todo:todo_public_fixture"
    )
    local["todo_records"] = {record_key: "rec_stale_fixture"}
    write_lark_kanban_local_config(config_path, local)
    calls: list[list[str]] = []

    def runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        calls.append(args)
        if "+field-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {"fields": lark_kanban_schema_payload()["fields"]},
                }
            )
        if "+record-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "fields": ["LoopX Goal ID", "LoopX Todo ID"],
                        "data": [["goal_public_fixture", "todo_different_fixture"]],
                        "record_id_list": ["rec_stale_fixture"],
                        "has_more": False,
                    },
                }
            )
        if "+record-upsert" in args:
            assert "--record-id" not in args
            return _result({"ok": True, "data": {"record_id": "rec_fresh_fixture"}})
        raise AssertionError(args)

    payload = sync_loopx_projection_to_lark_kanban(
        config,
        projection={
            "schema_version": "goal_channel_projection_v0",
            "goal_id": "goal_public_fixture",
            "source_id": "source-public-fixture",
            "agent_todos": [
                {
                    "todo_id": "todo_public_fixture",
                    "title": "Repair the public fixture",
                    "status": "open",
                    "task_class": "advancement_task",
                }
            ],
        },
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    assert payload["ok"] is True
    assert payload["records"][0]["record_id"] == "rec_fresh_fixture"
    persisted = read_lark_kanban_local_config(config_path)["todo_records"]
    assert persisted[record_key] == "rec_fresh_fixture"
    assert persisted["goal_public_fixture:todo_different_fixture"] == (
        "rec_stale_fixture"
    )
    assert sum("+record-upsert" in args for args in calls) == 1


def test_partial_todo_sync_persists_successful_record_for_retry(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    config_path = project / ".loopx" / "lark-kanban.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "goal_public_fixture",
                        "repo": str(project),
                        "state_file": str(state_file),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_file.write_text(
        """# Active Goal State

## User Todo / Owner Review Reading Queue

## Agent Todo

- [ ] [P1] First public todo
  <!-- loopx:todo todo_id=todo_public_one status=open task_class=advancement_task -->
- [ ] [P1] Second public todo
  <!-- loopx:todo todo_id=todo_public_two status=open task_class=advancement_task -->
""",
        encoding="utf-8",
    )
    config = LarkKanbanConfig(
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        cli_bin="lark-cli",
        identity="user",
    )
    save_lark_kanban_board_config(
        config_path,
        base_token=config.base_token,
        table_id=config.table_id,
        cli_bin=config.cli_bin,
        identity=config.identity,
    )
    calls: list[list[str]] = []
    upsert_count = 0

    def runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        nonlocal upsert_count
        calls.append(args)
        if "+field-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {"fields": lark_kanban_schema_payload()["fields"]},
                }
            )
        if "+record-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "fields": ["LoopX Goal ID", "LoopX Todo ID"],
                        "data": [],
                        "record_id_list": [],
                        "has_more": True,
                    },
                }
            )
        if "+record-upsert" in args:
            upsert_count += 1
            todo_id = json.loads(args[args.index("--json") + 1])["LoopX Todo ID"]
            if upsert_count == 1:
                assert todo_id == "todo_public_one"
                assert "--record-id" not in args
                return _result(
                    {"ok": True, "data": {"record_id": "rec_public_one"}}
                )
            if upsert_count == 2:
                assert todo_id == "todo_public_two"
                return {
                    "returncode": 1,
                    "stdout": json.dumps(
                        {
                            "ok": False,
                            "error": {"message": "fixture second write failed"},
                        }
                    ),
                    "stderr": "",
                    "timed_out": False,
                }
            if upsert_count == 3:
                assert todo_id == "todo_public_one"
                assert args[args.index("--record-id") + 1] == "rec_public_one"
                return _result(
                    {"ok": True, "data": {"record_id": "rec_public_one"}}
                )
            if upsert_count == 4:
                assert todo_id == "todo_public_two"
                assert "--record-id" not in args
                return _result(
                    {"ok": True, "data": {"record_id": "rec_public_two"}}
                )
        raise AssertionError(args)

    first = sync_loopx_todos_to_lark_kanban(
        config,
        registry_path=registry_path,
        goal_id="goal_public_fixture",
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    assert first["ok"] is False
    assert first["successful_write_count"] == 1
    assert first["external_write_performed"] is True
    persisted_after_failure = read_lark_kanban_local_config(config_path)[
        "todo_records"
    ]
    assert persisted_after_failure[
        "goal_public_fixture:todo_public_one"
    ] == "rec_public_one"
    assert "goal_public_fixture:todo_public_two" not in persisted_after_failure

    second = sync_loopx_todos_to_lark_kanban(
        config,
        registry_path=registry_path,
        goal_id="goal_public_fixture",
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    assert second["ok"] is True
    assert second["successful_write_count"] == 2
    assert second["external_write_performed"] is True
    persisted_after_retry = read_lark_kanban_local_config(config_path)["todo_records"]
    assert persisted_after_retry == {
        "goal_public_fixture:todo_public_one": "rec_public_one",
        "goal_public_fixture:todo_public_two": "rec_public_two",
    }


def test_partial_setup_ignores_null_view_id_and_preserves_records_and_heartbeat_fallback(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.presentation.kanban import (
        lark_kanban_config_from_payload,
        lark_kanban_heartbeat,
        setup_lark_kanban_board,
    )

    config_path = tmp_path / "lark-kanban.json"
    write_lark_kanban_local_config(
        config_path,
        {
            "board": {
                "base_token": "base_public_fixture",
                "table_id": "tbl_public_fixture",
                "cli_bin": "lark-cli",
                "identity": "user",
                "view_ids": {
                    "Worker Queue": None,
                    "Kanban": "vew_kanban_fixture",
                },
            },
            "todo_records": {"goal_public_fixture:todo_1": "rec_existing_fixture"},
        },
    )

    def setup_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        if args == ["lark-cli", "--version"]:
            return _result({"version": "1.0.56"})
        if args == ["lark-cli", "auth", "status"]:
            return _result({"ok": True, "identities": {"user": {"available": True}}})
        if args[-1:] == ["--help"]:
            return _result({"ok": True})
        if "+field-list" in args:
            return {
                "returncode": 1,
                "stdout": json.dumps(
                    {"ok": False, "error": {"message": "permission denied"}}
                ),
                "stderr": "permission denied",
                "timed_out": False,
            }
        raise AssertionError(args)

    setup_payload = setup_lark_kanban_board(
        config_path=config_path,
        base_name="LoopX Existing Board",
        cli_bin="lark-cli",
        identity="user",
        execute=True,
        runner=setup_runner,
    )

    assert setup_payload["ok"] is True
    assert setup_payload["partial"] is True
    stored = read_lark_kanban_local_config(config_path)
    assert stored["board"]["view_id"] == "Worker Queue"
    assert stored["board"]["view_ids"].get("Worker Queue") is None
    assert stored["board"]["view_ids"]["Kanban"] == "vew_kanban_fixture"
    assert stored["todo_records"] == {
        "goal_public_fixture:todo_1": "rec_existing_fixture"
    }

    config = lark_kanban_config_from_payload(stored)
    assert config is not None
    assert config.view_id == "Worker Queue"

    record_list_calls: list[list[str]] = []

    def heartbeat_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        if "+record-list" in args:
            record_list_calls.append(args)
            view_arg = args[args.index("--view-id") + 1] if "--view-id" in args else args[args.index("--view") + 1]
            assert view_arg == "Worker Queue"
            assert view_arg != "None"
            return _result(
                {
                    "ok": True,
                    "data": {
                        "fields": ["Task", "Status"],
                        "data": [],
                        "record_id_list": [],
                        "has_more": False,
                    },
                }
            )
        raise AssertionError(args)

    heartbeat = lark_kanban_heartbeat(
        config,
        execute_lark=True,
        runner=heartbeat_runner,
    )

    assert heartbeat["ok"] is True
    assert heartbeat["decision"] == "no_claimable_task"
    assert len(record_list_calls) == 1
