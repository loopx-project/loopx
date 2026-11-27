from __future__ import annotations

import csv
from importlib.metadata import PathDistribution
import json
import os
from pathlib import Path
import threading
import time

import pytest

from loopx import doctor
from loopx.control_plane.runtime import runtime_projection_route


def test_python_distribution_does_not_require_release_installation_scripts(
    tmp_path: Path,
) -> None:
    absent_script = tmp_path / "site-packages" / "scripts" / "install-windows.ps1"

    check = doctor.optional_installation_script_check(
        "install_script_exists",
        exists=absent_script.exists(),
        path=absent_script,
        distribution_install=True,
    )

    assert check == {
        "id": "install_script_exists",
        "required": False,
        "ok": True,
        "applicable": False,
        "detail": "not applicable: Python package installs do not include release installer scripts",
    }


def test_release_installation_still_reports_missing_scripts(tmp_path: Path) -> None:
    absent_script = tmp_path / "release" / "scripts" / "install-windows.ps1"

    check = doctor.optional_installation_script_check(
        "install_script_exists",
        exists=absent_script.exists(),
        path=absent_script,
        distribution_install=False,
    )

    assert check["ok"] is False
    assert check["applicable"] is True
    assert check["detail"] == str(absent_script)


def test_doctor_cli_forwards_the_selected_registry_and_runtime(monkeypatch, tmp_path):
    import argparse
    from loopx.cli_commands import doctor as command
    registry = tmp_path / "registry.json"
    root = tmp_path / "runtime"
    captured = {}
    def collect(**kwargs):
        captured.update(kwargs)
        return {"ok": True}
    monkeypatch.setattr(command, "collect_doctor", collect)
    args = argparse.Namespace(deep=False, agent_type=None, installation_only=False,
                              runtime_root=str(root), format="json")
    assert command.handle_doctor_command(args, lambda *args: None, registry_path=registry) == 0
    assert captured["registry_path"] == registry
    assert captured["runtime_root_override"] == str(root)


def test_configured_doctor_route_does_not_hide_implicit_conflict(monkeypatch, tmp_path):
    from loopx import paths
    legacy, current = tmp_path / "legacy", tmp_path / "current"
    for root in [legacy, current]:
        root.mkdir()
        (root / "registry.global.json").write_text(json.dumps({"common_runtime_root": str(root), "goals": []}))
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    implicit = paths.default_runtime_route()
    assert implicit["status"] == "conflict"
    configured = paths.configured_runtime_route(registry_path=legacy / "registry.global.json")
    assert configured["status"] == "configured"
    assert configured["selected_runtime_root"] == str(legacy)
    assert configured["default_status"] == "conflict"
    assert paths.default_runtime_route() == implicit


@pytest.mark.parametrize(
    "failed_check",
    [None, "typescript_effect_runtime_ready", "representative_cli_imports"],
)
def test_installation_scope_preserves_required_checks_without_opening_user_state(
    monkeypatch, failed_check
):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "installation validation must not inspect user projects/integrations"
        )

    for name in [
        "installed_skill_summary",
        "probe_registry_write_path",
        "latest_promotion_readiness_event",
    ]:
        monkeypatch.setattr(doctor, name, forbidden)
    monkeypatch.setattr(
        "loopx.control_plane.runtime.runtime_projection_route.collect_runtime_projection_route_diagnostics",
        forbidden,
    )
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {
            "ready": failed_check != "typescript_effect_runtime_ready",
            "status": "ready"
            if failed_check != "typescript_effect_runtime_ready"
            else "missing",
            "deep": deep,
        },
    )
    required = [
        "command_package_same_root",
        "representative_cli_commands",
        "representative_cli_imports",
        "representative_package_paths",
    ]
    monkeypatch.setattr(
        "loopx.release_candidate.collect_deep_install_checks",
        lambda **kwargs: {
            "checks": [
                {"id": name, "required": True, "ok": name != failed_check}
                for name in required
            ]
        },
    )
    result = doctor.collect_doctor(deep=True, installation_only=True)
    assert result["scope"] == "installation_only"
    assert result["mode"] == "deep"
    assert result["ok"] is (failed_check is None)
    assert result["typescript_control_plane"]["deep"] is True
    assert set(required) <= {check["id"] for check in result["checks"]}


def test_installation_scope_cannot_claim_host_integration_health():
    from loopx.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(
        ["doctor", "--deep", "--installation-only"]
    ).installation_only
    with pytest.raises(SystemExit):
        parser.parse_args(["doctor", "--installation-only", "--agent-type", "codex"])


