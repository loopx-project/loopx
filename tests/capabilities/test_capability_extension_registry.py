from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loopx.capabilities.catalog import (
    build_capability_catalog_packet,
    build_capability_detail_packet,
    build_capability_registry,
)
from loopx.capabilities.context_providers import (
    OpenVikingContextProvider,
    build_context_provider,
)
from loopx.cli import main
from loopx.extensions.runtime import (
    default_extension_state_file,
    disable_extension,
    install_extension,
)

BUILTIN_IDS = [
    "benchmark-toolkit",
    "integration-branch-reconcile",
    "repository-change-window",
    "change-quality-qualification",
    "pull-request-review",
    "issue-fix",
    "decision-context",
    "project-skill-delivery",
    "material-lifecycle",
    "agent-turn-recall",
    "semantic-preference",
    "reward-memory",
    "periodic-report",
    "content-ops",
    "value-connectors",
    "explore",
    "deep-research",
    "public-safe-outbound",
    "connector-registry",
    "reliability-diagnostics",
]


def test_auto_research_is_not_a_product_catalog_capability() -> None:
    """Auto-research is a demo showcase, not a shipped product capability."""

    packet = build_capability_catalog_packet()
    ids = [item["id"] for item in packet["capabilities"]]
    assert "auto-research" not in ids


def test_benchmark_toolkit_catalog_exposes_integrity_boundary() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]

    assert capability["provider_id"] == "loopx-core"
    assert capability["entry_command"] == "loopx benchmark --help"
    assert any(
        "integrity-qualification" in command["command"]
        for command in capability["commands"]
    )
    assert any(
        "continuation-decision" in command["command"]
        for command in capability["commands"]
    )
    assert any(
        protocol["schema_version"] == "benchmark_public_progress_v0"
        for protocol in capability["implemented_protocols"]
    )
    assert any(
        protocol["schema_version"] == "benchmark_continuation_decision_v0"
        for protocol in capability["implemented_protocols"]
    )
    assert any(
        protocol["schema_version"] == "benchmark_integrity_qualification_v0"
        for protocol in capability["implemented_protocols"]
    )
    assert any(
        protocol["schema_version"] == "benchmark_restricted_access_adjudication_v0"
        for protocol in capability["implemented_protocols"]
    )


def _write_manifest(
    path: Path,
    *,
    capability_id: str = "sample-report",
    entrypoint: Path | None = None,
) -> Path:
    runtime = (
        ""
        if entrypoint is None
        else f"""\

[runtime]
protocol = "sample_report_provider_v0"
entrypoint = {json.dumps(str(entrypoint))}
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 5
"""
    )
    path.write_text(
        f'''\
schema_version = "loopx_extension_manifest_v0"
id = "sample-extension"
version = "1.2.3"
requires_loopx_api = ">=1,<2"
permissions = ["read_status"]
{runtime}

[[provides]]
id = "{capability_id}"
kind = "projection_sink"
title = "Sample report"
status = "active"
visibility = "public"
real_world_anchor = "deterministic extension fixture"
user_value = "Prove provider-aware capability composition."
entry_command = "sample-extension report"
next_real_step = "Keep the fixture bounded."

[[provides]]
id = "sample-internal-helper"
kind = "provider_helper"
title = "Sample internal helper"
status = "internal"
visibility = "internal"
real_world_anchor = "extension implementation detail"
user_value = "Remain hidden from the public catalog."
entry_command = ""
next_real_step = "Remain internal."
''',
        encoding="utf-8",
    )
    return path


def test_builtin_catalog_preserves_order_and_marks_provider() -> None:
    packet = build_capability_catalog_packet()

    assert packet["schema_version"] == "loopx_capability_catalog_v0"
    assert [item["id"] for item in packet["capabilities"]] == BUILTIN_IDS
    for item in packet["capabilities"]:
        assert item["origin"] == "builtin"
        assert item["visibility"] == "public"
        assert item["provider_id"] == "loopx-core"
    assert packet["providers"] == [
        {
            "id": "loopx-core",
            "origin": "builtin",
            "declared": True,
            "installed": True,
            "enabled": True,
            "ready": True,
        },
        {
            "id": "dsh-session-events",
            "origin": "extension",
            "declared": True,
            "installed": False,
            "enabled": False,
            "ready": False,
        },
    ]


def test_material_lifecycle_catalog_exposes_managed_project_skill() -> None:
    capability = build_capability_detail_packet("material-lifecycle")["capability"]

    assert capability["default_enabled"] is False
    assert capability["workflow_skill"] == {
        "name": "loopx-material",
        "delivery": "project_managed_copy",
        "activation": "explicit_project_install_plus_goal_authority",
        "project_copy_required": True,
        "install_command": (
            "loopx project-skill install --project . --skill "
            "loopx-material --surface codex --execute"
        ),
    }


