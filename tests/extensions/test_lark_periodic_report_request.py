from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from loopx.capabilities.periodic_report import request_action
from loopx.capabilities.periodic_report.request_action import (
    REQUEST_ADAPTER_PHASE,
    REQUEST_BIND_PORT,
    REQUEST_HOOK_ID,
    REQUEST_SETTLE_PORT,
    discover_periodic_report_request_ports,
)
from loopx.extensions.hook_adapters import (
    ExtensionHookAdapterDiscovery,
    ExtensionHookAdapterPortBinding,
)
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    write_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target
from loopx.extensions.lark.event_inbox import _event_from_payload
from loopx.extensions.runtime import disable_extension, install_extension
from loopx.extensions.lark import periodic_report_request


def _event(message_id: str, content: str) -> dict[str, object]:
    return {
        "schema_version": "lark_event_inbox_event_v0",
        "event_id": f"evt_{message_id}",
        "message_id": message_id,
        "create_time": "2026-09-06T05:00:00Z",
        "content": content,
        "attachment_count": 0,
        "sender_type": "user",
        "addressed_to_bot": True,
        "addressing_source": "provider_mention",
        "provider_mention_count": 1,
        "target_mention_count": 1,
        "reply_context_verified": False,
        "reply_to_bot": False,
    }


def test_lark_adapter_binds_only_agent_selected_source_without_text_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    selected = "om_selected"
    (inbox / f"{selected}.json").write_text(
        json.dumps(
            _event(selected, "I wrote a weekly report; let's discuss its format.")
        ),
        encoding="utf-8",
    )
    (inbox / "om_other.json").write_text(
        json.dumps(_event("om_other", "请生成周报")),
        encoding="utf-8",
    )
    context = {
        "project": tmp_path,
        "selected_config_ref": ".loopx/inbox.json",
        "config": {"inbox_path": inbox},
        "binding_revision": "sha256:" + "a" * 64,
    }
    monkeypatch.setattr(
        periodic_report_request,
        "_resolved_request_context",
        lambda **_kwargs: context,
    )
    ack_calls: list[list[str]] = []
    monkeypatch.setattr(
        periodic_report_request,
        "acknowledge_lark_event_inbox",
        lambda **kwargs: (
            ack_calls.append(list(kwargs["message_ids"]))
            or {
                "ok": True,
                "new_count": 1,
                "already_acknowledged_count": 0,
                "write_performed": True,
            }
        ),
    )

    receipt = periodic_report_request.bind_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_ref=selected,
    )

    assert receipt["source_ref"] == selected
    assert receipt["raw_content_returned"] is False
    assert "content" not in receipt
    assert ack_calls == []
    settled = periodic_report_request.settle_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_receipt=receipt,
        execute=True,
    )
    assert settled["status"] == "settled"
    assert ack_calls == [[selected]]


