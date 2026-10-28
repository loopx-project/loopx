"""Original delegation material reaches the actual native Host input.

Real File/SQLite and disposable subprocesses; no provider/model or live App.
The input oracle is the original sender material, independent of the serializer.
"""
import json
from pathlib import Path

import pytest

from loopx.control_plane.collaboration.inbox import _read, _root, _write
from loopx.control_plane.turn_driver.codex_cli import run_codex_cli_host
from test_delegation_source_authority import saved_handoff  # noqa: F401
from test_local_delegation import demo, service as delegation_service  # noqa: F401
from test_loopx_turn_codex_cli import _fake_codex


ORIGINAL = "原始委派：保留全部约束、负结果与原文。\n" * 700 + 'END: "quoted" \\ unicode 🧭'


def preview(worker, binding, operation, *, include_context=True, **override):
    row = _read(worker.path(operation))
    selector = {"goal-id": worker.goal_id, "agent-id": binding["agent_id"],
                "todo-id": binding["todo_id"], "turn-instance-id": worker._turn_instance_id(row)}
    selector.update(override)
    args = [value for key, item in selector.items() for value in ("--" + key, item)]
    context = []
    if include_context:
        from loopx.control_plane.collaboration.delegation_context import delegated_turn_context_arguments
        context = delegated_turn_context_arguments(worker, row)
    return worker._cli(binding, "turn", "run-once", *args,
        *worker._execution_arguments(binding, operation), *context)


def original_context(envelope):
    return next(source["content"] for source in envelope["work_context"]["sources"]
                if source.get("source") == "delegation_work")


@pytest.mark.parametrize("saved_handoff", [ORIGINAL], indirect=True, ids=["long-mixed-source"])
def test_original_material_reaches_capture_and_actual_codex_stdin(saved_handoff, monkeypatch):  # noqa: F811
    root, worker, binding, (_, _, _, request, receipt, _), operation = saved_handoff
    tasks = demo.canonical_tasks(root)
    plan = preview(worker, binding, operation, include_context=False)
    assert plan["route"]["would_invoke_host"], plan
    assert not (Path(binding["workspace"]) / "DELEGATION.json").exists()
    assert demo.canonical_tasks(root) == tasks
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("root = Path(sys.argv[1])",
        "root = Path(sys.argv[1])\n(root / 'received-request.json').write_text(json.dumps(request))"))
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    host_request = json.loads((root / "received-request.json").read_text())
    content = original_context(host_request["turn_envelope"])
    assert content["source_request"]["message"] == ORIGINAL
    assert content["source_request"]["request_id"] == receipt["request_id"]
    assert content["brief"] == content["source_request"]["brief"] == request["brief"]
    assert content["request_id"] == _read(worker.path(operation))["identity"]["request_id"]
    assert str(worker.config) not in json.dumps(content)
    assert not (Path(binding["workspace"]) / "DELEGATION.json").exists()
    tasks = demo.canonical_tasks(root)

    # Exercise the production Codex renderer and native process transport with
    # that real plan. This is stdin qualification, not another governed business
    # Turn, a paid model or a claim that a model adopted the material.
    executable, log = _fake_codex(root)
    prompt_file = root / "native-prompt.txt"
    executable.write_text(executable.read_text().replace("prompt = sys.stdin.read()",
        "prompt = sys.stdin.read()\npathlib.Path(os.environ['FAKE_CODEX_PROMPT']).write_text(prompt)"))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_PROMPT", str(prompt_file))
    result = run_codex_cli_host(host_request, runtime_root=worker.root,
        project=Path(binding["workspace"]), codex_bin=str(executable), registry_path=worker.registry,
        timeout_seconds=10)
    assert result["result_kind"] == "validated_progress"
    prompt = prompt_file.read_text()
    assert json.dumps(ORIGINAL, ensure_ascii=False)[1:-1] in prompt
    assert json.dumps(request["brief"], ensure_ascii=False, sort_keys=True, separators=(",", ":")) in prompt
    assert demo.canonical_tasks(root) == tasks


def test_historical_operation_retains_its_recorded_input_profile(saved_handoff):  # noqa: F811
    from loopx.control_plane.collaboration.delegation_context import delegated_turn_context_arguments
    root, worker, binding, _, operation = saved_handoff
    row = _read(worker.path(operation))
    row.pop("work_context_required")
    _write(worker.path(operation), row)
    assert not delegated_turn_context_arguments(worker, row)
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


def test_worker_consumes_captured_work_without_reading_compatibility_bootstrap(saved_handoff):  # noqa: F811
    root, worker, binding, (_, _, _, request, receipt, _), operation = saved_handoff
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace(
        "delegation = json.loads((workspace / 'DELEGATION.json').read_text())",
        "delegation = next(s['content'] for s in envelope['work_context']['sources'] if s.get('source') == 'delegation_work')\n"
        "(root / 'received-work.json').write_text(json.dumps(delegation))"))
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    content = json.loads((root / "received-work.json").read_text())
    assert content["brief"] == request["brief"]
    assert content["source_request"]["request_id"] == receipt["request_id"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert not (Path(binding["workspace"]) / "DELEGATION.json").exists()
    worker.execute(operation)  # Original capture/receipt replay, no second Host.
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


@pytest.mark.parametrize("field", ["agent-id", "todo-id", "turn-instance-id"])
def test_material_locator_cannot_retarget_work(saved_handoff, field):  # noqa: F811
    root, worker, binding, _, operation = saved_handoff
    tasks = demo.canonical_tasks(root)
    result = preview(worker, binding, operation, **{field: "wrong-original-identity"})
    assert result.get("ok") is False
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert demo.canonical_tasks(root) == tasks


@pytest.mark.parametrize("change", ["missing", "message"])
def test_missing_or_substituted_original_message_prevents_host(saved_handoff, change):  # noqa: F811
    root, worker, binding, (_, _, _, _, receipt, _), operation = saved_handoff
    tasks = demo.canonical_tasks(root)
    path = next((_root(worker.root) / "entries").glob("*/" + receipt["request_id"] + ".json"))
    if change == "missing":
        path.unlink()
    else:
        entry = _read(path)
        entry["message"] = "A substituted request with the same brief is not the original message."
        _write(path, entry)
    worker.execute(operation)
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert demo.canonical_tasks(root) == tasks
