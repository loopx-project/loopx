"""Structured guidance survives summary/chunk noise without changing other clients."""

import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.context_providers.openviking import (
    OpenVikingContextProvider,
    build_openviking_context_provider,
    classify_openviking_scope,
)


def test_reward_retrieval_uses_bodies_and_refills_after_unusable_hits():
    root = "viking://resources/example"
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "--version":
            output = "OpenViking 0.4.9"
        elif command[1] == "status":
            output = "{}"
        elif command[1] == "search":
            assert "-L" in command and command[command.index("-L") + 1] == "2"
            assert command[command.index("-n") + 1] == "8"
            output = json.dumps(
                {
                    "resources": [
                        {"uri": root + "/.overview.md", "level": 1},
                        {"uri": root + "/bad", "level": 2},
                        {"uri": root + "/one#chunk_0001", "level": 2},
                        {"uri": root + "/one#chunk_0002", "level": 2},
                        {"uri": root + "/two", "level": 2},
                    ]
                }
            )
        else:
            assert command[1] == "read"
            assert "#" not in command[2] and "overview" not in command[2]
            output = json.dumps(
                {
                    "content": "not a record"
                    if command[2].endswith("bad")
                    else json.dumps(
                        {"schema_version": "reward_memory_active_record_v0"}
                    )
                }
            )
        return subprocess.CompletedProcess(command, 0, output)

    provider = OpenVikingContextProvider(runner=runner)
    result = provider.retrieve(
        namespace="reward_memory",
        scope_ref=root,
        query="example destination guidance",
        query_summary="guidance",
        max_results=2,
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
    )
    assert [item.resource_ref for item in result.items] == [
        root + "/one",
        root + "/two",
    ]
    assert sum(c[1:3] == ["read", root + "/one"] for c in calls) == 1


def test_other_namespaces_keep_original_search_contract():
    def runner(command, **kwargs):
        if command[1] == "--version":
            output = "OpenViking 0.4.9"
        elif command[1] == "search":
            assert "-L" not in command
            assert command[command.index("-n") + 1] == "2"
            output = '{"resources": []}'
        else:
            output = "{}"
        return subprocess.CompletedProcess(command, 0, output)

    result = OpenVikingContextProvider(runner=runner).retrieve(
        namespace="ordinary_context",
        scope_ref="viking://resources/example",
        query="context",
        query_summary="context",
        max_results=2,
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
    )
    assert result.status == "completed" and not result.items


def test_opted_in_l2_recall_rejects_overview_noise_without_reading_it():
    root = "viking://resources/example"
    reads = []

    def runner(command, **kwargs):
        if command[1] == "--version":
            output = "OpenViking 0.4.23"
        elif command[1] == "search":
            assert command[command.index("-L") + 1] == "2"
            assert command[command.index("-n") + 1] == "2"
            output = json.dumps(
                {
                    "resources": [
                        {"uri": root + "/.overview.md", "level": 1},
                        {"uri": root + "/.abstract.md"},
                        {"uri": root + "/body.md", "level": 2},
                    ]
                }
            )
        elif command[1] == "read":
            reads.append(command[2])
            output = json.dumps({"content": "Exact source body"})
        else:
            output = "{}"
        return subprocess.CompletedProcess(command, 0, output)

    provider = OpenVikingContextProvider(result_levels=[2], runner=runner)
    result = provider.retrieve(
        namespace="decision_context",
        scope_ref=root,
        query="body",
        query_summary="Source representation",
        max_results=2,
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
    )
    assert reads == [root + "/body.md"]
    assert [item.content for item in result.items] == ["Exact source body"]


@pytest.mark.parametrize("levels", [[], [True], [3], [-1], [1, 1], "2", [2.0]])
def test_provider_level_config_rejects_invalid_values_before_io(levels):
    with pytest.raises(ValueError, match="result_levels"):
        build_openviking_context_provider({"result_levels": levels})


def test_advisory_level_config_cannot_weaken_reward_memory_body_requirement():
    calls = []
    provider = OpenVikingContextProvider(
        result_levels=[1], runner=lambda *args, **kwargs: calls.append(args)
    )
    with pytest.raises(ValueError, match="full-body"):
        provider.retrieve(
            namespace="reward_memory",
            scope_ref="viking://resources/example",
            query="advice",
            query_summary="Advice",
            max_results=2,
            timeout_seconds=30,
            observed_at="2026-01-01T00:00:00Z",
        )
    assert not calls


