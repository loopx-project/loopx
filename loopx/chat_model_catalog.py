"""Read-only model discovery from the selected Chat host's own catalog.

Host catalogs declare choices, not authorization or successful inference. This
transport returns only picker metadata, never host instructions or credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

from .chat_agent import (
    CodexChatAgentSession, _LegacyModelCatalogSchemaError,
    _current_builtin_model_catalog, _reader,
)
from .chat_store import utc_now
from .extensions.process_runtime import terminate_process_tree


def _model(identifier: Any, label: Any = None, description: Any = None) -> dict[str, str]:
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 180:
        raise ValueError("invalid host model identifier")
    if any(ord(char) < 32 for char in identifier):
        raise ValueError("invalid host model identifier")
    return {"id": identifier.strip(), "label": str(label or identifier)[:120],
            "description": str(description or "")[:400]}


def codex_models(codex_bin: str, codex_home: Path | None, *, compatibility_catalog: Path | None = None) -> list[dict[str, str]]:
    executable = shutil.which(codex_bin)
    if not executable:
        raise ValueError("Codex host unavailable")
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str((codex_home or Path(os.environ.get("CODEX_HOME") or "~/.codex")).expanduser().resolve())
    command = [executable, "app-server"]
    if compatibility_catalog is not None:
        command.extend(["-c", "model_catalog_json=" + json.dumps(str(compatibility_catalog))])
    command.extend(["--listen", "stdio://"])
    process = subprocess.Popen(command, cwd=str(Path.home()), env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1, start_new_session=os.name == "posix")
    messages: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True)
    reader.start()
    session = CodexChatAgentSession(process=process, messages=messages, thread_id="", work_dir=Path.home(),
                                   response_timeout_sec=10)
    deadline = time.monotonic() + 15
    try:
        session._request("initialize", {"clientInfo": {"name": "loopx_chat_models", "version": "0.1.0"},
                                       "capabilities": {"experimentalApi": True}})
        session._notify("initialized", {})
        models: dict[str, dict[str, str]] = {}
        cursor = None
        observed = set()
        for _ in range(20):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("model/list deadline exceeded")
            session.response_timeout_sec = min(10, remaining)
            payload = session._request("model/list", {"limit": 100, "includeHidden": False,
                                                       **({"cursor": cursor} if cursor else {})})
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise ValueError("invalid model/list response")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid model/list row")
                item = _model(row.get("model") or row.get("id"), row.get("displayName"), row.get("description"))
                models[item["id"]] = item
            cursor = payload.get("nextCursor")
            if not cursor:
                return list(models.values())
            if not isinstance(cursor, str) or cursor in observed:
                raise ValueError("invalid model/list cursor")
            observed.add(cursor)
        raise ValueError("model/list pagination exceeded")
    finally:
        terminate_process_tree(process, grace_seconds=0.1)
        reader.join(timeout=1)


def claude_models() -> list[dict[str, str]]:
    # This is the native host's declared picker, including enterprise model
    # aliases. A process setting pins the same directory used by that host.
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    payload = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    picker = payload.get("modelPicker") if isinstance(payload, dict) else None
    options = picker.get("options") if isinstance(picker, dict) else None
    if not isinstance(options, list) or not options:
        raise ValueError("Native host has no configured model picker")
    models = {}
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("invalid native model picker")
        item = _model(option.get("model"), option.get("label"), option.get("description"))
        models[item["id"]] = item
    return list(models.values())


def chat_model_catalog(controller, endpoint: str) -> dict[str, Any]:
    payload = {"ok": True, "endpoint_id": endpoint, "models": [], "available": False,
               "source": "", "observed_at": utc_now(), "compatibility_applied": False,
               "unavailable_reason": None}
    try:
        if endpoint == "codex":
            payload["source"] = "Codex app-server model/list"
            try:
                models = codex_models(controller.codex_bin, controller.codex_home)
            except _LegacyModelCatalogSchemaError:
                with _current_builtin_model_catalog(controller.codex_bin) as catalog:
                    models = codex_models(controller.codex_bin, controller.codex_home, compatibility_catalog=catalog)
                payload["compatibility_applied"] = True
        elif endpoint == "claude-code":
            payload["source"] = "Claude host model picker configuration"
            models = claude_models()
        else:
            raise ValueError("Endpoint does not expose a model catalog")
        if not models:
            raise ValueError("Host catalog is empty")
        payload.update(models=models, available=True)
    except Exception:
        # Provider errors can include private paths or credential payloads.
        payload["unavailable_reason"] = "The selected host's model catalog could not be read. You can still enter an explicit model."
    return payload
