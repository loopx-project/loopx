"""Tests for the unified HostHarnessCapabilities contract and flagship hosts."""

from __future__ import annotations

from argparse import Namespace
import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
from typing import cast, get_args

import pytest

from loopx.hosts.capabilities import (
    HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,
    HOST_HARNESS_CAPABILITY_FLAG_VALUES,
    HOST_INTEGRATION_TIER_VALUES,
    HostCapabilitiesRegistry,
    HostHarnessCapabilities,
    HostHarnessCapabilityFlag,
    HostIntegrationTier,
    get_host_capabilities,
    host_supports_capability,
    is_host_harness_capability_flag,
    is_host_integration_tier,
    list_host_capabilities,
    register_host_capabilities,
    validate_host_harness_capabilities,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_CONTRACT_PATH = (
    REPO_ROOT / "loopx" / "control_plane" / "hosts" / "host_harness_capabilities.ts"
)
PY_CONTRACT_PATH = REPO_ROOT / "loopx" / "hosts" / "capabilities.py"


def _load_ts_contract_snapshot() -> dict[str, object]:
    script = (
        f'import * as contract from {json.dumps(TS_CONTRACT_PATH.as_uri())};\n'
        "console.log(JSON.stringify({\n"
        "  schema_version: contract.HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,\n"
        "  tiers: contract.HOST_INTEGRATION_TIERS,\n"
        "  flags: contract.HOST_HARNESS_CAPABILITY_FLAGS,\n"
        "  canonical: contract.listHostCapabilities(),\n"
        "}));\n"
    )
    completed = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            script,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_python_mirror_contains_zero_any() -> None:
    tree = ast.parse(PY_CONTRACT_PATH.read_text(encoding="utf-8"))
    any_names = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    ]
    assert not any_names, f"unexpected Any references in {PY_CONTRACT_PATH}: {any_names}"


def test_python_and_typescript_vocabulary_and_canonical_descriptors_match() -> None:
    snapshot = _load_ts_contract_snapshot()
    assert snapshot["schema_version"] == HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION
    raw_tiers = snapshot["tiers"]
    raw_flags = snapshot["flags"]
    assert isinstance(raw_tiers, list)
    assert isinstance(raw_flags, list)
    assert tuple(raw_tiers) == HOST_INTEGRATION_TIER_VALUES
    assert tuple(raw_tiers) == get_args(HostIntegrationTier)
    assert tuple(raw_flags) == HOST_HARNESS_CAPABILITY_FLAG_VALUES
    assert tuple(raw_flags) == get_args(HostHarnessCapabilityFlag)

    py_descriptors = [entry.to_dict() for entry in list_host_capabilities()]
    assert snapshot["canonical"] == py_descriptors


def test_flagship_hosts_register_exact_shipped_capabilities() -> None:
    from loopx.agy_goal_mode import AGY_HOST_CAPABILITIES
    from loopx.agy_goal_mode.host_runtime import (
        AGY_HOST_CAPABILITIES as RUNTIME_AGY_HOST_CAPABILITIES,
    )
    from loopx.claude_goal_mode import CLAUDE_HOST_CAPABILITIES
    from loopx.dsh_goal_mode import DSH_HOST_CAPABILITIES
    from loopx.dsh_goal_mode.turn_host_adapter import (
        DSH_HOST_CAPABILITIES as ADAPTER_DSH_HOST_CAPABILITIES,
    )

    assert AGY_HOST_CAPABILITIES is RUNTIME_AGY_HOST_CAPABILITIES
    assert DSH_HOST_CAPABILITIES is ADAPTER_DSH_HOST_CAPABILITIES

    assert get_host_capabilities("agy") == AGY_HOST_CAPABILITIES
    assert get_host_capabilities("claude") == CLAUDE_HOST_CAPABILITIES
    assert get_host_capabilities("dsh") == DSH_HOST_CAPABILITIES

    assert AGY_HOST_CAPABILITIES.integration_tier == "orchestrated_app"
    assert AGY_HOST_CAPABILITIES.supported_capabilities == ()
    assert AGY_HOST_CAPABILITIES.hook_events == ("Stop",)
    assert AGY_HOST_CAPABILITIES.native_goal_primitive is True
    assert AGY_HOST_CAPABILITIES.native_scheduler_primitive is True

    assert CLAUDE_HOST_CAPABILITIES.integration_tier == "boundary_cli"
    assert CLAUDE_HOST_CAPABILITIES.supported_capabilities == ("verifier_hook",)
    assert CLAUDE_HOST_CAPABILITIES.hook_events == ("PreToolUse",)
    assert CLAUDE_HOST_CAPABILITIES.native_goal_primitive is False
    assert CLAUDE_HOST_CAPABILITIES.native_scheduler_primitive is True

    assert DSH_HOST_CAPABILITIES.integration_tier == "in_tree_codesigned"
    assert DSH_HOST_CAPABILITIES.supported_capabilities == ("native_context_budget",)
    assert DSH_HOST_CAPABILITIES.hook_events == ()
    assert DSH_HOST_CAPABILITIES.native_goal_primitive is False
    assert DSH_HOST_CAPABILITIES.native_scheduler_primitive is False

    assert host_supports_capability("claude", "verifier_hook") is True
    assert host_supports_capability("claude", "compact_hook") is False
    assert host_supports_capability("dsh", "native_context_budget") is True
    assert host_supports_capability("dsh", "managed_thread_resume") is False
    assert host_supports_capability("agy", "quiet_turn") is False
    assert [entry.host_id for entry in list_host_capabilities()] == [
        "agy",
        "claude",
        "dsh",
    ]
    assert [
        entry.host_id for entry in list_host_capabilities(tier="boundary_cli")
    ] == ["claude"]
    assert [
        entry.host_id
        for entry in list_host_capabilities(capability="native_context_budget")
    ] == ["dsh"]


