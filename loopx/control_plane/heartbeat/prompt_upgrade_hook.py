"""Content-free update receipts and a read-only, conditional turn-start hint.

Installed-prompt reconciliation owns discovery. The existing typed hook owns
observation admission.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shlex
from typing import Any, Mapping

from ...file_lock import exclusive_file_lock
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...upgrade import codex_home
from ..capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)
from .automation_upgrade import _atomic, _connect, _read, digest

_SCHEMA = "loopx_deferred_prompt_upgrade_v0"
_HOOK = "heartbeat.prompt_upgrade"


def receipt_path(runtime_root: Path, registry: Path, home: Path) -> Path:
    scope = json.dumps([str(registry.resolve()), str(home.resolve())])
    return runtime_root / "automation-prompt-upgrades" / (digest(scope) + ".json")


def _read_receipts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA
            or not isinstance(payload.get("entries"), dict)):
        raise ValueError("invalid deferred prompt upgrade receipt")
    return dict(payload["entries"])


def record_deferred_upgrades(*, registry: Path, home: Path, runtime_root: str | None,
                             cli_bin: str, entries: dict[str, Any], results: list[dict[str, Any]]) -> None:
    root = resolve_runtime_root(load_registry(registry), runtime_root, registry_path=registry)
    path = receipt_path(root, registry, home)
    if not path.exists() and not any(result["status"] == "deferred" for result in results):
        return
    # Independent sync-installed subsets cannot erase each other's reminders.
    with exclusive_file_lock(path):
        pending = _read_receipts(path)
        for result in results:
            identifier = result["automation_id"]
            if result["status"] == "deferred":
                entry = entries[identifier]
                pending[identifier] = {key: entry[key] for key in (
                    "goal_id", "agent_id", "prompt_sha256", "target_thread_id",
                )}
                pending[identifier].update(cli_bin=cli_bin, runtime_root=runtime_root)
            else:
                pending.pop(identifier, None)
        if pending:
            _atomic(path, json.dumps({"schema_version": _SCHEMA, "entries": pending}))
        elif path.exists():
            path.unlink()


def prompt_upgrade_hook(*, registry: Path, runtime_root: Path, goal_id: str,
                        agent_id: str) -> TurnStartHookRegistration | None:
    home = codex_home().expanduser().resolve()
    pending = _read_receipts(receipt_path(runtime_root, registry, home))
    candidates = [(key, value) for key, value in pending.items()
        if isinstance(value, dict) and value.get("goal_id") == goal_id
        and value.get("agent_id") == agent_id]
    if len(candidates) != 1:
        return None
    identifier, record = candidates[0]
    command = [record["cli_bin"], "--format", "json", "--registry", str(registry)]
    if record.get("runtime_root"):
        command += ["--runtime-root", record["runtime_root"]]
    command += ["automation-prompts", "plan", "--codex-home", str(home),
        "--automation-id", identifier, "--cli-bin", record["cli_bin"]]
    required_read = {
        "kind": "automation_prompt_upgrade",
        "command": shlex.join(command),
        "reason": "A managed prompt upgrade is pending. Read the fresh plan; review and apply only the prompt through automation_update, then read back. Preserve other fields; no quota spend for repair. Continue normal work under its existing decision.",
        "ordering": "before_work",
        "prompt_budget_bytes": 1536,
    }

    def produce() -> dict[str, Any]:
        with closing(_connect(home)) as connection:
            _, item, _ = _read(home, identifier, connection)
        applicable = (digest(item["prompt"]) == record.get("prompt_sha256")
            and item["target_thread_id"] == record.get("target_thread_id"))
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": _HOOK, "capability_id": "automation-prompt-upgrade",
            "phase": "turn_start", "status": "observed" if applicable else "empty",
            "observation_count": int(applicable), "agent_read_required": applicable,
            "external_reads_performed": False, "external_writes_performed": False,
            "local_private_state_mutated": False, "private_content_returned": False,
            "provider_payload_returned": False, "error_code": None,
        }

    return TurnStartHookRegistration(
        hook_id=_HOOK, capability_id="automation-prompt-upgrade",
        requested_read_scope=("deferred_prompt_upgrade", "installed_automation"),
        requested_write_scope=(), producer=produce, required_read=required_read,
    )


def extend_prompt_upgrade_reads(
    dispatch: Mapping[str, Any] | None, **kwargs: Any,
) -> Mapping[str, Any] | None:
    """Fixed hook loading; healthy lanes preserve their exact existing projection."""
    try:
        hook = prompt_upgrade_hook(**kwargs)
        if hook is None:
            return dispatch
        extra = dispatch_turn_start_hooks((hook,))
    except (OSError, ValueError, KeyError, TypeError):
        return dispatch
    if not extra["required_reads"]:
        return dispatch
    return {**(dispatch or {}), "required_reads": [
        *(dispatch or {}).get("required_reads", []), *extra["required_reads"],
    ]}
