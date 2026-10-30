"""Synthetic app-server peer for the real OAR transport.

Existing Turn fixtures express their independent artifact/settlement scenarios as
small exec-style programs. Run those programs behind this protocol peer so their
oracles survive the transport migration. Tests of native options read the actual
RPC journal; the compatibility argv is never proof of OAR's wire behavior.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from typing import Any

from tests.control_plane.host_process_fixture import write_python_host_fixture


def write_codex_app_server_fixture(path: Path, source: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    header = (
        f"#!{sys.executable}\n"
        "import sys as _fixture_sys\n"
        "if '--version' in _fixture_sys.argv[1:]:\n"
        "    print('codex-cli 0.165.0'); raise SystemExit(0)\n"
        "if '--help' in _fixture_sys.argv[1:]:\n"
        "    print('app-server --listen stdio://'); raise SystemExit(0)\n"
        "if 'app-server' in _fixture_sys.argv[1:]:\n"
        f"    _fixture_sys.path.insert(0, {str(root)!r})\n"
        "    from tests.control_plane.codex_app_server_fixture import serve\n"
        "    serve(__file__); raise SystemExit(0)\n"
    )
    return write_python_host_fixture(path, header + source)


def serve(program: str) -> None:
    stream = sys.stdout
    write_lock = threading.Lock()
    launch = list(sys.argv[1:])
    native_log = os.environ.get("FAKE_CODEX_RPC_LOG")
    if native_log is None and os.environ.get("FAKE_CODEX_LOG"):
        native_log = os.environ["FAKE_CODEX_LOG"] + ".rpc"

    def log(value: dict[str, Any]) -> None:
        if native_log:
            with Path(native_log).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value) + "\n")

    log({"launch": launch})
    config = {}
    for index, arg in enumerate(launch[:-1]):
        if arg == "-c" and "=" in launch[index + 1]:
            key, value = launch[index + 1].split("=", 1)
            try:
                config[key] = json.loads(value)
            except ValueError:
                config[key] = value
    # The fixture's original native identity remains stable across resumes.
    source = Path(program).read_text(encoding="utf-8")
    identity = re.search(r'["\']thread_id["\']\s*:\s*["\']([^"\']+)', source)
    session_id = identity.group(1) if identity else "session-fixture-0001"
    cwd = str(Path.cwd())
    model: str | None = None
    effort: str | None = None
    opened = "thread/start"
    turn_id = "fixture-turn-1"

    def send(payload: dict[str, Any]) -> None:
        with write_lock:
            stream.write(json.dumps(payload) + "\n")
            stream.flush()

    def event(method: str, params: dict[str, Any]) -> None:
        send({"method": method, "params": {"threadId": session_id, **params}})

    def execute(params: dict[str, Any]) -> None:
        prompt = "\n".join(item["text"] for item in params.get("input", [])
                           if item.get("type") == "text")
        with tempfile.TemporaryDirectory(prefix="codex-native-fixture-") as directory:
            output = Path(directory) / "result.json"
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps(params.get("outputSchema", {})), encoding="utf-8")
            args = ["exec", "--skip-git-repo-check", "-C", cwd]
            if opened == "thread/resume":
                args.append("resume")
            args.extend(["-c", "sandbox_mode=" + json.dumps(config.get("sandbox_mode", "read-only")),
                         "--json", "--output-schema", str(schema), "--output-last-message", str(output)])
            for key, value in config.items():
                if key != "sandbox_mode":
                    args.extend(["-c", key + "=" + json.dumps(value, separators=(",", ":"))])
            if model is not None:
                args.extend(["--model", model])
            if effort is not None:
                args.extend(["-c", "model_reasoning_effort=" + json.dumps(effort)])
            if opened == "thread/resume":
                args.append(session_id)
            args.append("-")
            child = subprocess.Popen([sys.executable, program, *args], cwd=cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
            errors: list[dict[str, Any]] = []

            def read() -> None:
                for line in child.stdout:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("type") == "item.completed":
                        event("item/completed", {"turnId": turn_id, "item": item.get("item")})
                    elif item.get("type") in {"error", "turn.failed", "response.failed"}:
                        native = item.get("error") or item.get("response", {}).get("error") or item
                        # Retain a conflicting outer code too; it must not be
                        # turned into a retry from familiar inner error prose.
                        detail = {"error": native, "willRetry": False}
                        if "code" in item:
                            detail["code"] = item["code"]
                        errors.append(native)
                        event("error", detail)

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            child.stdin.write(prompt)
            child.stdin.close()
            code = child.wait()
            reader.join(timeout=.05)
            if code == 0 and output.is_file():
                text = output.read_text(encoding="utf-8")
                event("item/agentMessage/delta", {"turnId": turn_id, "itemId": "answer", "delta": text})
                event("item/completed", {"turnId": turn_id, "item": {
                    "id": "answer", "type": "agentMessage", "text": text, "phase": "final_answer"}})
                event("turn/completed", {"turn": {"id": turn_id, "status": "completed"}})
            else:
                event("turn/completed", {"turn": {"id": turn_id, "status": "failed",
                    **({"error": errors[-1]} if errors else {})}})

    for line in sys.stdin:
        request = json.loads(line)
        method, params = request.get("method"), request.get("params", {})
        log(request)
        if "id" not in request:
            continue
        result: dict[str, Any] = {}
        if method in {"thread/start", "thread/resume"}:
            if error := os.environ.get("FAKE_CODEX_OPEN_ERROR"):
                send({"id": request["id"], "error": json.loads(error)})
                continue
            opened = method
            cwd = params.get("cwd", cwd)
            model = params.get("model")
            effort = params.get("config", {}).get("model_reasoning_effort")
            result = {"thread": {"id": session_id}, "model": model, "reasoningEffort": effort}
        elif method == "thread/settings/update":
            effort = params["effort"]
        elif method == "turn/start":
            if error := os.environ.get("FAKE_CODEX_TURN_ERROR"):
                send({"id": request["id"], "error": json.loads(error)})
                continue
            result = {"turn": {"id": turn_id, "status": "inProgress"}}
        send({"id": request["id"], "result": result})
        if method == "thread/settings/update":
            event("thread/settings/updated", {"threadSettings": {"effort": effort}})
        elif method == "turn/start":
            event("turn/started", {"turn": {"id": turn_id}})
            threading.Thread(target=execute, args=(params,), daemon=True).start()
        elif method == "turn/interrupt":
            event("turn/completed", {"turn": {"id": turn_id, "status": "interrupted"}})
