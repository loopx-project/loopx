#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


GOAL_ID = "user-todo-review-material-smoke"
LOCAL_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(^|[\s`'\"=:(])(?:/[A-Za-z0-9._-]+(?:/[^\s`'\",)]+)+|[A-Za-z]:[\\/][^\s`'\",)]+)"
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return int(error.code), json.loads(raw)


def wait_for_health(base_url: str) -> None:
    deadline = time.time() + 10
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            status, payload = request_json(f"{base_url}/healthz")
            if status == 200 and payload.get("ok") is True:
                return
        except Exception as exc:  # noqa: BLE001 - preserve startup diagnostics.
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def start_server(repo_root: Path, registry: Path, runtime_root: Path, port: int) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime_root),
        "serve-status",
        "--port",
        str(port),
        "--scan-root",
        str(repo_root),
    ]
    server = subprocess.Popen(
        command,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wait_for_health(f"http://127.0.0.1:{port}")
    return server


def stop_server(server: subprocess.Popen[str]) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def iter_strings(value: Any, *, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        strings: list[tuple[str, str]] = []
        for key, child in value.items():
            strings.extend(iter_strings(child, path=f"{path}.{key}"))
        return strings
    if isinstance(value, list):
        strings: list[tuple[str, str]] = []
        for index, child in enumerate(value):
            strings.extend(iter_strings(child, path=f"{path}[{index}]"))
        return strings
    return []


def assert_no_local_paths(value: Any) -> None:
    for path, text in iter_strings(value):
        match = LOCAL_ABSOLUTE_PATH_PATTERN.search(text)
        if match:
            raise AssertionError({path: text, "match": match.group(0)})


def assert_rejects_local_path(value: Any) -> None:
    try:
        assert_no_local_paths(value)
    except AssertionError:
        return
    raise AssertionError(f"expected local path rejection: {value}")


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    runtime_root = root / "runtime"
    project = root / "project"
    docs = project / "docs"
    docs.mkdir(parents=True)
    review_doc = docs / "review.md"
    review_doc.write_text("# Review Packet\n\nRead this before approving the owner gate.\n", encoding="utf-8")
    (root / "outside.md").write_text("# Outside\n", encoding="utf-8")
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                "- [ ] Read `docs/review.md` before approving the owner gate.",
                "",
                "## Next Action",
                "",
                "- Keep the fixture connected and readable.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "domain": "todo-review-material",
                        "status": "connected-read-only",
                        "adapter": {"kind": "generic_project_goal_v0", "status": "connected-read-only"},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry, runtime_root, review_doc


def review_material_url(base_url: str, *, path: str) -> str:
    query = urllib.parse.urlencode({"goal_id": GOAL_ID, "path": path})
    return f"{base_url}/review-material?{query}"


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from loopx.presentation.renderers.status_markdown import (  # noqa: PLC0415
        render_status_markdown,
    )
    from loopx.status import collect_status  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        assert_no_local_paths({"relative": ".codex/goals/example.md", "url": "https://example.test/docs/page"})
        assert_rejects_local_path({"absolute": str(root / "project" / "docs" / "review.md")})
        registry, runtime_root, review_doc = write_fixture(root)
        payload = collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime_root),
            limit=5,
        )
        assert_no_local_paths(payload["attention_queue"])
        item = next(item for item in payload["attention_queue"]["items"] if item["goal_id"] == GOAL_ID)
        todo = item["user_todos"]["items"][0]
        material = todo["review_materials"][0]
        assert material["label"] == "review.md", material
        assert material["path"] == "docs/review.md", material
        assert material["exists"] is True, material
        assert "resolved_path" not in material, material
        assert "resolved_path" not in json.dumps(item, ensure_ascii=False), item
        assert "todo_state_file" not in item, item
        markdown = render_status_markdown(payload)
        assert "review_material: review.md exists=True" in markdown, markdown
        assert "resolved_path" not in markdown, markdown
        assert "todo_state_file" not in markdown, markdown

        port = free_port()
        server = start_server(repo_root, registry, runtime_root, port)
        base_url = f"http://127.0.0.1:{port}"
        try:
            status, body = request_json(review_material_url(base_url, path="docs/review.md"))
            assert status == 200, body
            assert body["content"] == review_doc.read_text(encoding="utf-8"), body
            assert body["resolved_path"].endswith("docs/review.md"), body

            status, body = request_json(review_material_url(base_url, path=str(root / "outside.md")))
            assert status == 400, body
            assert "outside" in body["error"], body

            status, body = request_json(review_material_url(base_url, path="../outside.md"))
            assert status == 400, body
            assert "outside" in body["error"], body
        finally:
            stop_server(server)

    print("user-todo-review-material-smoke ok")


if __name__ == "__main__":
    main()
