#!/usr/bin/env python3
"""Prove the steward channel selects its executor explicitly and stays put."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_agent import CodexChatAgentError  # noqa: E402
from loopx.capabilities.machine_configuration.builtins import (  # noqa: E402
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (  # noqa: E402
    configure_machine_configuration,
)
from loopx.chat_manager import (  # noqa: E402
    MANAGER_ENDPOINT_MANAGED,
    MANAGER_ENDPOINT_DEFAULT_REASON_STEWARD_CHANNEL_DEFAULT,
    MANAGER_ENDPOINT_ENV_VAR,
    MANAGER_ENDPOINT_SOURCE_EXPLICIT_CONFIG,
    MANAGER_ENDPOINT_SOURCE_MACHINE_CONFIGURATION,
    MANAGER_MODEL_SOURCE_MANAGED_PROFILE,
    MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION,
    MANAGER_MODEL_SOURCE_VENDOR_DEFAULT,
    MANAGER_CHANNEL_SESSION_MODE_SOURCE_READBACK,
    MANAGER_CHANNEL_SESSION_MODE_SOURCE_UNBOUND,
    MANAGER_CHANNEL_SESSION_MODE_SOURCE_UNRECOGNIZED,
    manager_channel_session_mode_readback,
    manager_channel_binding,
    manager_executor_endpoint_default,
    manager_model_config,
    open_manager_session,
    steward_machine_defaults,
)
from loopx.chat_runtime import ChatRuntimeController  # noqa: E402
from loopx.chat_store import ChatSessionStore  # noqa: E402
from loopx.control_plane.turn_driver import host_binding  # noqa: E402


CREDENTIAL_ENV = "DEEPSEEK_API_KEY"
CREDENTIAL_VALUE = "fixture-operator-credential"
STEWARD_EXECUTOR_NAMESPACE = "steward_executor"
STEWARD_EXECUTOR_SCHEMA = "steward_executor_machine_defaults_v0"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"steward channel binding smoke failed: {message}")


def _assert_the_shipped_default_ignores_the_credential() -> dict[str, object]:
    """The shipped default is one endpoint, and a credential does not move it."""

    without_credential = manager_channel_binding({})
    with_credential = manager_channel_binding({CREDENTIAL_ENV: CREDENTIAL_VALUE})
    _assert(
        without_credential["executor_endpoint"] == "codex"
        and with_credential["executor_endpoint"] == "codex",
        "the shipped default must be the reachable CLI endpoint on every machine",
    )
    _assert(
        without_credential["executor_endpoint_source"]
        == with_credential["executor_endpoint_source"]
        == "product_default",
        "a credential must never become the endpoint source",
    )
    _assert(
        without_credential["executor_endpoint_default_reason"]
        == with_credential["executor_endpoint_default_reason"]
        == MANAGER_ENDPOINT_DEFAULT_REASON_STEWARD_CHANNEL_DEFAULT,
        "the one shipped decision must be named, and it is not a credential branch",
    )
    _assert(
        without_credential["model"] == with_credential["model"] == "gpt-6-astra"
        and with_credential["model_source"] == MANAGER_MODEL_SOURCE_VENDOR_DEFAULT
        and with_credential["execution_profile"] is None,
        "a credential must not hand the CLI endpoint a managed model or profile",
    )
    _assert(
        with_credential["operator_credential_configured"] is True
        and without_credential["operator_credential_configured"] is False,
        "the credential must still be reported as a fact",
    )
    _assert(
        with_credential["credential_env_var"] == ""
        and CREDENTIAL_VALUE not in json.dumps(with_credential),
        "the credential must be reported by name for the endpoint it authenticates",
    )
    _assert(
        manager_model_config(
            {CREDENTIAL_ENV: CREDENTIAL_VALUE, "LOOPX_MANAGER_MODEL": "fixture-model"}
        )
        == {"model": "fixture-model", "reasoning_effort": "high"},
        "an explicit model override must win over the shipped default",
    )
    return {
        "without_credential": without_credential,
        "with_credential": with_credential,
    }


def _assert_selection_moves_the_channel_to_the_managed_host() -> dict[str, object]:
    """Selecting the managed host is what puts the managed profile on it."""

    selected = manager_channel_binding(
        {MANAGER_ENDPOINT_ENV_VAR: "dsh", CREDENTIAL_ENV: CREDENTIAL_VALUE}
    )
    _assert(
        selected["executor_endpoint"] == MANAGER_ENDPOINT_MANAGED
        and selected["executor_kind"] == "managed"
        and selected["executor_endpoint_source"] == "explicit_config"
        and selected["executor_endpoint_default_reason"] == "",
        "an explicit endpoint selection must be the only way onto the managed host",
    )
    _assert(
        selected["model"] == "deepseek-flash"
        and selected["model_source"] == MANAGER_MODEL_SOURCE_MANAGED_PROFILE
        and selected["execution_profile"] == "deepseek-flash@high",
        "the managed endpoint must run the managed execution profile",
    )
    _assert(
        selected["credential_env_var"] == CREDENTIAL_ENV
        and CREDENTIAL_VALUE not in json.dumps(selected),
        "the binding must name the credential without ever echoing its value",
    )
    return selected


def _assert_explicit_selection_and_managed_host_verdict() -> dict[str, object]:
    """Explicit selection wins; the managed host quotes the Turn verdict.

    The dsh runtime is an installable dependency, so whether the machine running
    this smoke happens to have it decides which typed reason a launch reports.
    Pin the availability probe: the fact under test here is the missing
    credential, and the runtime-absent reason has its own pinned test.
    """

    with mock.patch.object(
        host_binding, "dsh_runtime_importable", lambda *args, **kwargs: True
    ):
        return _assert_managed_host_verdict()


def _assert_managed_host_verdict() -> dict[str, object]:
    """The managed host is listed, and an unauthenticated launch is a typed gate."""

    selected = manager_channel_binding({MANAGER_ENDPOINT_ENV_VAR: "dsh"})
    _assert(
        selected["executor_endpoint"] == "dsh"
        and selected["executor_endpoint_source"]
        == MANAGER_ENDPOINT_SOURCE_EXPLICIT_CONFIG
        and selected["executor_endpoint_default_reason"] == "",
        "an explicit endpoint selection must win over the shipped default",
    )
    _assert(
        selected["executor_kind"] == "managed"
        and selected["available"] is False
        and selected["unavailable_reason"] == "operator_credential_unconfigured",
        "an unauthenticated managed host must fail closed on the missing credential",
    )
    _assert(
        manager_executor_endpoint_default(
            {MANAGER_ENDPOINT_ENV_VAR: "dsh", CREDENTIAL_ENV: CREDENTIAL_VALUE}
        )
        == "dsh",
        "an explicit selection must survive the credential check",
    )

    with tempfile.TemporaryDirectory() as gate_root:
        root = Path(gate_root)
        runtime = ChatRuntimeController(
            store=ChatSessionStore(root / "store"), codex_bin="fixture-codex"
        )
        ambient = os.environ.pop(CREDENTIAL_ENV, None)
        try:
            try:
                runtime.open_session(
                    goal_id="loopx-steward-binding-fixture",
                    agent_id="dsh",
                    work_dir=root,
                    objective="fixture",
                    mode="new",
                )
            except CodexChatAgentError as exc:
                _assert(
                    exc.error_code == "agent_endpoint_unavailable"
                    and exc.gate.get("kind") == "host_tool_gate"
                    and CREDENTIAL_ENV in exc.gate.get("next_action", ""),
                    "an unauthenticated managed host must fail as a typed gate",
                )
            else:
                raise SystemExit(
                    "steward channel binding smoke failed: an unauthenticated dsh session opened"
                )
        finally:
            if ambient is not None:
                os.environ[CREDENTIAL_ENV] = ambient
            runtime.close()
    # The managed host is a listed capability with its own verdict, so a caller
    # can tell "cannot launch here" from "not an endpoint".
    capability = next(
        item
        for item in runtime.capabilities()
        if item["agent_id"] == MANAGER_ENDPOINT_MANAGED
    )
    _assert(
        capability["adapter_kind"] == "deepseek_harness_segment"
        and capability["streaming"] is False
        and capability["tool_calls"] is False
        and capability["available"] is False,
        "the managed host must be listed with the transport it really offers",
    )
    return selected


def _assert_session_opens_the_resolved_endpoint() -> str:
    """The channel opens the endpoint its own readback resolved."""

    opened: list[dict[str, object]] = []

    class _Controller:
        def open_session(self, **kwargs):
            opened.append(kwargs)
            return {"session_id": "fixture-session"}, False

    controller = _Controller()
    with tempfile.TemporaryDirectory() as work_dir:
        ambient = os.environ.pop(CREDENTIAL_ENV, None)
        ambient_endpoint = os.environ.pop(MANAGER_ENDPOINT_ENV_VAR, None)
        try:
            open_manager_session(
                controller=controller,
                goal_id="loopx-steward-binding-fixture",
                work_dir=Path(work_dir),
            )
            _assert(
                opened[-1]["agent_id"] == "codex",
                "without a selection the manager session must open the shipped endpoint",
            )

            os.environ[CREDENTIAL_ENV] = CREDENTIAL_VALUE
            open_manager_session(
                controller=controller,
                goal_id="loopx-steward-binding-fixture",
                work_dir=Path(work_dir),
            )
            _assert(
                opened[-1]["agent_id"] == "codex",
                "a credential must not re-point an endpoint nobody selected",
            )

            # Selecting the managed host is how a machine runs its steward on
            # the operator-billed executor, which is what the env var is for.
            os.environ[MANAGER_ENDPOINT_ENV_VAR] = MANAGER_ENDPOINT_MANAGED
            open_manager_session(
                controller=controller,
                goal_id="loopx-steward-binding-fixture",
                work_dir=Path(work_dir),
            )
            _assert(
                opened[-1]["agent_id"] == MANAGER_ENDPOINT_MANAGED,
                "the manager session must open the endpoint the operator selected",
            )
        finally:
            os.environ.pop(CREDENTIAL_ENV, None)
            if ambient is not None:
                os.environ[CREDENTIAL_ENV] = ambient
            if ambient_endpoint is not None:
                os.environ[MANAGER_ENDPOINT_ENV_VAR] = ambient_endpoint
            else:
                os.environ.pop(MANAGER_ENDPOINT_ENV_VAR, None)
    return str(opened[-1]["agent_id"])


def _assert_the_machine_default_is_first_class() -> dict[str, object]:
    """The machine's steward choice is editable, readable, and authoritative.

    One document is written through the public machine-configuration
    transaction, read back through the catalog the Dashboard edits, and quoted
    by the channel the same document serves. The service environment stays a
    lower layer, so a machine decision cannot be silently overridden by a launch
    file nobody can inspect.
    """

    registry = build_builtin_machine_configuration_registry()
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            STEWARD_EXECUTOR_NAMESPACE: {
                "schema_version": STEWARD_EXECUTOR_SCHEMA,
                "executor_endpoint": MANAGER_ENDPOINT_MANAGED,
                "executor_model": "deepseek-v4-flash",
                "executor_reasoning_effort": "high",
            }
        },
    }
    with tempfile.TemporaryDirectory() as root_text:
        runtime_root = Path(root_text)
        runtime = ChatRuntimeController(
            store=ChatSessionStore(runtime_root), codex_bin="fixture-codex"
        )
        opened: list[dict[str, object]] = []
        try:
            preview = configure_machine_configuration(
                runtime_root=runtime_root,
                configuration=configuration,
                registry=registry,
                execute=False,
            )
            applied = configure_machine_configuration(
                runtime_root=runtime_root,
                configuration=configuration,
                registry=registry,
                execute=True,
                expected_plan_revision=str(preview["plan_revision"]),
            )
            _assert(
                applied["status"] == "applied"
                and applied["changed_namespaces"] == [STEWARD_EXECUTOR_NAMESPACE]
                and applied["readback_verified"] is True,
                "the machine selection must apply through the revision-locked store",
            )
            descriptor = next(
                item
                for item in registry.public_catalog()["namespaces"]
                if item["namespace"] == STEWARD_EXECUTOR_NAMESPACE
            )
            _assert(
                descriptor["template_status"] == "ready"
                and descriptor["configuration_template"]["executor_endpoint"]
                == "codex"
                and descriptor["documentation"]["path"].endswith(".md"),
                "the catalog must offer the shipped default and its documentation",
            )

            defaults = runtime.steward_executor_defaults()
            _assert(
                defaults["status"] == "ready"
                and defaults["source"] == "machine_configuration"
                and str(defaults["configuration_revision"]).startswith("sha256:"),
                "the running controller must read the stored machine selection",
            )
            with mock.patch.object(
                host_binding, "dsh_runtime_importable", lambda *args, **kwargs: True
            ):
                binding = manager_channel_binding(
                    {
                        MANAGER_ENDPOINT_ENV_VAR: "codex",
                        "LOOPX_MANAGER_MODEL": "env-model",
                        "LOOPX_MANAGER_REASONING_EFFORT": "low",
                        CREDENTIAL_ENV: CREDENTIAL_VALUE,
                    },
                    machine_defaults=defaults,
                )
            _assert(
                binding["executor_endpoint"] == MANAGER_ENDPOINT_MANAGED
                and binding["executor_endpoint_source"]
                == MANAGER_ENDPOINT_SOURCE_MACHINE_CONFIGURATION
                and binding["executor_endpoint_default_reason"] == "",
                "the machine selection must outrank the service environment",
            )
            _assert(
                binding["model"] == "deepseek-v4-flash"
                and binding["model_source"] == MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION
                and binding["execution_profile"] == "deepseek-flash@high",
                "the model and the effort must follow the machine selection",
            )
            _assert(
                binding["machine_defaults_status"] == "ready"
                and binding["machine_defaults_revision"]
                == defaults["configuration_revision"]
                and str(runtime_root) not in json.dumps(binding),
                "the channel must quote the document it read without leaking its path",
            )

            class _Controller:
                def steward_executor_defaults(self):
                    return runtime.steward_executor_defaults()

                def open_session(self, **kwargs):
                    opened.append(kwargs)
                    return {"session_id": "fixture-session"}, False

            controller = _Controller()
            _assert(
                steward_machine_defaults(controller) == defaults,
                "an entry point must read the machine default through its owner",
            )
            open_manager_session(
                controller=controller,
                goal_id="loopx-steward-binding-fixture",
                work_dir=runtime_root,
            )
            _assert(
                opened[-1]["agent_id"] == MANAGER_ENDPOINT_MANAGED,
                "the channel must open the executor the machine selected",
            )
        finally:
            runtime.close()
    return {
        "machine_defaults_status": "ready",
        "executor_endpoint_source": MANAGER_ENDPOINT_SOURCE_MACHINE_CONFIGURATION,
        "model_source": MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION,
    }


def _assert_mode_readback_quotes_the_session() -> dict[str, object]:
    """The channel reports the mode it serves, and derives none on its own."""

    selected = {
        MANAGER_ENDPOINT_ENV_VAR: MANAGER_ENDPOINT_MANAGED,
        CREDENTIAL_ENV: CREDENTIAL_VALUE,
    }
    # Pin the runtime probe: this assertion is about the mode, and whether the
    # machine running the smoke has the dsh runtime installed is not the fact
    # under test. The unavailable reasons have their own pinned coverage.
    with mock.patch.object(
        host_binding, "dsh_runtime_importable", lambda *args, **kwargs: True
    ):
        unbound = manager_channel_binding(selected)
        attached = manager_channel_binding(
            selected,
            session={"session_mode": "attached_host", "status": "busy"},
        )
    _assert(
        unbound["executor_kind"] == "managed"
        and unbound["available"] is True
        and unbound["session_mode"] is None
        and unbound["session_mode_source"]
        == MANAGER_CHANNEL_SESSION_MODE_SOURCE_UNBOUND,
        "a ready managed endpoint is not evidence that the channel is bound",
    )
    _assert(
        attached["session_mode"] == "attached_host"
        and attached["session_status"] == "busy"
        and attached["session_mode_source"]
        == MANAGER_CHANNEL_SESSION_MODE_SOURCE_READBACK,
        "the channel must quote the Session's own mode, not the executor it resolved",
    )
    unrecognized = manager_channel_session_mode_readback(
        {"session_mode": "hybrid_handoff", "status": "ready"}
    )
    _assert(
        unrecognized["session_mode"] is None
        and unrecognized["session_mode_source"]
        == MANAGER_CHANNEL_SESSION_MODE_SOURCE_UNRECOGNIZED,
        "a mode outside the closed set must be named rather than coerced",
    )
    return {
        "unbound_session_mode_source": unbound["session_mode_source"],
        "quoted_session_mode": attached["session_mode"],
        "quoted_session_status": attached["session_status"],
        "unrecognized_session_mode_source": unrecognized["session_mode_source"],
    }


def main() -> int:
    payload = {
        "ok": True,
        "shipped_default_probe": _assert_the_shipped_default_ignores_the_credential(),
        "selected_managed_host": (
            _assert_selection_moves_the_channel_to_the_managed_host()
        ),
        "explicit_selection": _assert_explicit_selection_and_managed_host_verdict(),
        "mode_readback": _assert_mode_readback_quotes_the_session(),
        "opened_endpoint": _assert_session_opens_the_resolved_endpoint(),
        "machine_default": _assert_the_machine_default_is_first_class(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
