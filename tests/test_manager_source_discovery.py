"""One ordinary read discovers peers across authorized hosts without status fanout."""
import json
import subprocess
import sys

from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
from loopx.capabilities.manager_context import POLICY_SCHEMA, _root


def reader_fixture(tmp_path, *, fail=False, revoke=False):
    local = tmp_path / "registry.json"
    local.write_text(json.dumps({"goals": [{"id": "project", "registered_agents":
        [f"local-{i}" for i in range(20)]}]}))
    remote = tmp_path / "remote.json"
    remote.write_text(json.dumps({"goals": [
        {"id": "project", "registered_agents": ["remote-worker"], "coordination": {
            "agent_profiles": {"remote-worker": {"scope_summary": "Current service rollout"}}}},
        {"id": "private", "registered_agents": ["outside-audience"]},
    ]}))
    config = tmp_path / "ssh_config"
    config.write_text("Host sample-host\n  HostName example.invalid\n")
    policy = _root(tmp_path) / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"schema_version": POLICY_SCHEMA, "sources": {
        "manager.external.test": {"evidence_ssh_hosts": {"sample-host": ["project"]}}}}))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert "goal-portfolio --manager-view agents" in argv[-1]
        assert "--goal-id project" in argv[-1]
        if fail:
            raise subprocess.TimeoutExpired("ssh", kwargs["timeout"])
        # Use the real source CLI and registry decoder; only replace SSH transport.
        import shlex
        command = shlex.split(argv[-1])
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(remote),
            "--runtime-root", str(tmp_path / "remote-runtime"), "--format", "json",
            *command[command.index("goal-portfolio"):]], capture_output=True, text=True)
        if revoke:
            policy.write_text(json.dumps({"schema_version": POLICY_SCHEMA, "sources": {}}))
        return result

    records = []
    reader = ManagerInspection(context={"scope": "external_goal_scope", "goals": []},
        registry_path=local, runtime_root=tmp_path, owner_scope=False,
        channel_id="manager.external.test", scope_valid=lambda: True,
        discovery_scope=lambda: ["project"], record=records.append,
        ssh_config_path=config, remote_runner=run)
    return reader, calls


def test_default_discovery_includes_remote_and_keeps_source_qualified_paging(tmp_path):
    reader, calls = reader_fixture(tmp_path)
    result = reader.read(TOOL_NAME, {"view": "agents", "limit": 3})
    assert result["unknown"] is False and result["matched"] == 21
    assert [(row["source_id"], row["agent_id"]) for row in result["rows"]] == [
        ("local", "local-0"), ("ssh:sample-host", "remote-worker"), ("local", "local-1")]
    assert len(result["next_reads"]) == 1
    next_page = reader.read(TOOL_NAME, result["next_reads"][0])
    assert next_page["offset"] == 2 and next_page["rows"][0]["agent_id"] == "local-10"
    assert len(calls) == 1  # Continuing local results does not poll the remote again.
    assert "outside-audience" not in json.dumps(result)


def test_topic_search_is_federated_without_an_explicit_remote_hint(tmp_path):
    reader, calls = reader_fixture(tmp_path)
    result = reader.read(TOOL_NAME, {"view": "agents", "query": "rollout"})
    assert result["matched"] == 1 and result["rows"][0]["source_id"] == "ssh:sample-host"
    assert len(calls) == 1 and result["next_reads"] == []


def test_source_failure_is_unknown_and_local_scope_remains_explicit(tmp_path):
    reader, calls = reader_fixture(tmp_path, fail=True)
    result = reader.read(TOOL_NAME, {"view": "agents", "query": "rollout"})
    assert result["unknown"] and result["matched"] is None
    assert result["next_reads"][0]["source_id"] == "ssh:sample-host"
    reader.read(TOOL_NAME, {"view": "agents", "source_id": "local"})
    assert len(calls) == 1
    reader.context["scope"] = "owner_goal"
    reader.read(TOOL_NAME, {"view": "agents"})
    assert len(calls) == 1


def test_revoked_source_cannot_survive_federated_read(tmp_path):
    reader, _ = reader_fixture(tmp_path, revoke=True)
    result = reader.read(TOOL_NAME, {"view": "agents"})
    assert result == {"ok": False, "error": "authorization_changed"}


def test_legacy_remote_cannot_silently_ignore_task_query(tmp_path):
    from loopx.capabilities.manager_context.ssh_evidence import read_remote
    reader, _ = reader_fixture(tmp_path)
    result = read_remote(tmp_path, reader.channel_id, False,
        {"view": "todos", "source_id": "ssh:sample-host", "goal_id": "project", "query": "release"},
        lambda: True, config_path=reader.ssh_config_path,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps({
            "schema_version": "manager_evidence_page_v1", "ok": True,
            "rows": [{"goal_id": "project", "title": "Unfiltered old page"}]}), ""))
    assert result["reason_code"] == "remote_query_unsupported"
    assert not result["rows"]
