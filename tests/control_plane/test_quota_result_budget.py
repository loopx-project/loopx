"""Bounded settlement presentation preserves the original result and recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.cli_commands.quota_capture import budgeted_settlement_result, settlement_capture_failure_view
from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError


def _read(command: str) -> tuple[int, dict]:
    words = shlex.split(command)
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
        *words[words.index("quota"):]], capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parents[2], env={**os.environ, "LOOPX_USAGE_PING": "0"})
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("ok", [False, True])
def test_small_result_stays_complete_without_capture(tmp_path, ok):
    original = {"ok": ok, "appended": ok, "after": None, "unknown": [False, 0, "", None]}
    assert budgeted_settlement_result(original, runtime_root=tmp_path, output_bytes=8192) == original
    assert not (tmp_path / "context-delivery").exists()


def test_large_diagnostic_keeps_controls_and_immutable_complete_source(tmp_path):
    original = {"ok": False, "mode": "spend-slot", "appended": False, "registry_mutated": False,
        "workspace_guard": {"action": "return_to_delivery_worktree", "blocks_delivery": True},
        "before": {"text": "complete source 漢字 " * 10000, "last": "Original stop remains."},
        "after": None, "unknown": {"value": 2**61 + 1, "empty": []}}
    view = budgeted_settlement_result(original, runtime_root=tmp_path, output_bytes=8192)
    for field in ("ok", "mode", "appended", "registry_mutated", "workspace_guard", "unknown"):
        assert view[field] == original[field]
    assert len(json.dumps(view, ensure_ascii=True, indent=2).encode()) + 1 <= 8192
    reference = Path(view["result_details"]["reference_file"])
    captured = Path(json.loads(reference.read_text())["path"])
    assert json.loads(captured.read_text()) == original
    assert captured.stat().st_mode & 0o077 == 0
    assert reference.stat().st_mode & 0o077 == 0
    rc, page = _read(view["result_details"]["command"] + " --select /unknown --select /workspace_guard")
    assert rc == 0
    selected = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
    assert selected == {"/unknown": original["unknown"], "/workspace_guard": original["workspace_guard"]}
    captured.write_text(json.dumps({**original, "before": {"text": "replacement"}}))
    rc, rejected = _read(view["result_details"]["command"])
    assert rc == 1 and rejected["ok"] is False
    captured.unlink()
    rc, missing = _read(view["result_details"]["command"])
    assert rc == 1 and missing["ok"] is False


def test_large_unknown_control_is_reachable_without_truncation(tmp_path):
    original = {"ok": False, "future/control~v": {"stop": "must remain " * 4000 + "FINAL STOP"},
        "instruction": "Preserve the original control instruction.", "result_details": {"future": True},
        "before": {"history": "diagnostic " * 10000}}
    view = budgeted_settlement_result(original, runtime_root=tmp_path, output_bytes=8192)
    assert "command" in view
    parts = []
    command = view["command"]
    while command:
        rc, page = _read(command)
        assert rc == 0
        parts.append(page["content"])
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 8192
        command = page.get("next_command")
    selected = {row["pointer"]: row["value"] for row in json.loads("".join(parts))["selections"]}
    assert selected["/future~1control~0v"] == original["future/control~v"]
    assert selected["/instruction"] == original["instruction"]
    assert selected["/result_details"] == original["result_details"]


@pytest.mark.parametrize("symlink", [False, True])
def test_result_capture_rejects_public_or_symlink_root(tmp_path, symlink):
    original = {"ok": False, "before": {"history": "complete " * 20000}}
    public = tmp_path / "public"
    public.mkdir()
    if symlink:
        public.chmod(0o700)
        (tmp_path / "context-delivery").symlink_to(public, target_is_directory=True)
    else:
        public.rename(tmp_path / "context-delivery")
    with pytest.raises(QuotaDecisionTransportError):
        budgeted_settlement_result(original, runtime_root=tmp_path, output_bytes=8192)
    target = public if symlink else tmp_path / "context-delivery"
    assert not list(target.iterdir())
    assert target.stat().st_mode & 0o777 == (0o700 if symlink else 0o755)


@pytest.mark.parametrize("value", ["reader.json", r"C:\private\result.reader.json"])
@pytest.mark.skipif(os.name != "posix", reason="Simulates a missing POSIX identity provider")
def test_reference_without_posix_identity_fails_structurally_before_open(monkeypatch, value):
    from loopx.control_plane.turn_driver import decision_capture

    monkeypatch.delattr(decision_capture.os, "getuid", raising=False)
    with pytest.raises(ValueError, match="private.*unsupported"):
        decision_capture.read_context_read_reference(value)


@pytest.mark.parametrize("replayed", [False, True])
def test_failed_capture_omits_large_durable_event_and_preserves_receipt(tmp_path, replayed):
    event = {"before": "漢字" * 2000, "after": {"spent_slots": 1}}
    artifact = tmp_path / "spend.json"
    artifact.write_text(json.dumps({"quota_event": event}), encoding="utf-8")
    identity = {"turn_instance_id": "original-turn"}
    receipts = {"ok": True, "receipts": [{"step_kind": "quota_spend"}]}
    payload = {"ok": True, "appended": not replayed, "idempotent_replay": replayed, "quota_event": event,
        "before": event["before"], "after": event["after"], "json_path": str(artifact),
        "settlement_identity": identity, "settlement_result": receipts, "future_control": {"keep": True}}
    error = QuotaDecisionTransportError("Private capture directory failed validation")
    view = settlement_capture_failure_view(payload, output_bytes=8192, error=error)
    assert len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode()) + len(os.linesep) <= 8192
    assert view["settlement_result"] == receipts and view["settlement_identity"] == identity
    assert view["idempotent_replay"] is replayed and view["appended"] is not replayed
    assert view["future_control"] == payload["future_control"]
    assert "quota_event" not in view
    assert view["result_delivery_error"]["omitted_diagnostics"] == ["before", "after", "quota_event"]
    assert json.loads(Path(view["json_path"]).read_text(encoding="utf-8"))["quota_event"] == payload["quota_event"]


@pytest.mark.skipif(os.name != "posix", reason="Simulates a missing POSIX identity provider")
def test_large_result_without_getuid_preserves_private_boundary(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver import decision_capture

    monkeypatch.delattr(decision_capture.os, "getuid", raising=False)
    with pytest.raises(QuotaDecisionTransportError, match="private.*unsupported"):
        budgeted_settlement_result({"ok": True, "before": "large" * 20000},
            runtime_root=tmp_path, output_bytes=8192)
    assert not (tmp_path / "context-delivery").exists()


def test_bounded_spend_without_getuid_rejects_before_effect(tmp_path, monkeypatch):
    from loopx.cli import main
    from loopx.cli_commands import quota
    from loopx.control_plane.turn_driver import decision_capture

    monkeypatch.delattr(decision_capture.os, "getuid", raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported private capture reached effect preparation")
    monkeypatch.setattr(quota, "_prepare_quota_command_execution", forbidden)
    assert main(["--format", "json", "--registry", str(tmp_path / "unused.json"),
        "quota", "spend-slot", "--goal-id", "fixture", "--agent-id", "fixture",
        "--turn-instance-id", "same-turn", "--execute", "--max-page-output-bytes", "64000"]) == 1
