from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from loopx import doctor
from loopx.control_plane.runtime import runtime_projection_route


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
    monkeypatch.setattr(doctor, "current_script_invocation_path", lambda: invocation)
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