def test_latest_openviking_uri_contract_rejects_legacy_agent_writes():
    with pytest.raises(ValueError, match="shared read-only compatibility scope"):
        classify_openviking_scope("viking://agent/pilot/memories/reward.json")
    with pytest.raises(ValueError, match="safe user id"):
        classify_openviking_scope(
            "viking://user/goal:one/peers/pilot/memories/reward.json"
        )

    scope = classify_openviking_scope(
        "viking://user/default/peers/goal-one-pilot/memories/reward.json"
    )
    assert scope.scope_kind == "peer_memories"
    assert scope.write_strategy == "content_write"
    assert scope.user_scope_id == "default"
    assert scope.actor_scope_id == "goal-one-pilot"


def test_peer_scope_actor_mismatch_is_rejected_before_provider_call(tmp_path: Path):
    source = tmp_path / "reward.json"
    source.write_text("{}\n", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "{}")

    provider = OpenVikingContextProvider(
        actor_peer_id="goal-one-pilot",
        runner=runner,
    )
    with pytest.raises(ValueError, match="must match"):
        provider.sync(
            namespace="reward_memory",
            resources=[
                (
                    str(source),
                    "viking://user/default/peers/goal-two-pilot/memories/reward.json",
                )
            ],
            timeout_seconds=30,
            observed_at="2026-01-01T00:00:00Z",
            execute=True,
        )
    assert calls == []


def test_private_preview_checks_latest_cli_without_writing(tmp_path: Path):
    actor = "goal-one-pilot"
    root = f"viking://user/default/peers/{actor}/memories/reward-memory"
    target = f"{root}/probe.json"
    source = tmp_path / "probe.json"
    source.write_text('{"probe":true}\n', encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        if operation == "--version":
            return subprocess.CompletedProcess(command, 0, "openviking 0.4.18")
        if operation == "version":
            return subprocess.CompletedProcess(
                command, 0, "CLI: 0.4.18\nServer: 0.4.19"
            )
        if operation == "status":
            return subprocess.CompletedProcess(command, 0, "{}")
        if operation == "read":
            return subprocess.CompletedProcess(command, 1, "not found")
        assert operation == "ls"
        return subprocess.CompletedProcess(command, 0, '{"items":[]}')

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[(str(source), target)],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=False,
    )

    assert result.status == "preflight_ready"
    assert result.visibility == "private"
    assert result.target_scope_kind == "peer_memories"
    assert result.write_strategy == "content_write"
    assert result.actor_binding_verified is True
    assert result.provider_preflight_performed is True
    assert result.writability_verified is False
    assert not any("write" in command for command in calls)


def test_private_preview_accepts_typed_missing_parent_for_content_create(
    tmp_path: Path,
):
    actor = "goal-one-pilot"
    target = f"viking://user/default/peers/{actor}/memories/reward-memory/probe.json"
    source = tmp_path / "probe.json"
    source.write_text('{"probe":true}\n', encoding="utf-8")

    def runner(command, **kwargs):
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        if operation == "--version":
            return subprocess.CompletedProcess(command, 0, "openviking 0.4.18")
        if operation == "version":
            return subprocess.CompletedProcess(
                command, 0, "CLI: 0.4.18\nServer: 0.4.19"
            )
        if operation == "status":
            return subprocess.CompletedProcess(command, 0, "{}")
        assert operation in {"read", "ls"}
        return subprocess.CompletedProcess(
            command,
            1,
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Directory not found",
                    },
                }
            ),
        )

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[(str(source), target)],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=False,
    )

    assert result.status == "preflight_ready"
    assert result.target_access_preflight_verified is True
    assert result.writability_verified is False
    assert result.reason_code == "execute_required_for_verified_write"


