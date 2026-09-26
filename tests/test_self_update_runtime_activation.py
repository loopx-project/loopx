from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest import mock

from loopx import __version__
import pytest

from loopx.control_plane.runtime import runtime_projection_route
from loopx.self_update import (
    UpdateAction,
    build_update_plan,
    execute_update_plan,
    resolve_update_action,
)
from loopx.runtime_activation import restart_managed_loopx_services


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_COMMIT = "1" * 40
TARGET_COMMIT = "2" * 40


class FakeVersionResponse:
    def __init__(self, body: bytes | None = None) -> None:
        self.body = body if body is not None else f'__version__ = "{__version__}"\n'.encode()

    def __enter__(self) -> FakeVersionResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def doctor_payload(
    *,
    installed_commit: str | None = INSTALLED_COMMIT,
    target_commit: str | None = TARGET_COMMIT,
    relation: str = "installed_behind",
    source_ref: str = "main",
) -> dict[str, object]:
    return {
        "path": {
            "loopx": "/home/user/.local/bin/loopx",
            "loopx_realpath": "/home/user/.local/share/loopx/releases/fixture/scripts/loopx",
        },
        "package": {
            "release_root": "/home/user/.local/share/loopx/releases/fixture",
        },
        "install_freshness": {
            "status": "stale" if relation == "installed_behind" else "fresh",
            "requires_upgrade": relation == "installed_behind",
            "current_version": __version__,
            "release_id": "fixture",
            "manifest_package_version": __version__,
            "manifest_package_version_matches_runtime": True,
            "manifest_source_repo": "example/loopx",
            "manifest_source_ref": source_ref,
            "manifest_source_git_commit": installed_commit,
            "freshness_source_label": f"example/loopx@{source_ref}",
            "freshness_source_git_commit": target_commit,
            "manifest_source_freshness_relation": relation,
        },
        "release_manifest": {
            "available": True,
            "manifest": {
                "source": {
                    "repo": "example/loopx",
                    "ref": source_ref,
                    "git_commit": installed_commit,
                }
            },
        },
    }


def build_check(doctor: dict[str, object], *, ref: str = "main") -> dict[str, object]:
    with mock.patch("loopx.self_update.urlopen", return_value=FakeVersionResponse()):
        return build_update_plan(
            repo="example/loopx",
            ref=ref,
            check_only=True,
            doctor_payload=doctor,
        )


def build_live_check(
    doctor: dict[str, object], *, remote_commit: str
) -> dict[str, object]:
    def response(request: object, **_kwargs: object) -> FakeVersionResponse:
        url = getattr(request, "full_url", "")
        return FakeVersionResponse(
            remote_commit.encode()
            if "/commits/main" in url
            else f'__version__ = "{__version__}"\n'.encode()
        )

    with mock.patch("loopx.self_update.collect_doctor", return_value=doctor), mock.patch(
        "loopx.self_update.urlopen", side_effect=response
    ):
        return build_update_plan(
            repo="example/loopx", ref="main", check_only=True
        )


def test_update_plan_resolves_mutable_ref_commit_before_claiming_no_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = doctor_payload(target_commit=None, relation="same")
    freshness = doctor["install_freshness"]
    assert isinstance(freshness, dict)
    freshness["requires_upgrade"] = False
    monkeypatch.setattr("loopx.self_update.collect_doctor", lambda: doctor)
    monkeypatch.setattr(
        "loopx.self_update.urlopen",
        lambda request, **_kwargs: FakeVersionResponse(
            TARGET_COMMIT.encode()
            if "/commits/main" in request.full_url
            else f'__version__ = "{__version__}"\n'.encode()
        ),
    )

    payload = build_update_plan(repo="example/loopx", ref="main", action="plan")
    commit_check = payload["source_commit_check"]
    assert commit_check["status"] == "available"
    assert commit_check["matches_current"] is False
    assert commit_check["target_commit"] == TARGET_COMMIT
    assert payload["runtime_activation_qualification"]["runtime_active"] is not True
    assert "no update needed" not in payload["recommended_action"]