def test_capabilities_lookup_loads_flagship_adapters_lazily_without_cold_import_leak() -> None:
    script = """
import sys
from loopx.hosts.capabilities import get_host_capabilities
assert 'loopx.agy_goal_mode.host_runtime' not in sys.modules
assert 'loopx.dsh_goal_mode.turn_host_adapter' not in sys.modules
assert 'loopx.claude_goal_mode' not in sys.modules
agy = get_host_capabilities('agy')
assert agy is not None and agy.integration_tier == 'orchestrated_app'
assert 'loopx.agy_goal_mode.host_runtime' in sys.modules
assert 'loopx.dsh_goal_mode.turn_host_adapter' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_default_registry_enforces_builtin_conflict_in_both_orders_preserving_laziness() -> None:
    cold_conflict_script = """
import sys
import pytest
from loopx.hosts.capabilities import (
    HostHarnessCapabilities,
    get_host_capabilities,
    register_host_capabilities,
)

conflicting_claude = HostHarnessCapabilities(
    host_id='claude',
    integration_tier='boundary_cli',
    adapter_module='loopx.claude_goal_mode',
    supported_capabilities=('verifier_hook',),
    hook_events=('PreToolUse',),
    requires_utf8_stdio=True,
    native_goal_primitive=False,
    native_scheduler_primitive=True,
    description='Unauthorized description overwrite while cold.',
)
with pytest.raises(ValueError, match='conflicting host capabilities registration for claude'):
    register_host_capabilities(conflicting_claude)

assert 'loopx.claude_goal_mode' in sys.modules
assert 'loopx.agy_goal_mode.host_runtime' not in sys.modules
assert 'loopx.dsh_goal_mode.turn_host_adapter' not in sys.modules

import loopx.claude_goal_mode as claude_mod
assert get_host_capabilities('claude') == claude_mod.CLAUDE_HOST_CAPABILITIES
assert register_host_capabilities(claude_mod.CLAUDE_HOST_CAPABILITIES) == claude_mod.CLAUDE_HOST_CAPABILITIES
"""
    cold_replace_script = """
import sys
from loopx.hosts.capabilities import (
    HostHarnessCapabilities,
    get_host_capabilities,
    register_host_capabilities,
)

override_claude = HostHarnessCapabilities(
    host_id='claude',
    integration_tier='boundary_cli',
    adapter_module='loopx.claude_goal_mode',
    supported_capabilities=('verifier_hook',),
    hook_events=('PreToolUse',),
    requires_utf8_stdio=True,
    native_goal_primitive=False,
    native_scheduler_primitive=True,
    description='Explicit cold override.',
)
registered = register_host_capabilities(override_claude, replace=True)
assert registered == override_claude
assert 'loopx.agy_goal_mode.host_runtime' not in sys.modules
assert 'loopx.dsh_goal_mode.turn_host_adapter' not in sys.modules

import loopx.claude_goal_mode as claude_mod
assert claude_mod.CLAUDE_HOST_CAPABILITIES != override_claude
assert get_host_capabilities('claude') == override_claude
"""
    lookup_then_register_script = """
import sys
import pytest
from loopx.hosts.capabilities import (
    HostHarnessCapabilities,
    get_host_capabilities,
    register_host_capabilities,
)

authoritative = get_host_capabilities('claude')
assert authoritative is not None
conflicting_claude = HostHarnessCapabilities(
    host_id='claude',
    integration_tier='boundary_cli',
    adapter_module='loopx.claude_goal_mode',
    supported_capabilities=('verifier_hook',),
    hook_events=('PreToolUse',),
    requires_utf8_stdio=True,
    native_goal_primitive=False,
    native_scheduler_primitive=True,
    description='Unauthorized overwrite after lookup.',
)
with pytest.raises(ValueError, match='conflicting host capabilities registration for claude'):
    register_host_capabilities(conflicting_claude)
