from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
    inspect_machine_configuration,
)
from loopx.capabilities.periodic_report.machine_store import (
    configure_periodic_report_machine_defaults,
    inspect_periodic_report_machine_defaults,
    machine_defaults_store_path,
    plan_periodic_report_machine_defaults_rollback,
    read_periodic_report_machine_defaults,
    rollback_periodic_report_machine_defaults,
)
from loopx.cli import main
from loopx.registry import atomic_write_json


def _defaults(*, route_ref: str = "loopx-concierge") -> dict:
    return {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "periodic_report": {
                "schema_version": "periodic_report_machine_defaults_v0",
                "enabled": True,
                "inheritance": "live_machine_default",
                "profile_preset": "weekly-progress",
                "route_ref": route_ref,
                "timezone": "Asia/Shanghai",
            },
        },
    }


def _apply(runtime_root: Path, defaults: dict) -> dict:
    preview = configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
    )
    return configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def test_configure_machine_defaults_is_preview_only_without_execute(
    tmp_path: Path,
) -> None:
    preview = configure_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        machine_defaults=_defaults(),
    )

    assert preview["status"] == "preview"
    assert preview["action"] == "create"
    assert preview["writes_required"] == 1
    assert not machine_defaults_store_path(tmp_path).exists()


def test_generic_store_reports_changed_typed_namespaces(tmp_path: Path) -> None:
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=tmp_path,
        configuration=_defaults(),
        registry=registry,
    )

    assert preview["changed_namespaces"] == ["periodic_report"]
    assert preview["machine_configuration"] == _defaults()


def test_generic_store_deletes_and_rolls_back_the_last_namespace(
    tmp_path: Path,
) -> None:
    registry = build_builtin_machine_configuration_registry()
    _apply(tmp_path, _defaults())

    preview = configure_machine_configuration(
        runtime_root=tmp_path,
        configuration=None,
        registry=registry,
    )
    assert preview["action"] == "delete"
    assert preview["changed_namespaces"] == ["periodic_report"]
    assert preview["machine_configuration"] is None

    removed = configure_machine_configuration(
        runtime_root=tmp_path,
        configuration=None,
        registry=registry,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )
    assert removed["status"] == "applied"
    assert removed["machine_configuration"] is None
    assert not machine_defaults_store_path(tmp_path).exists()

    rollback_preview = plan_periodic_report_machine_defaults_rollback(
        runtime_root=tmp_path,
        transaction_id=removed["transaction_id"],
    )
    restored = rollback_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        transaction_id=removed["transaction_id"],
        execute=True,
        expected_plan_revision=rollback_preview["plan_revision"],
    )
    assert restored["status"] == "rolled_back"
    assert read_periodic_report_machine_defaults(tmp_path) == _defaults()


def test_apply_requires_the_exact_preview_revision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected_plan_revision is required"):
        configure_periodic_report_machine_defaults(
            runtime_root=tmp_path,
            machine_defaults=_defaults(),
            execute=True,
        )

    preview = configure_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        machine_defaults=_defaults(),
    )
    with pytest.raises(ValueError, match="plan revision changed"):
        configure_periodic_report_machine_defaults(
            runtime_root=tmp_path,
            machine_defaults=_defaults(route_ref="different-route"),
            execute=True,
            expected_plan_revision=preview["plan_revision"],
        )


