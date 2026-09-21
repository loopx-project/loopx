from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from pathlib import Path

import pytest

from scripts.codex_app_apply_rrule import (
    _now_ms,
    _parse_args,
    _scheduler_hint_turn_instance_id,
    _update_sqlite,
    _update_toml,
    main,
)


def _write_toml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'version = 1\nid = "loopx"\nkind = "heartbeat"\n'
        'status = "ACTIVE"\nrrule = "FREQ=MINUTELY;INTERVAL=3"\n',
        encoding="utf-8",
    )


def _write_db(path: Path, *, rrule: str = "FREQ=MINUTELY;INTERVAL=3") -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE automations ("
            "id TEXT PRIMARY KEY, name TEXT, prompt TEXT, status TEXT, "
            "next_run_at INTEGER, last_run_at INTEGER, cwds TEXT, rrule TEXT, "
            "model TEXT, reasoning_effort TEXT, created_at INTEGER, "
            "updated_at INTEGER, target_type TEXT, project_id TEXT)"
        )
        connection.execute(
            "INSERT INTO automations (id, name, prompt, status, cwds, rrule, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "loopx",
                "LoopX 旁路",
                "advance loopx-meta",
                "ACTIVE",
                "[]",
                rrule,
                _now_ms(),
                _now_ms(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _hint_payload(*, apply_needed: bool, ack_needed: bool = False) -> dict:
    return {
        "ok": True,
        "scheduler_hint": {
            "codex_app": {
                "recommended_rrule": (
                    "FREQ=MINUTELY;INTERVAL=5" if apply_needed else None
                ),
                "stateful_backoff": {
                    "apply_needed": apply_needed,
                    "ack_needed": ack_needed,
                    "current_rrule": "FREQ=MINUTELY;INTERVAL=3",
                },
                "ack_hint": (
                    {
                        "cli_args": [
                            "quota",
                            "scheduler-ack-current",
                            "--goal-id",
                            "loopx-meta",
                            "--applied-rrule",
                            "FREQ=MINUTELY;INTERVAL=5",
                            "--host-match-observed",
                            "--execute",
                        ]
                    }
                    if apply_needed or ack_needed
                    else None
                ),
            }
        },
    }


class _FakeCompleted:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        returncode: int = 0,
        stdout: str | None = None,
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = json.dumps(payload or {}) if stdout is None else stdout
        self.stderr = stderr


def test_scheduler_hint_replays_parent_turn_without_synthetic_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parent_turn = "heartbeat-turn-3231"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _FakeCompleted(_hint_payload(apply_needed=False))

    monkeypatch.setattr("scripts.codex_app_apply_rrule.subprocess.run", fake_run)

    assert (
        main(
            [
                "--agent-id",
                "codex-fixture",
                "--automations-root",
                str(tmp_path / "automations"),
                "--db-path",
                str(tmp_path / "codex-dev.db"),
                "--loopx",
                "loopx",
                "--turn-instance-id",
                parent_turn,
            ]
        )
        == 0
    )

    replay_turn = calls[0][calls[0].index("--turn-instance-id") + 1]
    assert replay_turn == _scheduler_hint_turn_instance_id(parent_turn)
    assert replay_turn == parent_turn
    assert len(replay_turn) <= 128
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", replay_turn)
    assert "--available-capability" not in calls[0]


def test_default_app_stores_follow_codex_home_and_capabilities_are_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    defaults = _parse_args(["--agent-id", "codex-fixture"])
    assert defaults.automations_root == codex_home / "automations"
    assert defaults.db_path == codex_home / "sqlite/codex-dev.db"
    assert defaults.capability == []

    explicit = _parse_args(
        ["--agent-id", "codex-fixture", "--capability", "network"]
    )
    assert explicit.capability == ["network"]


def test_scheduler_bridge_requires_explicit_agent_identity() -> None:
    with pytest.raises(SystemExit) as error:
        _parse_args([])

    assert error.value.code == 2


def test_heartbeat_guide_matches_parent_turn_replay_contract() -> None:
    guide = (
        Path(__file__).parents[1] / "docs/heartbeat-automation-prompt.md"
    ).read_text(encoding="utf-8")

    assert "reuses the provided parent Turn" in guide
    assert "same-Turn replay preserves the committed" in guide
    assert "bound Todo, observed capabilities, and settlement identity" in guide
    assert "explicitly conflicting identity still fails closed" in guide
    assert "derives a stable child receipt id" not in guide
    assert "does not reuse the already-settled heartbeat receipt" not in guide


def test_should_run_failure_reports_structured_stdout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    failure = {
        "ok": False,
        "error_code": "heartbeat_receipt_identity_conflict",
        "reason": "heartbeat receipt settlement identity conflicts",
    }

    monkeypatch.setattr(
        "scripts.codex_app_apply_rrule.subprocess.run",
        lambda command, **kwargs: _FakeCompleted(failure, returncode=1),
    )

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--agent-id",
                "codex-fixture",
                "--automations-root",
                str(tmp_path / "automations"),
                "--db-path",
                str(tmp_path / "codex-dev.db"),
                "--loopx",
                "loopx",
                "--turn-instance-id",
                "heartbeat-turn-3231",
            ]
        )

    message = str(raised.value)
    assert "loopx should-run failed (1)" in message
    assert "stdout=" in message
    assert "heartbeat_receipt_identity_conflict" in message
    assert "heartbeat receipt settlement identity conflicts" in message


