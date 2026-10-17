"""Actual HTTP -> registered source -> disposable process -> cited result.

The fixture executable is synthetic; this does not qualify model judgment.
No active Goal, solver state, model account or evaluator is touched.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import register_pack
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    runtime, sources = tmp_path / "runtime", tmp_path / "sources"
    sources.mkdir()
    evidence = b"Prior question\nOnly the diagnostic was measured; native utility remains untested.\n"
    (sources / "facts.txt").write_bytes(evidence)
    data = {
        "schema_version": "trajectory_assessment_input_v1",
        "scope": dict(goal_id="solver-goal", case_id="case", run_id="run", revision="fixed", window="one fixed episode"),
        "objective": "Improve native utility, including end-to-end cost",
        "selection": dict(rule="Fixed synthetic episode", population_episodes=None, exhaustive=False),
        "sources": [dict(id="facts", artifact="facts.txt", artifact_sha256=hashlib.sha256(evidence).hexdigest(), first_line=2, text=evidence.decode().splitlines()[1])],
        "episodes": [dict(id="episode", question="Did diagnostic improvement test native utility?", source_ids=["facts"], eligible_metrics=["benefit_test"])],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(data))
    pack = register_pack(runtime_root=runtime, owner_goal_id="research-owner", input_path=input_path, source_root=sources, title="A fixed diagnostic episode")
    mode, count = tmp_path / "mode", tmp_path / "process-count"
    mode.write_text("wait")
    output = {"assessments": [dict(episode_id="episode", metric="benefit_test", verdict="no", reason="Diagnostic measurements do not establish a native utility test.", citations=[dict(source_id="facts", first_line=2, last_line=2)])], "limitations": ["Synthetic process output, not a calibrated model assessment."]}
    binary = tmp_path / "bin"
    binary.mkdir()
    provider = binary / "codex"
    provider.write_text(f'''#!{sys.executable}
from pathlib import Path
import json,sys,time
sys.stdin.read()
with Path({str(count)!r}).open('a') as stream: stream.write('started\\n')
mode=Path({str(mode)!r}).read_text()
if mode=='wait': time.sleep(15)
if mode=='fail': raise SystemExit(2)
Path(sys.argv[sys.argv.index('-o')+1]).write_text({json.dumps(json.dumps(output))})
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'output_tokens':2}}}}))
''')
    provider.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    state, registry = tmp_path / "active.md", tmp_path / "registry.json"
    state.write_text("# Synthetic research workspace\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": goal, "repo": str(tmp_path), "state_file": "active.md"} for goal in ("research-owner", "other-owner")]}))
    before = {path: path.read_bytes() for path in (state, registry, input_path, sources / "facts.txt")}
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, None, False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/research-analysis"

    def request(*, goal="research-owner", body=None, suffix="", headers=None, **query):
        url = base + suffix
        if body is None:
            url += "?" + urlencode({"goal_id": goal, **query})
        req = Request(url, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
        with urlopen(req, timeout=25) as response:
            return json.load(response)

    operation = dict(goal_id="research-owner", pack_id=pack["pack_id"], analysis_key=pack["analysis_key"])
    try:
        yield dict(request=request, operation=operation, mode=mode, count=count, server=server, sources=sources, before=before, tmp_path=tmp_path)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_registered_http_cancel_failure_retry_reuse_and_source_return(workspace):
    w = workspace
    read, operation = w["request"], w["operation"]
    initial = read()
    assert initial["pack"]["scope"]["goal_id"] == "solver-goal"
    assert initial["pack"]["job"] is None and initial["pack"]["result"] is None
    assert not w["count"].exists()  # opening and refreshing never launch analysis
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(read, body=operation)
        until = time.monotonic() + 8
        while time.monotonic() < until:
            snapshot = read()
            if snapshot["pack"]["job"] and snapshot["pack"]["job"]["status"] == "running":
                break
            time.sleep(.05)
        else:
            pytest.fail("actual process never reached running")
        assert read(body=operation)["job"]["status"] == "busy"
        assert read(body=operation, suffix="/cancel")["job"]["cancel_requested"]
        assert running.result(timeout=8)["job"]["status"] == "cancelled"
    w["mode"].write_text("fail")
    with pytest.raises(HTTPError) as failed:
        read(body={**operation, "retry": True})
    assert failed.value.code == 400
    assert read()["pack"]["job"]["status"] == "failed"
    w["mode"].write_text("ok")
    result = read(body={**operation, "retry": True})
    assert result["job"]["status"] == "succeeded" and result["job"]["attempt"] == 3
    view = read()["pack"]
    assert view["result"]["calibration"] == "uncalibrated_model"
    assert view["result"]["assessments"][0]["verdict"] == "no"
    assert str(w["tmp_path"]) not in json.dumps(view)
    calls = w["count"].read_text()
    assert read(body=operation)["job"]["reused"] and w["count"].read_text() == calls
    citation_query = dict(pack_id=operation["pack_id"], analysis_key=operation["analysis_key"], source_id="facts", first_line=2, last_line=2)
    citation = read(suffix="/citation", **citation_query)
    assert citation["text"] == "Only the diagnostic was measured; native utility remains untested."
    for path, original in w["before"].items():
        assert path.read_bytes() == original
    (w["sources"] / "facts.txt").write_text("changed source")
    historical = read()["pack"]
    assert historical["availability"] == "unavailable" and historical["applicability"] == "historical"
    assert historical["result"] == view["result"]
    assert read(suffix="/citation", **citation_query)["text"] == citation["text"]
    with pytest.raises(HTTPError) as changed:
        read(body=operation)
    assert changed.value.code == 409


def test_http_rejects_other_owner_paths_free_configuration_and_wrong_origin(workspace):
    w = workspace
    read, operation = w["request"], w["operation"]
    assert read(goal="other-owner")["catalog"]["packs"] == []
    for query in ({"pack_id": operation["pack_id"]}, {"input_path": "/private/file"}):
        with pytest.raises(HTTPError):
            read(goal="other-owner", **query)
    for body in ({**operation, "input_path": "/private/file"}, {**operation, "model": "another-model"}, {**operation, "retry": "true"}, {**operation, "analysis_key": "../escape"}):
        with pytest.raises(HTTPError):
            read(body=body)
    with pytest.raises(HTTPError) as origin:
        read(body=operation, headers={"Origin": "https://unrelated.example"})
    assert origin.value.code == 403
    w["server"].selected_goal_id = "other-owner"
    with pytest.raises(HTTPError):
        read()
    assert not w["count"].exists()


def test_native_outcomes_and_bound_citations_read_over_http_without_analysis(workspace):
    w = workspace
    data = json.loads((w["tmp_path"] / "input.json").read_text())
    native = {"schema_version": "trajectory_native_outcomes_v1", "scope": data["scope"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:03:00Z", "complete": True,
        "metric": {"name": "error", "unit": "points", "higher_is_better": False},
        "retained_result_id": "result-one", "samples": [{"submission_index": 1,
            "result_id": "result-one", "candidate_ref": "candidate-one", "valid": True, "value": 0.000001,
            "submitted_at": "2026-01-01T00:01:00Z", "observed_at": "2026-01-01T00:02:00Z",
            "citation": {"source_id": "facts", "first_line": 2, "last_line": 2}}]}
    (w["sources"] / "native.json").write_text(json.dumps(native))
    registered = register_pack(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner",
        input_path=w["tmp_path"] / "input.json", source_root=w["sources"], title="Native capture", native_outcomes_json="native.json")
    snapshot = w["request"](pack_id=registered["pack_id"])
    assert snapshot["pack"]["native_outcomes"]["projection"]["retained"]["value"] == 0.000001
    assert snapshot["pack"]["job"] is None
    citation = w["request"](suffix="/citation", pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
        source_id="facts", first_line=2, last_line=2)
    assert citation["text"] == data["sources"][0]["text"]
    assert not w["count"].exists()
    with pytest.raises(HTTPError):
        w["request"](goal="other-owner", pack_id=registered["pack_id"])
