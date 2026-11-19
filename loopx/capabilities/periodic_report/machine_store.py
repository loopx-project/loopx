"""Compatibility aliases for the generic machine-configuration effect owner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ...registry import atomic_write_json
from ..machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from ..machine_configuration.store import (
    JsonWriter,
    configure_machine_configuration,
    inspect_machine_configuration,
    machine_configuration_store_path,
    plan_machine_configuration_rollback,
    plan_machine_configuration_update,
    read_machine_configuration,
    rollback_machine_configuration,
)


def machine_defaults_store_path(runtime_root: Path) -> Path:
    return machine_configuration_store_path(runtime_root)


def read_periodic_report_machine_defaults(runtime_root: Path) -> dict[str, Any] | None:
    return read_machine_configuration(
        runtime_root, registry=build_builtin_machine_configuration_registry(),
        namespace_ids=("periodic_report",),
    )


def inspect_periodic_report_machine_defaults(runtime_root: Path) -> dict[str, Any]:
    return inspect_machine_configuration(
        runtime_root, registry=build_builtin_machine_configuration_registry()
    )


def plan_periodic_report_machine_defaults_update(
    *, runtime_root: Path, machine_defaults: Mapping[str, Any]
) -> dict[str, Any]:
    return plan_machine_configuration_update(
        runtime_root=runtime_root,
        configuration=machine_defaults,
        registry=build_builtin_machine_configuration_registry(),
    )


def configure_periodic_report_machine_defaults(
    *,
    runtime_root: Path,
    machine_defaults: Mapping[str, Any],
    execute: bool = False,
    expected_plan_revision: str | None = None,
    now: datetime | None = None,
    writer: JsonWriter = atomic_write_json,
) -> dict[str, Any]:
    return configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=machine_defaults,
        registry=build_builtin_machine_configuration_registry(),
        execute=execute,
        expected_plan_revision=expected_plan_revision,
        now=now,
        writer=writer,
    )


def plan_periodic_report_machine_defaults_rollback(
    *, runtime_root: Path, transaction_id: str
) -> dict[str, Any]:
    return plan_machine_configuration_rollback(
        runtime_root=runtime_root,
        transaction_id=transaction_id,
        registry=build_builtin_machine_configuration_registry(),
    )


def rollback_periodic_report_machine_defaults(
    *,
    runtime_root: Path,
    transaction_id: str,
    execute: bool = False,
    expected_plan_revision: str | None = None,
    now: datetime | None = None,
    writer: JsonWriter = atomic_write_json,
) -> dict[str, Any]:
    return rollback_machine_configuration(
        runtime_root=runtime_root,
        transaction_id=transaction_id,
        registry=build_builtin_machine_configuration_registry(),
        execute=execute,
        expected_plan_revision=expected_plan_revision,
        now=now,
        writer=writer,
    )


__all__ = [
    "configure_periodic_report_machine_defaults",
    "inspect_periodic_report_machine_defaults",
    "machine_defaults_store_path",
    "plan_periodic_report_machine_defaults_rollback",
    "plan_periodic_report_machine_defaults_update",
    "read_periodic_report_machine_defaults",
    "rollback_periodic_report_machine_defaults",
]
