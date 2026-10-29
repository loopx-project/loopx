#!/usr/bin/env python3
"""Install and upgrade a runtime archive in a disposable, isolated environment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
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
        source_record = json.loads((release / "release.json").read_text())["source"]
        assert source_record["git_commit"] == manifest["source_revision"]
        assert source_record["ref"] == manifest["channel"]
        # Every preserved skill reference and TS input is supplied by the same
        # committed archive; validate the exact Chat bundle through its owner.
        subprocess.run([sys.executable, str(release / "scripts/chat_bundle.py"), "verify", "--source"],
            env=env, check=True, stdout=subprocess.DEVNULL)
        return {"ok": True, "source_revision": manifest["source_revision"], "bytes": manifest["size"],
            "clean_install": results[0] == 0, "upgrade": results[1] == 0,
            "doctor": True, "skills": True, "frontend_source_contract": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(qualify(parser.parse_args().directory)))