def test_apply_updates_toml_db_and_runs_ack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    toml_path = tmp_path / "automations/loopx/automation.toml"
    db_path = tmp_path / "codex-dev.db"
    _write_toml(toml_path)
    _write_db(db_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        if "should-run" in command:
            return _FakeCompleted(_hint_payload(apply_needed=True))
        calls.append(command)
        return _FakeCompleted({"ok": True})

    monkeypatch.setattr("scripts.codex_app_apply_rrule.subprocess.run", fake_run)

    code = main(
        [
            "--agent-id",
            "codex-fixture",
            "--automations-root",
            str(tmp_path / "automations"),
            "--db-path",
            str(db_path),
            "--loopx",
            "loopx",
        ]
    )

    assert code == 0
    assert 'rrule = "FREQ=MINUTELY;INTERVAL=5"' in toml_path.read_text(
        encoding="utf-8"
    )
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT rrule FROM automations WHERE id='loopx'"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("FREQ=MINUTELY;INTERVAL=5",)
    assert calls and calls[0][0] == "loopx"
    assert any("scheduler-ack-current" in call for call in calls)
    backups = list(tmp_path.glob("codex-dev.db.bak-*"))
    assert len(backups) == 1


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    toml_path = tmp_path / "automations/loopx/automation.toml"
    db_path = tmp_path / "codex-dev.db"
    _write_toml(toml_path)
    _write_db(db_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _FakeCompleted(_hint_payload(apply_needed=True))

    monkeypatch.setattr("scripts.codex_app_apply_rrule.subprocess.run", fake_run)

    code = main(
        [
            "--agent-id",
            "codex-fixture",
            "--automations-root",
            str(tmp_path / "automations"),
            "--db-path",
            str(db_path),
            "--loopx",
            "loopx",
            "--dry-run",
        ]
    )

    assert code == 0
    assert 'rrule = "FREQ=MINUTELY;INTERVAL=3"' in toml_path.read_text(
        encoding="utf-8"
    )
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT rrule FROM automations WHERE id='loopx'"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("FREQ=MINUTELY;INTERVAL=3",)
    assert not list(tmp_path.glob("codex-dev.db.bak-*"))
    assert all("scheduler-ack-current" not in call for call in calls)


def test_no_apply_needed_is_quiet_noop(tmp_path: Path, monkeypatch) -> None:
    toml_path = tmp_path / "automations/loopx/automation.toml"
    db_path = tmp_path / "codex-dev.db"
    _write_toml(toml_path)
    _write_db(db_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _FakeCompleted(_hint_payload(apply_needed=False, ack_needed=True))

    monkeypatch.setattr("scripts.codex_app_apply_rrule.subprocess.run", fake_run)

    code = main(
        [
            "--agent-id",
            "codex-fixture",
            "--automations-root",
            str(tmp_path / "automations"),
            "--db-path",
            str(db_path),
            "--loopx",
            "loopx",
        ]
    )

    assert code == 0
    assert 'rrule = "FREQ=MINUTELY;INTERVAL=3"' in toml_path.read_text(
        encoding="utf-8"
    )
    assert not list(tmp_path.glob("codex-dev.db.bak-*"))
    assert any("scheduler-ack-current" in call for call in calls)


def test_update_toml_and_sqlite_helpers(tmp_path: Path) -> None:
    toml_path = tmp_path / "automation.toml"
    db_path = tmp_path / "store.db"
    _write_toml(toml_path)
    _write_db(db_path)

    _update_toml(toml_path, "FREQ=MINUTELY;INTERVAL=10")
    _update_sqlite(
        db_path,
        automation_id="loopx",
        rrule="FREQ=MINUTELY;INTERVAL=10",
        backup_path=tmp_path / "store.db.bak",
    )

    assert 'rrule = "FREQ=MINUTELY;INTERVAL=10"' in toml_path.read_text(
        encoding="utf-8"
    )
    assert (tmp_path / "store.db.bak").exists()
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT rrule FROM automations WHERE id='loopx'"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("FREQ=MINUTELY;INTERVAL=10",)


def test_apply_creates_missing_automation_toml_and_sqlite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    automations_root = tmp_path / "automations"
    db_path = tmp_path / "codex-dev.db"
    registry_path = tmp_path / "registry.json"
    sqlite3.connect(str(db_path)).close()
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal",
                        "repo": "/tmp/workspace",
                        "coordination": {
                            "thread_agent_bindings": [
                                {
                                    "agent_id": "agent",
                                    "thread_id": "thread-1",
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        if "should-run" in command:
            return _FakeCompleted(_hint_payload(apply_needed=True))
        if "heartbeat-prompt" in command:
            return _FakeCompleted(
                {
                    "ok": True,
                    "task_body": (
                        "Advance `goal` from active state. "
                        "Agent: `agent`."
                    ),
                }
            )
        calls.append(command)
        return _FakeCompleted({"ok": True})

    monkeypatch.setattr("scripts.codex_app_apply_rrule.subprocess.run", fake_run)

    code = main(
        [
            "--automations-root",
            str(automations_root),
            "--db-path",
            str(db_path),
            "--registry",
            str(registry_path),
            "--goal-id",
            "goal",
            "--agent-id",
            "agent",
            "--automation-id",
            "loopx-goal-agent",
            "--loopx",
            "loopx",
        ]
    )

    assert code == 0
    toml_path = automations_root / "loopx-goal-agent" / "automation.toml"
    assert toml_path.exists()
    toml_text = toml_path.read_text(encoding="utf-8")
    assert 'rrule = "FREQ=MINUTELY;INTERVAL=5"' in toml_text
    assert 'target_thread_id = "thread-1"' in toml_text
    assert tomllib.loads(toml_text)["prompt"] == (
        "Advance `goal` from active state. Agent: `agent`."
    )

    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT id, rrule, prompt, cwds FROM automations "
            "WHERE id='loopx-goal-agent'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[1] == "FREQ=MINUTELY;INTERVAL=5"
    assert "Advance `goal`" in row[2]
    assert "/tmp/workspace" in row[3]
    assert any("scheduler-ack-current" in call for call in calls)
    assert list(tmp_path.glob("codex-dev.db.bak-*"))
