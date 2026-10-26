"""Actual HTTP -> registered source -> disposable process -> cited result.

The fixture executable is synthetic; this does not qualify model judgment.
No active Goal, solver state, model account or evaluator is touched.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.managed_effect_runtime_fixture import stop_disposable_effect_runtimes
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import register_pack
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.status.goal_source import GoalSource
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer

_SYSTEM_TEMP = Path(tempfile.gettempdir())


@pytest.fixture
def workspace(tmp_path, monkeypatch, request):
    if os.name == "nt":
        # Hash-qualified job directories must also be valid CreateProcess cwd
        # values. This HTTP fixture does not exercise the long-path contract.
        parent = str(_SYSTEM_TEMP.resolve())
        if not parent.startswith("\\\\?\\"):
            parent = "\\\\?\\UNC\\" + parent[2:] if parent.startswith("\\\\") else "\\\\?\\" + parent
        # Cleanup must still handle deeper source namespaces after a failure.
        temporary = tempfile.TemporaryDirectory(prefix="lxra-", dir=parent)
        request.addfinalizer(temporary.cleanup)
        tmp_path = _SYSTEM_TEMP / Path(temporary.name).name
    process_temp = tmp_path / "process-tmp"
    process_temp.mkdir()
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(process_temp))
    monkeypatch.setattr(tempfile, "tempdir", str(process_temp))
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
    output["synthesis"] = {"summary": {"text": "A diagnostic improvement leaves native utility unresolved.", "episode_ids": ["episode"],
                                      "citations": [dict(source_id="facts", first_line=2, last_line=2)]}, "findings": [], "next_experiments": []}
    binary = tmp_path / "bin"
    binary.mkdir()

    def write_provider(source):
        return write_python_host_fixture(binary / "codex", source)

    shebang = "#!python" if os.name == "nt" else f"#!{sys.executable}"
    provider = write_provider(f'''{shebang}
from pathlib import Path
import json,sys,time
sys.stdin.reconfigure(encoding='utf-8')
prompt=sys.stdin.read()
with Path({str(count)!r}).open('a') as stream: stream.write('started\\n')
mode=Path({str(mode)!r}).read_text()
if mode=='wait': time.sleep(15)
if mode=='fail': raise SystemExit(2)
output=json.loads({json.dumps(json.dumps(output))})
if 'Output language:' in prompt:
    zh='Output language: Simplified Chinese' in prompt
    output['episode_questions']=[{{'episode_id':'episode','question':'诊断改善是否检验了原生收益？' if zh else 'Did diagnostic improvement test native utility?'}}]
    output['assessments'][0]['reason']='只测量了诊断，尚未检验原生收益。' if zh else 'Only the diagnostic was measured; native utility was not tested.'
    output['limitations']=['合成进程输出，并非模型质量验证。' if zh else 'Synthetic process, not model quality evidence.']
if '\\n\\ndisplay_fields:\\n' in prompt:
    fields=json.loads(prompt.split('\\n\\ndisplay_fields:\\n')[1])
    output['display_translations']=[{{'field_id':field['field_id'],'text':field['text']}} for field in fields]
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'output_tokens':2}}}}))
''')
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    selected = shutil.which("codex")
    assert selected and Path(selected).samefile(provider), "Use only the synthetic provider"
    state, registry = tmp_path / "active.md", tmp_path / "registry.json"
    state.write_text("# Synthetic research workspace\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": goal, "repo": str(tmp_path), "state_file": "active.md"} for goal in ("research-owner", "other-owner")]}))
    before = {path: path.read_bytes() for path in (state, registry, input_path, sources / "facts.txt")}
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = (
        registry, str(tmp_path / "private-chat") if getattr(request, "param", False) else None, False
    )
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
        yield dict(request=request, operation=operation, mode=mode, count=count, server=server, sources=sources, before=before, tmp_path=tmp_path,
                   provider_source=provider.with_suffix(".py") if os.name == "nt" else provider,
                   write_provider=write_provider)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        stop_disposable_effect_runtimes(tmp_path)


@pytest.mark.parametrize("workspace", [False, True], indirect=True)
def test_shared_excerpts_reverify_through_http_without_dispatch(workspace, monkeypatch):
    w = workspace
    path = w["tmp_path"] / "input.json"
    data = json.loads(path.read_text())
    data["sources"].append({**data["sources"][0], "id": "prior", "first_line": 1, "text": "Prior question"})
    data["episodes"][0]["source_ids"].append("prior")
    path.write_text(json.dumps(data))
    registered = register_pack(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner",
        input_path=path, source_root=w["sources"], title="Two excerpts of one artifact")
    original = type(path).read_bytes
    reads = []

    def observed(source):
        if source == w["sources"] / "facts.txt":
            reads.append(source)
        return original(source)

    monkeypatch.setattr(type(path), "read_bytes", observed)
    view = w["request"](pack_id=registered["pack_id"])
    assert view["pack"]["availability"] == "available"
    # Two catalog entries plus the selected pack; each verification reads once.
    assert len(reads) == 3 and not w["count"].exists()
    source = w["sources"] / "facts.txt"
    stat = source.stat()
    source.write_bytes(original(source).replace(b"Prior", b"Other"))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reads.clear()
    changed = w["request"](pack_id=registered["pack_id"])
    assert changed["pack"]["reason_code"] == "source_changed"
    assert len(reads) == 3 and not w["count"].exists()


def test_display_context_uses_registered_sources_and_the_existing_http_reader(workspace):
    w, read = workspace, workspace["request"]
    path = w["tmp_path"] / "input.json"
    data = json.loads(path.read_text())
    page = dict(ok=True, goal_id="solver-goal", total=1, next_cursor=None, items=[dict(
        schema_version="loopx_explore_result_event_v0", event_id="record", result_id="question",
        goal_id="solver-goal", event_kind="node", node_kind="question", recorded_at="2026-01-01T00:00:00Z",
        title="Did the diagnostic improve utility?", summary="Native benefit remains unknown.", status="tentative")])
    text = json.dumps(page)
    (w["sources"] / "history.json").write_text(text)
    data["sources"].append(dict(id="history", artifact="history.json", artifact_sha256=hashlib.sha256(text.encode()).hexdigest(), first_line=1, text=text))
    citation = dict(source_id="facts", first_line=2, last_line=2)
    data["display_snapshot"] = dict(schema_version="research_display_snapshot_v1", source_id="history", source_namespace="local",
        contexts=[dict(event_id="record", field="summary", citations=[citation])])
    path.write_text(json.dumps(data))
    registered = register_pack(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner", input_path=path,
                               source_root=w["sources"], title="Synthetic contextual display")
    view = read(pack_id=registered["pack_id"], output_language="zh")["pack"]
    assert view["job"] is None and not w["count"].exists()
    operation = dict(goal_id="research-owner", pack_id=registered["pack_id"], analysis_key=view["analysis_key"], output_language="zh")
    w["mode"].write_text("ok")
    assert read(body=operation)["job"]["status"] == "succeeded"
    shown = read(pack_id=registered["pack_id"], output_language="zh")["pack"]
    field = next(field for field in shown["result"]["display"]["fields"] if field["field"] == "summary")
    assert field["context_citations"] == [citation]
    query = {key: value for key, value in operation.items() if key != "goal_id"}
    assert read(suffix="/citation", **query, **citation)["text"] == data["sources"][0]["text"]
    for overrides in [dict(first_line=1), dict(source_id="history", first_line=1, last_line=1)]:
        with pytest.raises(HTTPError) as denied:
            read(suffix="/citation", **query, **{**citation, **overrides})
        assert denied.value.code in (400, 409)
    assert w["count"].read_text().splitlines() == ["started"]
    assert json.loads(path.read_text()) == data


def test_frontend_language_scopes_process_retry_cache_and_original_citations(workspace):
    w, read = workspace, workspace["request"]
    zh, en, legacy = read(output_language="zh")["pack"], read(output_language="en")["pack"], read()["pack"]
    assert len({p["analysis_key"] for p in (zh, en, legacy)}) == 3
    assert zh["pack_id"] == en["pack_id"] == legacy["pack_id"]
    assert zh["episodes"] == en["episodes"] == legacy["episodes"]
    assert not w["count"].exists()
    operation = {**w["operation"], "analysis_key": zh["analysis_key"], "output_language": "zh"}
    for language in ("fr", "", None, ["zh"]):
        with pytest.raises(HTTPError) as invalid:
            read(body={**operation, "output_language": language})
        assert invalid.value.code == 400
    assert not w["count"].exists()
    with pytest.raises(HTTPError) as mismatch:
        read(body={**operation, "output_language": "en"})
    assert mismatch.value.code == 409 and not w["count"].exists()
    w["mode"].write_text("fail")
    with pytest.raises(HTTPError):
        read(body=operation)
    assert read(output_language="zh")["pack"]["job"]["status"] == "failed"
    assert read(output_language="en")["pack"]["job"] is None
    w["mode"].write_text("ok")
    assert read(body={**operation, "retry": True})["job"]["status"] == "succeeded"
    view = read(output_language="zh")["pack"]
    assert view["result"]["output_language"] == "zh"
    assert view["result"]["episode_questions"][0]["question"] == "诊断改善是否检验了原生收益？"
    assert view["result"]["assessments"][0]["reason"] == "只测量了诊断，尚未检验原生收益。"
    assert view["result"]["limitations"] == ["合成进程输出，并非模型质量验证。"]
    assert view["episodes"][0]["question"] == "Did diagnostic improvement test native utility?"
    calls = w["count"].read_text()
    assert read(body=operation)["job"]["reused"] and w["count"].read_text() == calls
    citation_query = {k: v for k, v in operation.items() if k != "goal_id"}
    citation = read(suffix="/citation", **citation_query, source_id="facts", first_line=2, last_line=2)
    assert citation["text"] == "Only the diagnostic was measured; native utility remains untested."
    assert read(output_language="en")["pack"]["result"] is None
    assert read()["pack"]["result"] is None
    english_operation = {**operation, "analysis_key": en["analysis_key"], "output_language": "en"}
    with pytest.raises(HTTPError) as wrong_cancel:
        read(body={**operation, "output_language": "en"}, suffix="/cancel")
    assert wrong_cancel.value.code == 409
    assert read(body=english_operation)["job"]["status"] == "succeeded"
    assert read(output_language="en")["pack"]["result"]["output_language"] == "en"
    assert read(output_language="zh")["pack"]["result"] == view["result"]
    assert all(path.read_bytes() == data for path, data in w["before"].items())


def test_continuation_http_preserves_previous_analysis_through_cancel_retry_and_equivalent_reuse(workspace):
    w, read = workspace, workspace["request"]
    w["mode"].write_text("ok")
    first = read(output_language="zh")["pack"]
    operation = {**w["operation"], "analysis_key": first["analysis_key"], "output_language": "zh"}
    assert read(body=operation)["job"]["status"] == "succeeded"
    previous = read(output_language="zh")["pack"]["result"]
    source, path = w["sources"], w["tmp_path"] / "input.json"
    data = json.loads(path.read_text())
    later = b"A late follow-up still has no representative utility test.\n"
    (source / "later.txt").write_bytes(later)
    data["sources"].append(dict(id="later", artifact="later.txt", artifact_sha256=hashlib.sha256(later).hexdigest(), first_line=1, text=later.decode().strip()))
    data["episodes"][0]["source_ids"].append("later")
    data["scope"]["revision"] = "later-evidence"
    path.write_text(json.dumps(data))
    scope = dict(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner")
    second = register_pack(**scope, input_path=path, source_root=source, title="Later evidence", base_pack_id=first["pack_id"])
    current = read(output_language="zh")["pack"]
    assert current["result"] is None and current["job"] is None
    assert current["continuation"]["sources"]["added"] == ["later"]
    assert current["continuation"]["reassessed_episode_ids"] == ["episode"]
    assert not current["continuation"]["can_reuse"]
    successor = {**operation, "pack_id": second["pack_id"], "analysis_key": current["analysis_key"]}
    w["mode"].write_text("wait")
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(read, body=successor)
        until = time.monotonic() + 8
        while time.monotonic() < until:
            view = read(output_language="zh")["pack"]
            if view["job"] and view["job"]["status"] == "running":
                break
            time.sleep(.05)
        else:
            pytest.fail("successor never reached its actual process receipt")
        assert read(body=successor)["job"]["status"] == "busy"
        assert read(pack_id=first["pack_id"], output_language="zh")["pack"]["result"] == previous
        assert read(body=successor, suffix="/cancel")["job"]["cancel_requested"]
        assert running.result(timeout=8)["job"]["status"] == "cancelled"
    assert read(pack_id=first["pack_id"], output_language="zh")["pack"]["result"] == previous
    w["mode"].write_text("fail")
    with pytest.raises(HTTPError):
        read(body={**successor, "retry": True})
    w["mode"].write_text("ok")
    assert read(body={**successor, "retry": True})["job"]["attempt"] == 3
    completed = read(output_language="zh")["pack"]
    assert completed["result"]["metrics"][1]["eligible"] == 1
    assert completed["continuation"]["verdict_changes"] == []
    calls = w["count"].read_text()
    assert read(output_language="en")["pack"]["result"] is None
    assert w["count"].read_text() == calls  # locale and history reads never execute
    # Capture metadata outside the selected excerpt is not new evidence.
    evidence = (source / "facts.txt").read_bytes() + b"Additional unselected metadata\n"
    (source / "facts.txt").write_bytes(evidence)
    data["sources"][0]["artifact_sha256"] = hashlib.sha256(evidence).hexdigest()
    data["scope"]["revision"] = "capture-only"
    path.write_text(json.dumps(data))
    third = register_pack(**scope, input_path=path, source_root=source, title="Equivalent capture", base_pack_id=second["pack_id"])
    view = read(output_language="zh")["pack"]
    assert view["continuation"]["can_reuse"] and view["result"] is None
    reused = read(body={**successor, "pack_id": third["pack_id"], "analysis_key": view["analysis_key"]})["job"]
    assert reused["reused_from_analysis_key"] == completed["analysis_key"]
    assert w["count"].read_text() == calls
    assert read(output_language="zh")["pack"]["result"]["scope"]["revision"] == "capture-only"
    assert read(pack_id=second["pack_id"], output_language="zh")["pack"]["result"] == completed["result"]
    base_path = next((scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob(f"*/packs/{second['pack_id']}.json"))
    captured = base_path.read_bytes()
    base_path.unlink()
    unavailable_base = read(output_language="zh")
    assert unavailable_base["catalog"]["unreadable_pack_ids"] == [second["pack_id"]]
    assert unavailable_base["pack"]["continuation_unavailable"] == {"base_pack_id": second["pack_id"]}
    assert unavailable_base["pack"]["result"]["scope"]["revision"] == "capture-only"
    assert w["count"].read_text() == calls
    base_path.write_bytes(captured)
    assert read(output_language="zh")["pack"]["continuation"]["base_available"]


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


@pytest.mark.parametrize("workspace", [False, True], indirect=True)
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


@pytest.mark.parametrize("workspace", [False, True], indirect=True)
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


def test_explicit_native_context_reaches_process_and_changed_source_blocks_reuse(workspace):
    w, read = workspace, workspace["request"]
    path = w["tmp_path"] / "input.json"
    data = json.loads(path.read_text())
    native = {"schema_version": "trajectory_native_outcomes_v1", "scope": data["scope"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:04:00Z", "complete": True,
        "metric": {"name": "error", "unit": "points", "higher_is_better": False},
        "retained_result_id": None,
        "comparison": {"baseline_result_id": "result-1", "retention_citation": None},
        "samples": [{"submission_index": i, "result_id": f"result-{i}", "candidate_ref": f"candidate-{i}",
            "valid": True, "value": value, "submitted_at": f"2026-01-01T00:0{i}:00Z",
            "observed_at": f"2026-01-01T00:0{i}:30Z",
            "citation": {"source_id": "facts", "first_line": 2, "last_line": 2}}
            for i, value in enumerate((10, 9), 1)]}
    capture = json.dumps(native)
    native_path = w["sources"] / "native.json"
    native_path.write_text(capture)
    scope = dict(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner",
        input_path=path, source_root=w["sources"], native_outcomes_json="native.json")
    # Display-only registration keeps the existing analysis identity.
    legacy = register_pack(**scope, title="Display-only native outcomes")
    assert legacy["analysis_key"] == w["operation"]["analysis_key"]
    data["sources"].append(dict(id="capture", artifact="native.json",
        artifact_sha256=hashlib.sha256(capture.encode()).hexdigest(), first_line=1, text=capture))
    data["native_outcomes_source_id"] = "capture"
    path.write_text(json.dumps(data))
    registered = register_pack(**scope, title="Explicit native context", base_pack_id=legacy["pack_id"])
    assert registered["analysis_key"] != legacy["analysis_key"]
    before = read(pack_id=registered["pack_id"])["pack"]
    assert before["native_outcomes"]["projection"]["retained"] is None
    assert before["job"] is None and not w["count"].exists()
    provider = w["provider_source"]
    prompt_path = w["tmp_path"] / "received-prompt.txt"
    w["write_provider"](provider.read_text(encoding="utf-8").replace("prompt=sys.stdin.read()",
        f"prompt=sys.stdin.read()\nPath({str(prompt_path)!r}).write_text(prompt,encoding='utf-8')"))
    operation = {**w["operation"], "pack_id": registered["pack_id"], "analysis_key": registered["analysis_key"]}
    w["mode"].write_text("ok")
    assert read(body=operation)["job"]["status"] == "succeeded"
    context = json.loads(prompt_path.read_text(encoding="utf-8").split("\n\nNative outcome context:\n")[1])
    assert context["metric"]["higher_is_better"] is False
    assert context["best_valid"]["value"] == context["final_valid"]["value"] == 9
    assert context["retained"] is None
    assert context["retained_improvement"]["value"] is None
    assert context["retained_improvement"]["final_candidate_delta"] == 1
    assert capture not in prompt_path.read_text()
    assert read(body=operation)["job"]["reused"]
    read(pack_id=registered["pack_id"], output_language="en")
    assert w["count"].read_text().splitlines() == ["started"]
    native_path.write_text(capture + "\n")
    historical = read(pack_id=registered["pack_id"])["pack"]
    assert historical["result"] is not None
    assert historical["native_outcomes"]["applicability"] == "historical"
    with pytest.raises(HTTPError) as changed:
        read(body=operation)
    assert changed.value.code == 409
    assert w["count"].read_text().splitlines() == ["started"]


@contextmanager
def source_analysis_workspace(w, name, *, enabled=True):
    """Synthetic source transport; actual local store, HTTP and provider process."""
    class Source(GoalSource):
        failed = False
        registered = True
        reads = 0

        def __init__(self):
            root = w["tmp_path"]
            super().__init__(name, name, str(root / "registry.json"), str(root / "runtime"),
                             str(root / "source-cli"), sys.executable,
                             str(root / "bin"), "local", "host", "research-owner")

        def read_status(self, goal_id=None):
            self.reads += 1
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


@pytest.mark.parametrize("registered,failed,code", [(True, True, 503), (False, False, 404)])
def test_source_analysis_cancel_does_not_require_live_source(workspace, registered, failed, code):
    w = workspace
    with source_analysis_workspace(w, "offline-cancel") as s:
        read, operation, source = s["request"], s["operation"], s["server"].source
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(read, body=operation)
            until = time.monotonic() + 8
            while not w["count"].exists() and time.monotonic() < until:
                time.sleep(.05)
            assert w["count"].exists(), "Local analysis process did not start"
            reads = source.reads
            source.registered, source.failed = registered, failed
            try:
                assert read(body=operation, suffix="/cancel")["job"]["cancel_requested"]
                assert running.result(timeout=8)["job"]["status"] == "cancelled"
                assert source.reads == reads, "Cancelling a local job must not read its original source"
                for retry in (False, True):
                    with pytest.raises(HTTPError) as denied:
                        read(body={**operation, "retry": retry})
                    assert denied.value.code == code
                assert w["count"].read_text().splitlines() == ["started"]
            finally:
                # The same test must clean up its child when run against the
                # defective baseline, where offline cancellation is rejected.
                source.registered, source.failed = True, False
                read(body=operation, suffix="/cancel")
                running.result(timeout=8)
        assert read()["pack"]["job"]["status"] == "cancelled"
        for path, original in w["before"].items():
            assert path.read_bytes() == original


def test_source_analysis_offline_cancel_requires_pinned_job_identity(workspace):
    w = workspace
    with source_analysis_workspace(w, "cancel-identity") as s:
        read, operation, source = s["request"], s["operation"], s["server"].source
        source.failed = True
        with pytest.raises(HTTPError):
            read(body=operation, suffix="/cancel")
        assert not w["count"].exists(), "Cancellation cannot create a missing local job"
        source.failed = False
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(read, body=operation)
            until = time.monotonic() + 8
            while not w["count"].exists() and time.monotonic() < until:
                time.sleep(.05)
            assert w["count"].exists(), "Local analysis process did not start"
            source.failed = True
            try:
                for namespace in (None, "0" * 64):
                    with pytest.raises(HTTPError) as denied:
                        read(body=operation, suffix="/cancel", namespace=namespace)
                    assert denied.value.code == 400
                for field, value, code in (
                    ("goal_id", "another-goal", 404),
                    ("pack_id", "0" * 64, 409),
                    ("analysis_key", "0" * 64, 409),
                ):
                    with pytest.raises(HTTPError) as denied:
                        read(body={**operation, field: value}, suffix="/cancel")
                    assert denied.value.code == code
                assert not list(s["server"].analysis_runtime_root.rglob("cancel.request"))
                assert not running.done(), "A mismatched selector must not cancel the running job"
                assert read(body=operation, suffix="/cancel")["job"]["cancel_requested"]
                assert running.result(timeout=8)["job"]["status"] == "cancelled"
                assert w["count"].read_text().splitlines() == ["started"]
            finally:
                source.failed = False
                read(body=operation, suffix="/cancel")
                running.result(timeout=8)


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


@pytest.mark.parametrize("registered,failed,code", [(False, False, 404), (True, True, 503)])
@pytest.mark.parametrize("retry", [False, True])
def test_source_execution_rechecks_a_warm_source_before_start_or_retry(
        workspace, monkeypatch, registered, failed, code, retry):
    w = workspace
    w["mode"].write_text("ok")
    # Hold the display-cache clock inside its normal lifetime. Source facts
    # change after a real GET; the test never alters private cache fields.
    monkeypatch.setattr("loopx.control_plane.status.goal_source.monotonic", lambda: 100.0)
    with source_analysis_workspace(w, "warm-source") as s:
        read, operation, source = s["request"], s["operation"], s["server"].source
        assert read()["pack"]["job"] is None
        assert read()["pack"]["scope"]["goal_id"] == "research-owner"
        assert source.reads == 1  # ordinary reads still share the display cache
        source.registered, source.failed = registered, failed
        with pytest.raises(HTTPError) as denied:
            read(body={**operation, "retry": retry})
        assert denied.value.code == code
        assert source.reads == 2
        assert not w["count"].exists()
        source.registered, source.failed = True, False
        assert read(body={**operation, "retry": retry})["job"]["status"] == "succeeded"
        assert source.reads == 3
        assert w["count"].read_text().splitlines() == ["started"]
        assert read()["pack"]["scope"]["goal_id"] == "research-owner"
        for path, original in w["before"].items():
            assert path.read_bytes() == original


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


def test_synthesis_only_citation_uses_existing_reader_and_does_not_execute(workspace):
    w, read = workspace, workspace["request"]
    # The exact two-line synthesis citation is distinct from the metric's
    # single-line citation. Arbitrary ranges remain unauthorized.
    path = w["tmp_path"] / "input.json"
    data = json.loads(path.read_text())
    data["sources"][0].update(first_line=1, text=(w["sources"] / "facts.txt").read_text().rstrip("\n"))
    path.write_text(json.dumps(data))
    registered = register_pack(runtime_root=w["tmp_path"] / "runtime", owner_goal_id="research-owner", input_path=path,
                               source_root=w["sources"], title="Synthetic grounded synthesis")
    provider = w["provider_source"]
    script = provider.read_text(encoding="utf-8").replace("Path(sys.argv[sys.argv.index('-o')+1]).write_text", "output['synthesis']['summary']['citations'][0]['first_line']=1\nPath(sys.argv[sys.argv.index('-o')+1]).write_text")
    w["write_provider"](script)
    before = read(pack_id=registered["pack_id"])["pack"]
    assert before["job"] is None and not w["count"].exists()
    operation = {**w["operation"], "pack_id":registered["pack_id"], "analysis_key":before["analysis_key"]}
    w["mode"].write_text("ok")
    assert read(body=operation)["job"]["status"] == "succeeded"
    result = read(pack_id=registered["pack_id"])["pack"]["result"]
    citation = result["synthesis"]["summary"]["citations"][0]
    assert citation == dict(source_id="facts", first_line=1, last_line=2)
    query = {key:value for key,value in operation.items() if key != "goal_id"}
    assert read(suffix="/citation", **query, **citation)["text"] == data["sources"][0]["text"]
    with pytest.raises(HTTPError):
        read(suffix="/citation", **query, **{**citation,"last_line":3})
    assert w["count"].read_text().splitlines() == ["started"]
    assert json.loads(path.read_text()) == data
