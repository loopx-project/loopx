#!/usr/bin/env python3
"""Smoke-test the local checkout default/canary promotion boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-local.sh"


def run_install(
    env: dict[str, str],
    *,
    release_id: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALL_SCRIPT)],
        cwd=REPO_ROOT,
        env={**env, "LOOPX_RELEASE_ID": release_id},
        check=check,
        capture_output=True,
        text=True,
    )


def base_env(root: Path) -> tuple[dict[str, str], Path, Path]:
    home = root / "home"
    bin_dir = home / ".local" / "bin"
    releases_dir = home / ".local" / "share" / "loopx" / "releases"
    bin_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "LOOPX_BIN_DIR": str(bin_dir),
        "LOOPX_RELEASES_DIR": str(releases_dir),
        "LOOPX_SHELL_PROFILE": str(home / ".zshrc"),
        "LOOPX_INSTALL_CANARY": "1",
        "LOOPX_INSTALL_SKILL": "0",
        "LOOPX_INSTALL_SLASH_COMMANDS": "0",
        "LOOPX_INSTALL_CLAUDE": "0",
        "LOOPX_APPROVED_DEFAULT_REF": "HEAD^",
        "LOOPX_PYTHON": sys.executable,
        "PATH": os.environ.get("PATH", ""),
        "SHELL": "/bin/zsh",
    }
    return env, bin_dir, releases_dir


def assert_untrusted_checkout_is_canary_only() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-promotion-canary-only-") as tmp:
        env, bin_dir, releases_dir = base_env(Path(tmp))
        skills_dir = Path(tmp) / "workspace" / ".agents" / "skills"
        env["LOOPX_INSTALL_SKILL"] = "1"
        env["LOOPX_SKILLS_DIR"] = str(skills_dir)
        env["LOOPX_ENTRY_HOST_SURFACE"] = "ark-managed-agent"
        stale_lock = skills_dir / ".loopx-install-lock"
        stale_lock.mkdir(parents=True)
        (stale_lock / "pid").write_text("999999999\n", encoding="utf-8")
        default = bin_dir / "loopx"
        default.write_text("#!/usr/bin/env bash\nexit 41\n", encoding="utf-8")
        default.chmod(0o755)

        install = run_install(env, release_id="guarded-untrusted")
        assert install.returncode == 0, install.stderr
        assert "loopx checkout installed as canary only" in install.stdout, install.stdout
        assert "promotion mode: canary_only_untrusted_checkout" in install.stdout, install.stdout
        assert not default.is_symlink(), default
        assert "exit 41" in default.read_text(encoding="utf-8"), default
        canary = bin_dir / "loopx-canary"
        assert canary.is_symlink(), canary
        assert canary.resolve() == REPO_ROOT / "scripts" / "loopx", canary.resolve()
        assert not releases_dir.exists(), releases_dir
        expected_skill_ids = {
            "loopx",
            "loopx-doc-registry",
            "loopx-benchmark",
            "loopx-pr-program",
            "loopx-pr-review",
            "loopx-project",
            "loopx-self-repair",
            "loopx-performance-diagnosis",
        }
        assert {
            path.parent.name for path in skills_dir.glob("*/SKILL.md")
        } == expected_skill_ids
        loopx_entry = (skills_dir / "loopx" / "SKILL.md").read_text(encoding="utf-8")
        assert "exact current host `ark-managed-agent`" in loopx_entry
        assert "--host-surface ark-managed-agent" in loopx_entry
        assert f"`{canary} start-goal" in loopx_entry
        assert "`loopx start-goal" not in loopx_entry
        assert not (skills_dir / "loopx-change-quality").exists()
        assert not (skills_dir / "loopx-material").exists()
        # A repo-kept workflow has no scope marker and must never reach a host.
        repo_only_skill_ids = sorted(
            path.name
            for path in (REPO_ROOT / "skills").iterdir()
            if path.is_dir() and not (path / ".loopx-skill-scope").exists()
        )
        assert repo_only_skill_ids, "the checkout no longer carries a repo-only skill source"
        for skill_id in repo_only_skill_ids:
            assert not (skills_dir / skill_id).exists(), skill_id
            assert (
                f"repo-only skill source: {REPO_ROOT / 'skills' / skill_id}" in install.stdout
            ), install.stdout
        readback = json.loads(
            (skills_dir / ".loopx-skill-install.json").read_text(encoding="utf-8")
        )
        assert readback["integration_mode"] == "fixed_install_script", readback
        assert set(readback["materialized_skill_ids"]) == expected_skill_ids, readback
        assert readback["source"]["revision"], readback
        assert not stale_lock.exists()
        assert (
            f"- skill readback: {skills_dir}/.loopx-skill-install.json"
            in install.stdout
        )


def assert_untrusted_checkout_preserves_implicit_default_skill_root() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-promotion-skill-root-") as tmp:
        env, _bin_dir, _releases_dir = base_env(Path(tmp))
        env["LOOPX_INSTALL_SKILL"] = "1"
        implicit_skill = (
            Path(env["CODEX_HOME"]) / "skills" / "loopx-project" / "SKILL.md"
        )
        implicit_skill.parent.mkdir(parents=True)
        implicit_skill.write_text("existing default skill\n", encoding="utf-8")

        install = run_install(env, release_id="guarded-implicit-skills")

        assert "skill: unchanged (set LOOPX_SKILLS_DIR" in install.stdout
        assert implicit_skill.read_text(encoding="utf-8") == "existing default skill\n"
        assert not (implicit_skill.parents[1] / ".loopx-skill-install.json").exists()


def assert_exact_host_install_rejects_user_owned_entry_skill() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-entry-skill-collision-") as tmp:
        env, _bin_dir, _releases_dir = base_env(Path(tmp))
        skills_dir = Path(tmp) / "workspace" / ".agents" / "skills"
        user_skill = skills_dir / "loopx" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("# User-owned LoopX skill\n", encoding="utf-8")
        stale_readback = skills_dir / ".loopx-skill-install.json"
        stale_readback.write_text('{"materialized_skill_ids":["loopx"]}\n', encoding="utf-8")
        env["LOOPX_INSTALL_SKILL"] = "1"
        env["LOOPX_SKILLS_DIR"] = str(skills_dir)
        env["LOOPX_ENTRY_HOST_SURFACE"] = "ark-managed-agent"

        install = run_install(
            env,
            release_id="guarded-entry-collision",
            check=False,
        )

        assert install.returncode != 0, install.stdout
        assert "exact-host entry skill was not materialized" in install.stderr
        assert user_skill.read_text(encoding="utf-8") == "# User-owned LoopX skill\n"
        assert not stale_readback.exists()


def assert_explicit_promotion_is_auditable() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-promotion-explicit-") as tmp:
        env, bin_dir, _releases_dir = base_env(Path(tmp))
        env["LOOPX_PROMOTE_DEFAULT"] = "1"

        install = run_install(env, release_id="explicit-promotion")
        assert install.returncode == 0, install.stderr
        assert "loopx installed locally" in install.stdout, install.stdout
        assert "promotion mode: explicit_override" in install.stdout, install.stdout
        default = bin_dir / "loopx"
        assert default.is_symlink(), default
        release_root = default.resolve().parents[1]
        manifest = json.loads((release_root / "release.json").read_text(encoding="utf-8"))
        assert manifest["source"]["promotion_mode"] == "explicit_override", manifest

        doctor = subprocess.run(
            [str(default), "--format", "json", "doctor"],
            env={**env, "PATH": f"{bin_dir}:{env['PATH']}"},
            check=True,
            capture_output=True,
            text=True,
        )
        doctor_payload = json.loads(doctor.stdout)
        default_release = doctor_payload["release_provenance"]["default_release"]
        assert default_release["promotion_mode"] == "explicit_override", default_release
        # Doctor starts a managed runtime in this synthetic HOME. Stop that
        # installed revision before TemporaryDirectory removes its files.
        shutdown = subprocess.run(
            [sys.executable, "-c", (
                "import json; from loopx.control_plane.effect_runtime import "
                "restart_effect_runtime; print(json.dumps(restart_effect_runtime()))"
            )],
            cwd=release_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(shutdown.stdout)["status"] != "shutdown_pending", shutdown.stdout


def assert_skill_preflight_failure_preserves_default() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-promotion-skill-preflight-") as tmp:
        root = Path(tmp)
        env, bin_dir, releases_dir = base_env(root)
        env["LOOPX_PROMOTE_DEFAULT"] = "1"
        env["LOOPX_INSTALL_SKILL"] = "1"
        skills_dir = root / "workspace" / ".agents" / "skills"
        env["LOOPX_SKILLS_DIR"] = str(skills_dir)
        env["LOOPX_ENTRY_HOST_SURFACE"] = "ark-managed-agent"

        user_skill = skills_dir / "loopx" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("# User-owned LoopX skill\n", encoding="utf-8")
        default = bin_dir / "loopx"
        default.write_text("#!/usr/bin/env bash\nexit 41\n", encoding="utf-8")
        default.chmod(0o755)

        install = run_install(
            env,
            release_id="blocked-skill-preflight",
            check=False,
        )

        assert install.returncode != 0, install.stdout
        assert "exact-host entry skill cannot be materialized" in install.stderr
        assert not default.is_symlink(), default
        assert "exit 41" in default.read_text(encoding="utf-8"), default
        assert not (releases_dir / "blocked-skill-preflight").exists(), releases_dir
        assert user_skill.read_text(encoding="utf-8") == "# User-owned LoopX skill\n"


def main() -> int:
    assert_untrusted_checkout_is_canary_only()
    assert_untrusted_checkout_preserves_implicit_default_skill_root()
    assert_exact_host_install_rejects_user_owned_entry_skill()
    assert_explicit_promotion_is_auditable()
    assert_skill_preflight_failure_preserves_default()
    print("local-install-promotion-boundary-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
