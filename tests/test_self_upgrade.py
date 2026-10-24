"""One upgrade intent covers installed surfaces without inventing success."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
from unittest import mock

import pytest

from loopx.cli_commands.support_control_update import (
    handle_update_command,
    register_update_command,
)
from loopx.self_update import (
    UpdateAction,
    _installed_readback,
    render_update_plan_markdown,
)
from loopx.cli_commands.support_control_update_progress import UpdateProgress
from loopx.cli_commands.support_control_upgrade import (
    build_upgrade_plan,
    desktop_upgrade_plan,
    finish_upgrade,
    render_upgrade_markdown,
    _upgrade_desktop,
    _restart_app,
)


def test_app_restart_keeps_an_explicit_terminal_runtime_pin(monkeypatch):
    monkeypatch.setenv("LOOPX_BIN", "/synthetic/pinned/loopx")
    with mock.patch("loopx.cli_commands.support_control_upgrade.subprocess.run") as run:
        _restart_app(Path("/synthetic/LoopX.app"), "a" * 32, "main", 60)
    quit_command = run.call_args_list[0].args[0]
    launch_command = run.call_args_list[1].args[0]
    assert launch_command[:4] == ["/usr/bin/open", "-n", "-a", "/synthetic/LoopX.app"]
    assert quit_command[-1] == "/synthetic/LoopX.app"
    assert "/synthetic/LoopX.app" not in quit_command[2]
    assert (
        launch_command[launch_command.index("--env") + 1]
        == "LOOPX_BIN=/synthetic/pinned/loopx"
    )
    assert "--loopx-upgrade-channel=main" in launch_command


def test_upgrade_never_restarts_the_app_while_service_activation_is_deferred():
    payload = {"ok": False, "changes_applied": True,
               "execution": {"install_returncode": 0, "doctor_returncode": 0, "activation_pending": True},
               "desktop_upgrade": {"status": "planned"}}
    with mock.patch("loopx.cli_commands.support_control_upgrade._upgrade_desktop") as upgrade:
        result = finish_upgrade(payload, timeout_seconds=10)
    upgrade.assert_not_called()
    assert not result["ok"] and not result["upgrade_complete"]
    assert result["desktop_upgrade"]["status"] == "skipped_service_activation_pending"


def app_fixture(tmp_path: Path, version: str = "0.0.0-main.2.1") -> Path:
    app = tmp_path / "LoopX.app"
    runtime = app / "Contents/Resources/runtime"
    runtime.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.loopx.control-plane",
                "CFBundleShortVersionString": version,
            }
        )
    )
    (runtime / "identity.json").write_text(
        json.dumps(
            {
                "schema_version": "desktop_runtime_bundle_v1",
                "source_revision": "a" * 40,
            }
        )
    )
    return app


def snapshot_doctor(ref: str = "main") -> dict:
    return {
        "package": {
            "install_kind": "release_snapshot",
            "release_root": "/synthetic/releases/test",
        },
        "install_freshness": {
            "manifest_source_repo": "loopx-project/loopx",
            "manifest_source_ref": ref,
            "manifest_source_git_commit": "a" * 40,
        },
    }


def test_upgrade_preserves_archive_channel_and_never_installs_an_absent_app(
    monkeypatch,
):
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._applications", lambda: ())
    monkeypatch.delenv("LOOPX_REF", raising=False)
    monkeypatch.delenv("LOOPX_REPO", raising=False)
    for ref in ("main", "stable", "a" * 40):
        result = build_upgrade_plan(
            doctor_payload=snapshot_doctor(ref), action=UpdateAction.PLAN
        )
        assert result["source"]["ref"] == ref
        assert result["desktop_upgrade"]["status"] == "not_installed"
        assert result["changes_applied"] is False


def test_package_manager_ref_override_is_rejected_before_mutation(monkeypatch):
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._applications", lambda: ())
    doctor = {
        "package": {
            "install_kind": "python_distribution",
            "python_distribution": {"installer": "pip"},
        }
    }
    result = build_upgrade_plan(doctor_payload=doctor, ref="main")
    assert result["ok"] is False
    assert result["plan"]["apply_supported"] is False
    assert result["changes_applied"] is False


def test_multiple_apps_block_the_whole_plan_before_cli_mutation(monkeypatch, tmp_path):
    first = app_fixture(tmp_path / "system")
    second = app_fixture(tmp_path / "user")
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._applications", lambda: (first, second))
    result = build_upgrade_plan(doctor_payload=snapshot_doctor())
    assert result["desktop_upgrade"]["status"] == "ambiguous_installation"
    assert result["plan"]["apply_supported"] is False


@pytest.mark.parametrize(
    "command,expected", [("update", UpdateAction.PLAN), ("upgrade", UpdateAction.APPLY)]
)
def test_bare_upgrade_applies_while_bare_update_stays_read_only(
    monkeypatch, tmp_path, command, expected
):
    parser = argparse.ArgumentParser()
    register_update_command(parser.add_subparsers(dest="command"), lambda _parser: None)
    args = parser.parse_args([command])
    args.runtime_root = None
    seen = []

    def plan(**kwargs):
        seen.append(kwargs["action"])
        return {"ok": True, "plan": {"apply_supported": False}}

    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade.build_upgrade_plan", plan)
    monkeypatch.setattr(
        "loopx.cli_commands.support_control_update.build_update_plan", plan
    )
    handle_update_command(
        args,
        registry_path=tmp_path / "registry.json",
        registry_was_supplied=False,
        print_payload=lambda *_args: None,
        output_format=lambda _args: "json",
    )
    assert seen == [expected]


def test_app_success_requires_a_matching_request_and_native_service_readback(
    monkeypatch, tmp_path
):
    app = app_fixture(tmp_path)
    directory = tmp_path / "receipts"
    directory.mkdir()
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._applications", lambda: (app,))
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._receipt_directory", lambda: directory)
    plan = desktop_upgrade_plan(None)

    def restart(_app, request_id, channel, _timeout):
        (directory / f"{request_id}.json").write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "channel": channel,
                    "app_version": "0.0.0-main.2.1",
                    "bundled_source_revision": "a" * 40,
                    "phase": "ready",
                    "update_checked": True,
                    "pid": os.getpid(),
                    "runtime_identity": {
                        "schema_version": "loopx_runtime_identity_v1",
                        "source_revision": "b" * 40,
                    },
                }
            )
        )

    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._restart_app", restart)
    result = _upgrade_desktop(plan, 1)
    assert result["ok"] is True
    # A selected CLI may be newer than the bundled Core; equality is not required.
    assert result["readback"]["runtime_identity"]["source_revision"] == "b" * 40
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize(
    "patch",
    [
        {"request_id": "stale"},
        {"channel": "stable"},
        {"app_version": "0.0.0-main.1.1"},
        {"bundled_source_revision": "c" * 40},
        {"update_checked": False},
        {"runtime_identity": None},
    ],
)
def test_unknown_or_stale_app_readback_never_certifies_success(
    monkeypatch, tmp_path, patch
):
    app = app_fixture(tmp_path)
    directory = tmp_path / "receipts"
    directory.mkdir()
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._applications", lambda: (app,))
    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._receipt_directory", lambda: directory)

    def restart(_app, request_id, channel, _timeout):
        (directory / f"{request_id}.json").write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "channel": channel,
                    "app_version": "0.0.0-main.2.1",
                    "bundled_source_revision": "a" * 40,
                    "phase": "ready",
                    "update_checked": True,
                    "pid": os.getpid(),
                    "runtime_identity": {"source_revision": "b" * 40},
                    **patch,
                }
            )
        )

    monkeypatch.setattr("loopx.cli_commands.support_control_upgrade._restart_app", restart)
    result = _upgrade_desktop(desktop_upgrade_plan(None), 0.01)
    assert result["ok"] is False
    assert result["status"] == "readback_timeout"


def test_app_failure_preserves_cli_success_as_partial_result(monkeypatch):
    monkeypatch.setattr(
        "loopx.cli_commands.support_control_upgrade._upgrade_desktop",
        lambda *_args: {
            "ok": False,
            "status": "failed",
            "changes_applied": False,
        },
    )
    result = finish_upgrade(
        {
            "ok": True,
            "changes_applied": True,
            "upgrade_complete": True,
            "execution": {"installed_runtime_identity": {"package_version": "1.0.0"}},
            "desktop_upgrade": {"status": "planned"},
        },
        timeout_seconds=1,
    )
    assert result["ok"] is False
    assert result["upgrade_complete"] is False
    assert result["changes_applied"] is True


@pytest.mark.parametrize("prompt_status", ["current", "discovery_failed", "review_required"])
def test_cli_only_completion_includes_prompt_adoption(prompt_status):
    complete = prompt_status == "current"
    recovery = {"kind": "apply_host_prompt_updates", "requires_explicit_approval": True}
    result = finish_upgrade(
        {
            "ok": True,
            "changes_applied": True,
            "upgrade_complete": complete,
            "execution": {"installed_runtime_identity": {"package_version": "2.0.0"}},
            "automation_prompt_upgrade": {"status": prompt_status},
            "desktop_upgrade": {"status": "not_installed"},
            "recommended_action": "Review pending prompts through the App API.",
            "next_action": recovery,
        },
        timeout_seconds=1,
    )
    assert result["ok"] is complete
    assert result["upgrade_complete"] is complete
    assert result["changes_applied"] is True
    assert result["automation_prompt_upgrade"]["status"] == prompt_status
    assert result["recommended_action"] == "Review pending prompts through the App API."
    assert result["next_action"] == recovery


def test_cli_only_missing_identity_never_certifies_success():
    result = finish_upgrade(
        {
            "ok": True,
            "upgrade_complete": True,
            "changes_applied": True,
            "desktop_upgrade": {"status": "not_installed"},
        },
        timeout_seconds=1,
    )
    assert result["ok"] is False
    assert result["upgrade_complete"] is False
    assert result["changes_applied"] is True
    assert "runtime identity" in result["recommended_action"]


def test_cli_only_discovery_failure_exit_render_and_recovery(monkeypatch, tmp_path):
    home = tmp_path / "codex"
    database = home / "sqlite/codex-dev.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a SQLite database")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(
        "loopx.cli_commands.support_control_upgrade.build_upgrade_plan",
        lambda **_kwargs: {
            "ok": True,
            "execute_requested": True,
            "plan": {"apply_supported": True},
            "desktop_upgrade": {"status": "not_installed"},
        },
    )
    monkeypatch.setattr(
        "loopx.cli_commands.support_control_update.execute_update_plan",
        lambda payload, **_kwargs: {
            **payload,
            "ok": True,
            "changes_applied": True,
            "execution": {"installed_runtime_identity": {"package_version": "2.0.0"}},
        },
    )
    parser = argparse.ArgumentParser()
    register_update_command(parser.add_subparsers(dest="command"), lambda _parser: None)
    args = parser.parse_args(["upgrade"])
    args.runtime_root = str(tmp_path / "runtime")
    outputs = []

    def run():
        return handle_update_command(
            args,
            registry_path=tmp_path / "registry.json",
            registry_was_supplied=True,
            print_payload=lambda payload, _format, renderer: outputs.append(
                (payload, renderer(payload))
            ),
            output_format=lambda _args: "json",
        )

    assert run() == 1
    failed, text = outputs[-1]
    assert failed["upgrade_complete"] is False
    assert failed["changes_applied"] is True
    assert failed["automation_prompt_upgrade"]["status"] == "discovery_failed"
    assert text.startswith("LoopX upgrade incomplete.")
    assert failed["recommended_action"] in text
    assert render_upgrade_markdown(failed) == text
    # Repair only the synthetic discovery source, then retry the same command.
    database.unlink()
    assert run() == 0
    recovered, text = outputs[-1]
    assert recovered["upgrade_complete"] is True
    assert text.startswith("LoopX upgrade completed.")


def test_failed_cli_does_not_restart_the_app(monkeypatch):
    with mock.patch("loopx.cli_commands.support_control_upgrade._upgrade_desktop") as restart:
        result = finish_upgrade(
            {"ok": False, "desktop_upgrade": {"status": "planned"}}, timeout_seconds=1
        )
    restart.assert_not_called()
    assert result["desktop_upgrade"]["status"] == "skipped_runtime_update_failed"


def test_progress_stays_on_stderr_and_json_mode_is_quiet(capsys):
    progress = UpdateProgress(True)
    progress.report("Installing CLI and building workspace resources")
    progress.stop()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Installing CLI" in captured.err
    progress = UpdateProgress(False)
    progress.report("Checking")
    progress.stop()
    assert capsys.readouterr().err == ""


def test_post_install_identity_is_separate_from_the_pre_update_plan():
    result = mock.Mock(
        stdout=json.dumps(
            {
                "service_runtime_identity": {
                    "package_version": "1.2.0",
                    "source_revision": "b" * 40,
                }
            }
        )
    )
    identity = _installed_readback(result)
    text = render_update_plan_markdown(
        {
            "execute_requested": True,
            "execution": {"installed_runtime_identity": identity},
        }
    )
    assert text.index("## Installed CLI Readback") < text.index(
        "## Plan (before execution)"
    )
    assert "b" * 40 in text
