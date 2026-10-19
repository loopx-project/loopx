"""Actual HTTP -> registered source -> disposable process -> cited result.

The fixture executable is synthetic; this does not qualify model judgment.
No active Goal, solver state, model account or evaluator is touched.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
from loopx.control_plane.status.goal_source import GoalSource
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer


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


@contextmanager
def source_analysis_workspace(w, name, *, enabled=True):
    """Synthetic source transport; actual local store, HTTP and provider process."""
    class Source(GoalSource):
        failed = False
        registered = True

        def __init__(self):
            super().__init__(name, name, "/registry", "/runtime", "/cli", "/python",
                             "/bin", "local", "host", "research-owner")

        def read_status(self, goal_id=None):
            self.check_access(goal_id)
            if self.failed:
                raise OSError("Private source diagnostic")
            return {"ok": True, "run_history": {"goals": [{"id": self.goal_id,
                    "registry_member": self.registered}]}}

    server = RuntimeWorkspaceServer(Source(), w["tmp_path"],
        analysis_runtime_root=w["tmp_path"] / "source-analysis" if enabled else None)
    data = json.loads((w["tmp_path"] / "input.json").read_text())
    data["scope"].update(goal_id="research-owner", case_id=name)
    path = w["tmp_path"] / f"input-{name}.json"
    path.write_text(json.dumps(data))
    pack = register_pack(runtime_root=server.analysis_runtime_root or w["tmp_path"] / "disabled",
        owner_goal_id="research-owner", input_path=path, source_root=w["sources"], title=name)
    operation = dict(goal_id="research-owner", pack_id=pack["pack_id"], analysis_key=pack["analysis_key"])
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(*, body=None, suffix="", namespace=server.source.namespace, method=None, headers=None, **query):
        if namespace is not None:
            query["source_namespace"] = namespace
        if body is None:
            query.setdefault("goal_id", "research-owner")
        url = f"http://127.0.0.1:{server.server_port}/api/chat/research-analysis{suffix}?{urlencode(query)}"
        req = Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                      headers={"Content-Type": "application/json", **(headers or {})})
        with urlopen(req, timeout=25) as response:
            return json.load(response)

    try:
        yield dict(request=request, operation=operation, server=server, input_path=path)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_source_goal_analysis_reads_cancel_retry_and_reuse_do_not_mutate_source(workspace):
    w = workspace
    with source_analysis_workspace(w, "retained") as s:
        read, operation = s["request"], s["operation"]
        snapshot = read()
        assert snapshot["execution_enabled"] is True
        assert snapshot["pack"]["scope"]["goal_id"] == "research-owner"
        assert snapshot["pack"]["job"] is None and not w["count"].exists()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(read, body=operation)
            until = time.monotonic() + 8
            while time.monotonic() < until:
                job = read()["pack"]["job"]
                if job and job["status"] == "running":
                    break
                time.sleep(.05)
            else:
                pytest.fail("Local source-analysis process did not start")
            assert read(body=operation)["job"]["status"] == "busy"
            assert read(body=operation, suffix="/cancel")["job"]["cancel_requested"]
            assert running.result(timeout=8)["job"]["status"] == "cancelled"
        w["mode"].write_text("ok")
        assert read(body={**operation, "retry": True})["job"]["status"] == "succeeded"
        view = read()["pack"]
        assert view["result"]["assessments"][0]["verdict"] == "no"
        calls = w["count"].read_bytes()
        assert read(body=operation)["job"]["reused"]
        assert w["count"].read_bytes() == calls
        citation = dict(pack_id=operation["pack_id"], analysis_key=operation["analysis_key"],
                        source_id="facts", first_line=2, last_line=2)
        assert read(suffix="/citation", **citation)["text"] == "Only the diagnostic was measured; native utility remains untested."
        for path, original in w["before"].items():
            assert path.read_bytes() == original
        (w["sources"] / "facts.txt").write_text("changed source")
        assert read()["pack"]["applicability"] == "historical"
        assert read(suffix="/citation", **citation)["text"] == "Only the diagnostic was measured; native utility remains untested."
        with pytest.raises(HTTPError) as changed:
            read(body=operation)
        assert changed.value.code == 409


def test_source_analysis_requires_exact_namespace_goal_and_live_membership(workspace):
    w = workspace
    with source_analysis_workspace(w, "retained") as a, source_analysis_workspace(w, "fresh") as b:
        assert a["server"].analysis_runtime_root != b["server"].analysis_runtime_root
        assert a["request"]()["pack"]["scope"]["case_id"] == "retained"
        assert b["request"]()["pack"]["scope"]["case_id"] == "fresh"
        for namespace in (None, "0" * 64, b["server"].source.namespace):
            with pytest.raises(HTTPError):
                a["request"](namespace=namespace)
            with pytest.raises(HTTPError):
                a["request"](namespace=namespace, body=a["operation"])
        for body in (a["operation"], {**b["operation"], "goal_id": "another-goal"}):
            with pytest.raises(HTTPError):
                b["request"](body=body)
        with pytest.raises(HTTPError):
            b["request"](pack_id=a["operation"]["pack_id"])
        source = a["server"].source
        for registered, failed, code in ((False, False, 404), (True, True, 503)):
            source.registered, source.failed = registered, failed
            a["server"].reader._read_at = 0
            with pytest.raises(HTTPError) as denied:
                a["request"](body=a["operation"])
            assert denied.value.code == code
        source.registered, source.failed = True, False
        a["server"].reader._read_at = 0
        assert a["request"]()["pack"]["scope"]["case_id"] == "retained"
        assert not w["count"].exists()


def test_source_analysis_default_off_and_non_analysis_writes_stay_disabled(workspace):
    w = workspace
    with source_analysis_workspace(w, "disabled", enabled=False) as s:
        with pytest.raises(HTTPError) as unavailable:
            s["request"]()
        assert unavailable.value.code == 503
        with pytest.raises(HTTPError) as unavailable_citation:
            s["request"](suffix="/citation")
        assert unavailable_citation.value.code == 404
        with pytest.raises(HTTPError) as readonly:
            s["request"](body=s["operation"])
        assert readonly.value.code == 405
    with source_analysis_workspace(w, "enabled") as s:
        for method in ("PUT", "PATCH", "DELETE"):
            with pytest.raises(HTTPError) as readonly:
                s["request"](body=s["operation"], method=method)
            assert readonly.value.code == 405
        with pytest.raises(HTTPError) as origin:
            s["request"](body=s["operation"], headers={"Origin": "https://unrelated.example"})
        assert origin.value.code == 403
        data = json.loads(s["input_path"].read_text())
        data["scope"]["goal_id"] = "another-goal"
        s["input_path"].write_text(json.dumps(data))
        registered = register_pack(runtime_root=s["server"].analysis_runtime_root,
            owner_goal_id="research-owner", input_path=s["input_path"], source_root=w["sources"], title="Wrong Goal")
        for body in (None, dict(goal_id="research-owner", pack_id=registered["pack_id"], analysis_key=registered["analysis_key"])):
            with pytest.raises(HTTPError):
                s["request"](body=body)
        assert not w["count"].exists()