assert get_host_capabilities('claude') == authoritative
assert register_host_capabilities(authoritative) == authoritative
replaced = register_host_capabilities(conflicting_claude, replace=True)
assert get_host_capabilities('claude') == replaced
assert 'loopx.agy_goal_mode.host_runtime' not in sys.modules
assert 'loopx.dsh_goal_mode.turn_host_adapter' not in sys.modules
"""
    for script in (
        cold_conflict_script,
        cold_replace_script,
        lookup_then_register_script,
    ):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_negative_cases_and_invariant_validation() -> None:
    assert get_host_capabilities("nonexistent_host") is None
    assert host_supports_capability("nonexistent_host", "quiet_turn") is False
    assert is_host_integration_tier("boundary_cli") is True
    assert is_host_integration_tier("unknown_tier") is False
    assert is_host_harness_capability_flag("quiet_turn") is True
    assert is_host_harness_capability_flag("unknown_flag") is False

    isolated = HostCapabilitiesRegistry()
    assert get_host_capabilities("agy", registry=isolated) is None
    assert host_supports_capability("agy", "quiet_turn", registry=isolated) is False
    assert list_host_capabilities(registry=isolated) == ()

    with pytest.raises(ValueError, match="host_id"):
        get_host_capabilities("")
    with pytest.raises(ValueError, match="host_id"):
        get_host_capabilities("InvalidHost")
    with pytest.raises(ValueError, match="unsupported host harness capability flag"):
        host_supports_capability("claude", "invalid_flag")
    with pytest.raises(ValueError, match="unsupported host integration tier"):
        list_host_capabilities(tier="invalid_tier")
    with pytest.raises(ValueError, match="unsupported host harness capability flag"):
        list_host_capabilities(capability="invalid_flag")

    valid = HostHarnessCapabilities(
        host_id="sample_host",
        integration_tier="in_tree_codesigned",
        adapter_module="loopx.hosts.sample",
        supported_capabilities=("native_context_budget", "quiet_turn"),
        hook_events=(),
        requires_utf8_stdio=True,
        native_goal_primitive=False,
        native_scheduler_primitive=False,
        description="  Canonical sample host.  ",
    )
    assert valid.supported_capabilities == ("quiet_turn", "native_context_budget")
    assert valid.description == "Canonical sample host."
    assert valid.supports("quiet_turn") is True
    assert valid.supports("session_fork") is False

    with pytest.raises(FrozenInstanceError):
        valid.host_id = "mutated"  # type: ignore[misc]

    for invalid_kwargs in (
        {"schema_version": "wrong_schema"},
        {"host_id": "BadHost"},
        {"integration_tier": "custom_tier"},
        {"adapter_module": "loopx/bad/path"},
        {"supported_capabilities": ("quiet_turn", "quiet_turn")},
        {"supported_capabilities": ("bogus_flag",)},
        {"supported_capabilities": ("compact_hook",), "hook_events": ()},
        {"supported_capabilities": ("verifier_hook",), "hook_events": ()},
        {"hook_events": ("Stop", "Stop")},
        {"requires_utf8_stdio": "yes"},
        {"description": "   "},
    ):
        payload = valid.to_dict()
        payload.update(invalid_kwargs)
        with pytest.raises(ValueError):
            validate_host_harness_capabilities(payload)

    with pytest.raises(ValueError, match="unsupported host harness capabilities fields"):
        validate_host_harness_capabilities({**valid.to_dict(), "unexpected": 1})

    missing = valid.to_dict()
    missing.pop("description")
    with pytest.raises(ValueError, match="missing host harness capabilities fields"):
        validate_host_harness_capabilities(missing)

    for accepted in ("a" * 512, "😀" * 256, "😀" * 257, "😀" * 512):
        validated = validate_host_harness_capabilities(
            {**valid.to_dict(), "description": accepted}
        )
        assert validated.description == accepted
    for rejected in ("a" * 513, "😀" * 513):
        with pytest.raises(
            ValueError, match="description exceeds 512 Unicode code points"
        ):
            validate_host_harness_capabilities(
                {**valid.to_dict(), "description": rejected}
            )

    for empty_trimmed in ("\u0085", "\ufeff", "\u0085 \ufeff\u00a0\u3000"):
        with pytest.raises(
            ValueError, match="description must be a non-empty string"
        ):
            validate_host_harness_capabilities(
                {**valid.to_dict(), "description": empty_trimmed}
            )
    for raw_desc, expected_desc in (
        ("\u0085" + ("a" * 512) + "\u0085", "a" * 512),
        ("\ufeff" + ("a" * 512) + "\ufeff", "a" * 512),
        ("\u001c", "\u001c"),
        ("\u0085\ufeff\u2003ok\u202f\ufeff\u0085", "ok"),
    ):
        validated = validate_host_harness_capabilities(
            {**valid.to_dict(), "description": raw_desc}
        )
        assert validated.description == expected_desc
        constructed = HostHarnessCapabilities(
            host_id="sample_host",
            integration_tier="in_tree_codesigned",
            adapter_module="loopx.hosts.sample",
            supported_capabilities=("native_context_budget", "quiet_turn"),
            hook_events=(),
            requires_utf8_stdio=True,
            native_goal_primitive=False,
            native_scheduler_primitive=False,
            description=raw_desc,
        )
        assert constructed.description == expected_desc
    with pytest.raises(
        ValueError, match="description exceeds 512 Unicode code points"
    ):
        validate_host_harness_capabilities(
            {**valid.to_dict(), "description": "\u0085" + ("a" * 513) + "\ufeff"}
        )

    ts_probe_cases = [
        "\u0085",
        "\ufeff",
        "\u0085" + ("a" * 512) + "\u0085",
        "\ufeff" + ("a" * 512) + "\ufeff",
        "\u0085" + ("a" * 513) + "\ufeff",
        "\u001c",
        "😀" * 257,
        "😀" * 513,
    ]
    ts_probe = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            (
                "import { validateHostHarnessCapabilities } from "
                "'./loopx/control_plane/hosts/host_harness_capabilities.ts';\n"
                "const cases = JSON.parse(process.argv[1]);\n"
                "const base = {schema_version: 'loopx_host_harness_capabilities_v0', "
                "host_id: 'sample_host', integration_tier: 'in_tree_codesigned', "
                "adapter_module: 'loopx.hosts.sample', supported_capabilities: [], "
                "hook_events: [], requires_utf8_stdio: true, native_goal_primitive: false, "
                "native_scheduler_primitive: false};\n"
                "console.log(JSON.stringify(cases.map((description) => {\n"
                "  try { return { ok: true, description: validateHostHarnessCapabilities({ ...base, description }).description }; }\n"
                "  catch (err) { return { ok: false, message: err instanceof Error ? err.message : String(err) }; }\n"
                "})));\n"
            ),
            json.dumps(ts_probe_cases, ensure_ascii=False),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    ts_outcomes = json.loads(ts_probe.stdout)
    py_outcomes: list[dict[str, object]] = []
    for raw_case in ts_probe_cases:
        try:
            py_desc = validate_host_harness_capabilities(
                {**valid.to_dict(), "supported_capabilities": (), "description": raw_case}
            ).description
            py_outcomes.append({"ok": True, "description": py_desc})
        except ValueError as exc:
            py_outcomes.append({"ok": False, "message": str(exc)})
    assert py_outcomes == ts_outcomes

    register_host_capabilities(valid, registry=isolated)
    assert register_host_capabilities(valid, registry=isolated) == valid
    conflicting = HostHarnessCapabilities(
        host_id="sample_host",
        integration_tier="in_tree_codesigned",
        adapter_module="loopx.hosts.sample",
        supported_capabilities=("quiet_turn",),
        hook_events=(),
        requires_utf8_stdio=True,
        native_goal_primitive=False,
        native_scheduler_primitive=False,
        description="Conflicting capability set.",
    )
    with pytest.raises(ValueError, match="conflicting host capabilities registration"):
        register_host_capabilities(conflicting, registry=isolated)
    replaced = register_host_capabilities(
        conflicting, registry=isolated, replace=True
    )
    assert get_host_capabilities("sample_host", registry=isolated) == replaced


def test_flagship_wiring_preserves_default_runtime_behavior(tmp_path: Path) -> None:
    from loopx.agy_goal_mode import host_runtime as agy_runtime
    from loopx.control_plane.turn_driver.host_binding import selected_turn_host

    args = Namespace(
        cli_bin="loopx",
        registry=None,
        runtime_root=None,
        goal_id="g",
        agent_id="a",
        conversation_id="conv-1",
        state_dir=str(tmp_path / "agy-state"),
    )
    assert (
        agy_runtime.stop_hook(
            args,
            {
                "conversationId": "conv-1",
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "lastUserInput": "done",
            },
        )
        == {}
    )
    hook_path = REPO_ROOT / "loopx" / "claude_goal_mode" / "hooks" / "goal_policy.py"
    hook_result = subprocess.run(
        [sys.executable, str(hook_path)],
        cwd=tmp_path,
        input=json.dumps({"cwd": str(tmp_path), "tool_name": "Bash"}),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(hook_result.stdout) == {}
    assert selected_turn_host({}) == ("codex-cli", "no_operator_credential")