def test_update_plan_accepts_exact_commit_under_pinned_install_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = doctor_payload(
        target_commit=None, relation="same", source_ref=INSTALLED_COMMIT
    )
    freshness = doctor["install_freshness"]
    assert isinstance(freshness, dict)
    freshness["requires_upgrade"] = False
    monkeypatch.setattr("loopx.self_update.collect_doctor", lambda: doctor)
    monkeypatch.setattr(
        "loopx.self_update.urlopen",
        lambda *_args, **_kwargs: FakeVersionResponse(INSTALLED_COMMIT.encode()),
    )

    payload = build_update_plan(repo="example/loopx", ref="main", action="plan")
    assert payload["source_commit_check"]["matches_current"] is True
    assert payload["runtime_activation_qualification"]["decision"] == "runtime_active"
    assert "no update needed" in payload["recommended_action"]


def test_update_plan_offline_never_calls_unqualified_source_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even an equal *locally cached* ref cannot prove the moving remote ref
    # has not advanced while the source lookup is offline.
    doctor = doctor_payload(target_commit=INSTALLED_COMMIT, relation="same")
    freshness = doctor["install_freshness"]
    assert isinstance(freshness, dict)
    freshness["requires_upgrade"] = False
    monkeypatch.setattr("loopx.self_update.collect_doctor", lambda: doctor)
    monkeypatch.setattr("loopx.self_update.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))

    payload = build_update_plan(repo="example/loopx", ref="main", action="plan")
    assert payload["source_commit_check"]["status"] == "unavailable"
    assert payload["runtime_activation_qualification"]["decision"] == "activation_qualification_required"
    assert "no update needed" not in payload["recommended_action"]


@pytest.mark.parametrize("action", ["check", "plan"])
def test_update_readiness_remains_bounded_when_one_source_registry_stalls(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    global_registry = runtime_root / "registry.global.json"
    stalled_registry = tmp_path / "offline" / "registry.json"
    stalled_registry.parent.mkdir()
    stalled_registry.write_text("{}", encoding="utf-8")
    global_registry.write_text(
        json.dumps(
            {
                "registry_role": "global-local",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "offline-goal",
                        "source_registry": str(stalled_registry),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    release_stalled_read = threading.Event()
    real_load_registry = runtime_projection_route.load_registry

    def load_registry(path: Path):
        if path == stalled_registry:
            release_stalled_read.wait(timeout=2)
        return real_load_registry(path)

    observed: dict[str, object] = {}

    def collect_bounded_doctor() -> dict[str, object]:
        diagnostics = (
            runtime_projection_route.collect_runtime_projection_route_diagnostics(
                registry_path=global_registry,
                runtime_root=runtime_root,
                source_registry_read_timeout_seconds=0.02,
            )
        )
        observed.update(diagnostics)
        return doctor_payload(target_commit=INSTALLED_COMMIT, relation="same")

    monkeypatch.setattr(runtime_projection_route, "load_registry", load_registry)
    monkeypatch.setattr("loopx.self_update.collect_doctor", collect_bounded_doctor)
    monkeypatch.setattr("loopx.self_update.urlopen", lambda *_args, **_kwargs: FakeVersionResponse())

    started = time.monotonic()
    payload = build_update_plan(
        action=action,
        repo="example/loopx",
        ref="main",
    )
    elapsed = time.monotonic() - started
    release_stalled_read.set()

    assert elapsed < 0.5
    assert payload["ok"] is True
    assert observed["counts"] == {
        "healthy": 0,
        "ready": 0,
        "single_runtime": 0,
        "missing": 0,
        "unavailable": 1,
        "ambiguous": 0,
        "lagging": 0,
    }


def test_same_version_older_commit_requires_release_or_install_successor() -> None:
    payload = build_live_check(doctor_payload(), remote_commit=TARGET_COMMIT)

    assert payload["source_version_check"]["matches_current"] is True
    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "release_or_install_successor_required"
    assert activation["runtime_active"] is False
    assert activation["installed_source_commit"] == INSTALLED_COMMIT
    assert activation["target_source_commit"] == TARGET_COMMIT
    assert activation["successor"] == {
        "required": True,
        "kind": "release_or_install",
    }
    assert "project a release/install successor" in payload["recommended_action"]


def test_equal_commit_proves_runtime_active() -> None:
    payload = build_live_check(
        doctor_payload(target_commit=INSTALLED_COMMIT, relation="same"),
        remote_commit=INSTALLED_COMMIT,
    )

    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "runtime_active"
    assert activation["runtime_active"] is True
    assert activation["source_identity_matches"] is True
    assert activation["successor"] == {"required": False, "kind": None}


def test_injected_doctor_snapshot_does_not_prove_current_mutable_ref() -> None:
    payload = build_check(
        doctor_payload(target_commit=INSTALLED_COMMIT, relation="same")
    )
    assert payload["source_commit_check"]["status"] == "not_requested"
    assert payload["runtime_activation_qualification"]["decision"] == (
        "activation_qualification_required"
    )
    assert "no update needed" not in payload["recommended_action"]


def test_missing_commit_lineage_never_proves_runtime_active() -> None:
    payload = build_check(doctor_payload(target_commit=None, relation="same"))

    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "activation_qualification_required"
    assert activation["runtime_active"] is None
    assert activation["successor"] == {
        "required": True,
        "kind": "activation_qualification",
    }


def test_immutable_ref_resolves_the_target_commit_without_remote_lineage() -> None:
    immutable = INSTALLED_COMMIT
    payload = build_check(
        doctor_payload(
            target_commit=None,
            relation=None,
            source_ref=immutable,
        ),
        ref=immutable,
    )

    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "runtime_active"
    assert activation["runtime_active"] is True
    assert activation["target_source_commit"] == immutable
    assert activation["revision_relation"] == "same"
    assert activation["successor"] == {"required": False, "kind": None}
    assert payload["recommended_action"] == (
        "installed version and trusted source lineage match; no update needed"
    )


def test_immutable_ref_mismatch_names_the_commit_difference() -> None:
    payload = build_check(
        doctor_payload(
            target_commit=None,
            relation=None,
            source_ref=TARGET_COMMIT,
        ),
        ref=TARGET_COMMIT,
    )

    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "activation_qualification_required"
    assert activation["runtime_active"] is None
    assert activation["target_source_commit"] == TARGET_COMMIT
    assert activation["reason"] == (
        "installed source commit does not match the selected immutable source commit"
    )


def test_different_selected_source_never_reuses_unrelated_lineage() -> None:
    payload = build_check(
        doctor_payload(
            target_commit=INSTALLED_COMMIT, relation="same", source_ref="stable"
        ),
        ref="main",
    )

    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "activation_qualification_required"
    assert activation["runtime_active"] is None
    assert activation["source_identity_matches"] is False
    assert activation["qualified_source"]["ref"] == "stable"
    assert activation["selected_source"]["ref"] == "main"


def test_cli_check_qualifies_explicit_installed_doctor_snapshot(tmp_path: Path) -> None:
    doctor_path = tmp_path / "installed-doctor.json"
    doctor_path.write_text(json.dumps(doctor_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "update",
            "check",
            "--format",
            "json",
            "--repo",
            "example/loopx",
            "--ref",
            "main",
            "--archive-url",
            "https://example.invalid/loopx.tar.gz",
            "--installed-doctor-json",
            str(doctor_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["installed_doctor_source"] == "explicit_json"
    activation = payload["runtime_activation_qualification"]
    assert activation["decision"] == "activation_qualification_required"
    assert activation["installed_source_commit"] == INSTALLED_COMMIT
    assert activation["target_source_commit"] == TARGET_COMMIT
    assert activation["reason"] == "custom archive URL is not identified by the selected GitHub ref"


def test_update_actions_are_explicit_and_legacy_flags_remain_compatible() -> None:
    assert resolve_update_action("check") is UpdateAction.CHECK
    assert resolve_update_action("plan") is UpdateAction.PLAN
    assert resolve_update_action("apply") is UpdateAction.APPLY
    assert resolve_update_action(check=True) is UpdateAction.CHECK
    assert resolve_update_action(dry_run=True) is UpdateAction.PLAN
    assert resolve_update_action(execute=True) is UpdateAction.APPLY
    assert resolve_update_action() is UpdateAction.PLAN

    with pytest.raises(ValueError, match="conflicts with the legacy option"):
        resolve_update_action("check", execute=True)


def test_python_distribution_apply_uses_the_owning_interpreter_pip() -> None:
    doctor = doctor_payload()
    doctor["package"] = {
        "install_kind": "python_distribution",
        "release_root": None,
    }
    doctor["install_freshness"]["install_kind"] = "python_distribution"
    doctor["install_freshness"]["python_distribution_installer"] = "pip"
    doctor["install_freshness"]["upgrade_command"] = (
        "/managed/python -m pip install --upgrade loopx\n"
        "loopx workflow-skills --install\n"
        "loopx slash-commands --install\n"
        "loopx doctor"
    )

    payload = build_update_plan(action="apply", doctor_payload=doctor)

    assert payload["ok"] is True
    assert payload["requested_action"] == "apply"
    assert payload["changes_applied"] is False
    assert payload["install_lifecycle"] == {
        "install_kind": "python_distribution",
        "owner": "python_package_manager",
        "loopx_apply_supported": True,
        "execution_driver": "python_pip",
        "package_manager": "pip",
        "package_manager_environment": None,
        "reason": (
            "LoopX can ask the owning Python interpreter's pip to upgrade the same environment"
        ),
        "owner_upgrade_command": doctor["install_freshness"]["upgrade_command"],
    }
    assert payload["commands"]["apply"] == "loopx update apply"
    assert payload["commands"]["owner_upgrade"].startswith("/managed/python -m pip")
    assert payload["plan"]["install_command"] == payload["commands"]["owner_upgrade"]
    assert payload["plan"]["mutates_release_install"] is True
    assert payload["plan"]["backup"]["rollback_version"] == __version__
    assert f"loopx=={__version__}" in payload["plan"]["backup"]["rollback_command"]
    assert payload["error"] is None

    passed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"ok": true}',
        stderr="",
    )
    with (
        mock.patch(
            "loopx.self_update.subprocess.run",
            side_effect=[passed, passed, passed, passed, passed, passed],
        ) as run,
        mock.patch(
            "loopx.runtime_activation.restart_managed_loopx_services",
            return_value=["com.loopx.status"],
        ),
    ):
        result = execute_update_plan(payload)

    assert result["ok"] is True
    assert result["changes_applied"] is True
    assert result["execution"]["driver"] == "python_pip"
    assert result["execution"]["restarted_services"] == ["com.loopx.status"]
    assert run.call_args_list[0].args[0] == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "loopx",
    ]
    assert run.call_args_list[1].args[0][-4:] == ["authority-archive", "upgrade", "--all-known", "--execute"]
    assert run.call_args_list[2].args[0][3:5] == ["workflow-skills", "--install"]
    assert run.call_args_list[3].args[0][3:5] == ["slash-commands", "--install"]


def test_pipx_distribution_apply_preserves_the_pipx_environment() -> None:
    doctor = doctor_payload()
    doctor["package"] = {"install_kind": "python_distribution"}
    doctor["install_freshness"].update(
        {
            "install_kind": "python_distribution",
            "python_distribution_installer": "pipx",
            "python_distribution_installer_environment": "loopx-preview",
            "upgrade_command": "pipx upgrade loopx-preview\nloopx doctor",
        }
    )
    payload = build_update_plan(action="apply", doctor_payload=doctor)

    assert payload["install_lifecycle"]["execution_driver"] == "python_pipx"
    assert (
        payload["install_lifecycle"]["package_manager_environment"] == "loopx-preview"
    )
    assert payload["plan"]["backup"]["available"] is False

    passed = subprocess.CompletedProcess([], 0, '{"ok": true}', "")
    with (
        mock.patch(
            "loopx.self_update.subprocess.run",
            side_effect=[passed, passed, passed, passed, passed, passed],
        ) as run,
        mock.patch(
            "loopx.runtime_activation.restart_managed_loopx_services",
            return_value=[],
        ),
    ):
        result = execute_update_plan(payload)

    assert result["ok"] is True
    assert run.call_args_list[0].args[0] == ["pipx", "upgrade", "loopx-preview"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="archive snapshot updates require the POSIX installer path",
)
def test_archive_apply_restarts_managed_services_when_only_extensions_are_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = build_update_plan(action="apply", doctor_payload=doctor_payload())

    assert payload["install_lifecycle"]["execution_driver"] == "archive_snapshot"

    installed = subprocess.CompletedProcess([], 0, "", "")
    blocked = subprocess.CompletedProcess([], 1, '{"blocked_count": 1}', "")
    monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
    monkeypatch.setattr(
        "loopx.self_update.run_archive_installer",
        lambda *_args, **_kwargs: (installed, {"stage": "installer_execution"}),
    )
    monkeypatch.setattr(
        "loopx.self_update.subprocess.run",
        mock.Mock(side_effect=[installed, blocked]),
    )
    monkeypatch.setattr(
        "loopx.runtime_activation.restart_managed_loopx_services",
        lambda: ["com.loopx.chat"],
    )

    result = execute_update_plan(payload)

    # The runtime is installed and serving, so the managed services must move
    # onto it even though an unrelated enabled extension provider is blocked.
    assert result["changes_applied"] is True
    assert result["execution"]["restarted_services"] == ["com.loopx.chat"]
    assert result["execution"]["restart_status"] == "restarted"
    assert result["ok"] is False
    assert result["next_action"]["kind"] == "repair_blocked_extensions"
    assert result["next_action"]["mutating"] is False
    assert "rollback" not in result["recommended_action"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="archive snapshot updates require the POSIX installer path",
)
def test_archive_apply_skips_managed_service_restart_when_the_runtime_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = build_update_plan(action="apply", doctor_payload=doctor_payload())

    failed_install = subprocess.CompletedProcess([], 1, "", "installer failed")
    monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
    monkeypatch.setattr(
        "loopx.self_update.run_archive_installer",
        lambda *_args, **_kwargs: (failed_install, {"stage": "installer_execution"}),
    )
    monkeypatch.setattr(
        "loopx.self_update.subprocess.run",
        mock.Mock(return_value=failed_install),
    )
    restarted = mock.Mock(return_value=["com.loopx.chat"])
    monkeypatch.setattr("loopx.runtime_activation.restart_managed_loopx_services", restarted)

    result = execute_update_plan(payload)

    assert result["changes_applied"] is False
    assert result["ok"] is False
    assert result["execution"]["restarted_services"] == []
    assert result["execution"]["restart_status"] == "skipped_runtime_not_activated"
    assert result["next_action"]["kind"] == "review_or_rollback"
    restarted.assert_not_called()


def test_live_checkout_apply_never_mutates_git_or_switches_install_channels() -> None:
    doctor = doctor_payload()
    doctor["package"] = {"install_kind": "live_checkout", "release_root": None}
    doctor["install_freshness"]["contributor_upgrade_command"] = (
        "/workspace/loopx/scripts/install-local.sh\nloopx doctor"
    )

    payload = build_update_plan(action="apply", doctor_payload=doctor)

    assert payload["ok"] is False
    assert payload["install_lifecycle"]["owner"] == "source_checkout"
    assert payload["install_lifecycle"]["execution_driver"] is None
    assert payload["commands"]["apply"] is None
    assert payload["plan"]["install_command"].startswith("/workspace/loopx/")
    assert "git pull" not in payload["plan"]["install_command"]
    assert payload["changes_applied"] is False


def test_unknown_package_manager_apply_fails_without_guessing_pip() -> None:
    doctor = doctor_payload()
    doctor["package"] = {"install_kind": "python_distribution"}
    doctor["install_freshness"].update(
        {
            "install_kind": "python_distribution",
            "python_distribution_installer": "custom-manager",
            "upgrade_command": None,
        }
    )

    payload = build_update_plan(action="apply", doctor_payload=doctor)

    assert payload["ok"] is False
    assert payload["install_lifecycle"]["execution_driver"] is None
    assert payload["commands"]["apply"] is None
    assert payload["commands"]["owner_upgrade"] is None
    assert payload["next_action"]["command"] is None
    assert "pip install" not in payload["recommended_action"]


def test_cli_rejects_conflicting_named_and_legacy_actions() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "update",
            "check",
            "--execute",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["changes_applied"] is False
    assert "conflicts with the legacy option" in payload["error"]


def test_cli_rejects_installed_doctor_snapshot_outside_check(tmp_path: Path) -> None:
    doctor_path = tmp_path / "installed-doctor.json"
    doctor_path.write_text(json.dumps(doctor_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "update",
            "--format",
            "json",
            "--dry-run",
            "--installed-doctor-json",
            str(doctor_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "--installed-doctor-json requires `loopx update check`"


def test_restart_managed_loopx_services_restarts_only_loopx_launchagents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("loopx.runtime_activation.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    for name in (
        "com.loopx.status.plist",
        "com.loopx.chat.plist",
        "com.goal-harness.status.plist",
        "unrelated.plist",
    ):
        (agents / name).write_text("<plist/>", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", fake_run)

    restarted = restart_managed_loopx_services()
    assert set(restarted) == {
        "com.loopx.status",
        "com.loopx.chat",
        "com.goal-harness.status",
    }
    assert len(calls) == 3
    assert all(call[0] == "launchctl" for call in calls)


@pytest.mark.skipif(
    os.name == "nt",
    reason="archive snapshot updates require the POSIX installer path",
)
def test_successful_update_revalidates_enabled_extensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "source": {"installer_url": "https://example.invalid/install.sh"},
        "plan": {"backup": {}},
    }
    completed = [
        subprocess.CompletedProcess([], 0, "200", ""),
        subprocess.CompletedProcess([], 0, "installed", ""),
        subprocess.CompletedProcess([], 0, '{"ok": true}', ""),
        subprocess.CompletedProcess([], 0, '{"ok": true}', ""),
    ]
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if args[0] == "curl":
            Path(args[args.index("--output") + 1]).write_text("echo installed")
        return completed[len(calls) - 1]

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        "loopx.self_update._installer_env_for_source",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", fake_run)

    updated = execute_update_plan(payload)

    assert updated["ok"] is True
    assert updated["execution"]["extension_doctor_returncode"] == 0
    assert calls[3] == [
        str(tmp_path / ".local" / "bin" / "loopx"),
        "--format",
        "json",
        "extension",
        "doctor",
        "--all-enabled",
        "--execute",
    ]


@pytest.mark.skipif(os.name != "nt", reason="native Windows update boundary")
def test_windows_execute_update_fails_closed_without_launching_bash() -> None:
    payload = {"ok": True, "source": {}, "plan": {}}

    with mock.patch("loopx.self_update.subprocess.run") as run:
        updated = execute_update_plan(payload)

    run.assert_not_called()
    assert updated["ok"] is False
    assert updated["execution"]["status"] == "unsupported_platform"
    assert "install-windows.ps1" in updated["recommended_action"]


def test_failed_authority_upgrade_does_not_activate_services_or_continue_host_updates():
    doctor = doctor_payload()
    doctor["package"] = {"install_kind": "python_distribution", "release_root": None}
    doctor["install_freshness"].update(install_kind="python_distribution", python_distribution_installer="pip")
    payload = build_update_plan(action="apply", doctor_payload=doctor)
    with mock.patch("loopx.self_update.subprocess.run", side_effect=[
        subprocess.CompletedProcess([], 0, "installed", ""),
        subprocess.CompletedProcess([], 1, '{"status":"failed","reason":"backup failed"}', ""),
    ]) as run, mock.patch("loopx.runtime_activation.restart_managed_loopx_services") as restart:
        result = execute_update_plan(payload)
    assert not result["ok"]
    assert result["changes_applied"]  # Package installed; data migration failure is not a rollback.
    assert result["execution"]["authority_upgrade_returncode"] == 1
    assert result["execution"]["doctor_status"] == "skipped_prior_step_failed"
    assert run.call_count == 2
    restart.assert_not_called()
