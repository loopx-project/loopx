from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx_ark_turn.config import AdapterError, Config
from loopx_ark_turn.host import config_digest
from loopx_ark_turn.receipt import Receipt, Stage


def argv(tmp_path: Path) -> list[str]:
    return [sys.executable, "-m", "loopx_ark_turn.cli", "--model", "public-model",
            "--environment-id", "env-fixture", "--workspace", str(tmp_path / "work"),
            "--state-dir", str(tmp_path / "receipts")]


def test_doctor_and_receipt_readback_need_no_provider_credential(tmp_path, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    (tmp_path / "work").mkdir()
    command = argv(tmp_path)
    doctor = subprocess.run([*command, "--doctor"], capture_output=True, text=True, check=True)
    assert json.loads(doctor.stdout)["network_checked"] is False
    cfg = Config("public-model", "env-fixture", tmp_path / "work", tmp_path / "receipts")
    receipt = Receipt(cfg.state_dir, "sha256:" + "a" * 64)
    receipt.load("fixture-binding")
    receipt.update(provider_config_digest=config_digest(cfg))
    readback = subprocess.run([*command, "--inspect-turn-key", "sha256:" + "a" * 64], capture_output=True, text=True, check=True)
    assert json.loads(readback.stdout)["stage"] == "prepared"
    receipt.update(provider_config_digest=config_digest(replace(cfg, model="changed")))
    rejected = subprocess.run([*command, "--inspect-turn-key", "sha256:" + "a" * 64], capture_output=True, text=True)
    assert rejected.returncode == 1
    assert "configuration_mismatch" in rejected.stderr


def test_receipt_rejects_skipped_transition_and_unknown_persisted_state(tmp_path):
    receipt = Receipt(tmp_path, "fixture")
    receipt.load("binding")
    with pytest.raises(AdapterError, match="transition_invalid"):
        receipt.update(stage=Stage.FINISHED)
    receipt.update(stage=Stage.CREATING_AGENT)
    receipt.data["stage"] = "accepted_work"  # Provider-local state cannot invent acceptance.
    receipt.save()
    with pytest.raises(AdapterError, match="state_invalid"):
        receipt.read()


def test_file_profile_preserves_inline_config_and_rejects_ambiguous_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"model": "public-model", "environment_id": "env-fixture",
        "workspace": str(work), "state_dir": str(tmp_path / "receipts")}))
    command = [sys.executable, "-m", "loopx_ark_turn.cli", "--config", str(profile)]
    monkeypatch.setenv("ARK_BASE_URL", "https://example.invalid/api/v3")
    file_result = subprocess.run([*command, "--doctor"], capture_output=True, text=True, check=True)
    inline_result = subprocess.run([*argv(tmp_path), "--doctor"], capture_output=True, text=True, check=True)
    assert json.loads(file_result.stdout) == json.loads(inline_result.stdout)
    bad = subprocess.run([*command, "--model", "different", "--doctor"], capture_output=True, text=True)
    assert bad.returncode == 1 and "exclusive" in bad.stderr
    profile.write_text(json.dumps({"model": "public-model", "environment_id": "env-fixture",
        "workspace": str(work), "state_dir": str(tmp_path / "receipts"), "timeout_seconds": True}))
    bad = subprocess.run([*command, "--doctor"], capture_output=True, text=True)
    assert bad.returncode == 1 and "timeouts" in bad.stderr


def test_sandbox_flag_and_file_profile_agree_without_claiming_live_enforcement(tmp_path, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    (tmp_path / "work").mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"model": "public-model", "environment_id": "env-fixture",
        "workspace": str(tmp_path / "work"), "state_dir": str(tmp_path / "receipts"), "sandbox_builtins": True}))
    command = [sys.executable, "-m", "loopx_ark_turn.cli", "--config", str(profile)]
    file_result = subprocess.run([*command, "--doctor"], capture_output=True, text=True, check=True)
    inline_result = subprocess.run([*argv(tmp_path), "--sandbox-builtins", "--doctor"], capture_output=True, text=True, check=True)
    data = json.loads(file_result.stdout)
    assert data == json.loads(inline_result.stdout)
    assert data["sandbox_builtins"] is True and data["sandbox_enforcement_verified"] is False
    bad = subprocess.run([*command, "--sandbox-builtins", "--doctor"], capture_output=True, text=True)
    assert bad.returncode == 1 and "exclusive" in bad.stderr
    raw = json.loads(profile.read_text()) | {"sandbox_builtins": "true"}
    profile.write_text(json.dumps(raw))
    bad = subprocess.run([*command, "--doctor"], capture_output=True, text=True)
    assert bad.returncode == 1 and "must_be_boolean" in bad.stderr