def test_apply_persists_readback_backup_and_transaction_receipt(
    tmp_path: Path,
) -> None:
    result = _apply(tmp_path, _defaults())

    assert result["status"] == "applied"
    assert result["readback_verified"] is True
    assert result["rollback_available"] is True
    assert read_periodic_report_machine_defaults(tmp_path) == _defaults()
    receipt_path = tmp_path / result["transaction_ref"]
    backup_path = tmp_path / result["backup_ref"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == result
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["prior_revision"] == "absent"
    assert backup["prior_machine_configuration"] is None
    assert machine_defaults_store_path(tmp_path).stat().st_mode & 0o777 == 0o600


def test_reapplying_the_same_defaults_is_idempotent(tmp_path: Path) -> None:
    first = _apply(tmp_path, _defaults())
    transaction_dir = tmp_path / "machine" / "configuration" / "transactions"
    before = sorted(transaction_dir.iterdir())

    second = _apply(tmp_path, _defaults())

    assert first["status"] == "applied"
    assert second["status"] == "unchanged"
    assert second["transaction_id"] is None
    assert sorted(transaction_dir.iterdir()) == before


def test_rollback_restores_the_exact_prior_policy_and_is_idempotent(
    tmp_path: Path,
) -> None:
    _apply(tmp_path, _defaults(route_ref="original"))
    update = _apply(tmp_path, _defaults(route_ref="replacement"))

    preview = plan_periodic_report_machine_defaults_rollback(
        runtime_root=tmp_path,
        transaction_id=update["transaction_id"],
    )
    assert preview["action"] == "restore"
    result = rollback_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        transaction_id=update["transaction_id"],
        execute=True,
        expected_plan_revision=preview["plan_revision"],
        now=datetime(2026, 9, 3, 1, tzinfo=timezone.utc),
    )

    assert result["status"] == "rolled_back"
    assert result["readback_verified"] is True
    assert read_periodic_report_machine_defaults(tmp_path) == _defaults(
        route_ref="original"
    )

    repeated_preview = plan_periodic_report_machine_defaults_rollback(
        runtime_root=tmp_path,
        transaction_id=update["transaction_id"],
    )
    repeated = rollback_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        transaction_id=update["transaction_id"],
        execute=True,
        expected_plan_revision=repeated_preview["plan_revision"],
    )
    assert repeated["status"] == "unchanged"
    assert repeated["readback_verified"] is True


def test_rollback_refuses_to_overwrite_a_newer_revision(tmp_path: Path) -> None:
    first = _apply(tmp_path, _defaults(route_ref="first"))
    _apply(tmp_path, _defaults(route_ref="second"))

    preview = plan_periodic_report_machine_defaults_rollback(
        runtime_root=tmp_path,
        transaction_id=first["transaction_id"],
    )

    assert preview["action"] == "blocked"
    assert preview["reason"] == "current_revision_changed"
    assert preview["rollback_allowed"] is False
    with pytest.raises(ValueError, match="blocked by a newer revision"):
        rollback_periodic_report_machine_defaults(
            runtime_root=tmp_path,
            transaction_id=first["transaction_id"],
            execute=True,
            expected_plan_revision=preview["plan_revision"],
        )


