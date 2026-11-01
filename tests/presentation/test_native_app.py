"""Packaged Codex profile/launch boundaries, without a personal dotfiles install."""
import importlib
import json
import os
from pathlib import Path
import plistlib
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/loopx-codex-provider-routing/src"))
app = importlib.import_module("loopx_codex_provider_routing.native_app")


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "synthetic home"
    home.mkdir()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_ELECTRON_USER_DATA_PATH", raising=False)
    return home, app.route(home)


def bundle(home):
    path = home / "Applications/Codex.app"
    executable = path / "Contents/MacOS/Codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Codex"}))
    return path


def test_default_profile_discovery_is_read_only_and_does_not_require_dotfiles(profile):
    home, spec = profile
    assert spec["home"] == home / ".codex"
    assert spec["account_store"] == home / ".loopx/codex-native-accounts/vault"
    assert list(home.iterdir()) == []
    app.check_provider(spec)  # A missing config is a native default, not an IO failure.
    assert list(home.iterdir()) == []


def test_existing_profile_reuses_vault_and_locks_without_copying(profile):
    home, _ = profile
    path = home / ".config/personal/apps.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "apps": {"gpt": {
        "home": "~/.codex-gpt", "frontend": "~/Library/Synthetic GPT", "app": ""}}}))
    spec = app.route(home)
    assert spec["home"] == home / ".codex-gpt"
    assert spec["lock_root"] == home / ".local/state/personal-apps"
    assert spec["account_store"] == spec["lock_root"] / "gpt-accounts"
    assert not spec["account_store"].exists()
    path.chmod(0o666)
    with pytest.raises(ValueError, match="not trusted"):
        app.route(home)


def test_profile_rejects_overlapping_and_symlinked_state(profile, monkeypatch):
    home, _ = profile
    for frontend in (home / ".codex", home / ".codex/child"):
        monkeypatch.setenv("CODEX_ELECTRON_USER_DATA_PATH", str(frontend))
        with pytest.raises(ValueError, match="separate"):
            app.route(home)
    monkeypatch.delenv("CODEX_ELECTRON_USER_DATA_PATH")
    (home / ".codex").symlink_to(home / "another-home")
    with pytest.raises(ValueError, match="symlinks"):
        app.route(home)


@pytest.mark.parametrize("occupied", [True, False])
def test_launch_rechecks_caller_admission_under_same_lock(profile, monkeypatch, occupied):
    home, spec = profile
    spec["app"] = str(bundle(home))
    preflight = Mock()
    monkeypatch.setattr(app, "running", Mock(side_effect=[42, 42] if occupied else [None, None, 42]))
    run = Mock()
    monkeypatch.setattr(app.subprocess, "run", run)
    child = Mock(pid=42)
    child.poll.return_value = None
    launch = Mock(return_value=child)
    monkeypatch.setattr(app.subprocess, "Popen", launch)
    result = app.open_codex(home, spec, preflight=preflight)
    assert preflight.call_count == 2
    assert result["pid"] == 42
    if occupied:
        assert result["action"] == "focused"
        assert run.call_args.args[0][-1] == "42"
        assert "NSRunningApplication" in run.call_args.kwargs["input"]
        launch.assert_not_called()
    else:
        assert result["action"] == "launch-requested"
        argv = launch.call_args.args[0]
        env = launch.call_args.kwargs["env"]
        assert argv[1:] == ["--user-data-dir=" + str(spec["frontend"])]
        assert env["CODEX_HOME"] == str(spec["home"])
        assert env["CODEX_ELECTRON_USER_DATA_PATH"] == str(spec["frontend"])
        assert spec["frontend"].stat().st_mode & 0o777 == 0o700


def test_changed_admission_cannot_launch_or_focus(profile, monkeypatch):
    home, spec = profile
    spec["app"] = str(bundle(home))
    monkeypatch.setattr(app, "running", Mock(return_value=None))
    launch, run = Mock(), Mock()
    monkeypatch.setattr(app.subprocess, "Popen", launch)
    monkeypatch.setattr(app.subprocess, "run", run)
    def admit():
        if (spec["lock_root"] / "launch.lock").exists():
            raise ValueError("selection changed")
    with pytest.raises(ValueError, match="selection changed"):
        app.open_codex(home, spec, preflight=admit)
    launch.assert_not_called()
    run.assert_not_called()


def test_clean_environment_cannot_reuse_another_codex_session(profile, monkeypatch):
    home, spec = profile
    for name in ("CODEX_THREAD_ID", "CODEX_APP_TOOLS_PIPE_PATH", "OPENAI_API_KEY", "NODE_OPTIONS"):
        monkeypatch.setenv(name, "synthetic-inherited-setting")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:32100")
    env = app.clean_env(spec, home)
    assert not set(env) & {"CODEX_THREAD_ID", "CODEX_APP_TOOLS_PIPE_PATH", "OPENAI_API_KEY", "NODE_OPTIONS"}
    assert env["CODEX_HOME"] == str(spec["home"])
    assert env["HTTPS_PROXY"] == os.environ["HTTPS_PROXY"]
