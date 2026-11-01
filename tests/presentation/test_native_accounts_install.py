"""Installed wheel owns native transactions without personal-apps or dotfiles."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native account stores require POSIX locks")
ROOT = Path(__file__).resolve().parents[2]

INSTALLED_CHECK = r'''
import base64, hashlib, json, os, plistlib, shutil, sqlite3, sys
from pathlib import Path
from unittest.mock import patch
from loopx_codex_provider_routing import native_account_store as store
from loopx_codex_provider_routing import native_app as app
from loopx_codex_provider_routing.native_host import discover, NativeHost
root = Path(sys.argv[1])
assert Path(store.__file__).is_relative_to(Path(sys.prefix))
assert shutil.which("personal-apps") is None
home = root / "home"
home.mkdir()
codex = home / ".codex"
codex.mkdir(mode=0o700)
bundle = home / "Applications/Codex.app"
executable = bundle / "Contents/MacOS/Codex"
executable.parent.mkdir(parents=True)
executable.write_text("#!/bin/sh\nexit 0\n")
executable.chmod(0o755)
(bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable":"Codex"}))
(codex / "config.toml").write_text('model_provider="openai"\nmodel="example-model"\n')
claims = base64.urlsafe_b64encode(b'{"sub":"synthetic"}').decode().rstrip('=')
auth = store.encoded({"auth_mode":"chatgpt","tokens": {
    "id_token":"fixture."+claims+".signature","access_token":"fixture-access",
    "refresh_token":"fixture-refresh","account_id":"fixture-account"}})
store.atomic(codex / "auth.json", auth)
with sqlite3.connect(codex / "history.sqlite") as database:
    database.execute("create table fixture (text text)")
    database.execute("insert into fixture values ('existing synthetic history')")
history = (codex / "history.sqlite").read_bytes()
with patch.dict(os.environ, {"CODEX_HOME": str(codex), "CODEX_ELECTRON_USER_DATA_PATH": str(home / "frontend")}):
    adapter = discover(user_home=home)
    host = NativeHost(adapter, user_home=home)
    assert host.status()["slots"] == []
    assert not host.manager.root.exists()
    # The store/backend is real; no native process is launched by this install check.
    host.manager.idle = lambda: None
    enrolled = host.enroll_current(host.revision())
    assert enrolled["slots"] == ["primary"]
    host.enroll_api("openai-api", "sk-loopx-relay-synthetic-installed-token", "http://127.0.0.1:32100/v1")
    host.manager.switch("openai-api")
    assert host.status()["api_slot"] == "openai-api"
    assert host.status()["current_login_matches"] is True
    host.manager.recover(rollback=True)
    assert host.manager.auth.read_bytes() == auth
    assert (codex / "history.sqlite").read_bytes() == history
    assert "personal_apps" not in sys.modules and "personal_accounts" not in sys.modules
    assert not (home / ".local/state/personal-apps").exists()
    assert host.manager.root.stat().st_mode & 0o777 == 0o700
print(json.dumps({"installed_owner":True,"external_modules":False,"rollback":True,"history_preserved":True}))
'''


def test_installed_wheel_without_dotfiles(tmp_path):
    uv = shutil.which("uv")
    assert uv, "Source-checkout validation requires uv"
    wheels = tmp_path / "wheels"
    built = subprocess.run([uv, "build", "--wheel", "--out-dir", str(wheels),
                            str(ROOT / "packages/loopx-codex-provider-routing")],
                           text=True, capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr[-1500:]
    environment = tmp_path / "environment"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True, timeout=30)
    python = environment / "bin/python"
    installed = subprocess.run([uv, "pip", "install", "--python", str(python), "--no-deps", str(next(wheels.glob("*.whl")))],
                               text=True, capture_output=True, timeout=30)
    assert installed.returncode == 0, installed.stderr[-1000:]
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    clean = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    clean["PATH"] = str(binary) + os.pathsep + os.defpath
    # Exercise the installed CLI as well as the packaged native backend.
    read = subprocess.run([str(environment / "bin/loopx-codex-accounts"), "keys-status", "--runtime-root", str(tmp_path / "runtime")],
                          cwd=tmp_path, env=clean, text=True, capture_output=True, timeout=15)
    assert read.returncode == 0 and json.loads(read.stdout)["keys"] == []
    assert not (tmp_path / "runtime").exists()
    result = subprocess.run([str(python), "-c", INSTALLED_CHECK, str(tmp_path)], cwd=tmp_path, env=clean,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout)["external_modules"] is False