def test_installation_scope_uses_current_distribution_and_console_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from loopx import release_candidate

    invocation = tmp_path / "current" / "bin" / "loopx"
    other_command = tmp_path / "old" / "bin" / "loopx"
    distribution_root = tmp_path / "current" / "site-packages"
    observed: dict = {}
    monkeypatch.setattr(doctor, "doctor_invocation_path", lambda: (invocation, True))
    monkeypatch.setattr(
        "loopx.command_invocation.resolve_command_path", lambda _name: other_command
    )

    def distribution_install(module_path: Path) -> dict:
        observed["module_path"] = module_path
        return {"root": str(distribution_root)}

    def deep_checks(**kwargs) -> dict:
        observed["deep_checks"] = kwargs
        return {"checks": [{"id": "distribution_check", "required": True, "ok": True}]}

    monkeypatch.setattr(doctor, "python_distribution_install", distribution_install)
    monkeypatch.setattr(release_candidate, "collect_deep_install_checks", deep_checks)
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": True, "status": "ready"},
    )

    result = release_candidate.collect_installation_doctor(deep=True)
    assert result["ok"] is True
    assert result["path"]["loopx"] == str(invocation)
    assert observed["module_path"] == Path(doctor.__file__).resolve()
    assert observed["deep_checks"]["invocation_path"] == invocation
    assert observed["deep_checks"]["distribution_root"] == str(distribution_root)


@pytest.mark.parametrize("module", ["cli.py", "entrypoint.py"])
@pytest.mark.parametrize(
    "launcher_record",
    ["owned", "missing", "missing_file", "redirected", "ambiguous", "other_distribution", "source_manifest"],
)
def test_both_doctors_keep_packaged_module_readback_on_its_python_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module: str, launcher_record: str,
) -> None:
    module_path = Path(doctor.__file__).resolve()
    owned_console = tmp_path / ("loopx.exe" if os.name == "nt" else "loopx")
    if launcher_record not in {"missing_file", "redirected"}:
        owned_console.touch(mode=0o700)
    foreign_console = tmp_path / "other" / owned_console.name
    foreign_console.parent.mkdir()
    foreign_console.touch(mode=0o700)
    if launcher_record == "redirected":
        try:
            owned_console.symlink_to(foreign_console)
        except OSError:
            if os.name != "nt":
                raise
            pytest.skip("console script symlinks unavailable")
    dist_info = tmp_path / "loopx.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: loopx\nVersion: 2.4.0\n", encoding="utf-8")
    recorded_module = (
        tmp_path / "other/loopx/doctor.py" if launcher_record == "other_distribution" else module_path
    )
    if launcher_record == "source_manifest":
        (dist_info / "SOURCES.txt").write_text(f"{module_path.as_posix()}\n", encoding="utf-8")
    else:
        with (dist_info / "RECORD").open("w", encoding="utf-8", newline="") as record:
            writer = csv.writer(record)
            writer.writerow([recorded_module.as_posix(), "", ""])
            if launcher_record != "missing":
                writer.writerow([owned_console.as_posix(), "", ""])
            if launcher_record == "ambiguous":
                writer.writerow([foreign_console.as_posix(), "", ""])
    monkeypatch.setattr(doctor, "distribution", lambda _name: PathDistribution(dist_info))
    monkeypatch.setattr(doctor.sys, "argv", [str(module_path.with_name(module)), "doctor"])
    def find_command(name):
        return foreign_console if name == "loopx" else None

    monkeypatch.setattr(doctor, "resolve_command_path", find_command)
    monkeypatch.setattr("loopx.command_invocation.resolve_command_path", find_command)
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": True, "status": "ready"},
    )

    runtime = tmp_path / "runtime"
    result = doctor.collect_doctor(runtime_root_override=str(runtime))
    installation = doctor.collect_doctor(installation_only=True)
    assert result["path"]["loopx_on_path"] == str(foreign_console)
    expected = {
        "owned": str(owned_console),
        "missing": None,
        "missing_file": None,
        "redirected": None,
        "ambiguous": None,
        "other_distribution": str(foreign_console),
        "source_manifest": str(foreign_console),
    }
    assert result["path"]["loopx"] == installation["path"]["loopx"] == expected[launcher_record]
    checks = {check["id"]: check for check in result["checks"]}
    available = expected[launcher_record] is not None
    assert checks["command_available"]["ok"] is available
    assert checks["command_resolves"]["ok"] is available
    assert installation["ok"] is available


