#!/usr/bin/env python3
"""Install and upgrade a runtime archive in a disposable, isolated environment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loopx.cli_commands.update_artifact import extract_artifact, file_hash, validate_manifest


def qualify(directory: Path) -> dict:
    manifest = validate_manifest(json.loads((directory / "loopx-runtime.json").read_text()))
    archive = directory / f"loopx-runtime-{manifest['source_revision']}.tar.gz"
    assert archive.stat().st_size == manifest["size"] and file_hash(archive) == manifest["sha256"]
    with tempfile.TemporaryDirectory(prefix="loopx-runtime-qualification-") as raw:
        scratch = Path(raw)
        source = scratch / "source"
        source.mkdir()
        extract_artifact(archive, source)
        env = dict(os.environ)
        # No developer's runtime, home, skills or host config may participate.
        for key in tuple(env):
            if key.startswith("LOOPX_") or key in {"CODEX_HOME", "PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE"}:
                env.pop(key)
        env.update(HOME=str(scratch / "home"), CODEX_HOME=str(scratch / "home/codex"),
            XDG_CONFIG_HOME=str(scratch / "home/config"), XDG_DATA_HOME=str(scratch / "home/data"),
            XDG_CACHE_HOME=str(scratch / "home/cache"), XDG_STATE_HOME=str(scratch / "home/state"),
            LOOPX_BIN_DIR=str(scratch / "bin"), LOOPX_RELEASES_DIR=str(scratch / "releases"),
            LOOPX_MAN_ROOT=str(scratch / "man"), LOOPX_SKILLS_DIR=str(scratch / "skills"),
            LOOPX_REGISTRY=str(scratch / "registry.json"), LOOPX_RUNTIME_ROOT=str(scratch / "runtime"),
            LOOPX_INSTALL_CANARY="0", LOOPX_PROMOTE_DEFAULT="1", LOOPX_PREBUILT_RUNTIME="1",
            LOOPX_INSTALL_SLASH_COMMANDS="0", LOOPX_INSTALL_REVALIDATE_EXTENSIONS="0",
            LOOPX_RESOLVED_SOURCE_GIT_COMMIT=manifest["source_revision"], LOOPX_REF=manifest["channel"],
            LOOPX_PYTHON=sys.executable, LOOPX_USAGE_PING="0", LOOPX_UPDATE_EVENTS="1")
        (scratch / "home").mkdir()
        tools = scratch / "tools"
        tools.mkdir()
        for name in ("npm", "npx"):
            command = tools / name
            command.write_text("#!/bin/sh\necho 'Frontend build tools must not run during runtime installation' >&2\nexit 91\n")
            command.chmod(0o755)
        env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")
        results = []
        for _ in range(2):
            result = subprocess.run(["bash", str(source / "scripts/install-local.sh")], env=env,
                text=True, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Isolated runtime install failed:\n" + result.stdout[-4000:] + result.stderr[-4000:])
            results.append(result.returncode)
        cli = scratch / "bin/loopx"
        def read(*args):
            result = subprocess.run([str(cli), "--format", "json", *args], env=env,
                text=True, capture_output=True, timeout=60, check=True)
            return json.loads(result.stdout)
        doctor = read("doctor", "--installation-only")
        skills = read("workflow-skills", "--skills-dir", str(scratch / "skills"))
        if not doctor.get("ok") or not skills.get("ok"):
            raise RuntimeError("Installed runtime/skills readback failed")
        release = cli.resolve().parents[1]
        for name in ("LICENSE", "NOTICE", "LICENSE-MIT"):
            assert (release / name).read_bytes() == (source / name).read_bytes()
        source_record = json.loads((release / "release.json").read_text())["source"]
        assert source_record["git_commit"] == manifest["source_revision"]
        assert source_record["ref"] == manifest["channel"]
        # Every preserved skill reference and TS input is supplied by the same
        # committed archive; validate the exact Chat bundle through its owner.
        subprocess.run([sys.executable, str(release / "scripts/chat_bundle.py"), "verify", "--source"],
            env=env, check=True, stdout=subprocess.DEVNULL)
        bundle = json.loads((release / "loopx/web/chat/bundle-manifest.json").read_text())
        assert bundle["source_revision"] == manifest["source_revision"]
        # Exercise the installed delivery owner, not a development file server.
        subprocess.run([sys.executable, "-c", """
import hashlib
import json
import threading
from urllib.request import urlopen
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir
assets = default_chat_assets_dir()
manifest = json.loads((assets / 'bundle-manifest.json').read_text())
server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
server.assets_dir, server.verbose = assets, False
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
try:
    for name, digest in manifest['files'].items():
        with urlopen(f'http://127.0.0.1:{server.server_port}/chat/{name}', timeout=10) as response:
            assert response.status == 200
            assert hashlib.sha256(response.read()).hexdigest() == digest, name
finally:
    server.shutdown()
    server.server_close()
    worker.join()
"""], env={**env, "PYTHONPATH": str(release)}, cwd=release,
            check=True, timeout=60, stdout=subprocess.DEVNULL)
        # Inject a post-switch executable failure at the manpage chmod step.
        # Only a disposable candidate is touched, never a real installation.
        real_chmod = shutil.which("chmod")
        failing_chmod = tools / "chmod"
        failing_chmod.write_text(f"#!{sys.executable}\n" +
            "import os,subprocess,sys\nfrom pathlib import Path\n" +
            f"subprocess.run([{real_chmod!r}, *sys.argv[1:]], check=True)\n" +
            "if sys.argv[1] == '0644':\n" +
            "    entry = Path(os.environ['LOOPX_BIN_DIR']) / 'loopx'\n" +
            "    entry.resolve().chmod(0o644)\n")
        failing_chmod.chmod(0o755)
        failed = subprocess.run(["bash", str(source / "scripts/install-local.sh")], env=env,
            text=True, capture_output=True, timeout=180)
        if failed.returncode == 0 or cli.resolve().parents[1] != release:
            raise RuntimeError("Activation failure did not preserve the previous executable: " + failed.stderr[-3000:])
        assert read("doctor", "--installation-only")["ok"]
        subprocess.run([sys.executable, "-c",
            "from loopx.control_plane.effect_runtime import restart_effect_runtime; "
            "restart_effect_runtime(wait_for_exit=True)"],
            env={**env, "PYTHONPATH": str(release)}, cwd=release,
            check=True, timeout=15, stdout=subprocess.DEVNULL)
        return {"ok": True, "source_revision": manifest["source_revision"], "bytes": manifest["size"],
            "clean_install": results[0] == 0, "upgrade": results[1] == 0,
            "doctor": True, "skills": True, "frontend_bundle_contract": True, "frontend_http": True,
            "no_frontend_build_tools": True, "license_notices": True, "activation_failure_restored": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(qualify(parser.parse_args().directory)))