def test_benchmark_toolkit_catalog_exposes_packaged_task_triggered_skill() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]

    assert capability["origin"] == "builtin"
    assert capability["workflow_skill"] == {
        "name": "loopx-benchmark",
        "delivery": "packaged_host_skill",
        "activation": "task_triggered",
        "goal_switch_required": False,
        "project_copy_required": False,
        "install_command": "loopx workflow-skills --install",
        "readback_command": "loopx doctor",
        "authority_boundary": (
            "Skill discovery does not grant runner, shell, network, credential, "
            "private-evidence, or Goal mutation authority; todo requirements, "
            "provider bindings, and host permissions remain authoritative."
        ),
    }


def test_benchmark_toolkit_separates_monitor_observation_from_delivery() -> None:
    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]
    handoff = capability["campaign_monitor_handoff"]

    assert "continuous_monitor observes" in handoff["lane_boundary"]
    command = handoff["material_transition_command"]
    for required_option in (
        "--material-change",
        "--next-agent-todo",
        "--next-action-kind",
        "--next-task-repository",
        "--next-required-capability",
    ):
        assert required_option in command
    assert handoff["material_transition_result"] == {
        "monitor_status": "open",
        "successor_task_class": "advancement_task",
        "successor_relationship": "independent_runnable_work",
        "quota_spend": False,
    }
    external_wait = handoff["external_wait_command"]
    assert "--status open" in external_wait
    assert "--resume-when monitor_changed:<monitor-todo-id>" in external_wait
    assert "--successor-todo-id <independent-runnable-successor-id>" in external_wait
    assert "Do not mark it blocked" in handoff["external_wait_result"]


def test_change_quality_catalog_exposes_default_off_managed_workflow() -> None:
    capability = build_capability_detail_packet("change-quality-qualification")[
        "capability"
    ]

    assert capability["default_enabled"] is False
    assert capability["workflow_skill"] == {
        "name": "loopx-change-quality",
        "delivery": "project_managed_copy",
        "activation": "explicit_project_install_plus_goal_policy",
        "project_copy_required": True,
        "install_command": (
            "loopx project-skill install --project . --skill "
            "loopx-change-quality --surface codex --execute"
        ),
    }
    assert "safe_fix permits at most one bounded repair pass" in "\n".join(
        capability["boundaries"]
    )


def test_issue_fix_catalog_is_default_off() -> None:
    capability = build_capability_detail_packet("issue-fix")["capability"]

    assert capability["default_enabled"] is False


def test_project_skill_delivery_catalog_is_host_neutral() -> None:
    capability = build_capability_detail_packet("project-skill-delivery")[
        "capability"
    ]

    assert capability["status"] == "active-preview"
    assert capability["entry_command"].startswith("loopx project-skill status")
    command_text = "\n".join(
        str(item["command"]) for item in capability["commands"]
    )
    assert "project-skill install" in command_text
    assert "project-skill uninstall" in command_text
    assert {
        protocol["schema_version"]
        for protocol in capability["implemented_protocols"]
    } == {
        "loopx_project_skill_status_v0",
        "loopx_managed_project_skill_v0",
    }


def test_periodic_report_catalog_exposes_extension_boundary_contracts() -> None:
    detail = build_capability_detail_packet("periodic-report")
    capability = detail["capability"]

    assert capability["default_enabled"] is False
    assert capability["entry_command"].startswith(
        "loopx periodic-report inspect-profile"
    )
    assert {
        protocol["schema_version"] for protocol in capability["implemented_protocols"]
    } >= {
        "periodic_report_profile_v0",
        "periodic_report_activation_v0",
        "periodic_report_generation_bundle_v0",
        "periodic_report_generation_receipt_v0",
        "periodic_report_sink_binding_v0",
        "periodic_report_extension_readiness_v0",
        "periodic_report_delivery_receipt_v0",
    }
    assert "python3 examples/periodic-report-bindings-smoke.py" in capability["smokes"]