def test_owned_python_module_does_not_retarget_bound_macos_services(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from loopx import runtime_activation

    script = tmp_path / "loopx"
    script.touch(mode=0o700)
    distribution = SimpleNamespace(
        name="loopx",
        read_text=lambda name: "" if name == "RECORD" else None,
        files=[script],
    )
    monkeypatch.setattr(doctor, "_python_distribution_owning_module", lambda _path: distribution)
    monkeypatch.setattr(doctor.sys, "argv", [str(Path(doctor.__file__).with_name("cli.py")), "update"])
    monkeypatch.setattr(runtime_activation.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_activation, "_managed_service_plists", lambda: [])
    monkeypatch.setattr(runtime_activation, "managed_chat_activation_guard", lambda _plists: nullcontext())
    monkeypatch.setattr("loopx.hosts.managed_service_runtime.has_runtime_binding", lambda: True)
    aligned = []
    monkeypatch.setattr(
        "loopx.hosts.managed_service_runtime.align_managed_runtime",
        lambda path: aligned.append(path) or {"restarted_services": []},
    )

    result = runtime_activation.restart_services_for_runtime_activation(
        changes_applied=True, runtime_ready=True,
    )

    assert result["restart_status"] == "deferred"
    assert result["restart_reason"] == "service_runtime_unverified"
    assert result["activation_pending"] is True
    assert not aligned


def test_runtime_projection_diagnostics_bound_one_stalled_source_and_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    global_registry = runtime_root / "registry.global.json"
    stalled_registry = tmp_path / "offline" / "registry.json"
    healthy_registry = tmp_path / "healthy" / "registry.json"
    healthy_registry.parent.mkdir()
    healthy_registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [{"id": "healthy-goal"}],
            }
        ),
        encoding="utf-8",
    )
    global_registry.write_text(
        json.dumps(
            {
                "registry_role": "global-local",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "offline-goal",
                        "source_registry": str(stalled_registry),
                    },
                    {
                        "id": "healthy-goal",
                        "source_registry": str(healthy_registry),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    stalled_registry.parent.mkdir()
    stalled_registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [{"id": "offline-goal"}],
            }
        ),
        encoding="utf-8",
    )

    real_load_registry = runtime_projection_route.load_registry
    release_stalled_read = threading.Event()

    def load_registry(path: Path):
        if path == stalled_registry:
            release_stalled_read.wait(timeout=2)
        return real_load_registry(path)

    monkeypatch.setattr(runtime_projection_route, "load_registry", load_registry)
    started = time.monotonic()
    first = runtime_projection_route.collect_runtime_projection_route_diagnostics(
        registry_path=global_registry,
        runtime_root=runtime_root,
        source_registry_read_timeout_seconds=0.02,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert first["healthy"] is False
    assert first["counts"]["unavailable"] == 1
    assert first["counts"]["single_runtime"] == 1
    unavailable = next(
        item for item in first["items"] if item["goal_id"] == "offline-goal"
    )
    assert unavailable == {
        "goal_id": "offline-goal",
        "status": "unavailable",
        "reason": "source_registry_timeout",
    }
    assert str(tmp_path) not in json.dumps(unavailable)

    release_stalled_read.set()
    second = runtime_projection_route.collect_runtime_projection_route_diagnostics(
        registry_path=global_registry,
        runtime_root=runtime_root,
        source_registry_read_timeout_seconds=0.2,
    )

    assert second["counts"]["unavailable"] == 0
    assert second["counts"]["single_runtime"] == 2


def test_collect_companion_distribution_diagnostics_covers_all_core_companions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from types import SimpleNamespace

    from loopx import release_candidate, release_manifest
    from loopx.capabilities.agent_turn_recall import catalog_entry as recall_catalog
    from loopx.capabilities.benchmark_toolkit import catalog_entry as benchmark_catalog
    from loopx.capabilities.deep_research import catalog_entry as research_catalog
    from loopx.capabilities.issue_fix import catalog_entry as issue_fix_catalog
    from loopx.capabilities.pr_review_queue import catalog_entry as pr_review_catalog
    from loopx import cli_runtime

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args, **kwargs: None if name == "loopx.cli" else real_find_spec(name, *args, **kwargs),
    )
    monkeypatch.setattr(
        release_candidate,
        "installed_loopx_distribution",
        lambda _lookup: SimpleNamespace(name="loopx-core"),
    )

    def _stub(
        dist: str,
        *,
        installed: bool = False,
        ready: bool = False,
        version: str | None = None,
        core_version: str | None = "0.1.0",
        reasons: list[str] | None = None,
        missing_modules: list[str] | None = None,
    ) -> dict[str, object]:
        actual_reasons = list(reasons or ([] if ready else ["not_installed"]))
        return {
            "distribution": dist,
            "installed": installed,
            "ready": ready,
            "version": version,
            "core_version": core_version,
            "reason": actual_reasons[0] if actual_reasons else None,
            "reasons": actual_reasons,
            "missing_modules": list(missing_modules or []),
            "missing_resources": [],
            "unowned_modules": [],
            "unowned_resources": [],
        }

    states: dict[str, dict[str, object]] = {}
    recorded_order: list[str] = []

    def _bind(module: object, attr: str, dist: str) -> None:
        def _inspect(*, builtin_cli_present: bool | None = None) -> dict[str, object]:
            assert builtin_cli_present is False
            recorded_order.append(dist)
            return states[dist]

        monkeypatch.setattr(module, attr, _inspect)

    bindings = (
        (cli_runtime, "inspect_workspace_distribution", "loopx-workspace"),
        (benchmark_catalog, "inspect_benchmark_toolkit_distribution", "loopx-capability-benchmark-toolkit"),
        (recall_catalog, "inspect_agent_turn_recall_distribution", "loopx-capability-agent-turn-recall"),
        (pr_review_catalog, "inspect_pull_request_review_distribution", "loopx-capability-pull-request-review"),
        (issue_fix_catalog, "inspect_issue_fix_workflow_distribution", "loopx-capability-issue-fix"),
        (research_catalog, "inspect_research_workflow_distribution", "loopx-capability-research"),
    )
    assert tuple(dist for _, _, dist in bindings) == release_manifest._CORE_COMPANION_DISTRIBUTIONS
    for module, attr, dist in bindings:
        _bind(module, attr, dist)

    # 1. Bare loopx-core: no companions installed, healthy fingerprint.
    states = {dist: _stub(dist) for dist in release_manifest._CORE_COMPANION_DISTRIBUTIONS}
    companions, checks, fix = release_candidate.collect_companion_distribution_diagnostics(
        runtime_identity={"package_fingerprint": "sha256:abc"},
    )
    assert recorded_order == list(release_manifest._CORE_COMPANION_DISTRIBUTIONS)
    assert companions == []
    assert fix is None
    assert {c["id"]: c for c in checks}["companion_distributions_compatible"] == {
        "id": "companion_distributions_compatible",
        "required": False,
        "ok": True,
        "applicable": False,
        "detail": "no optional companion distributions installed",
    }

    # 2. [standard] installed cleanly + missing fingerprint -> fix names installed companions, not research.
    for dist in (
        "loopx-capability-agent-turn-recall",
        "loopx-capability-pull-request-review",
        "loopx-capability-issue-fix",
    ):
        states[dist] = _stub(dist, installed=True, ready=True, version="0.1.0", core_version="0.1.0")
    companions, checks, fix = release_candidate.collect_companion_distribution_diagnostics(
        runtime_identity={},
    )
    assert [c["distribution"] for c in companions] == [
        "loopx-capability-agent-turn-recall",
        "loopx-capability-pull-request-review",
        "loopx-capability-issue-fix",
    ]
    checks_by_id = {c["id"]: c for c in checks}
    assert checks_by_id["companion_distributions_compatible"]["required"] is True
    assert checks_by_id["companion_distributions_compatible"]["ok"] is True
    assert checks_by_id["package_runtime_fingerprint"]["required"] is True
    assert checks_by_id["package_runtime_fingerprint"]["ok"] is False
    assert fix is not None
    assert "loopx-core" in fix
    assert "loopx-capability-research" not in fix
    for dist in (
        "loopx-capability-agent-turn-recall",
        "loopx-capability-pull-request-review",
        "loopx-capability-issue-fix",
    ):
        assert dist in fix

    # 3. Failing non-research companion -> companion_distributions_compatible fails with exact distribution & fix.
    states["loopx-capability-issue-fix"] = _stub(
        "loopx-capability-issue-fix",
        installed=True,
        ready=False,
        version="0.0.9",
        core_version="0.1.0",
        reasons=["version_mismatch", "missing_files"],
        missing_modules=["loopx.capabilities.issue_fix.workflow_plan"],
    )
    companions, checks, fix = release_candidate.collect_companion_distribution_diagnostics(
        runtime_identity={},
    )
    checks_by_id = {c["id"]: c for c in checks}
    assert checks_by_id["companion_distributions_compatible"]["required"] is True
    assert checks_by_id["companion_distributions_compatible"]["ok"] is False
    assert "loopx-capability-issue-fix==0.0.9" in checks_by_id["companion_distributions_compatible"]["detail"]
    assert "loopx.capabilities.issue_fix.workflow_plan" in checks_by_id["companion_distributions_compatible"]["detail"]
    assert fix == "Reinstall matching `loopx-capability-issue-fix==0.1.0` or uninstall `loopx-capability-issue-fix`."


