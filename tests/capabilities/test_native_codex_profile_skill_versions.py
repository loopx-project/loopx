"""Pinned capability profiles validate their own managed skill versions."""
from __future__ import annotations

import sys
import json
import subprocess
from pathlib import Path

import pytest

from loopx import skill_install_readback
from loopx.capabilities.benchmark_toolkit.native_codex_profile import (
    NativeCodexProfileError,
    inspect_native_codex_profile,
    install_native_codex_profile,
    native_codex_profile_environment,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX release profile installer")
def test_profile_compares_skills_with_its_own_runtime(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]
    profile = install_native_codex_profile(
        source, tmp_path / "profile", require_clean_source=False,
    )
    # The supervisor upgrades; the isolated profile and its CLI remain pinned.
    monkeypatch.setattr(skill_install_readback, "__version__", "999.0.0")
    verified = inspect_native_codex_profile(
        profile.root, source_root=source, require_clean_source=False,
    )
    assert verified.skills_digest == profile.skills_digest

    # A self-consistent manifest matching the supervisor must not mask a
    # mismatch with the runtime that will actually load these skills.
    skill_install_readback.write_skill_install_readback(
        skills_dir=profile.skills_dir,
        skill_ids=profile.required_skill_ids,
        source_root=profile.release_root,
        loopx_version="999.0.0",
    )
    with pytest.raises(NativeCodexProfileError, match="loopx_version_mismatch"):
        inspect_native_codex_profile(profile.root, require_clean_source=False)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX release profile installer")
def test_equal_source_profiles_have_independent_runtime_shutdown(tmp_path):
    source = Path(__file__).resolve().parents[2]
    profiles = [
        install_native_codex_profile(
            source, tmp_path / name, require_clean_source=False,
        )
        for name in ("first", "second")
    ]

    def doctor(profile, *args):
        completed = subprocess.run(
            [str(profile.cli_bin), "--format", "json", "doctor",
             "--installation-only", *args],
            cwd=profile.root,
            env=native_codex_profile_environment(profile),
            capture_output=True, text=True, timeout=30, check=True,
        )
        payload = json.loads(completed.stdout)
        assert payload["ok"]
        return payload

    try:
        assert profiles[0].source_revision == profiles[1].source_revision
        for profile in profiles:
            assert doctor(profile, "--deep")["typescript_control_plane"]["ready"]
        stopped = doctor(profiles[0], "--restart-runtime")["effect_runtime_restart"]
        assert stopped["status"] == "stopped"
        assert Path(stopped["info_path"]).is_relative_to(profiles[0].home)
        other = doctor(profiles[1])["typescript_control_plane"]
        assert other["runtime_lifecycle"]["state"] == "running"
    finally:
        for profile in profiles:
            assert doctor(profile, "--restart-runtime")["effect_runtime_restart"][
                "status"
            ] in {"stopped", "not_running"}