def test_declared_manifest_composes_public_capability_without_claiming_readiness(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "extension.toml")

    packet = build_capability_catalog_packet([manifest])
    assert [item["id"] for item in packet["capabilities"]] == [
        *BUILTIN_IDS,
        "sample-report",
    ]
    extension = packet["capabilities"][-1]
    assert extension["origin"] == "extension"
    assert extension["visibility"] == "public"
    assert extension["provider_id"] == "sample-extension"
    assert packet["providers"][-1] == {
        "id": "sample-extension",
        "origin": "extension",
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
        "version": "1.2.3",
        "requires_loopx_api": ">=1,<2",
        "permissions": ["read_status"],
    }
    assert extension["provider_state"] == {
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
    }

    detail = build_capability_detail_packet("sample-report", [manifest])
    assert detail["capability"]["capability_kind"] == "projection_sink"
    assert detail["capability"]["provider_version"] == "1.2.3"


def test_internal_capability_is_registered_but_not_publicly_listed(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "extension.toml")
    registry = build_capability_registry([manifest])

    assert "sample-internal-helper" not in registry.capability_ids()
    assert "sample-internal-helper" in registry.capability_ids(include_internal=True)
    assert (
        registry.get("sample-internal-helper", include_internal=True)["visibility"]
        == "internal"
    )


def test_duplicate_capability_fails_closed(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "extension.toml",
        capability_id="value-connectors",
    )

    with pytest.raises(ValueError, match="duplicate capability `value-connectors`"):
        build_capability_catalog_packet([manifest])


def test_incompatible_extension_api_fails_closed(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "extension.toml")
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'requires_loopx_api = ">=1,<2"',
            'requires_loopx_api = ">=2,<3"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires LoopX extension API"):
        build_capability_catalog_packet([manifest])


def test_cli_lists_and_shows_explicit_extension_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path / "extension.toml")
    runtime_root = tmp_path / "runtime"

    assert (
        main(
            [
                "--format",
                "json",
                "--runtime-root",
                str(runtime_root),
                "capability",
                "list",
                "--extension-manifest",
                str(manifest),
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["capabilities"][-1]["id"] == "sample-report"

    assert (
        main(
            [
                "--format",
                "json",
                "--runtime-root",
                str(runtime_root),
                "capability",
                "show",
                "sample-report",
                "--extension-manifest",
                str(manifest),
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["capability"]["provider_id"] == "sample-extension"
    assert shown["capability"]["provider_state"]["ready"] is False


def test_installed_runtime_is_catalog_truth_and_cli_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = tmp_path / "provider"
    provider.write_text(
        f"#!{sys.executable}\nimport sys\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = _write_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
    )
    runtime_root = tmp_path / "runtime"
    state_file = default_extension_state_file(runtime_root)
    install_extension(manifest, state_file=state_file, execute=True)

    packet = build_capability_catalog_packet(extension_state_file=state_file)
    extension = packet["capabilities"][-1]
    assert extension["id"] == "sample-report"
    assert extension["provider_state"] == {
        "declared": True,
        "installed": True,
        "enabled": True,
        "ready": True,
    }

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "capability",
                "show",
                "sample-report",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["capability"]["provider_state"]["ready"] is True

    disable_extension("sample-extension", state_file=state_file, execute=True)
    disabled = build_capability_detail_packet(
        "sample-report",
        extension_state_file=state_file,
    )
    assert disabled["capability"]["provider_state"] == {
        "declared": True,
        "installed": True,
        "enabled": False,
        "ready": False,
    }


def test_active_explore_record_points_to_real_smokes() -> None:
    repository = Path(__file__).resolve().parents[2]
    for capability_id in ("explore",):
        record = build_capability_detail_packet(capability_id)["capability"]
        assert record["provider_state"]["ready"] is True
        assert record["smokes"]
        for command in record["smokes"]:
            prefix = "python3 "
            assert command.startswith(prefix)
            assert (repository / command.removeprefix(prefix)).is_file()


def test_issue_fix_capability_exposes_discovered_issue_promotion() -> None:
    capability = build_capability_detail_packet("issue-fix")["capability"]
    commands = [item["command"] for item in capability["commands"]]

    assert any(
        command.startswith("loopx issue-fix promote-discovered-issue ")
        for command in commands
    )
    assert any(
        protocol["schema_version"] == "issue_fix_discovered_issue_promotion_v0"
        for protocol in capability["implemented_protocols"]
    )


def test_context_provider_factory_dispatches_through_registered_builder() -> None:
    provider = build_context_provider(
        {
            "provider": "openviking",
            "provider_binary": "custom-ov",
            "actor_peer_id": "project-example",
        }
    )

    assert isinstance(provider, OpenVikingContextProvider)
    assert provider.executable == "custom-ov"
    assert provider.actor_peer_id == "project-example"


def test_cli_rejects_unknown_capability_without_traceback(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "capability",
                "show",
                "not-registered",
            ]
        )

    assert exc_info.value.code == 2
    assert "unknown capability `not-registered`" in capsys.readouterr().err