@pytest.mark.parametrize(
    "raw_direct_url",
    [
        "{corrupt-json",
        "",
        "[]",
        json.dumps({"dir_info": None}),
        json.dumps({"dir_info": "invalid"}),
        OSError("unreadable"),
    ],
)
def test_inspect_builtin_capability_distribution_handles_corrupt_direct_url_metadata(
    monkeypatch: pytest.MonkeyPatch,
    raw_direct_url: str | Exception,
) -> None:
    from types import SimpleNamespace

    from loopx import python_install_owner

    package_parent = Path(python_install_owner.__file__).resolve().parent.parent
    module_path = package_parent / "loopx" / "capabilities" / "issue_fix" / "workflow_plan.py"
    assert module_path.is_file()

    def _read_text(name: str) -> str | None:
        if name == "direct_url.json":
            if isinstance(raw_direct_url, Exception):
                raise raw_direct_url
            return raw_direct_url
        if name == "RECORD":
            return "loopx/capabilities/issue_fix/workflow_plan.py,,\n"
        return None

    fake_dist = SimpleNamespace(
        version="2.4.1",
        read_text=_read_text,
        files=[],
        locate_file=lambda path: package_parent / str(path),
    )
    monkeypatch.setattr(
        python_install_owner,
        "distribution",
        lambda name: fake_dist if name in {"loopx-core", "loopx-capability-issue-fix"} else (_ for _ in ()).throw(KeyError(name)),
    )
    monkeypatch.setattr(
        python_install_owner,
        "find_spec",
        lambda module: SimpleNamespace(origin=str(module_path)) if module == "loopx.capabilities.issue_fix.workflow_plan" else None,
    )

    state = python_install_owner.inspect_builtin_capability_distribution(
        "loopx-capability-issue-fix",
        ("loopx.capabilities.issue_fix.workflow_plan",),
        builtin_cli_present=False,
    )

    assert state["installed"] is True
    assert state["ready"] is False
    assert "invalid_companion_metadata" in state["reasons"]
    assert state["reason"] == "invalid_companion_metadata"
    assert not python_install_owner.builtin_capability_installed(
        "loopx-capability-issue-fix",
        ("loopx.capabilities.issue_fix.workflow_plan",),
        builtin_cli_present=False,
    )


