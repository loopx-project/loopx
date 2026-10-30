"""Tuple-returning CLI transport preserves producer JSON, including unknown fields."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing import canary_harness


@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("producer_field", [False, True])
def test_result_transport_does_not_inject_or_overwrite_source_fields(tmp_path, monkeypatch, exit_code, producer_field):
    source = {"number": 1.0, "unknown": {"allowed": False, "evidence": None}}
    if producer_field:
        source["_returncode"] = {"producer_fact": "Preserve this exact value."}
    raw = json.dumps(source, ensure_ascii=False)
    monkeypatch.setattr(canary_harness.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(stdout=raw, stderr="", returncode=exit_code))
    status, observed = canary_harness.run_json_cli_result("probe", registry_path=tmp_path / "absent.json")
    assert status == exit_code
    assert json.dumps(observed, ensure_ascii=False) == raw


@pytest.mark.parametrize("valid_digest", [False, True])
def test_real_capture_cli_returns_the_same_wire_through_the_helper(tmp_path, monkeypatch, valid_digest):
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    capture = tmp_path / "source.json"
    raw = b'{"goal":"Keep the original source and stop before publication.","value":1.0}'
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest() if valid_digest else "0" * 64
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    arguments = ["quota", "context-read", "--capture-file", str(capture), "--capture-sha256", digest]
    direct = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *arguments],
        cwd=tmp_path, capture_output=True, text=True, check=False)
    status, observed = canary_harness.run_json_cli_result(*arguments,
        registry_path=registry, runtime_root=runtime, cwd=tmp_path)
    assert status == direct.returncode == (0 if valid_digest else 1)
    assert observed == json.loads(direct.stdout)
    assert capture.read_bytes() == raw
    assert not registry.exists() and not runtime.exists()
