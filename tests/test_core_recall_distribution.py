"""Qualification for the independently installed built-in recall wheel."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from loopx import __version__
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.test_core_distribution import (
    AGENT,
    GOAL,
    HOST_SOURCE,
    REPOSITORY,
    _assert_committed,
    _environment,
    _prepare_module,
    _run,
    _spends,
    core_python as core_python,
    task as task,
)

RECALL_MODULES = {
    "loopx/capabilities/agent_turn_recall/cli.py",
    "loopx/capabilities/agent_turn_recall/core.py",
}


def test_recall_source_stages_only_the_optional_implementation(tmp_path):
    staged = _prepare_module().prepare_turn_recall_source(tmp_path / "recall")
    assert {path.relative_to(staged).as_posix() for path in (staged / "loopx").rglob("*.py")} == RECALL_MODULES
    for path in RECALL_MODULES:
        assert (staged / path).read_bytes() == (REPOSITORY / path).read_bytes()
    assert (staged / "requires-core.txt").read_text().strip() == f"loopx-core=={__version__}"


def _install_fixture_provider(tmp_path, core_python, configuration):
    config = json.loads(configuration.read_text())
    binding = config["project_provider_binding"]
    corpus = config["corpora"][0]["corpus"]
    scope = binding["corpus_scopes"][0]["scope_ref"]
    uri = f"{scope}/validation.json"
    guidance = "Read the checked artifact before reporting."
    record = {
        "schema_version": "reward_memory_active_record_v0", "corpus_id": corpus["corpus_id"],
        "candidate_ref": "candidate:checked-artifact", "target_class": corpus["class_id"],
        "content_summary": guidance, "scope": corpus["scope"],
        "lifecycle": {"state": "active", "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    }
    trace = tmp_path / "provider-operations"
    source = f'''#!{core_python}
import json
from pathlib import Path
import sys

args = sys.argv[1:]
if args[0] == "--actor-peer-id":
    assert args[1] == {binding["actor_peer_id"]!r}
    args = args[2:]
command = args[0]
if command == "--version":
    print("0.4.19")
elif command == "version":
    print("Server: 0.4.19")
elif command == "status":
    print("{{}}")
elif command in {{"search", "read"}}:
    with Path({str(trace)!r}).open("a") as stream:
        stream.write(command + "\\n")
    if command == "search":
        assert args[args.index("-u") + 1] == {scope!r}
        print(json.dumps([{{"uri": {uri!r}, "level": 2, "score": 0.97}}]))
    else:
        assert args[1] == {uri!r}
        print(json.dumps({{"content": json.dumps({record!r})}}))
else:
    raise AssertionError(command)
'''
    write_python_host_fixture(tmp_path / binding["provider_binary"], source)
    return trace, guidance


@pytest.mark.skipif(os.environ.get("LOOPX_RECALL_TEST_ENABLED") != "1",
                    reason="requires a disposable Core environment with the matching recall wheel")
def test_installed_core_with_recall_wheel(core_python, task, tmp_path):
    workspace, runtime, registry, arguments = task
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    probe = '''import importlib.metadata as m,sys
from loopx.entrypoint import main
recall=m.distribution("loopx-capability-agent-turn-recall")
core=m.distribution("loopx-core")
assert recall.version==core.version
assert f"loopx-core=={core.version}" in recall.requires
members={str(path) for path in recall.files if str(path).startswith("loopx/")}
assert members=={"loopx/capabilities/agent_turn_recall/cli.py","loopx/capabilities/agent_turn_recall/core.py"}
assert members.isdisjoint(str(path) for path in core.files)
assert main(["capability","show","agent-turn-recall","--format","json"])==0
assert "loopx.capabilities.agent_turn_recall.cli" not in sys.modules
assert "loopx.capabilities.agent_turn_recall.core" not in sys.modules
'''
    discovered = subprocess.run([core_python, "-I", "-c", probe], cwd=workspace, env=_environment(core_python),
        capture_output=True, text=True, timeout=45)
    assert discovered.returncode == 0, discovered.stderr
    capability = json.loads(discovered.stdout)["capability"]
    assert capability["provider_state"]["installed"] is True and capability["default_enabled"] is False
    assert "--quota-decision-json" in _run(core_python, ["agent-turn-recall", "--help"], workspace).stdout
    quota_path = tmp_path / "original-quota.json"
    turn_instance_id = "installed-recall"
    arguments.extend(["--turn-instance-id", turn_instance_id])
    recall_command = [*prefix, "agent-turn-recall", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", turn_instance_id, "--quota-decision-json", str(quota_path)]
    before = registry.read_bytes()
    disabled = json.loads(_run(core_python, recall_command, workspace).stdout)
    assert disabled["status"] == "disabled" and disabled["provider_call_count"] == 0
    assert registry.read_bytes() == before and _spends(runtime) == 0

    config = enable_live_memory(registry, ingest=False)
    operations, guidance = _install_fixture_provider(tmp_path, core_python, config)
    environment = {"PATH": os.pathsep.join([str(tmp_path), _environment(core_python)["PATH"]])}
    host = workspace / "recall-host.py"
    host.write_text(HOST_SOURCE.replace("request=json.load(sys.stdin)\n",
        "request=json.load(sys.stdin)\n"
        "pathlib.Path('recall-readback.json').write_text(json.dumps(request['reward_memory_recall']))\n"))
    arguments[arguments.index("--host-adapter-command-json") + 1] = json.dumps([core_python, "-I", str(host)])
    execution = subprocess.run([core_python, "-I", "-m", "loopx.entrypoint", *arguments], cwd=workspace,
        env=_environment(core_python, environment), capture_output=True, text=True, timeout=90)
    assert execution.returncode == 0, (execution.stdout, execution.stderr)
    _assert_committed(core_python, workspace, runtime, json.loads(execution.stdout))
    recall = json.loads((workspace / "recall-readback.json").read_text())
    assert recall["status"] == "applied" and recall["provider_call_count"] == 1
    assert recall["context"]["guidance"][0]["content_summary"] == guidance
    assert operations.read_text().splitlines() == ["search", "read"]

    # The manual command consumes a quota admission, distinct from the
    # generic Turn's lease-bound execution journal.
    _run(core_python, [*prefix, "configure-goal", "--goal-id", GOAL, "--quota-compute", "2", "--execute"], workspace)
    added = json.loads(_run(core_python, [*prefix, "todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--text", "Independently check the next artifact.", "--priority", "P1",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "manual-recall-task"], workspace).stdout)
    _run(core_python, [*prefix, "todo", "claim", "--goal-id", GOAL, "--todo-id", added["todo_id"],
        "--agent-id", AGENT, "--claimed-by", AGENT, "--claim-operation-id", "manual-recall-claim"], workspace)
    enable_live_memory(registry, recall=False, ingest=False)
    manual_turn = "installed-manual-recall"
    decision = _run(core_python, [*prefix, "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", added["todo_id"], "--turn-instance-id", manual_turn, "--context-delivery", "off"], workspace)
    quota_path.write_text(decision.stdout)
    enable_live_memory(registry, ingest=False)
    recall_command[recall_command.index("--turn-instance-id") + 1] = manual_turn
    preview = json.loads(_run(core_python, recall_command, workspace, environment=environment).stdout)
    assert preview["status"] == "preview" and preview["provider_call_count"] == 0
    execute = [*recall_command, "--execute"]
    selected = json.loads(_run(core_python, execute, workspace, environment=environment).stdout)
    assert selected["status"] == "applied" and selected["context"]["guidance"][0]["content_summary"] == guidance
    repeat = json.loads(_run(core_python, execute, workspace, environment=environment).stdout)
    assert repeat["status"] == "deduplicated" and repeat["context"] == selected["context"]
    assert operations.read_text().splitlines() == ["search", "read", "search", "read"] and _spends(runtime) == 1