def test_read_editable_direct_url_rejects_null_dir_info_and_unreadable_file_on_path_distribution(
    tmp_path: Path,
) -> None:
    from loopx import __version__, python_install_owner, release_manifest

    dist_info = tmp_path / "loopx_capability_issue_fix-2.4.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: loopx-capability-issue-fix\nVersion: {__version__}\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "loopx/capabilities/issue_fix/workflow_plan.py,,\n",
        encoding="utf-8",
    )
    installed = PathDistribution(dist_info)

    # Absent direct_url.json is a valid non-editable wheel install.
    assert python_install_owner.read_editable_direct_url(installed) is False
    assert "loopx/capabilities/issue_fix/workflow_plan.py" in release_manifest._owned_runtime_files(installed)

    direct_url = dist_info / "direct_url.json"
    direct_url.write_text(json.dumps({"dir_info": None}), encoding="utf-8")
    assert python_install_owner.read_editable_direct_url(installed) is None
    assert release_manifest._owned_runtime_files(installed) == {}

    if os.name != "nt" and os.geteuid() != 0:
        direct_url.write_text(json.dumps({"url": "file:///wheel.whl"}), encoding="utf-8")
        direct_url.chmod(0o000)
        try:
            assert python_install_owner.read_editable_direct_url(installed) is None
            assert release_manifest._owned_runtime_files(installed) == {}
        finally:
            direct_url.chmod(0o644)

    direct_url.unlink()
    direct_url.mkdir()
    try:
        assert python_install_owner.read_editable_direct_url(installed) is None
        assert release_manifest._owned_runtime_files(installed) == {}
    finally:
        direct_url.rmdir()
