"""Captured reader fixtures preserve source bytes and launch only synthetic Python."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable, _verify_sources,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_history_fixture", ROOT / "examples/personal-workspace-browser/research-history-backend.py",
)
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


@pytest.mark.parametrize("fixture", ["research-native-readback.json", "research-synthesis-readback.json"])
def test_captured_analysis_materialization_preserves_declared_bytes_and_excerpts(tmp_path, fixture):
    capture = json.loads((ROOT / "apps/presentation/dashboard/src/data/fixtures" / fixture).read_bytes())
    analysis = capture["analyses"][0]
    source_root = tmp_path / "analysis"
    input_path = backend.materialize_analysis(source_root, analysis)
    assert json.loads(input_path.read_bytes()) == analysis["input"]
    for artifact, text in analysis["artifacts"].items():
        assert (source_root / artifact).read_bytes() == text.encode("utf-8")
    record = {"source_root": str(source_root), "input": analysis["input"]}
    for source in record["input"]["sources"]:
        assert hashlib.sha256((source_root / source["artifact"]).read_bytes()).hexdigest() == source["artifact_sha256"]
    _verify_sources(record)
    source = source_root / record["input"]["sources"][0]["artifact"]
    original = source.read_bytes()
    source.write_bytes(b"X" + original[1:])
    with pytest.raises(PackUnavailable, match="source_changed"):
        _verify_sources(record)
    source.write_bytes(original)
    changed_excerpt = copy.deepcopy(record)
    changed_excerpt["input"]["sources"][0]["text"] = "An unsupported excerpt"
    with pytest.raises(PackUnavailable, match="excerpt_mismatch"):
        _verify_sources(changed_excerpt)


def test_synthetic_provider_uses_selected_python_with_real_arguments_pipes_and_exit(tmp_path):
    provider = backend.write_python_host_fixture(
        tmp_path / "synthetic provider",
        ("#!python\n" if sys.platform == "win32" else f"#!{sys.executable}\n")
        + "import json,os,sys\n"
        "print(json.dumps([sys.argv[1:],sys.stdin.read(),os.getcwd(),sys.executable]))\n"
        "sys.stderr.write('Synthetic diagnostic')\nraise SystemExit(7)\n",
    )
    arguments = ["exec", "argument with spaces", "-o", "result path.json"]
    result = subprocess.run([str(provider), *arguments], cwd=tmp_path, input="Synthetic input",
                            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
    assert result.returncode == 7
    assert result.stderr == "Synthetic diagnostic"
    assert json.loads(result.stdout) == [arguments, "Synthetic input", str(tmp_path), sys.executable]


@pytest.mark.parametrize("fixture_name", [
    "research-synthesis-readback.json", "edgebench-graph-saved-analysis.json",
    "edgebench-portfolio-native-results.json", "edgebench-ad-native-results.json",
])
def test_captured_analysis_fixture_serves_real_saved_results_without_source_mutation(tmp_path, monkeypatch, fixture_name):
    fixture = ROOT / "apps/presentation/dashboard/src/data/fixtures" / fixture_name
    capture = json.loads(fixture.read_bytes())

    class InspectingServer(backend.ChatHTTPServer):
        def serve_forever(self, poll_interval=0.5):
            worker = Thread(target=super().serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{self.server_port}"

            def read(path):
                with urlopen(base + path, timeout=10) as response:
                    return json.load(response)

            try:
                log = self.runtime_root / "goals" / capture["goal_id"] / "explore-result-log.jsonl"
                events = [json.loads(line) for line in log.read_bytes().decode("utf-8").splitlines()]
                assert events == capture["events"]
                history = read("/api/chat/explore-results?" + urlencode({
                    "goal_id": capture["goal_id"], "view": "history",
                }))
                assert {event["title"] for event in history["items"]} == {event["title"] for event in capture["events"]}
                if "analysis_readbacks" in capture:
                    # Pinned original HTTP bytes, not a self-updating digest.
                    expected = {
                        "edgebench-graph-saved-analysis.json": {
                            "original": "0e4bb4b931784abe1faf8cba49922aeb36f6c96f9e1730fdc77d82e7ebd3da2f",
                            "zh": "cb8fdf1a61915f06168816c82367e9d037bdf49424028cd2793dfe61364def64",
                            "en": "d76b579672d87f8b071d871ecfd5797b986f295b8dc93bc967471c0e1aab0b2b",
                            "initial_original": "7b68615db558732a9a78b4d83befcb3e31d92840f4d4944c37f5589288fcfcd2",
                            "initial_zh": "be0621f5d24cc61a9717611caf7b0ef89c7acdfc5eb03da0d763da7a1c87e531",
                            "initial_en": "c44c78a2404ea55183a42d3ae1c2e08b90920666b49f159d8f841a84d8f5a4da",
                        },
                        "edgebench-portfolio-native-results.json": {
                            "original": "b33ebfcc7c233f8cf87b70e23b3bc76e34d5b91b8fcf11393e532afa9597b717",
                            "zh": "cf7eb84be2cc7b349c3118c278a6957e84796033f7bedd207a2d5802206a45d5",
                            "en": "ef0f5b90e9204839bccde669f8cbf321795c8e285d12ea87ecdd71cc9b02ba5a",
                        },
                        "edgebench-ad-native-results.json": {
                            "original": "56670c1183da44d227be9d15088698baca8bfe800a9bc87ac0e715f072f58c69",
                            "zh": "08d1f35b52f9b58a867e104591a55f885e9c1076a510b9ca92d3e022785e9b6f",
                            "en": "d145136ee17d870ffbbcf89e45516b74302f71883fd82be92462f62faad263e7",
                        },
                    }[fixture_name]
                    for language, record in capture["analysis_readbacks"].items():
                        with urlopen(base + record["request_path"], timeout=10) as response:
                            raw = response.read()
                        assert hashlib.sha256(raw).hexdigest() == expected[language] == record["response_sha256"]
                    view = capture["analysis_readbacks"]["original"]["response"]["pack"]
                    assert view["episode_count"] == 6
                    native = view["native_outcomes"]["projection"]
                    if fixture_name == "edgebench-graph-saved-analysis.json":
                        assert len(view["result"]["assessments"]) == 36
                        assert {row["verdict"] for row in view["result"]["assessments"]} == {"yes", "no", "unknown"}
                        assert view["job"]["usage"]["input_tokens"] == 53768
                        assert view["job"]["usage"]["output_tokens"] == 19278
                        assert native["initial"] is None
                        assert native["retained"]["candidate_ref"] == "agent-16"
                        assert native["final_valid"]["candidate_ref"] == "agent-18"
                        assert native["retained_improvement"]["baseline"]["candidate_ref"] == "agent-2"
                        assert native["retained_improvement"]["value"] == 0.032297817031212595
                    else:
                        assert view["job"] is None and view["result"] is None
                        assert native["retained"] is None and "retained_improvement" not in native
                        initial, best, final = ((0, 10.86, 10.86) if "portfolio" in fixture_name
                                                else (49550815819, 49653326363, 49650446168))
                        assert native["initial"]["value"] == initial
                        assert native["best_valid"]["value"] == best
                        assert native["final_valid"]["value"] == final
                    assert "solver_usage" not in view and not events
                    for citation in capture["analysis_citations"]:
                        assert read(citation["request_path"]) == citation["response"]
                    citation = capture["analysis_citations"][0]
                    if fixture_name == "edgebench-graph-saved-analysis.json":
                        initial = read(capture["analysis_readbacks"]["initial_original"]["request_path"])["pack"]
                        initial_native = initial["native_outcomes"]["projection"]
                        assert initial_native["retained"] is None and initial_native["initial"] is None
                        assert "retained_improvement" not in initial_native
                        assert initial_native["best_valid"]["candidate_ref"] == "agent-16"
                        assert initial_native["final_valid"]["candidate_ref"] == "agent-18"
                        assert initial["result"] == view["result"]
                        assert initial["job"] == view["job"]
                    assert read(citation["request_path"]) == citation["response"]
                    for query in [
                        {"output_language": "fr"}, {"pack_id": "wrong-pack"},
                    ]:
                        with pytest.raises(HTTPError) as rejected:
                            read("/api/chat/research-analysis?" + urlencode({"goal_id": capture["goal_id"], **query}))
                        assert rejected.value.code == 404
                    with pytest.raises(HTTPError) as rejected:
                        read(citation["request_path"].replace(citation["response"]["source_id"], "uncaptured-source"))
                    assert rejected.value.code == 404
                    # No analysis artifacts, provider output or job state are synthesized.
                    assert not list(self.runtime_root.parent.glob("analysis-*"))
                for language in capture.get("analyses", [{}])[0].get("outputs", {}):
                    view = read("/api/chat/research-analysis?" + urlencode({
                        "goal_id": capture["goal_id"], "output_language": language,
                    }))["pack"]
                    assert view["availability"] == "available"
                    assert view["job"]["status"] == "succeeded"
                    assert view["result"]["output_language"] == language
                    assert view["result"]["assessments"]
                readback = read("/fixture/readback")
                assert readback["source_unchanged"] is True
                assert readback["mutation_attempts"] == 0
                assert readback["fixture_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
                if "analyses" in capture:
                    evidence = self.runtime_root.parent / f"analysis-{capture['goal_id']}-0" / "facts.txt"
                    original = evidence.read_bytes()
                    evidence.write_bytes(b"X" + original[1:])
                    try:
                        with pytest.raises(PackUnavailable, match="source_changed"):
                            backend.execute_pack(runtime_root=self.runtime_root, owner_goal_id=capture["goal_id"],
                                pack_id=view["pack_id"], analysis_key=view["analysis_key"], output_language=language,
                                codex="must-not-launch-for-changed-source")
                    finally:
                        evidence.write_bytes(original)
                for method in ("POST", "PUT", "DELETE", "PATCH"):
                    with pytest.raises(HTTPError) as rejected:
                        urlopen(Request(base + "/api/chat/turns", data=b"{}", method=method), timeout=10)
                    assert rejected.value.code == 405
                assert read("/fixture/readback")["mutation_attempts"] == 4
                assert read("/fixture/readback")["source_unchanged"] is True
            finally:
                self.shutdown()
                worker.join(timeout=10)

    monkeypatch.setattr(backend, "ChatHTTPServer", InspectingServer)
    # Pytest's session TEMP adds a long prefix unrelated to this byte/process
    # contract. Keep only this disposable fixture in the native temp namespace.
    parent = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else tmp_path
    monkeypatch.setattr(backend, "tempfile", SimpleNamespace(
        TemporaryDirectory=lambda **_: tempfile.TemporaryDirectory(prefix="lxr-", dir=parent),
    ))
    monkeypatch.setattr(backend, "signal", SimpleNamespace(SIGTERM=backend.signal.SIGTERM, signal=lambda *_: None))
    monkeypatch.setattr(sys, "argv", [str(SPEC.origin), "--fixture", str(fixture)])
    backend.main()
