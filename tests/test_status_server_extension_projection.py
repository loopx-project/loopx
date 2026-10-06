from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
from threading import Thread
from typing import Iterator
import urllib.error
import urllib.parse
import urllib.request

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

from loopx.extensions.presentation import publish_extension_projection
from loopx.extensions.runtime import default_extension_state_file, install_extension
from loopx.status_server import (
    DEFAULT_CONFIGURE_GOAL_APPLY_PATH,
    DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH,
    DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH,
    DEFAULT_EXTENSION_PROJECTION_PATH,
    DEFAULT_REWARD_APPEND_PATH,
    DEFAULT_REWARD_DRY_RUN_PATH,
    DEFAULT_STATUS_PATH,
    StatusHTTPServer,
    StatusRequestHandler,
)


def _provider(path: Path) -> Path:
    response = {
        "schema_version": "synthetic_research_packet_v0",
        "presentation_projection": {
            "schema_version": "extension_presentation_projection_v0",
            "surface_id": "investment-research",
            "goal_id": "synthetic-research-goal",
            "generated_at": "2026-01-15T12:00:00+00:00",
            "review_due_at": "2099-02-15T12:00:00+00:00",
            "lineage": {
                "source_id": "synthetic-research-2026-01-15",
                "version": 1,
                "row_lifecycle": "active",
                "supersedes": [],
                "superseded_by": None,
            },
            "view_schema": "synthetic_dashboard_v0",
            "view": {
                "headline": "Synthetic projection served over loopback",
            },
        },
    }
    path.write_text(
        f"""#!{sys.executable}
import json
import sys

if "--doctor" in sys.argv:
    raise SystemExit(0)

json.load(sys.stdin)
json.dump({response!r}, sys.stdout)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _manifest(
    path: Path,
    *,
    entrypoint: Path,
    visibility: str = "public-safe",
) -> Path:
    path.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "test-research-extension"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = []

[runtime]
protocol = "test_research_extension_v0"
entrypoint = {json.dumps(str(entrypoint))}
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 5

[[presentation_surfaces]]
id = "investment-research"
kind = "synthetic_dashboard"
title = "Investment Research"
view_schema = "synthetic_dashboard_v0"
view_validator = "loopx.extensions.presentation:validate_opaque_presentation_view"
visibility = "{visibility}"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
        encoding="utf-8",
    )
    return path


def _request_json(
    url: str,
    *,
    origin: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Origin": origin} if origin else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


@contextmanager
def _published_projection_server(
    tmp_path: Path,
    *,
    visibility: str = "public-safe",
    chat: bool = False,
) -> Iterator[tuple[str, dict[str, object], dict[str, object]]]:
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"common_runtime_root": "runtime", "goals": []}) + "\n",
        encoding="utf-8",
    )
    state_file = default_extension_state_file(runtime_root)
    installed = install_extension(
        _manifest(
            tmp_path / "extension.toml",
            entrypoint=_provider(tmp_path / "provider"),
            visibility=visibility,
        ),
        state_file=state_file,
        execute=True,
    )
    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )

    server = (ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler) if chat
              else StatusHTTPServer(("127.0.0.1", 0), StatusRequestHandler))
    server.registry_path = registry
    server.runtime_root_override = None
    server.runtime_root = runtime_root
    server.selected_goal_id = None
    server.scan_roots = [tmp_path]
    server.limit = 5
    server.status_path = DEFAULT_STATUS_PATH
    server.reward_dry_run_path = DEFAULT_REWARD_DRY_RUN_PATH
    server.reward_append_path = DEFAULT_REWARD_APPEND_PATH
    server.reward_write_enabled = False
    server.configure_goal_dry_run_path = DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH
    server.configure_goal_apply_path = DEFAULT_CONFIGURE_GOAL_APPLY_PATH
    server.control_plane_write_enabled = False
    server.verbose = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", installed, receipt
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("chat", [False, True])
def test_status_server_advertises_and_serves_projection_by_exact_ref(
    tmp_path: Path,
    chat: bool,
) -> None:
    with _published_projection_server(tmp_path, chat=chat) as (base_url, installed, receipt):
        status, capabilities = _request_json(f"{base_url}/status.json" if chat else f"{base_url}/")
        if chat:
            capabilities = capabilities["local_dashboard_api"]

        assert status == 200
        assert capabilities["presentation_surfaces_url"] == (
            DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH
        )
        assert capabilities["presentation_detail_url"] == DEFAULT_EXTENSION_PROJECTION_PATH
        status, surfaces_payload = _request_json(
            f"{base_url}{DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH}",
            origin="http://127.0.0.1:5173",
        )
        assert status == 200
        surfaces = surfaces_payload["presentation_surfaces"]
        assert isinstance(surfaces, dict)
        assert surfaces["ready_count"] == 1
        assert surfaces["items"][0]["state"] == "ready"
        status, status_payload = _request_json(
            f"{base_url}{DEFAULT_STATUS_PATH}",
            origin="http://127.0.0.1:5173",
        )
        assert status == 200
        assert "presentation_surfaces" not in status_payload
        query = urllib.parse.urlencode(
            {
                "extension_id": "test-research-extension",
                "surface_id": "investment-research",
                "extension_revision": installed["revision"],
                "payload_sha256": receipt["payload_sha256"],
            }
        )
        status, payload = _request_json(
            f"{base_url}{DEFAULT_EXTENSION_PROJECTION_PATH}?{query}",
            origin="http://127.0.0.1:5173",
        )

    assert status == 200
    assert payload["ok"] is True
    projection = payload["projection"]
    assert isinstance(projection, dict)
    assert projection["payload_sha256"] == receipt["payload_sha256"]
    assert projection["view"] == {
        "headline": "Synthetic projection served over loopback",
    }


@pytest.mark.parametrize("chat", [False, True])
def test_status_server_projection_route_rejects_non_loopback_origin(
    tmp_path: Path,
    chat: bool,
) -> None:
    with _published_projection_server(tmp_path, chat=chat) as (base_url, installed, receipt):
        query = urllib.parse.urlencode(
            {
                "extension_id": "test-research-extension",
                "surface_id": "investment-research",
                "extension_revision": installed["revision"],
                "payload_sha256": receipt["payload_sha256"],
            }
        )
        status, payload = _request_json(
            f"{base_url}{DEFAULT_EXTENSION_PROJECTION_PATH}?{query}",
            origin="https://public.example",
        )

    assert status == 403
    assert payload == {
        "ok": False,
        "error": "extension projection reads only accept loopback browser origins",
    }


@pytest.mark.parametrize("chat", [False, True])
def test_status_server_projection_route_rejects_owner_only_surface(
    tmp_path: Path,
    chat: bool,
) -> None:
    with _published_projection_server(
        tmp_path,
        visibility="owner-only",
        chat=chat,
    ) as (base_url, installed, receipt):
        query = urllib.parse.urlencode(
            {
                "extension_id": "test-research-extension",
                "surface_id": "investment-research",
                "extension_revision": installed["revision"],
                "payload_sha256": receipt["payload_sha256"],
            }
        )
        status, payload = _request_json(
            f"{base_url}{DEFAULT_EXTENSION_PROJECTION_PATH}?{query}",
            origin="http://127.0.0.1:5173",
        )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"] == (
        "owner-only projection reads require an authenticated audience"
    )


@pytest.mark.parametrize("chat", [False, True])
def test_status_server_projection_route_requires_complete_detail_ref(
    tmp_path: Path,
    chat: bool,
) -> None:
    with _published_projection_server(tmp_path, chat=chat) as (base_url, _installed, _receipt):
        status, payload = _request_json(
            f"{base_url}{DEFAULT_EXTENSION_PROJECTION_PATH}?extension_id=test-research-extension",
            origin="http://localhost:5173",
        )

    assert status == 400
    assert payload["ok"] is False
    assert "payload_sha256 are required" in str(payload["error"])