def test_lark_adapter_classifies_identity_drift_as_terminal_without_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_ref = "om_terminal_identity"
    context = {
        "project": tmp_path,
        "selected_config_ref": ".loopx/inbox.json",
        "config": {"inbox_path": inbox},
        "binding_revision": "sha256:" + "a" * 64,
    }
    monkeypatch.setattr(
        periodic_report_request,
        "_resolved_request_context",
        lambda **_kwargs: context,
    )
    ack_calls: list[list[str]] = []
    monkeypatch.setattr(
        periodic_report_request,
        "acknowledge_lark_event_inbox",
        lambda **kwargs: ack_calls.append(list(kwargs["message_ids"])),
    )
    source_receipt = {
        "schema_version": "periodic_report_source_binding_receipt_v0",
        "provider": "lark",
        "goal_id": "goal-alpha",
        "agent_id": "agent-alpha",
        "source_ref": source_ref,
        "source_digest": "sha256:" + "b" * 64,
        "observed_at": "2026-09-06T05:00:00Z",
        "requester_kind": "user",
        "addressing_source": "provider_mention",
        "binding_revision": context["binding_revision"],
        "raw_content_returned": False,
        "external_writes_performed": False,
    }

    unavailable = periodic_report_request.settle_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_receipt=source_receipt,
        execute=True,
    )
    assert unavailable["status"] == "terminal_failure"
    assert unavailable["failure_code"] == "source_unavailable"

    drifted = periodic_report_request.settle_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_receipt={
            **source_receipt,
            "binding_revision": "sha256:" + "c" * 64,
        },
        execute=True,
    )
    assert drifted["status"] == "terminal_failure"
    assert drifted["failure_code"] == "source_binding_drift"

    (inbox / f"{source_ref}.json").write_text(
        json.dumps(_event(source_ref, "synthetic request")),
        encoding="utf-8",
    )
    current = periodic_report_request.bind_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_ref=source_ref,
    )
    receipt_drifted = periodic_report_request.settle_lark_periodic_report_source(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        source_receipt={
            **current,
            "source_digest": "sha256:" + "e" * 64,
        },
        execute=True,
    )
    assert receipt_drifted["status"] == "terminal_failure"
    assert receipt_drifted["failure_code"] == "source_receipt_drift"
    assert ack_calls == []


def test_lark_request_ports_are_discovered_from_manifest(tmp_path: Path) -> None:
    state_file = tmp_path / "runtime" / "extensions" / "state.json"
    manifest = Path(periodic_report_request.__file__).with_name("extension.toml")
    installed = install_extension(manifest, state_file=state_file, execute=True)
    assert installed["doctor"]["verified"] is True

    ports = discover_periodic_report_request_ports(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        extension_state_file=state_file,
    )

    assert ports.adapter_ids == ("lark-periodic-report-source",)
    adapter = ports.adapters["lark-periodic-report-source"]
    assert callable(adapter.bind_source)
    assert callable(adapter.settle_source)


def test_request_port_discovery_preserves_multiple_complete_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bind_source(**_kwargs: object) -> dict[str, object]:
        return {}

    def settle_source(**_kwargs: object) -> dict[str, object]:
        return {}

    bindings = tuple(
        ExtensionHookAdapterPortBinding(
            extension_id=f"fixture-{adapter_id}",
            adapter_id=adapter_id,
            capability_id="periodic-report",
            target_hook_id=REQUEST_HOOK_ID,
            phase=REQUEST_ADAPTER_PHASE,
            port_name=port_name,
            handler=handler,
        )
        for adapter_id in ("source-b", "source-a")
        for port_name, handler in (
            (REQUEST_BIND_PORT, bind_source),
            (REQUEST_SETTLE_PORT, settle_source),
        )
    )
    monkeypatch.setattr(
        request_action,
        "discover_extension_hook_adapters",
        lambda **_kwargs: ExtensionHookAdapterDiscovery(
            ports=tuple(reversed(bindings)), failures=()
        ),
    )

    ports = discover_periodic_report_request_ports(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
    )

    assert ports.adapter_ids == ("source-a", "source-b")
    assert ports.select_source_adapter("source-b").adapter_id == "source-b"
    with pytest.raises(ValueError, match="ambiguous"):
        ports.select_source_adapter(None)