def test_private_preview_does_not_treat_permission_denied_as_missing_parent(
    tmp_path: Path,
):
    actor = "goal-one-pilot"
    target = f"viking://user/default/peers/{actor}/memories/reward-memory/probe.json"
    source = tmp_path / "probe.json"
    source.write_text('{"probe":true}\n', encoding="utf-8")

    def runner(command, **kwargs):
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        if operation == "--version":
            return subprocess.CompletedProcess(command, 0, "openviking 0.4.18")
        if operation == "version":
            return subprocess.CompletedProcess(
                command, 0, "CLI: 0.4.18\nServer: 0.4.19"
            )
        if operation == "status":
            return subprocess.CompletedProcess(command, 0, "{}")
        if operation == "read":
            return subprocess.CompletedProcess(command, 1, "not found")
        assert operation == "ls"
        return subprocess.CompletedProcess(
            command,
            1,
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "peer scope mismatch",
                    },
                }
            ),
        )

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[(str(source), target)],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=False,
    )

    assert result.status == "preflight_incomplete"
    assert result.target_access_preflight_verified is False
    assert result.reason_code == "provider_sync_target_parent_unavailable"


def test_private_execute_uses_content_write_and_exact_readback(tmp_path: Path):
    actor = "goal-one-pilot"
    target = f"viking://user/default/peers/{actor}/memories/reward-memory/probe.json"
    source = tmp_path / "probe.json"
    source_content = '{"probe":true}\n'
    source.write_text(source_content, encoding="utf-8")
    calls = []
    written = False

    def runner(command, **kwargs):
        nonlocal written
        calls.append(command)
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        if operation == "--version":
            return subprocess.CompletedProcess(command, 0, "openviking 0.4.18")
        if operation == "version":
            return subprocess.CompletedProcess(
                command, 0, "CLI: 0.4.18\nServer: 0.4.19"
            )
        if operation == "status":
            return subprocess.CompletedProcess(command, 0, "{}")
        if operation == "read":
            return subprocess.CompletedProcess(
                command,
                0 if written else 1,
                json.dumps({"content": source_content}) if written else "not found",
            )
        assert operation == "write"
        assert command[4] == target
        assert command[command.index("--from-file") + 1] == str(source)
        assert command[command.index("--mode") + 1] == "create"
        written = True
        return subprocess.CompletedProcess(command, 0, '{"status":"ok"}')

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[(str(source), target)],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=True,
    )

    assert result.status == "completed"
    assert result.write_count == 1
    assert result.result_refs == (target,)
    assert result.writability_verified is True
    assert any(command[3] == "write" for command in calls if len(command) > 3)


def test_private_peer_scope_requires_current_cli_generation(tmp_path: Path):
    actor = "goal-one-pilot"
    source = tmp_path / "probe.json"
    source.write_text("{}\n", encoding="utf-8")

    def runner(command, **kwargs):
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        output = "openviking 0.4.9" if operation == "--version" else "{}"
        return subprocess.CompletedProcess(command, 0, output)

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[
            (
                str(source),
                f"viking://user/default/peers/{actor}/memories/probe.json",
            )
        ],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=False,
    )
    assert result.status == "unavailable"
    assert result.reason_code == "provider_version_incompatible"


def test_private_peer_scope_requires_current_server_contract(tmp_path: Path):
    actor = "goal-one-pilot"
    source = tmp_path / "probe.json"
    source.write_text("{}\n", encoding="utf-8")

    def runner(command, **kwargs):
        operation = command[3] if command[1] == "--actor-peer-id" else command[1]
        if operation == "--version":
            output = "openviking 0.4.18"
        elif operation == "version":
            output = "CLI: 0.4.18\nServer: 0.4.17.dev7"
        else:
            pytest.fail("server version incompatibility must stop before data access")
        return subprocess.CompletedProcess(command, 0, output)

    result = OpenVikingContextProvider(
        actor_peer_id=actor,
        runner=runner,
    ).sync(
        namespace="reward_memory",
        resources=[
            (
                str(source),
                f"viking://user/default/peers/{actor}/memories/probe.json",
            )
        ],
        timeout_seconds=30,
        observed_at="2026-01-01T00:00:00Z",
        execute=False,
    )
    assert result.status == "unavailable"
    assert result.reason_code == "provider_server_version_incompatible"
    assert "server 0.4.17" in str(result.provider_version)


def test_openviking_rejects_malformed_utf8_stdout():
    def runner(command, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    with pytest.raises(UnicodeDecodeError):
        OpenVikingContextProvider(runner=runner).retrieve(
            namespace="reward_memory",
            scope_ref="viking://user/default/resources",
            query="test",
            query_summary="test",
            max_results=5,
            timeout_seconds=5,
            observed_at="2026-01-01T00:00:00Z",
        )