def test_rollback_fails_closed_when_the_transaction_receipt_was_tampered(
    tmp_path: Path,
) -> None:
    applied = _apply(tmp_path, _defaults())
    receipt_path = tmp_path / applied["transaction_ref"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["applied_revision"] = "sha256:" + "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="transaction receipt is invalid"):
        plan_periodic_report_machine_defaults_rollback(
            runtime_root=tmp_path,
            transaction_id=applied["transaction_id"],
        )
    assert read_periodic_report_machine_defaults(tmp_path) == _defaults()


def test_apply_failure_restores_the_prior_policy(tmp_path: Path) -> None:
    _apply(tmp_path, _defaults(route_ref="original"))
    writes = 0

    def fail_receipt(path: Path, payload: dict) -> None:
        nonlocal writes
        writes += 1
        if writes == 3:
            raise OSError("receipt write failed")
        atomic_write_json(path, payload)

    preview = configure_periodic_report_machine_defaults(
        runtime_root=tmp_path,
        machine_defaults=_defaults(route_ref="replacement"),
    )
    with pytest.raises(RuntimeError, match="prior state was restored"):
        configure_periodic_report_machine_defaults(
            runtime_root=tmp_path,
            machine_defaults=_defaults(route_ref="replacement"),
            execute=True,
            expected_plan_revision=preview["plan_revision"],
            writer=fail_receipt,
        )

    assert read_periodic_report_machine_defaults(tmp_path) == _defaults(
        route_ref="original"
    )


def test_machine_defaults_cli_previews_applies_reads_and_rolls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    defaults_path = tmp_path / "defaults.json"
    defaults_path.write_text(json.dumps(_defaults()), encoding="utf-8")
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "periodic-report",
    ]

    assert (
        main(
            [*common, "configure-machine-defaults", "--config-json", str(defaults_path)]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview"
    assert not machine_defaults_store_path(runtime_root).exists()

    assert (
        main(
            [
                *common,
                "configure-machine-defaults",
                "--config-json",
                str(defaults_path),
                "--execute",
                "--expected-plan-revision",
                preview["plan_revision"],
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"

    assert (
        main(
            [
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "periodic-report",
                "rollback-machine-defaults",
                "--transaction-id",
                applied["transaction_id"],
            ]
        )
        == 0
    )
    rollback_markdown = capsys.readouterr().out
    assert "- plan_revision: `sha256:" in rollback_markdown

    assert main([*common, "inspect-machine-defaults"]) == 0
    inspection = json.loads(capsys.readouterr().out)
    assert inspection["status"] == "configured"
    assert inspection["machine_configuration"] == _defaults()

    assert (
        main(
            [
                *common,
                "rollback-machine-defaults",
                "--transaction-id",
                applied["transaction_id"],
            ]
        )
        == 0
    )
    rollback_preview = json.loads(capsys.readouterr().out)
    assert rollback_preview["action"] == "delete"

    assert (
        main(
            [
                *common,
                "rollback-machine-defaults",
                "--transaction-id",
                applied["transaction_id"],
                "--execute",
                "--expected-plan-revision",
                rollback_preview["plan_revision"],
            ]
        )
        == 0
    )
    rolled_back = json.loads(capsys.readouterr().out)
    assert rolled_back["status"] == "rolled_back"
    assert not machine_defaults_store_path(runtime_root).exists()


def test_canonical_machine_config_cli_uses_the_same_store_and_projection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    defaults_path = tmp_path / "defaults.json"
    defaults_path.write_text(json.dumps(_defaults()), encoding="utf-8")
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "machine-config",
    ]

    assert main([*common, "describe"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["schema_version"] == "machine_configuration_catalog_v0"
    assert [item["namespace"] for item in catalog["namespaces"]] == [
        "change_quality_qualification",
        "goal_storage",
        "manager_runtime",
        "periodic_report",
        "pull_request_review",
        "steward_executor",
        "todo_replan_cadence",
    ]

    assert (
        main(
            [
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "machine-config",
                "preview",
                "--config-json",
                str(defaults_path),
            ]
        )
        == 0
    )
    markdown_preview = capsys.readouterr().out
    assert "- plan_revision: `sha256:" in markdown_preview

    assert main([*common, "preview", "--config-json", str(defaults_path)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["changed_namespaces"] == ["periodic_report"]

    assert (
        main(
            [
                *common,
                "apply",
                "--config-json",
                str(defaults_path),
                "--expected-plan-revision",
                preview["plan_revision"],
                "--execute",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main([*common, "inspect"]) == 0
    inspection = json.loads(capsys.readouterr().out)
    assert inspection == inspect_machine_configuration(
        runtime_root, registry=build_builtin_machine_configuration_registry()
    )


def test_periodic_report_compatibility_cli_renders_generic_machine_schemas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    defaults_path = tmp_path / "defaults.json"
    defaults_path.write_text(json.dumps(_defaults()), encoding="utf-8")
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "periodic-report",
    ]

    assert (
        main(
            [
                *common,
                "configure-machine-defaults",
                "--config-json",
                str(defaults_path),
            ]
        )
        == 0
    )
    preview = capsys.readouterr().out
    assert preview.startswith("# Machine Configuration")
    assert "- status: `preview`" in preview
    assert "- action: `create`" in preview
    assert "- plan_revision: `sha256:" in preview

    assert main([*common, "inspect-machine-defaults"]) == 0
    inspection = capsys.readouterr().out
    assert inspection.startswith("# Machine Configuration")
    assert "- status: `absent`" in inspection


def test_machine_config_cli_removes_a_namespace_with_preview_fencing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    _apply(runtime_root, _defaults())
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "machine-config",
        "remove",
        "--namespace",
        "periodic_report",
    ]

    assert (
        main(
            [
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "machine-config",
                "remove",
                "--namespace",
                "periodic_report",
            ]
        )
        == 0
    )
    markdown_preview = capsys.readouterr().out
    assert "- plan_revision: `sha256:" in markdown_preview

    assert main(common) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["action"] == "delete"

    assert (
        main(
            [
                *common,
                "--execute",
                "--expected-plan-revision",
                preview["plan_revision"],
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "applied"
    assert result["machine_configuration"] is None


def test_canonical_machine_config_cli_accepts_one_namespace_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    namespace_path = tmp_path / "periodic-report.json"
    namespace_path.write_text(
        json.dumps(_defaults()["namespaces"]["periodic_report"]), encoding="utf-8"
    )
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "machine-config",
    ]

    assert (
        main(
            [
                *common,
                "preview",
                "--namespace",
                "periodic_report",
                "--config-json",
                str(namespace_path),
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["machine_configuration"] == _defaults()

    assert (
        main(
            [
                *common,
                "apply",
                "--namespace",
                "periodic_report",
                "--config-json",
                str(namespace_path),
                "--expected-plan-revision",
                preview["plan_revision"],
                "--execute",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"


def test_namespace_patch_transactionally_replaces_legacy_installed_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"goals": []}', encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    store_path = machine_defaults_store_path(runtime_root)
    store_path.parent.mkdir(parents=True)
    legacy = _defaults()
    legacy["namespaces"]["periodic_report"]["inheritance"] = (
        "materialize_on_goal_connect"
    )
    store_path.write_text(json.dumps(legacy), encoding="utf-8")
    namespace_path = tmp_path / "periodic-report.json"
    namespace_path.write_text(
        json.dumps(_defaults()["namespaces"]["periodic_report"]), encoding="utf-8"
    )
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "machine-config",
    ]

    assert main([*common, "inspect"]) == 2
    assert (
        "must be live_machine_default" in json.loads(capsys.readouterr().out)["error"]
    )

    assert (
        main(
            [
                *common,
                "preview",
                "--namespace",
                "periodic_report",
                "--config-json",
                str(namespace_path),
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["action"] == "update"
    assert preview["changed_namespaces"] == ["periodic_report"]

    assert (
        main(
            [
                *common,
                "apply",
                "--namespace",
                "periodic_report",
                "--config-json",
                str(namespace_path),
                "--expected-plan-revision",
                preview["plan_revision"],
                "--execute",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    assert applied["rollback_available"] is True
    assert json.loads(store_path.read_text(encoding="utf-8")) == _defaults()

    rollback_preview = plan_periodic_report_machine_defaults_rollback(
        runtime_root=runtime_root,
        transaction_id=applied["transaction_id"],
    )
    restored = rollback_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        transaction_id=applied["transaction_id"],
        execute=True,
        expected_plan_revision=rollback_preview["plan_revision"],
    )
    assert restored["status"] == "rolled_back"
    assert json.loads(store_path.read_text(encoding="utf-8")) == legacy


def test_inspection_is_path_free_and_reports_absence(tmp_path: Path) -> None:
    result = inspect_periodic_report_machine_defaults(tmp_path)

    assert result == {
        "ok": True,
        "schema_version": "machine_configuration_inspection_v0",
        "status": "absent",
        "defaults_ref": "machine/configuration.json",
        "revision": "absent",
        "changed_namespaces": [],
        "machine_configuration": None,
    }