def test_request_ports_name_declared_adapters_that_failed_activation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declared adapter that did not activate is reported, not dropped.

    Discovery already isolates factory/lifecycle failures as content-free
    records; the request path used to discard them, so an operator saw only
    "source adapter is unavailable" with no adapter id or next action.
    """
    from loopx.extensions.hook_adapters import ExtensionHookAdapterFailure

    def bind_source(**_kwargs: object) -> dict[str, object]:
        return {}

    def settle_source(**_kwargs: object) -> dict[str, object]:
        return {}

    def port(adapter_id: str, port_name: str) -> ExtensionHookAdapterPortBinding:
        return ExtensionHookAdapterPortBinding(
            extension_id=f"fixture-{adapter_id}",
            adapter_id=adapter_id,
            capability_id="periodic-report",
            target_hook_id=REQUEST_HOOK_ID,
            phase=REQUEST_ADAPTER_PHASE,
            port_name=port_name,
            handler=bind_source if port_name == REQUEST_BIND_PORT else settle_source,
        )

    failure = ExtensionHookAdapterFailure(
        extension_id="fixture-source-broken",
        adapter_id="source-broken",
        capability_id="periodic-report",
        target_hook_id=REQUEST_HOOK_ID,
        phase=REQUEST_ADAPTER_PHASE,
    )
    monkeypatch.setattr(
        request_action,
        "discover_extension_hook_adapters",
        lambda **_kwargs: ExtensionHookAdapterDiscovery(
            ports=(
                port("source-partial", REQUEST_BIND_PORT),
                port("source-dup", REQUEST_BIND_PORT),
                port("source-dup", REQUEST_BIND_PORT),
                port("source-dup", REQUEST_SETTLE_PORT),
            ),
            failures=(failure,),
        ),
    )
    ports = discover_periodic_report_request_ports(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
    )

    assert ports.adapter_ids == ()
    assert ports.failed_adapters == {
        "source-broken": "extension_hook_adapter_unavailable",
        "source-dup": "ambiguous_adapter_ports",
        "source-partial": "incomplete_adapter_ports",
    }
    assert ports.state_error_code is None
    with pytest.raises(ValueError) as named:
        ports.select_source_adapter("source-broken")
    assert "source-broken is declared but was not activated" in str(named.value)
    assert "extension_hook_adapter_unavailable" in str(named.value)
    assert "loopx extension doctor" in str(named.value)
    with pytest.raises(ValueError) as unknown:
        ports.select_source_adapter("never-declared")
    assert str(unknown.value).endswith(": never-declared")
    with pytest.raises(ValueError) as unselected:
        ports.select_source_adapter(None)
    assert "declared adapters were not activated" in str(unselected.value)
    assert "source-broken (extension_hook_adapter_unavailable)" in str(unselected.value)
    assert "source-dup (ambiguous_adapter_ports)" in str(unselected.value)


def test_request_ports_name_unreadable_extension_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        request_action,
        "discover_extension_hook_adapters",
        lambda **_kwargs: ExtensionHookAdapterDiscovery(
            ports=(), failures=(), state_error_code="extension_state_unavailable"
        ),
    )
    ports = discover_periodic_report_request_ports(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal-alpha",
        agent_id="agent-alpha",
    )
    assert ports.state_error_code == "extension_state_unavailable"
    with pytest.raises(ValueError, match="extension state could not be read"):
        ports.select_source_adapter(None)


_PROBE_FACTORY_MODULE = "loopx_probe_periodic_report_source_hooks"


def _install_probe_source_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Install an effect-free source extension through the real lifecycle."""

    package_root = tmp_path / "probe-package"
    package_root.mkdir()
    (package_root / f"{_PROBE_FACTORY_MODULE}.py").write_text(
        "def build_adapter(context):\n"
        "    def refuse(**_kwargs):\n"
        "        raise AssertionError('probe ports must not be invoked')\n"
        f"    return {{{REQUEST_BIND_PORT!r}: refuse, {REQUEST_SETTLE_PORT!r}: refuse}}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(package_root))
    monkeypatch.delitem(sys.modules, _PROBE_FACTORY_MODULE, raising=False)
    provider = tmp_path / "probe-provider"
    provider.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n", encoding="utf-8")
    provider.chmod(0o755)
    manifest = tmp_path / "extension.toml"
    adapter = """
[[hook_adapters]]
id = "{adapter_id}"
capability_id = "{capability_id}"
target_hook_id = "{hook_id}"
phase = "{phase}"
factory = "{module}:build_adapter"
required_permissions = ["probe.source.read"]
ports = [{ports}]
"""
    manifest.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "probe-extension"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = ["probe.source.read"]

[runtime]
protocol = "probe_extension_lifecycle_v0"
entrypoint = {json.dumps(str(provider))}
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 5
"""
        + adapter.format(
            adapter_id="probe-source",
            capability_id="periodic-report",
            hook_id=REQUEST_HOOK_ID,
            phase=REQUEST_ADAPTER_PHASE,
            module=_PROBE_FACTORY_MODULE,
            ports=f'"{REQUEST_BIND_PORT}", "{REQUEST_SETTLE_PORT}"',
        )
        # Same extension, another capability's hook: outside this request scope.
        + adapter.format(
            adapter_id="other-hook-source",
            capability_id="sample-report",
            hook_id="sample_report.request",
            phase=REQUEST_ADAPTER_PHASE,
            module=_PROBE_FACTORY_MODULE,
            ports='"sample_report.request.bind_source"',
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "runtime" / "extensions" / "state.json"
    installed = install_extension(manifest, state_file=state_file, execute=True)
    assert installed["doctor"]["verified"] is True
    return provider, state_file


def _tree_digest(root: Path, *, exclude: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and exclude not in path.parents
    }


def _request_cli(
    capsys: pytest.CaptureFixture[str],
    *,
    registry: Path,
    runtime_root: Path,
    selector: str | None,
) -> tuple[int, dict[str, object]]:
    from loopx.cli import main

    code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "periodic-report",
            "request",
            "--goal-id",
            "goal-alpha",
            "--agent-id",
            "agent-alpha",
            "--source-ref",
            "om_probe_request",
            *(["--source-adapter-id", selector] if selector else []),
            "--execute",
        ]
    )
    return code, json.loads(capsys.readouterr().out)


def test_request_cli_without_installed_extensions_keeps_the_plain_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
    for selector, suffix in ((None, ""), ("never-declared", ": never-declared")):
        code, payload = _request_cli(
            capsys,
            registry=registry,
            runtime_root=tmp_path / "runtime",
            selector=selector,
        )
        assert (code, payload["ok"]) == (1, False)
        assert payload["error"] == (
            "periodic-report request source adapter is unavailable" + suffix
        )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["registry.json"]


@pytest.mark.parametrize(
    ("lifecycle", "expected_code"),
    [
        ("disabled", "extension_disabled"),
        ("doctor_stale", "extension_doctor_not_ready"),
        ("state_unreadable", "extension_state_unavailable"),
    ],
)
def test_request_cli_names_real_lifecycle_loss_for_every_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lifecycle: str,
    expected_code: str,
) -> None:
    provider, state_file = _install_probe_source_extension(tmp_path, monkeypatch)
    runtime_root = state_file.parent.parent
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")

    # Positive control: while ready, the probe is one complete adapter and its
    # factory is imported, so "not imported" below is not vacuous.
    ready = discover_periodic_report_request_ports(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id="goal-alpha",
        agent_id="agent-alpha",
    )
    assert ready.adapter_ids == ("probe-source",)
    assert ready.failed_adapters == {}
    assert _PROBE_FACTORY_MODULE in sys.modules
    del sys.modules[_PROBE_FACTORY_MODULE]

    if lifecycle == "disabled":
        disable_extension("probe-extension", state_file=state_file, execute=True)
    elif lifecycle == "doctor_stale":
        # Changing the doctor-verified executable invalidates its identity.
        provider.write_text(
            provider.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
        )
    else:
        state_file.write_text("{not json", encoding="utf-8")
    before = _tree_digest(tmp_path, exclude=tmp_path / "probe-package")

    for selector in (None, "probe-source", "never-declared"):
        code, payload = _request_cli(
            capsys, registry=registry, runtime_root=runtime_root, selector=selector
        )
        error = str(payload["error"])
        assert (code, payload["ok"]) == (1, False), (selector, payload)
        assert error.startswith("periodic-report request source adapter is unavailable")
        if selector:
            assert selector in error
        if lifecycle == "state_unreadable":
            # Unreadable state hides every declaration, known or not.
            assert f"extension state could not be read ({expected_code})" in error
        elif selector == "never-declared":
            assert "probe-source" not in error and expected_code not in error
        else:
            assert "probe-source" in error and f"({expected_code})" in error
            assert "loopx extension doctor" in error
        assert "other-hook-source" not in error
        assert _PROBE_FACTORY_MODULE not in sys.modules
        assert _tree_digest(tmp_path, exclude=tmp_path / "probe-package") == before
    assert not (runtime_root / "goals").exists()


def test_lark_inbox_rejects_same_name_mention_with_different_provider_identity() -> (
    None
):
    event = _event_from_payload(
        {
            "schema_version": "lark_event_inbox_event_v0",
            "event_id": "evt_same_name_wrong_identity",
            "message_id": "om_same_name_wrong_identity",
            "content": "@_user_1 请生成周报",
            "sender_type": "user",
            "mentions": [
                {
                    "id": {"open_id": "ou_different_bot"},
                    "name": "Agent Alpha",
                }
            ],
        },
        bot_display_name="Agent Alpha",
        bot_app_id="cli_public_fixture",
        bot_open_id="ou_agent_alpha",
    )

    assert event is not None
    assert event["addressed_to_bot"] is False
    assert event["provider_mention_count"] == 1
    assert event["target_mention_count"] == 0


def test_lark_request_context_resolves_registered_agent_inbox(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    config_ref = ".loopx/config/lark/agent-alpha.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-alpha",
                        "repo": str(project),
                        "objective": "Deliver a synthetic weekly report.",
                        "coordination": {
                            "registered_agents": ["agent-alpha"],
                        },
                        "control_plane": {
                            "lark_event_inboxes": {
                                "agent-alpha": {
                                    "enabled": True,
                                    "config_path": config_ref,
                                }
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config_path = project / config_ref
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/agent-alpha",
                "capture_scope": "addressed_only",
                "reply": {
                    "enabled": True,
                    "sender_profile": "agent-alpha-bot",
                    "sender_identity": "bot",
                    "bot_display_name": "Agent Alpha",
                    "bot_app_id": "cli_public_fixture",
                    "chat_id": "oc_public_fixture",
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    add_lark_goal_channel_target(
        target_path=runtime_root / "goal-channel-targets.json",
        target_name="agent-alpha-channel",
        chat_id="oc_public_fixture",
        chat_name="Synthetic Goal Channel",
        identity_mode="project_bot",
        sender_profile="agent-alpha-bot",
        sender_identity="bot",
        bot_app_id="cli_public_fixture",
        bot_display_name="Agent Alpha",
        cli_bin="lark-cli",
        execute=True,
    )
    write_goal_channel_binding(
        project / ".loopx" / "goal-channel.json",
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {
                "goal-alpha": {
                    "goal_id": "goal-alpha",
                    "agent_id": "agent-alpha",
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "agent-alpha-channel",
                    "routing": {
                        "capture_scope": "addressed_only",
                        "ingress_mode": "async_inbox",
                        "inbox_config_ref": config_ref,
                    },
                }
            },
        },
    )

    context = periodic_report_request._resolved_request_context(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-alpha",
        agent_id="agent-alpha",
    )

    assert context["project"] == project
    assert context["selected_config_ref"] == config_ref
    assert context["config"]["inbox_path"] == (
        project / ".loopx" / "inbox" / "agent-alpha"
    )
    assert str(context["binding_revision"]).startswith("sha256:")


def test_lark_request_timestamp_accepts_provider_epoch_and_rejects_naive() -> None:
    assert periodic_report_request._observed_at("1788670800000") == (
        "2026-09-06T05:00:00Z"
    )
    with pytest.raises(ValueError, match="source timestamp is invalid"):
        periodic_report_request._observed_at("2026-09-06T05:00:00")
