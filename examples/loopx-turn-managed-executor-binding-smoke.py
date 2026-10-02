#!/usr/bin/env python3
"""Prove explicit Turn host selection, its readback, and the fail-closed start.

The smoke drives the public CLI only. It never calls a provider and never reads
a credential value: the operator credential is a fixture string whose only role
is to authenticate the host the operator already selected.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import importlib.machinery
import io
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402
from loopx.control_plane import operator_provider  # noqa: E402
from loopx.control_plane.turn_driver import executor as turn_executor  # noqa: E402
from loopx.control_plane.turn_driver.host_binding import (  # noqa: E402
    DSH_RUNTIME_MODULE,
    DSH_RUNTIME_UNAVAILABLE,
    EXECUTOR_KIND_INDIVIDUAL,
    EXECUTOR_KIND_MANAGED,
    MANAGED_RUNTIME_PROBE_SCHEMA_VERSION,
    OPERATOR_CREDENTIAL_UNCONFIGURED,
    REMEDY_CONFIGURE_DSH_RUNTIME,
    REMEDY_CONFIGURE_OPERATOR_CREDENTIAL,
    REMEDY_SELECT_INDIVIDUAL_HOST,
    RUNTIME_PROBE_SCOPE_INTERPRETER,
)


GOAL_ID = "loopx-turn-managed-executor-fixture"
AGENT_ID = "codex-managed-executor-fixture"
TODO_ID = "todo_managedexec01"
CREDENTIAL_ENV = "DEEPSEEK_API_KEY"
CREDENTIAL_VALUE = "sk-fixture-operator"
RUNTIME_MODULE = "deepseek_harness"


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = root / "workspace"
    runtime.mkdir(parents=True)
    workspace.mkdir(parents=True)

    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# LoopX Managed Executor Fixture",
                "",
                "## Next Action",
                "",
                "Run one bounded managed Turn on the planned executor only.",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P1] Run one bounded managed Turn without leaving the planned executor.",
                (
                    f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
                    "task_class=advancement_task action_kind=implement "
                    f"claimed_by={AGENT_ID} priority=P1 -->"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "loopx-turn-public-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "adapter": {
                            "kind": "fixture_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 10.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "agent_profiles": {
                                AGENT_ID: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, workspace, registry


class _HarnessRuntimeFinder(importlib.abc.MetaPathFinder):
    """Pin whether the DeepSeek Harness runtime resolves, for one bounded call."""

    def __init__(self, available: bool) -> None:
        self._available = available

    def find_spec(self, fullname, path=None, target=None):
        if fullname != RUNTIME_MODULE and not fullname.startswith(f"{RUNTIME_MODULE}."):
            return None
        if not self._available:
            raise ModuleNotFoundError(fullname)
        return importlib.machinery.ModuleSpec(
            fullname, loader=None, is_package=fullname == RUNTIME_MODULE
        )


@contextlib.contextmanager
def _harness_runtime(*, available: bool) -> Iterator[None]:
    finder = _HarnessRuntimeFinder(available)
    sys.meta_path.insert(0, finder)
    sys.modules.pop(RUNTIME_MODULE, None)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(RUNTIME_MODULE, None)


@contextlib.contextmanager
def _operator_credential(value: str | None) -> Iterator[None]:
    previous = os.environ.get(CREDENTIAL_ENV)
    if value is None:
        os.environ.pop(CREDENTIAL_ENV, None)
    else:
        os.environ[CREDENTIAL_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(CREDENTIAL_ENV, None)
        else:
            os.environ[CREDENTIAL_ENV] = previous


def _run_cli(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    return exit_code, json.loads(output.getvalue())


def _plan_command(
    registry: Path, runtime: Path, project: Path, *, host: str | None = None
) -> list[str]:
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "plan",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-root",
        str(project),
    ]
    return [*argv, "--host", host] if host else argv


def _run_once_command(
    registry: Path,
    runtime: Path,
    project: Path,
    workspace: Path,
    *,
    instance: str,
    host: str | None = None,
) -> list[str]:
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        instance,
        "--project",
        str(workspace),
        "--scan-root",
        str(project),
        "--no-global-sync",
        "--execute",
    ]
    return [*argv, "--host", host] if host else argv


def _managed_binding(payload: dict[str, Any]) -> dict[str, Any]:
    binding = payload["managed_executor"]
    assert binding["schema_version"] == "managed_executor_binding_v0", binding
    assert binding["executor"] == payload["host"]["kind"], binding
    assert binding["executor_kind"] == EXECUTOR_KIND_MANAGED, binding
    assert (binding["unavailable_reason"] is None) is binding["available"], binding
    return binding


def _expect_probe(binding: dict[str, Any], *, available: bool) -> None:
    """Pin the scope of the launchability verdict at the CLI boundary.

    ``managed_executor_binding`` answers from the interpreter that probes, and
    one machine can hold a service environment where the dsh SDK resolves and a
    checkout environment where it does not. The verdict is only actionable when
    the readback says which environment answered, so the public payload this
    smoke reads has to carry it -- for the plan, for the fail-closed refusal,
    and for every executor kind, so no reader branches on the field's absence.
    """

    probe = binding["runtime_probe"]
    assert probe["schema_version"] == MANAGED_RUNTIME_PROBE_SCHEMA_VERSION, probe
    assert probe["module"] == DSH_RUNTIME_MODULE, probe
    assert probe["scope"] == RUNTIME_PROBE_SCOPE_INTERPRETER, probe
    assert probe["available"] is available, probe
    # This readback is carried into the Turn execution payload, so it stays
    # public-safe: no absolute path and no credential value.
    serialized = json.dumps(probe)
    assert "/" not in serialized, serialized
    assert CREDENTIAL_VALUE not in serialized, serialized


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="loopx-turn-managed-executor-"
    ) as directory, mock.patch.object(
        operator_provider, "select_default_runtime_root", return_value=Path(directory) / "machine"
    ):
        root = Path(directory)
        project, runtime, workspace, registry = _write_fixture(root)

        # 1. Without the operator credential the shipped default is the
        #    individual CLI host, so a default plan still runs here instead of
        #    gating on a managed host nothing can authenticate.
        with _operator_credential(None), _harness_runtime(available=True):
            exit_code, payload = _run_cli(_plan_command(registry, runtime, project))
        assert exit_code == 0, payload
        assert payload["host"]["kind"] == "codex-cli", payload
        uncredentialed_default = payload["managed_executor"]
        assert uncredentialed_default["executor_kind"] == EXECUTOR_KIND_INDIVIDUAL, (
            uncredentialed_default
        )
        assert uncredentialed_default["operator_credential_bound"] is False, (
            uncredentialed_default
        )
        assert uncredentialed_default["available"] is None, uncredentialed_default

        # 2. The credential resolves and authenticates the managed default.
        with (
            _operator_credential(CREDENTIAL_VALUE),
            _harness_runtime(available=True),
        ):
            exit_code, payload = _run_cli(_plan_command(registry, runtime, project))
        assert exit_code == 0, payload
        assert payload["host"]["kind"] == "dsh", payload
        bound = _managed_binding(payload)
        assert bound["credential_env"] == CREDENTIAL_ENV, bound
        assert bound["operator_credential_bound"] is True, bound
        assert bound["available"] is True, bound
        _expect_probe(bound, available=True)

        # 3. With the runtime genuinely missing the same plan reports the other
        #    typed reason rather than promising a launch, and the unchanged
        #    verdict travels with the scope it was answered at.
        with (
            _operator_credential(CREDENTIAL_VALUE),
            _harness_runtime(available=False),
        ):
            exit_code, payload = _run_cli(_plan_command(registry, runtime, project))
        assert exit_code == 0, payload
        missing = _managed_binding(payload)
        assert missing["available"] is False, missing
        assert missing["unavailable_reason"] == DSH_RUNTIME_UNAVAILABLE, missing
        _expect_probe(missing, available=False)

        # 4. An explicit individual host stays selected even while the operator
        #    credential is configured, and makes no launch claim. It probes no
        #    runtime, and the field is still present as an explicit ``None``.
        with (
            _operator_credential(CREDENTIAL_VALUE),
            _harness_runtime(available=True),
        ):
            exit_code, payload = _run_cli(
                _plan_command(registry, runtime, project, host="codex-cli")
            )
        assert exit_code == 0, payload
        assert payload["host"]["kind"] == "codex-cli", payload
        individual = payload["managed_executor"]
        assert individual["executor_kind"] == EXECUTOR_KIND_INDIVIDUAL, individual
        assert individual["available"] is None, individual
        assert individual["operator_credential_bound"] is False, individual
        assert individual["runtime_probe"] is None, individual

        # 5. Executing an explicitly selected managed host without the
        #    credential fails closed: typed status, no host invocation, no
        #    journal, and no quota slot spend.
        with _operator_credential(None), _harness_runtime(available=True):
            exit_code, refusal = _run_cli(
                _run_once_command(
                    registry,
                    runtime,
                    project,
                    workspace,
                    instance="managed-executor-unauthenticated",
                    host="dsh",
                )
            )
        assert exit_code == 1, refusal
        assert refusal["ok"] is False, refusal
        assert refusal["status"] == "unavailable", refusal
        assert refusal["reason"] == OPERATOR_CREDENTIAL_UNCONFIGURED, refusal
        # The refusal is actionable: the exits travel with the typed reason.
        assert refusal["remediation"] == [
            REMEDY_CONFIGURE_OPERATOR_CREDENTIAL,
            REMEDY_SELECT_INDIVIDUAL_HOST,
        ], refusal
        assert refusal["remediation_host"] == "codex-cli", refusal
        assert refusal["remediation_env_vars"] == [CREDENTIAL_ENV], refusal
        # The operator reads the refusal, so the refusal is where the scope has
        # to be visible: this environment answered "the credential is missing",
        # not "the runtime is missing".
        _expect_probe(refusal["managed_executor"], available=True)
        _expect_no_effects(refusal)
        _expect_no_journal(refusal, runtime)

        # 6. The same refusal covers a provably unlaunchable runtime.
        with (
            _operator_credential(CREDENTIAL_VALUE),
            _harness_runtime(available=False),
        ):
            exit_code, refusal = _run_cli(
                _run_once_command(
                    registry,
                    runtime,
                    project,
                    workspace,
                    instance="managed-executor-runtime-missing",
                )
            )
        assert exit_code == 1, refusal
        assert refusal["ok"] is False, refusal
        assert refusal["status"] == "unavailable", refusal
        assert refusal["reason"] == DSH_RUNTIME_UNAVAILABLE, refusal
        assert refusal["remediation"] == [
            REMEDY_CONFIGURE_DSH_RUNTIME,
            REMEDY_SELECT_INDIVIDUAL_HOST,
        ], refusal
        # The refusal that sends an operator to provision a runtime names the
        # environment that could not import it, so the same readback cannot be
        # taken for a machine-level fact.
        _expect_probe(refusal["managed_executor"], available=False)
        _expect_no_effects(refusal)
        _expect_no_journal(refusal, runtime)

    print("managed executor binding smoke passed")
    return 0


def _expect_no_effects(payload: dict[str, Any]) -> None:
    assert payload["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }, payload
    assert payload["quota_slot_spend_count"] == 0, payload


def _expect_no_journal(payload: dict[str, Any], runtime: Path) -> None:
    journal = turn_executor.turn_journal_path(
        runtime,
        goal_id=GOAL_ID,
        turn_key=str(payload["resume_turn_key"]),
    )
    assert journal.exists() is False, journal


if __name__ == "__main__":
    raise SystemExit(main())
