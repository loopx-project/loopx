from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(
    tmp_path: Path, *args: str, output_format: str | None = "json",
) -> subprocess.CompletedProcess[str]:
    format_args = [] if output_format is None else ["--format", output_format]
    return subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--runtime-root",
         str(tmp_path / "runtime"), "--registry", str(tmp_path / "registry.json"),
         *format_args, "external-evidence", *args],
        cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, check=False, timeout=30,
    )


def _plan(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    return _run(
        tmp_path, "plan", "--objective", "Inspect synthetic evidence",
        "--user-activity", "Choose a provider", "--decision", "Whether to adopt",
        "--evidence-kind", "current_behavior", "--provider-inventory-json", source,
    )


def _inventory() -> dict:
    return {"providers": [{
        "provider_id": "host:synthetic-research", "provider_kind": "method",
        "protocol": "external_evidence_research_v0", "declared": True,
        "installed": True, "enabled": True, "ready": True,
        "unavailable_reason": None,
    }]}


@pytest.mark.parametrize("action", [
    "discover", "plan", "execute", "readback", "receipt", "admit", "retire",
])
def test_source_cli_help_exposes_json_file_contract(
    tmp_path: Path, monkeypatch, action: str,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    result = _run(tmp_path, action, "--help")
    assert result.returncode == 0
    help_text = " ".join(result.stdout.split())
    assert "FILE" in help_text and "Path to" in help_text
    if action in {"discover", "plan"}:
        assert "--provider-inventory-json FILE" in help_text
        assert "providers array" in help_text and "not inline JSON" in help_text


def test_source_cli_file_read_error_is_actionable_without_echoing_input(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    for source in (
        '{"providers": [], "note": "DO_NOT_ECHO_INLINE_INPUT"}',
        str(tmp_path / "missing inventory.json"),
    ):
        result = _plan(tmp_path, source)
        payload = json.loads(result.stdout)
        assert result.returncode == 1 and payload["status"] == "invalid_request"
        assert "readable JSON file path" in payload["error"]
        assert "not inline JSON" in payload["error"]
        assert source not in result.stdout + result.stderr
        assert "Traceback" not in result.stderr


def test_source_cli_default_markdown_keeps_the_json_error_reason(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    args = ("discover", "--provider-inventory-json", str(tmp_path / "missing.json"))
    json_result = _run(tmp_path, *args)
    markdown_result = _run(tmp_path, *args, output_format=None)
    assert json_result.returncode == markdown_result.returncode == 1
    error = json.loads(json_result.stdout)
    assert error["status"] == "invalid_request"
    assert error["error"] in markdown_result.stdout
    assert "invalid_request" in markdown_result.stdout
    assert "Traceback" not in markdown_result.stderr


@pytest.mark.parametrize("content,diagnostic", [
    (b'{"providers":', "valid JSON"),
    (b'\xff\xfeinvalid', "valid JSON"),
    (b"[]", "JSON object"),
    (b'{"providers": {}}', "providers array"),
])
def test_source_cli_distinguishes_file_content_errors_and_recovers(
    tmp_path: Path, monkeypatch, content: bytes, diagnostic: str,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    path = tmp_path / "provider inventory.json"
    path.write_bytes(content)
    rejected = _plan(tmp_path, str(path))
    error = json.loads(rejected.stdout)
    assert rejected.returncode == 1 and error["status"] == "invalid_request"
    assert diagnostic in error["error"]
    assert "readable JSON file path" not in error["error"]
    assert "Traceback" not in rejected.stderr

    path.write_text(json.dumps(_inventory()), encoding="utf-8")
    recovered = _plan(tmp_path, str(path))
    plan = json.loads(recovered.stdout)
    assert recovered.returncode == 0 and plan["status"] == "ready"
    assert plan["selected_provider"]["provider_id"] == "host:synthetic-research"


def test_source_cli_preserves_valid_file_encodings_and_plan_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    plans = []
    for name, encoding in (("providers with spaces.json", "utf-8"), ("{}.json", "utf-16")):
        path = tmp_path / name
        path.write_text(json.dumps(_inventory()), encoding=encoding)
        result = _plan(tmp_path, str(path))
        assert result.returncode == 0
        plans.append(json.loads(result.stdout))
    assert plans[0]["status"] == plans[1]["status"] == "ready"
    assert plans[0]["plan_id"] == plans[1]["plan_id"]
