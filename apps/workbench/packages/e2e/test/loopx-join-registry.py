"""Join through daemon HTTP using real File/SQLite authority and Core registry codec.

Run after npm ci in apps/workbench: npm run test:loopx -w @calli/e2e.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "tests/control_plane"))
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.projects.registry_codec import (
    CURRENT_WRITER_PROTOCOL, _ProjectRegistryFormat, _encode_document,
)


@pytest.fixture(params=["file", "sqlite"], scope="module")
def authority(tmp_path_factory, request):
    # Both encodings use exactly the same payload, paths and authority store.
    root = tmp_path_factory.mktemp(f"join-{request.param}")
    with pytest.MonkeyPatch.context() as monkeypatch:
        isolate_sqlite_runtime(root, monkeypatch)
        monkeypatch.setenv("LOOPX_USAGE_PING", "0")
        monkeypatch.setenv("LOOPX_HOME", str(root / "home"))
        registry, _, _ = promoted_create_fixture(root, provider=request.param)
        payload = json.loads(registry.read_text())
        payload["goals"][0].update(domain="engineering", status="active", adapter={"kind": "codex"})
        yield registry, payload


def write_registry(registry, payload, encoding):
    registry.write_bytes(_encode_document(
        payload, format=(_ProjectRegistryFormat.STRICT_ENVELOPE_V1 if encoding == "v1"
                         else _ProjectRegistryFormat.LEGACY_OBJECT),
        minimum_writer_protocol=CURRENT_WRITER_PROTOCOL if encoding == "v1" else None,
    ))


def join_http(tmp_path, registry, allowed, requests):
    result = subprocess.run(
        ["node", "--import", "tsx", str(Path(__file__).with_name("loopx-join-http.mjs"))],
        cwd=ROOT / "apps/workbench", input=json.dumps({
            "dataDir": str(tmp_path / "daemon"), "allowed": [str(p) for p in allowed],
            "cli": {"command": [sys.executable, "-m", "loopx.cli"],
                    "cwd": str(ROOT), "registry": str(registry)},
            "requests": requests,
        }), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("encoding", ["object", "v1"])
def test_join_registry_encodings_preserve_root_and_read_only(authority, tmp_path, encoding):
    registry, payload = authority
    write_registry(registry, payload, encoding)
    project = Path(payload["goals"][0]["repo"])
    # Establish that Core accepts the same Goal before invoking daemon HTTP.
    status = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--format", "json", "status", "--goal-id", "goal-a", "--limit", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert status.returncode == 0, status.stderr or status.stdout
    assert json.loads(status.stdout)["run_history"]["goals"][0]["registry_member"] is True
    base = {"goalId": "goal-a", "agent": "codex"}
    result = join_http(tmp_path, registry, [project.parent, tmp_path], [
        {**base, "permission": "workspace_auto"}, {**base, "cwd": str(tmp_path)}, base,
    ])
    denied, mismatch, joined = result["responses"]
    assert joined["status"] == 200, joined
    assert (denied["status"], denied["body"]["code"]) == (409, "permission_mismatch")
    assert (mismatch["status"], mismatch["body"]["code"]) == (409, "workspace_mismatch")
    assert joined["body"]["workspace"] == str(project.resolve())
    assert joined["body"]["permission"] == "read_only"
    assert len(result["threads"]) == 1
    assert result["threads"][0]["cwd"] == str(project.resolve())
    assert result["threads"][0]["permissionMode"] == "ask"
    assert result["bindings"][0][1]["permission"] == "read_only"
    assert result["bindings"][0][1]["workspace"] == str(project.resolve())


@pytest.mark.parametrize("encoding", ["object", "v1"])
def test_join_rejects_root_outside_allowed_directories(authority, tmp_path, encoding):
    registry, payload = authority
    write_registry(registry, payload, encoding)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    result = join_http(tmp_path, registry, [allowed], [{"goalId": "goal-a", "agent": "codex"}])
    response = result["responses"][0]
    assert (response["status"], response["body"]["code"]) == (400, "workspace_not_allowed")
    assert result["threads"] == []
    assert result["bindings"] == []


def test_join_rejects_corrupt_strict_digest(authority, tmp_path):
    registry, payload = authority
    write_registry(registry, payload, "v1")
    envelope = json.loads(registry.read_text())
    envelope[1]["goals"][0]["repo"] = str(tmp_path)
    registry.write_text(json.dumps(envelope))
    result = join_http(tmp_path, registry, [tmp_path], [{"goalId": "goal-a", "agent": "codex"}])
    response = result["responses"][0]
    assert (response["status"], response["body"]["code"]) == (404, "goal_not_found")
    assert result["threads"] == []
    assert result["bindings"] == []
