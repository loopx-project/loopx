#!/usr/bin/env python3
"""Smoke-test the read-only LoopX update planning interface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx import __version__  # noqa: E402
from loopx.release_manifest import release_version_tag  # noqa: E402
from loopx.self_update import (  # noqa: E402
    DEFAULT_UPDATE_REF,
    _installer_env_for_source,
    build_rollback_plan,
    build_update_plan,
    execute_rollback_plan,
    execute_update_plan,
    render_update_plan_markdown,
)


INSTALLED_COMMIT = "1" * 40
TARGET_COMMIT = "2" * 40


def fake_doctor_payload() -> dict[str, object]:
    return {
        "path": {
            "loopx": "/home/user/.local/bin/loopx",
            "loopx_realpath": "/home/user/.local/share/loopx/releases/20260621T170342Z/scripts/loopx",
        },
        "package": {
            "release_root": "/home/user/.local/share/loopx/releases/20260621T170342Z",
        },
        "install_freshness": {
            "status": "stale",
            "requires_upgrade": True,
            "reason": "fixture is intentionally stale",
            "current_version": __version__,
            "current_version_tag": release_version_tag(),
            "release_id": "20260621T170342Z",
            "manifest_package_version": __version__,
            "manifest_package_version_tag": release_version_tag(),
            "manifest_package_version_matches_runtime": True,
            "manifest_source_repo": "example/loopx",
            "manifest_source_ref": "stable",
            "manifest_source_git_commit": INSTALLED_COMMIT,
            "freshness_source_label": "example/loopx@main",
            "freshness_source_git_commit": TARGET_COMMIT,
            "manifest_source_freshness_relation": "installed_behind",
        },
        "release_manifest": {
            "available": True,
            "path": "/home/user/.local/share/loopx/releases/20260621T170342Z/release.json",
            "manifest": {
                "schema_version": "loopx_release_manifest_v0",
                "source": {
                    "kind": "github_archive",
                    "repo": "example/loopx",
                    "ref": "stable",
                    "archive_url": "https://codeload.github.com/example/loopx/tar.gz/stable",
                    "archive_sha256": "abc123",
                    "git_commit": INSTALLED_COMMIT,
                },
                "package": {
                    "name": "loopx",
                    "version": __version__,
                    "version_tag": release_version_tag(),
                    "version_source": "loopx.__version__",
                },
                "skills": {
                    "digest": "skills123",
                    "items": {},
                },
            },
        },
    }


def fake_fresh_doctor_payload() -> dict[str, object]:
    payload = fake_doctor_payload()
    payload["install_freshness"] = {
        "status": "fresh",
        "requires_upgrade": False,
        "reason": "fixture is intentionally fresh",
        "current_version": __version__,
        "current_version_tag": release_version_tag(),
        "release_id": "20260622T170342Z",
        "manifest_package_version": __version__,
        "manifest_package_version_tag": release_version_tag(),
        "manifest_package_version_matches_runtime": True,
        "manifest_source_repo": "example/loopx",
        "manifest_source_ref": "stable",
        "manifest_source_git_commit": INSTALLED_COMMIT,
        "freshness_source_label": "example/loopx@main",
        "freshness_source_git_commit": INSTALLED_COMMIT,
        "manifest_source_freshness_relation": "same",
    }
    return payload


def fake_doctor_payload_for_release(release_root: Path, *, home: Path) -> dict[str, object]:
    return {
        "path": {
            "loopx": str(home / ".local" / "bin" / "loopx"),
            "loopx_realpath": str(release_root / "scripts" / "loopx"),
        },
        "package": {
            "release_root": str(release_root),
        },
        "install_freshness": {
            "status": "fresh",
            "requires_upgrade": False,
            "reason": "fixture release",
            "current_version": __version__,
            "current_version_tag": release_version_tag(),
            "release_id": release_root.name,
            "manifest_package_version": __version__,
            "manifest_package_version_tag": release_version_tag(),
            "manifest_package_version_matches_runtime": True,
        },
    }


def write_fixture_release(home: Path, release_id: str, *, doctor_ok: bool = True) -> Path:
    script = home / ".local" / "share" / "loopx" / "releases" / release_id / "scripts" / "loopx"
    script.parent.mkdir(parents=True, exist_ok=True)
    doctor_body = (
        "  printf '{\"ok\": true, \"source\": \"rollback-smoke\"}\\n'\n"
        "  exit 0\n"
        if doctor_ok
        else "  printf '{\"ok\": false, \"source\": \"rollback-smoke\"}\\n'\n"
        "  exit 7\n"
    )
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == \"--format json doctor\" ]]; then\n"
        f"{doctor_body}"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script.parent.parent


def test_module_plan() -> None:
    payload = build_update_plan(
        repo="example/loopx",
        ref="fixture",
        archive_url="https://example.invalid/loopx.tar.gz",
        doctor_payload=fake_doctor_payload(),
    )
    assert payload["ok"] is True, payload
    assert payload["mode"] == "update", payload
    assert payload["dry_run"] is True, payload
    assert payload["execute_requested"] is False, payload
    assert payload["requested_action"] == "plan", payload
    assert payload["changes_applied"] is False, payload
    assert payload["next_action"]["command"].startswith("loopx update apply"), payload
    assert payload["next_action"]["requires_explicit_approval"] is True, payload
    assert payload["current"]["requires_upgrade"] is True, payload
    assert payload["current"]["current_version"] == __version__, payload
    assert payload["current"]["current_version_tag"] == release_version_tag(), payload
    assert payload["current"]["manifest_package_version"] == __version__, payload
    assert payload["current"]["manifest_package_version_tag"] == release_version_tag(), payload
    assert payload["current"]["manifest_package_version_matches_runtime"] is True, payload
    assert payload["current"]["release_manifest_available"] is True, payload
    assert payload["current"]["release_manifest"]["source"]["ref"] == "stable", payload
    assert payload["current"]["release_manifest"]["source"]["archive_sha256"] == "abc123", payload
    assert payload["plan"]["mutates_loopx_runtime_state"] is False, payload
    assert payload["plan"]["mutates_release_install"] is False, payload
    assert payload["plan"]["backup"]["available"] is True, payload
    assert payload["plan"]["backup"]["rollback_release_id"] == "20260621T170342Z", payload
    assert "loopx update --rollback 20260621T170342Z" in payload["plan"]["backup"]["rollback_command"], payload
    assert "ln -sfn" not in payload["plan"]["backup"]["rollback_command"], payload
    assert "--archive-url https://example.invalid/loopx.tar.gz" in payload["plan"]["install_command"], payload
    rendered = render_update_plan_markdown(payload)
    assert "**No update was applied.**" in rendered, rendered
    assert "## Next Action" in rendered, rendered
    assert "loopx update apply" in rendered, rendered
    assert f"Current version tag: `{release_version_tag()}`" in rendered, rendered
    assert f"Manifest package version tag: `{release_version_tag()}`" in rendered, rendered


def test_default_source_uses_stable_ref() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        payload = build_update_plan(doctor_payload=fake_doctor_payload())
    assert payload["source"]["ref"] == DEFAULT_UPDATE_REF == "stable", payload
    assert payload["source"]["channel"] == "github_archive_stable", payload
    assert payload["source"]["ref_source"] == "default_stable", payload
    assert "/tar.gz/stable" in payload["source"]["archive_url"], payload
    assert payload["plan"]["install_command"] == "loopx update apply", payload
    assert "--archive-url" not in payload["plan"]["install_command"], payload
    default_env = _installer_env_for_source(
        payload["source"],
        base_env={"LOOPX_ARCHIVE_URL": "https://stale.invalid/archive.tar.gz"},
    )
    assert "LOOPX_ARCHIVE_URL" not in default_env, default_env


def test_explicit_archive_url_reaches_installer() -> None:
    payload = build_update_plan(
        repo="example/loopx",
        ref="fixture",
        archive_url="https://example.invalid/loopx.tar.gz",
        doctor_payload=fake_doctor_payload(),
    )
    assert payload["source"]["channel"] == "github_archive_url_override", payload
    assert "--archive-url https://example.invalid/loopx.tar.gz" in payload["plan"]["install_command"], payload
    assert "loopx update apply" in payload["plan"]["install_command"], payload
    assert "--repo example/loopx" in payload["plan"]["install_command"], payload
    assert "--ref fixture" in payload["plan"]["install_command"], payload
    installer_env = _installer_env_for_source(payload["source"], base_env={})
    assert installer_env["LOOPX_ARCHIVE_URL"] == "https://example.invalid/loopx.tar.gz", installer_env


def test_update_preview_uses_managed_download_path() -> None:
    payload = build_update_plan(doctor_payload=fake_doctor_payload())
    command = payload["plan"]["install_command"]
    assert command.startswith("loopx update apply"), command
    assert "curl" not in command, command


def test_execute_update_propagates_installer_download_failure() -> None:
    payload = build_update_plan(
        repo="example/loopx",
        ref="fixture",
        archive_url="https://example.invalid/loopx.tar.gz",
        execute=True,
        doctor_payload=fake_doctor_payload(),
    )
    install_failed = subprocess.CompletedProcess(
        args=[],
        returncode=22,
        stdout="",
        stderr="curl: (22) requested URL returned error",
    )
    doctor_passed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"ok": true}',
        stderr="",
    )
    with mock.patch(
        "loopx.self_update._installer_env_for_source",
        return_value={},
    ), mock.patch(
        "loopx.self_update.subprocess.run",
        side_effect=[install_failed, doctor_passed],
    ) as run:
        result = execute_update_plan(payload)

    assert result["ok"] is False, result
    install_command = run.call_args_list[0].args[0]
    assert install_command[0] == "curl", install_command
    assert "--output" in install_command, install_command
    assert result["execution"]["install_returncode"] == 22, result
    assert result["execution"]["doctor_returncode"] == 0, result
    assert result["execution"]["installer_download"]["stage"] == "installer_download", result
    assert result["changes_applied"] is False, result
    assert "Download attempt 1: HTTP `0`, curl `22`" in render_update_plan_markdown(result)


def test_active_release_python_reaches_installer() -> None:
    with TemporaryDirectory() as tmpdir:
        release_root = Path(tmpdir) / "release"
        release_root.mkdir()
        (release_root / ".loopx-python").write_text(f"{sys.executable}\n", encoding="utf-8")
        source = {"repo": "example/loopx", "ref": "stable"}

        inherited = _installer_env_for_source(
            source,
            base_env={},
            current_release_root=release_root,
        )
        assert inherited["LOOPX_PYTHON"] == sys.executable, inherited

        explicit = _installer_env_for_source(
            source,
            base_env={"LOOPX_PYTHON": "/explicit/python"},
            current_release_root=release_root,
        )
        assert explicit["LOOPX_PYTHON"] == "/explicit/python", explicit


def test_active_release_python_marker_fails_closed() -> None:
    with TemporaryDirectory() as tmpdir:
        release_root = Path(tmpdir) / "release"
        release_root.mkdir()
        source = {"repo": "example/loopx", "ref": "stable"}
        try:
            _installer_env_for_source(
                source,
                base_env={},
                current_release_root=release_root,
            )
        except ValueError as exc:
            assert "missing its persisted Python runtime" in str(exc), exc
        else:
            raise AssertionError("missing release Python marker must fail closed")

        (release_root / ".loopx-python").write_text("\n", encoding="utf-8")
        try:
            _installer_env_for_source(
                source,
                base_env={},
                current_release_root=release_root,
            )
        except ValueError as exc:
            assert "empty persisted Python runtime" in str(exc), exc
        else:
            raise AssertionError("empty release Python marker must fail closed")


def test_execute_update_uses_persisted_release_python() -> None:
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        release_root = home / "release"
        release_root.mkdir()
        missing_python = str(release_root / "missing-python")
        (release_root / ".loopx-python").write_text(f"{missing_python}\n", encoding="utf-8")
        payload = build_update_plan(
            execute=True,
            doctor_payload=fake_doctor_payload_for_release(release_root, home=home),
        )
        failed = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="loopx installer error: Python executable not found",
        )
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "loopx.self_update.subprocess.run",
            side_effect=[failed, failed],
        ) as run:
            result = execute_update_plan(payload)

        assert result["ok"] is False, result
        assert run.call_count == 2, run.call_args_list
        install_env = run.call_args_list[0].kwargs["env"]
        doctor_env = run.call_args_list[1].kwargs["env"]
        assert install_env["LOOPX_PYTHON"] == missing_python, install_env
        assert doctor_env["LOOPX_PYTHON"] == missing_python, doctor_env


def test_fresh_check_is_noop_recommendation() -> None:
    payload = build_update_plan(
        repo="example/loopx",
        ref="fixture",
        archive_url="https://example.invalid/loopx.tar.gz",
        check_only=True,
        doctor_payload=fake_fresh_doctor_payload(),
    )
    assert payload["ok"] is True, payload
    assert payload["check_only"] is True, payload
    assert payload["current"]["requires_upgrade"] is False, payload
    assert payload["source_version_check"]["status"] == "skipped", payload
    assert "source version comparison was skipped" in payload["recommended_action"], payload


class FakeVersionResponse:
    def __init__(self, version: str) -> None:
        self.body = f'__version__ = "{version}"\n'.encode()

    def __enter__(self) -> "FakeVersionResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def test_check_compares_selected_source_version() -> None:
    with mock.patch("loopx.self_update.urlopen", return_value=FakeVersionResponse("9.9.9")) as fetch:
        payload = build_update_plan(
            repo="example/loopx",
            ref="stable",
            check_only=True,
            doctor_payload=fake_fresh_doctor_payload(),
        )
    source_check = payload["source_version_check"]
    assert source_check["status"] == "available", payload
    assert source_check["version"] == "9.9.9", payload
    assert source_check["version_tag"] == "v9.9.9", payload
    assert source_check["matches_current"] is False, payload
    assert "differs from installed version" in payload["recommended_action"], payload
    request = fetch.call_args.args[0]
    assert request.full_url == (
        "https://raw.githubusercontent.com/example/loopx/stable/loopx/__init__.py"
    ), request.full_url

    with mock.patch("loopx.self_update.urlopen", return_value=FakeVersionResponse(__version__)):
        current_payload = build_update_plan(
            repo="example/loopx",
            ref="stable",
            check_only=True,
            doctor_payload=fake_fresh_doctor_payload(),
        )
    assert current_payload["source_version_check"]["matches_current"] is True, current_payload
    assert current_payload["source_commit_check"]["status"] == "not_requested", current_payload
    assert "no update needed" not in current_payload["recommended_action"], current_payload


def test_check_degrades_when_source_version_is_unavailable() -> None:
    with mock.patch("loopx.self_update.urlopen", side_effect=OSError("offline")):
        payload = build_update_plan(
            repo="example/loopx",
            ref="stable",
            check_only=True,
            doctor_payload=fake_fresh_doctor_payload(),
        )
    source_check = payload["source_version_check"]
    assert payload["ok"] is True, payload
    assert source_check["status"] == "unavailable", payload
    assert source_check["attempted"] is True, payload
    assert "could not be checked" in payload["recommended_action"], payload


def immutable_fresh_doctor_payload(ref: str) -> dict[str, object]:
    payload = fake_fresh_doctor_payload()
    freshness = payload["install_freshness"]
    assert isinstance(freshness, dict)
    freshness["manifest_source_ref"] = ref
    freshness["freshness_source_label"] = f"example/loopx@{ref}"
    freshness["freshness_source_git_commit"] = None
    return payload


def test_immutable_source_ref_qualifies_activation() -> None:
    pinned = INSTALLED_COMMIT
    with mock.patch(
        "loopx.self_update.urlopen",
        return_value=FakeVersionResponse(__version__),
    ):
        activated = build_update_plan(
            repo="example/loopx",
            ref=pinned,
            check_only=True,
            doctor_payload=immutable_fresh_doctor_payload(pinned),
        )
    qualification = activated["runtime_activation_qualification"]
    assert qualification["decision"] == "runtime_active", activated
    assert qualification["runtime_active"] is True, activated
    assert qualification["target_source_commit"] == pinned, activated
    assert qualification["revision_relation"] == "same", activated
    assert "no update needed" in activated["recommended_action"], activated

    other = TARGET_COMMIT
    unproven = build_update_plan(
        repo="example/loopx",
        ref=other,
        check_only=True,
        doctor_payload=immutable_fresh_doctor_payload(other),
    )
    mismatch = unproven["runtime_activation_qualification"]
    assert mismatch["decision"] == "activation_qualification_required", unproven
    assert mismatch["target_source_commit"] == other, unproven
    assert "does not match the selected immutable source commit" in mismatch["reason"], unproven
    assert mismatch["successor"] == {
        "required": True,
        "kind": "activation_qualification",
    }, unproven


def test_rollback_previous_executes_with_temp_home() -> None:
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        previous_release = write_fixture_release(home, "20260621T170342Z")
        current_release = write_fixture_release(home, "20260622T170342Z")
        payload = build_rollback_plan(
            release_id="previous",
            doctor_payload=fake_doctor_payload_for_release(current_release, home=home),
            home=home,
        )
        assert payload["ok"] is True, payload
        assert payload["mode"] == "rollback", payload
        assert payload["plan"]["selected_release_id"] == previous_release.name, payload
        result = execute_rollback_plan(payload, home=home)
        assert result["ok"] is True, result
        loopx_bin = home / ".local" / "bin" / "loopx"
        assert loopx_bin.is_symlink(), loopx_bin
        assert loopx_bin.resolve() == (previous_release / "scripts" / "loopx").resolve(), loopx_bin.resolve()
        assert result["execution"]["doctor_returncode"] == 0, result


def test_rollback_restores_previous_when_doctor_fails() -> None:
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        bad_release = write_fixture_release(home, "20260621T170342Z", doctor_ok=False)
        current_release = write_fixture_release(home, "20260622T170342Z")
        loopx_bin = home / ".local" / "bin" / "loopx"
        loopx_bin.parent.mkdir(parents=True, exist_ok=True)
        loopx_bin.symlink_to(current_release / "scripts" / "loopx")
        payload = build_rollback_plan(
            release_id=bad_release.name,
            doctor_payload=fake_doctor_payload_for_release(current_release, home=home),
            home=home,
        )
        assert payload["ok"] is True, payload
        result = execute_rollback_plan(payload, home=home)
        assert result["ok"] is False, result
        assert result["execution"]["doctor_returncode"] == 7, result
        assert result["execution"]["restored_previous_on_failure"] is True, result
        assert loopx_bin.resolve() == (current_release / "scripts" / "loopx").resolve(), loopx_bin.resolve()


def test_cli_check() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "update",
            "--format",
            "json",
            "--check",
            "--repo",
            "example/loopx",
            "--ref",
            "fixture",
            "--archive-url",
            "https://example.invalid/loopx.tar.gz",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    assert payload["mode"] == "update", payload
    assert payload["check_only"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["source"]["repo"] == "example/loopx", payload
    assert payload["source"]["ref"] == "fixture", payload
    assert payload["plan"]["mutates_loopx_runtime_state"] is False, payload


def test_cli_rollback_previous_with_temp_home() -> None:
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        previous_release = write_fixture_release(home, "20260621T170342Z")
        current_release = write_fixture_release(home, "20260622T170342Z")
        loopx_bin = home / ".local" / "bin" / "loopx"
        loopx_bin.parent.mkdir(parents=True, exist_ok=True)
        loopx_bin.symlink_to(current_release / "scripts" / "loopx")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "update",
                "--format",
                "json",
                "--rollback",
                "previous",
            ],
            cwd=REPO_ROOT,
            env={"HOME": str(home), "PATH": f"{home / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
                 "LOOPX_USAGE_PING": "0"},
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["ok"] is True, payload
        assert payload["mode"] == "rollback", payload
        assert payload["plan"]["selected_release_id"] == previous_release.name, payload
        assert loopx_bin.resolve() == (previous_release / "scripts" / "loopx").resolve(), loopx_bin.resolve()


def main() -> int:
    test_module_plan()
    test_default_source_uses_stable_ref()
    test_explicit_archive_url_reaches_installer()
    test_update_preview_uses_managed_download_path()
    test_execute_update_propagates_installer_download_failure()
    test_active_release_python_reaches_installer()
    test_active_release_python_marker_fails_closed()
    test_execute_update_uses_persisted_release_python()
    test_fresh_check_is_noop_recommendation()
    test_check_compares_selected_source_version()
    test_check_degrades_when_source_version_is_unavailable()
    test_immutable_source_ref_qualifies_activation()
    test_rollback_previous_executes_with_temp_home()
    test_rollback_restores_previous_when_doctor_fails()
    test_cli_check()
    test_cli_rollback_previous_with_temp_home()
    print("loopx-update-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
