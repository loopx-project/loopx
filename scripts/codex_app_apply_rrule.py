#!/usr/bin/env python3
"""Apply a LoopX scheduler-hint RRULE to the Codex App automation store.

Fallback host bridge for hosts where the Codex App does not expose
``automation_update``. The script:

1. reads ``loopx quota should-run`` and its ``scheduler_hint.codex_app``;
2. when ``stateful_backoff.apply_needed=true`` and ``recommended_rrule`` is
   present, creates the automation TOML and ``automations`` row when they are
   missing, backs up the Codex App automation SQLite database, and updates
   both stores to the recommended RRULE;
3. runs the bound ``ack_hint.cli_args`` so LoopX persists the reset token and
   progression index.

The script is public-safe: it carries no credentials and all host paths can be
overridden for tests or alternate installations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

from loopx.turn_identity import normalize_turn_instance_id


_FAILURE_OUTPUT_LIMIT = 2_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bounded_failure_output(value: str) -> str:
    output = value.strip()
    if len(output) <= _FAILURE_OUTPUT_LIMIT:
        return output
    half = (_FAILURE_OUTPUT_LIMIT - len(" ... ")) // 2
    return f"{output[:half]} ... {output[-half:]}"


def _subprocess_failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    details = []
    for label, value in (
        ("stdout", completed.stdout),
        ("stderr", completed.stderr),
    ):
        bounded = _bounded_failure_output(value or "")
        if bounded:
            details.append(f"{label}={bounded}")
    return "; ".join(details) or "no diagnostic output"


def _scheduler_hint_turn_instance_id(parent_turn_instance_id: str) -> str:
    """Reuse the host Turn so the fallback query replays its quota receipt."""

    try:
        normalized = normalize_turn_instance_id(parent_turn_instance_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not normalized:
        raise SystemExit("--turn-instance-id must not be empty")
    return normalized


def _codex_home() -> Path:
    """Resolve the active Codex App home without changing LoopX registry roots."""

    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _load_registry_goal(registry: Path, goal_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    goals = payload.get("goals") if isinstance(payload, dict) else None
    if not isinstance(goals, list):
        return {}
    for goal in goals:
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return goal
    return {}


def _heartbeat_task_body(
    *,
    loopx: str,
    registry: Path,
    goal_id: str,
    agent_id: str,
) -> str:
    command = [
        loopx,
        "--format",
        "json",
        "--registry",
        str(registry),
        "heartbeat-prompt",
        "--thin",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--codex-app",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"loopx heartbeat-prompt failed ({completed.returncode}): "
            f"{_subprocess_failure_detail(completed)}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"loopx heartbeat-prompt returned invalid JSON: {exc}") from exc
    body = payload.get("task_body") if isinstance(payload, dict) else None
    if not isinstance(body, str) or not body.strip():
        raise SystemExit("loopx heartbeat-prompt returned no task_body")
    return body


def _toml_string(value: str) -> str:
    """Encode a string in the JSON-compatible subset of TOML basic strings."""

    return json.dumps(value, ensure_ascii=False)


def _write_automation_toml(
    path: Path,
    *,
    automation_id: str,
    name: str,
    prompt: str,
    rrule: str,
    thread_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "version = 1\n"
        f"id = {_toml_string(automation_id)}\n"
        'kind = "heartbeat"\n'
        f"name = {_toml_string(name)}\n"
        f"prompt = {_toml_string(prompt)}\n"
        'status = "ACTIVE"\n'
        f"rrule = {_toml_string(rrule)}\n"
        f"target_thread_id = {_toml_string(thread_id)}\n"
        f"created_at = {_now_ms()}\n"
        f"updated_at = {_now_ms()}\n"
    )
    path.write_text(text, encoding="utf-8")


def _read_automation_prompt(path: Path) -> str | None:
    try:
        automation = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"automation TOML is unreadable or invalid: {path}") from exc
    prompt = automation.get("prompt")
    return prompt if isinstance(prompt, str) and prompt.strip() else None


def _sqlite_row_exists(db_path: Path, automation_id: str) -> bool:
    if not db_path.exists():
        return False
    connection = sqlite3.connect(str(db_path))
    try:
        try:
            row = connection.execute(
                "SELECT 1 FROM automations WHERE id=?", (automation_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return False
    finally:
        connection.close()
    return row is not None


def _ensure_sqlite_row(
    db_path: Path,
    *,
    automation_id: str,
    name: str,
    prompt: str,
    rrule: str,
    cwd: str,
    backup_path: Path | None,
) -> None:
    if backup_path is not None and db_path.exists():
        connection = sqlite3.connect(str(db_path))
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.backup(sqlite3.connect(str(backup_path)))
        finally:
            connection.close()
    now = _now_ms()
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                next_run_at INTEGER,
                last_run_at INTEGER,
                cwds TEXT NOT NULL DEFAULT '[]',
                rrule TEXT NOT NULL DEFAULT 'FREQ=HOURLY;INTERVAL=24;BYMINUTE=0',
                model TEXT,
                reasoning_effort TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                target_type TEXT,
                project_id TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO automations (
                id, name, prompt, status, next_run_at, last_run_at,
                cwds, rrule, model, reasoning_effort, created_at,
                updated_at, target_type, project_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                prompt=excluded.prompt,
                status=excluded.status,
                next_run_at=excluded.next_run_at,
                last_run_at=excluded.last_run_at,
                cwds=excluded.cwds,
                rrule=excluded.rrule,
                updated_at=excluded.updated_at
            """,
            (
                automation_id,
                name,
                prompt,
                "ACTIVE",
                now + 30_000,
                now,
                json.dumps([cwd]) if cwd else "[]",
                rrule,
                "",
                "",
                now,
                now,
                "",
                "",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _ensure_automation(
    *,
    loopx: str,
    registry: Path,
    automations_root: Path,
    db_path: Path,
    automation_id: str,
    goal_id: str,
    agent_id: str,
    rrule: str,
    backup_path: Path | None,
) -> bool:
    toml_path = automations_root / automation_id / "automation.toml"
    if toml_path.exists() and _sqlite_row_exists(db_path, automation_id):
        return False
    goal = _load_registry_goal(registry, goal_id)
    thread_id = ""
    bindings = (goal.get("coordination") or {}).get("thread_agent_bindings") or []
    if isinstance(bindings, list):
        matches = [
            binding
            for binding in bindings
            if isinstance(binding, dict)
            and str(binding.get("agent_id") or "") == agent_id
        ]
        if len(matches) == 1:
            thread_id = str(matches[0].get("thread_id") or "")
    cwd = str(goal.get("repo") or "")
    name = f"{goal_id} LoopX"
    if toml_path.exists():
        prompt = _read_automation_prompt(toml_path) or _heartbeat_task_body(
            loopx=loopx,
            registry=registry,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    else:
        prompt = _heartbeat_task_body(
            loopx=loopx,
            registry=registry,
            goal_id=goal_id,
            agent_id=agent_id,
        )
        _write_automation_toml(
            toml_path,
            automation_id=automation_id,
            name=name,
            prompt=prompt,
            rrule=rrule,
            thread_id=thread_id,
        )
    _ensure_sqlite_row(
        db_path,
        automation_id=automation_id,
        name=name,
        prompt=prompt,
        rrule=rrule,
        cwd=cwd,
        backup_path=backup_path,
    )
    return True


def _load_scheduler_hint(
    *,
    loopx: str,
    registry: Path,
    goal_id: str,
    agent_id: str,
    parent_turn_instance_id: str,
    capabilities: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    turn_instance_id = _scheduler_hint_turn_instance_id(parent_turn_instance_id)
    command = [
        loopx,
        "--format",
        "json",
        "--registry",
        str(registry),
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--codex-app",
        "--turn-instance-id",
        turn_instance_id,
    ]
    for capability in capabilities:
        command.extend(["--available-capability", capability])
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"loopx should-run failed ({completed.returncode}): "
            f"{_subprocess_failure_detail(completed)}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"loopx should-run returned invalid JSON: {exc}") from exc
    hint = payload.get("scheduler_hint", {})
    codex_app = hint.get("codex_app", {})
    if not isinstance(codex_app, dict):
        codex_app = {}
    stateful = codex_app.get("stateful_backoff", {})
    if not isinstance(stateful, dict):
        stateful = {}
    return codex_app, stateful


def _update_toml(toml_path: Path, rrule: str) -> None:
    text = toml_path.read_text(encoding="utf-8")
    pattern = re.compile(r'^rrule\s*=\s*".*?"', re.MULTILINE)
    if not pattern.search(text):
        raise SystemExit(f"automation TOML has no rrule field: {toml_path}")
    replacement = f"rrule = {_toml_string(rrule)}"
    toml_path.write_text(
        pattern.sub(lambda _: replacement, text),
        encoding="utf-8",
    )


def _update_sqlite(
    db_path: Path,
    *,
    automation_id: str,
    rrule: str,
    backup_path: Path | None,
) -> None:
    if backup_path is not None:
        if not db_path.exists():
            raise SystemExit(f"automation SQLite does not exist: {db_path}")
        connection = sqlite3.connect(str(db_path))
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.backup(sqlite3.connect(str(backup_path)))
        finally:
            connection.close()
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        cursor = connection.execute(
            "UPDATE automations SET rrule=?, updated_at=? WHERE id=?",
            (rrule, _now_ms(), automation_id),
        )
        connection.commit()
        if cursor.rowcount != 1:
            raise SystemExit(
                f"automation row not found or not updated in {db_path}: "
                f"{automation_id}"
            )
    finally:
        connection.close()


def _run_ack(loopx: str, ack_args: list[str]) -> None:
    completed = subprocess.run(
        [loopx, *ack_args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"loopx scheduler ACK failed ({completed.returncode}): "
            f"{_subprocess_failure_detail(completed)}"
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the LoopX scheduler-hint RRULE to the Codex App automation "
            "store (TOML + SQLite) and run the bound ACK."
        )
    )
    parser.add_argument("--goal-id", default="loopx-meta")
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Registered LoopX Agent that owns this scheduler mutation.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path.home() / ".codex/loopx/registry.global.json",
    )
    parser.add_argument("--automation-id", default="loopx")
    parser.add_argument(
        "--automations-root",
        type=Path,
        default=_codex_home() / "automations",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_codex_home() / "sqlite/codex-dev.db",
    )
    parser.add_argument("--loopx", default="loopx")
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--turn-instance-id",
        default=f"apply-rrule-{_now_ms()}",
        help=(
            "outer host turn identity; the bridge reuses it when replaying the "
            "scheduler hint"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    codex_app, stateful = _load_scheduler_hint(
        loopx=args.loopx,
        registry=args.registry,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        parent_turn_instance_id=args.turn_instance_id,
        capabilities=args.capability,
    )
    apply_needed = stateful.get("apply_needed") is True
    rrule = codex_app.get("recommended_rrule") or stateful.get("current_rrule")
    ack_hint = codex_app.get("ack_hint", {})
    ack_args = ack_hint.get("cli_args") if isinstance(ack_hint, dict) else None
    toml_path = args.automations_root / args.automation_id / "automation.toml"
    toml_missing = not toml_path.exists()
    sqlite_missing = not _sqlite_row_exists(args.db_path, args.automation_id)

    if not apply_needed:
        print(
            f"no apply needed (apply_needed=false); rrule={rrule or 'unknown'}; "
            f"ack_needed={stateful.get('ack_needed') is True}"
        )
        if (
            stateful.get("ack_needed") is True
            and isinstance(ack_args, list)
            and not args.dry_run
        ):
            _run_ack(args.loopx, ack_args)
        return 0
    if not rrule:
        raise SystemExit(
            "apply_needed=true but no recommended_rrule/current_rrule was projected"
        )
    if not isinstance(ack_args, list):
        raise SystemExit("apply_needed=true but no ack_hint.cli_args was projected")

    backup_path = (
        Path(str(args.db_path) + f".bak-{time.strftime('%Y%m%dT%H%M%S')}")
        if not args.dry_run
        else None
    )
    print(f"apply rrule={rrule}")
    print(f"toml={toml_path}")
    print(f"db={args.db_path}")
    print(f"backup={backup_path or 'none (dry-run)'}")
    print(f"ack_args={ack_args}")
    if args.dry_run:
        if toml_missing or sqlite_missing:
            print(
                "dry-run: would create missing automation "
                f"{args.automation_id} (toml={not toml_missing}, "
                f"sqlite={not sqlite_missing})"
            )
        print("dry-run: no writes performed")
        return 0

    created = False
    if toml_missing or sqlite_missing:
        created = _ensure_automation(
            loopx=args.loopx,
            registry=args.registry,
            automations_root=args.automations_root,
            db_path=args.db_path,
            automation_id=args.automation_id,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            rrule=rrule,
            backup_path=backup_path,
        )
        print(f"created missing automation: {args.automation_id}")
    _update_toml(toml_path, rrule)
    _update_sqlite(
        args.db_path,
        automation_id=args.automation_id,
        rrule=rrule,
        backup_path=None if created else backup_path,
    )
    _run_ack(args.loopx, ack_args)
    print(f"applied {rrule} and ACKed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
