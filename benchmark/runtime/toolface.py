"""Qualify a pinned Codex binary + model catalog at the first-request boundary.

A staged catalog file and a rendered ``config.toml`` prove configuration, not
tools: the tool surface is decided inside the Codex client per binary version,
model slug and catalog entry. This module captures the first real request the
client would send, with inference deliberately stopped by a local HTTP stub
that answers 400, so qualification needs no network, credentials or model
spend. Run it once per pinned binary/catalog pair before a study and keep the
receipt next to the study configuration.

    python3 -m benchmark.runtime.toolface --codex-bin /usr/local/bin/codex \
        --model gpt-5.6-sol --model-catalog model-catalog.json \
        --web-search live --expect web_search --expect apply_patch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .codex import Execution, prepare_codex_home

_STOP_BODY = json.dumps({"error": {
    "message": "toolface qualification stub: capture complete, inference deliberately stopped",
}}).encode()


def _request_tools(body: dict) -> list[str]:
    """Name every tool the request offers, wherever the client put it."""
    names = []
    for tool in body.get("tools") or []:
        names.append(tool.get("name") or tool.get("type") or "unnamed")
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            for namespace in item.get("tools") or []:
                for tool in namespace.get("tools") or []:
                    names.append(f"{namespace.get('name', 'ns')}.{tool.get('name', 'unnamed')}")
    return names


def capture_first_request(
    codex_bin: str | Path,
    *,
    model: str,
    effort: str = "low",
    web_search: str = "disabled",
    model_catalog: Path | None = None,
    timeout_sec: float = 120.0,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Run ``codex exec`` against a capturing stub; return the first request."""
    captured: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence the stub
            return

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not captured:
                try:
                    captured["body"] = json.loads(raw)
                except json.JSONDecodeError:
                    captured["body"] = {}
                captured["path"] = self.path
                done.set()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_STOP_BODY)))
            self.end_headers()
            self.wfile.write(_STOP_BODY)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="toolface-") as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            home = Path(temp) / "home"
            prepare_codex_home(
                home,
                execution=Execution(mode="plain", sandbox="read-only"),
                workspace=workspace,
                model=model,
                effort=effort,
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key="toolface-qualification",
                wire_api="responses",
                skills=None,
                web_search=web_search,
                model_catalog=model_catalog,
            )
            stderr_path = Path(temp) / "codex-stderr.log"
            with stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    [str(codex_bin), "exec", "--json", "--skip-git-repo-check",
                     "--cd", str(workspace), "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=stderr, env={
                        "CODEX_HOME": str(home), "HOME": temp,
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "OPENAI_API_KEY": "toolface-qualification",
                        **(extra_env or {}),
                    },
                )
                try:
                    process.stdin.write(b"Reply with the word OK.\n")
                    process.stdin.close()
                    deadline = time.monotonic() + timeout_sec
                    while not done.is_set():
                        if process.poll() is not None:
                            # A client can reject its configuration (for
                            # example a catalog entry it cannot parse) and die
                            # before any HTTP request; surface its own words
                            # instead of a silent qualification timeout.
                            done.wait(0.5)
                            if done.is_set():
                                break
                            tail = stderr_path.read_text(errors="replace")[-800:].strip()
                            raise RuntimeError(
                                "client exited before sending a request "
                                f"(exit code {process.returncode}): {tail or 'no stderr'}"
                            )
                        if time.monotonic() > deadline:
                            tail = stderr_path.read_text(errors="replace")[-800:].strip()
                            raise TimeoutError(
                                "Codex sent no request within the qualification "
                                f"window; stderr tail: {tail or 'empty'}"
                            )
                        done.wait(0.2)
                finally:
                    process.kill()
                    process.wait()
    finally:
        server.shutdown()
        thread.join()
    version = subprocess.run(
        [str(codex_bin), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "binary": str(codex_bin),
        "binary_version": version,
        "model": model,
        "web_search": web_search,
        "model_catalog": str(model_catalog) if model_catalog else None,
        "catalog_sha256": hashlib.sha256(model_catalog.read_bytes()).hexdigest()
        if model_catalog else None,
        "request_path": captured.get("path"),
        "tools": sorted(_request_tools(captured.get("body") or {})),
    }


def qualify_toolface(expected: set[str], **capture_kwargs) -> dict:
    """Capture the first request and require the expected tool names."""
    receipt = capture_first_request(**capture_kwargs)
    tools = set(receipt["tools"])
    if not tools:
        raise RuntimeError(
            "qualification failed: first request carries an empty tool list "
            f"({receipt['binary_version'] or 'unknown binary'})"
        )
    missing = {name for name in expected
               if not any(name == t or t.endswith(f".{name}") for t in tools)}
    if missing:
        raise RuntimeError(
            f"qualification failed: expected tools missing {sorted(missing)}; "
            f"observed {receipt['tools']}"
        )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--web-search", default="disabled")
    parser.add_argument("--model-catalog", type=Path, default=None)
    parser.add_argument("--expect", action="append", default=[],
                        help="tool name that must appear in the first request")
    parser.add_argument("--receipt", type=Path, default=None,
                        help="write the qualification receipt to this file")
    args = parser.parse_args()
    receipt = qualify_toolface(
        set(args.expect),
        codex_bin=args.codex_bin, model=args.model, effort=args.effort,
        web_search=args.web_search, model_catalog=args.model_catalog,
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.receipt:
        args.receipt.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
