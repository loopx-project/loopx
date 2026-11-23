"""Installed prompt lifecycle; execution policy stays in heartbeat-prompt.

The App API is the preferred interactive writer. The journaled store writer is
a qualified local compatibility adapter.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
import tomllib
from typing import Any
from types import SimpleNamespace

from .bootstrap_prompt import (
    _matches_published_bootstrap,
    HEARTBEAT_BOOTSTRAP,
    BOOTSTRAP_INSTRUCTION,
    host_bootstrap_binding,
    goal_bootstrap,
    render_heartbeat_bootstrap,
)

from loopx.paths import codex_automation_database
from loopx.upgrade import (
    codex_home, infer_agent_id_from_prompt, infer_goal_id_from_prompt,
    infer_available_capabilities_from_prompt,
)

SCHEMA = "loopx_automation_prompt_upgrade_v0"
BOOTSTRAP = HEARTBEAT_BOOTSTRAP
_LEGACY_BOOTSTRAP = "LoopX managed heartbeat bootstrap v1"
_LEGACY_INSTRUCTION = (
    "读取完整结果；仅 ok=true 时按本次 task_body 执行，不复用旧指令；"
    "失败或结果不完整则停止并报告，不执行任务或记账。"
)
_BOOTSTRAP_INSTRUCTION = BOOTSTRAP_INSTRUCTION


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bootstrap_prompt(*, registry: Path, goal_id: str, agent_id: str,
                     runtime_root: str | None = None,
                     capabilities: list[str] | None = None,
                     cli_bin: str = "loopx") -> str:
    args = [cli_bin, "--format", "json", "--registry", str(registry.resolve())]
    if runtime_root:
        args += ["--runtime-root", str(Path(runtime_root).expanduser().resolve())]
    args += ["heartbeat-prompt", "--thin", "--codex-app", "--goal-id", goal_id,
             "--agent-id", agent_id]
    if cli_bin != "loopx":
        args += ["--cli-bin", cli_bin]
    for capability in capabilities or []:
        args += ["--available-capability", capability]
    return render_heartbeat_bootstrap(args)


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".loopx-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bootstrap_binding(prompt: str) -> dict | None:
    if not prompt.startswith((BOOTSTRAP + "\n", _LEGACY_BOOTSTRAP + "\n")):
        return None
    try:
        command = prompt.split("```sh\n", 1)[1].split("\n```", 1)[0]
        tokens = shlex.split(command)
        if tokens[1:3] != ["--format", "json"]:
            return None
        values: dict[str, Any] = {"capabilities": []}
        flags = {"--registry": "registry", "--runtime-root": "runtime_root",
                 "--goal-id": "goal_id", "--agent-id": "agent_id", "--cli-bin": "cli_bin"}
        index = 3
        while index < len(tokens):
            token = tokens[index]
            if token in ("heartbeat-prompt", "--thin", "--codex-app"):
                index += 1
                continue
            if token == "--available-capability":
                values["capabilities"].append(tokens[index + 1])
            elif token in flags and flags[token] not in values:
                values[flags[token]] = tokens[index + 1]
            else:
                return None
            index += 2
        values["registry"] = Path(values["registry"])
        if tokens[0] != values.get("cli_bin", "loopx"):
            return None
        expected = bootstrap_prompt(**values)
        legacy = expected.replace(BOOTSTRAP, _LEGACY_BOOTSTRAP, 1).removesuffix(
            _BOOTSTRAP_INSTRUCTION
        ) + _LEGACY_INSTRUCTION
        return values if _matches_published_bootstrap(prompt, expected) or prompt == legacy else None
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _replace_prompt(source: str, prompt: str) -> str:
    """Retain unknown TOML fields/comments; prove only prompt changed by parsing.

    Candidate spans are not trusted lexically: multiline strings can contain
    fake assignments. Both prefix and full-document semantic checks must pass.
    """
    original = tomllib.loads(source)
    desired = {**original, "prompt": prompt}
    lines = source.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if not re.match(r"^prompt\s*=", line):
            continue
        try:
            prefix = tomllib.loads("".join(lines[:start]))
        except tomllib.TOMLDecodeError:
            continue
        if "prompt" in prefix:
            continue
        for end in range(start + 1, len(lines) + 1):
            candidate = "".join(lines[:start]) + "prompt = " + json.dumps(prompt, ensure_ascii=False) + "\n" + "".join(lines[end:])
            try:
                if tomllib.loads(candidate) == desired:
                    return candidate
            except tomllib.TOMLDecodeError:
                pass
    raise ValueError("unsupported automation TOML prompt encoding; use the App API")


def _connect(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(automations)")}
    if not {"id", "kind", "prompt", "status", "target_thread_id"} <= columns:
        connection.close()
        raise ValueError("unsupported Codex automation schema; use the App API")
    return connection


def _read(home: Path, automation_id: str, connection: sqlite3.Connection) -> tuple[str, dict, dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", automation_id):
        raise ValueError("invalid automation id")
    path = home / "automations" / automation_id / "automation.toml"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlink automation stores are not supported")
    source = path.read_text(encoding="utf-8")
    if len(source.encode("utf-8")) > 256_000:
        raise ValueError("automation manifest exceeds the supported offline size")
    item = tomllib.loads(source)
    row = connection.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
    if row is None or item.get("id") != automation_id:
        raise ValueError("automation identity missing or mismatched")
    row = dict(row)
    if item.get("kind") != "heartbeat" or row["kind"] != "heartbeat":
        raise ValueError(
            "heartbeat kind/binding is not confirmed by both stores; reconcile through "
            "the App before prompt adoption (do not convert a cron row or infer its thread)"
        )
    if item.get("status") == "DELETED" or row["status"] == "DELETED":
        raise ValueError("deleted automation cannot be upgraded")
    for key in ("prompt", "status", "target_thread_id", "rrule"):
        if item.get(key) != row.get(key):
            raise ValueError(f"automation stores disagree on {key}; reconcile through the App")
    return source, item, row


def build_plan(*, registry: Path, home: Path | None = None,
               runtime_root: str | None = None, cli_bin: str = "loopx") -> dict[str, Any]:
    from loopx.agent_registry import registered_agent_ids_for_goal
    from loopx.history import load_registry
    from loopx.registry import registry_goals

    home = (home or codex_home()).expanduser().resolve()
    goals = {str(goal["id"]): goal for goal in registry_goals(load_registry(registry))}
    entries = []
    database = codex_automation_database(home)
    with closing(_connect(database)) as connection:
        for path in sorted((home / "automations").glob("*/automation.toml")):
            entry: dict[str, Any] = {"automation_id": path.parent.name}
            try:
                source, item, row = _read(home, path.parent.name, connection)
                prompt = item["prompt"]
                goal_id = infer_goal_id_from_prompt(prompt)
                agent_id = infer_agent_id_from_prompt(prompt)
                goal_mentions = set(re.findall(r"--goal-id\s+([A-Za-z0-9_.:-]+)", prompt))
                agent_mentions = set(re.findall(r"--agent-id\s+([A-Za-z0-9_.:-]+)", prompt))
                if (goal_mentions - {goal_id}) or (agent_mentions - {agent_id}):
                    raise ValueError("ambiguous Goal/agent bindings; select and migrate through the App")
                if goal_id not in goals or agent_id not in registered_agent_ids_for_goal(goals[goal_id]):
                    entry.update(status="unmanaged", reason="no unique registered Goal/agent binding")
                else:
                    desired = bootstrap_prompt(registry=registry, goal_id=goal_id, agent_id=agent_id,
                        runtime_root=runtime_root, capabilities=infer_available_capabilities_from_prompt(prompt),
                        cli_bin=cli_bin)
                    loaded_binding = host_bootstrap_binding(prompt)
                    if (loaded_binding and loaded_binding["registry"].resolve() == registry.resolve()
                            and (loaded_binding.get("codex_app") or
                                 loaded_binding.get("runtime_profile") == "codex_app_heartbeat")):
                        # Upgrade the wrapper while retaining explicit owner
                        # policy and scheduler inputs from the exact loader.
                        desired = goal_bootstrap(SimpleNamespace(**loaded_binding), registry=registry)
                    entry.update(status="current" if prompt == desired else "adoption_required",
                        goal_id=goal_id, agent_id=agent_id, prompt_sha256=digest(prompt),
                        current_prompt=prompt,
                        source_sha256=digest(source), desired_prompt=desired,
                        desired_sha256=digest(desired), target_thread_id=row["target_thread_id"])
            except (ValueError, OSError) as error:
                entry.update(status="blocked", reason=str(error))
            entries.append(entry)
    return {"schema_version": SCHEMA, "ok": True, "codex_home": str(home), "database_name": database.name,
            "entries": entries, "writes": False,
            "policy": "Discovery is not adoption authority. Review each replacement; use the App API first."}


def apply_offline(*, home: Path, automation_id: str, expected_prompt_sha256: str,
                  desired_prompt: str, expected_source_sha256: str | None = None,
                  expected_database: str | None = None) -> dict[str, Any]:
    """Journaled prompt-only write, also used by qualified update-time migration.

    Keep the SQLite writer lock through mirror replacement and readback. TOML
    cannot join that transaction: a crash is explicitly journal-recoverable,
    not falsely advertised as an atomic two-store commit. Concurrent external
    edits detected at either boundary fail instead of being retried blindly.
    The historical Python name is retained for the explicit offline CLI.
    """
    home = home.expanduser().resolve()
    journal = home / "loopx-automation-backups" / (automation_id + ".json")
    if journal.is_symlink() or journal.parent.is_symlink():
        raise ValueError("symlink backup stores are not supported")
    database = codex_automation_database(home)
    if expected_database is not None and database.name != expected_database:
        raise ValueError("automation database changed after preview; refresh the plan")
    with closing(_connect(database, writable=True)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        source, item, row = _read(home, automation_id, connection)
        path = home / "automations" / automation_id / "automation.toml"
        if expected_source_sha256 is not None and digest(source) != expected_source_sha256:
            raise ValueError("automation metadata changed after preview; no writes performed")
        if digest(item["prompt"]) != expected_prompt_sha256:
            raise ValueError("prompt changed after preview; no writes performed")
        if item["prompt"] == desired_prompt:
            return {"ok": True, "status": "current", "automation_id": automation_id}
        if journal.exists():
            raise ValueError("previous migration journal exists; recover or rollback it first")
        replacement = _replace_prompt(source, desired_prompt)
        # Entire originals stay private for recovery; no raw prompts in receipts.
        _atomic(journal, json.dumps({"schema_version": SCHEMA, "automation_id": automation_id,
            "database_name": database.name, "before": source, "after": replacement, "row": row}, ensure_ascii=False))
        if path.read_text(encoding="utf-8") != source:
            raise ValueError("automation manifest changed during migration; journal retained")
        changed = connection.execute("UPDATE automations SET prompt=? WHERE id=? AND prompt=?",
                                     (desired_prompt, automation_id, item["prompt"]))
        if changed.rowcount != 1:
            raise ValueError("automation prompt compare-and-swap failed")
        _atomic(path, replacement)
        if path.read_text(encoding="utf-8") != replacement:
            raise ValueError("automation manifest changed during readback; journal retained")
        _read(home, automation_id, connection)
        connection.commit()
    with closing(_connect(database)) as connection:
        final_source, _, final_row = _read(home, automation_id, connection)
        if final_source != replacement or final_row["prompt"] != desired_prompt:
            raise ValueError("automation changed after commit; inspect the retained journal")
    return {"ok": True, "status": "updated", "automation_id": automation_id,
            "backup": str(journal), "future_policy": "read installed heartbeat-prompt on every wake"}


def recover_offline(*, home: Path, automation_id: str, rollback: bool = False) -> dict:
    home = home.expanduser().resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", automation_id):
        raise ValueError("invalid automation id")
    journal = home / "loopx-automation-backups" / (automation_id + ".json")
    if journal.is_symlink() or journal.parent.is_symlink():
        raise ValueError("symlink backup stores are not supported")
    saved = json.loads(journal.read_text(encoding="utf-8"))
    if saved.get("schema_version") != SCHEMA or saved.get("automation_id") != automation_id:
        raise ValueError("invalid migration journal")
    before, after = saved["before"], saved["after"]
    prompts = [tomllib.loads(text)["prompt"] for text in (before, after)]
    path = home / "automations" / automation_id / "automation.toml"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlink automation stores are not supported")
    database = codex_automation_database(home)
    if saved.get("database_name", "codex-dev.db") != database.name:
        raise ValueError("automation database changed since migration; reconcile through the App")
    with closing(_connect(database, writable=True)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
        if row is None or row["prompt"] not in prompts or path.read_text(encoding="utf-8") not in (before, after):
            raise ValueError("automation changed since migration; recovery refuses to overwrite it")
        original = saved["row"]
        if any(row[key] != value for key, value in original.items() if key != "prompt"):
            raise ValueError("automation metadata changed; reconcile through the App")
        selected = before if rollback else after
        connection.execute("UPDATE automations SET prompt=? WHERE id=?", (tomllib.loads(selected)["prompt"], automation_id))
        connection.commit()
    _atomic(path, selected)
    with closing(_connect(database)) as connection:
        _read(home, automation_id, connection)
    return {"ok": True, "status": "rolled_back" if rollback else "recovered", "automation_id": automation_id}
