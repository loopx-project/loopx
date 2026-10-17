"""Scoped pack-to-process journey with actual files and a disposable child."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from loopx.control_plane.effect_runtime import EffectRuntimeRejected

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable,
    cancel_pack,
    discover,
    execute_pack,
    inspect_pack,
    read_citation,
    register_pack,
)


@pytest.fixture
def pack(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    evidence = "Prior question\nThe native measurement did not improve utility · 原始测量。\n".encode(
        "utf-8"
    )
    (sources / "result.txt").write_bytes(evidence)
    data = {
        "schema_version": "trajectory_assessment_input_v1",
        "scope": dict(
            goal_id="solver",
            case_id="case",
            run_id="run",
            revision="fixed",
            window="one episode · 观察窗口",
        ),
        "objective": "Improve native utility · 原始目标",
        "selection": dict(
            rule="synthetic complete population", population_episodes=1, exhaustive=True
        ),
        "sources": [
            dict(
                id="source",
                artifact="result.txt",
                artifact_sha256=hashlib.sha256(evidence).hexdigest(),
                first_line=2,
                text=evidence.decode().split("\n")[1],
            )
        ],
        "episodes": [
            dict(
                id="episode",
                question="Does it help?",
                source_ids=["source"],
                eligible_metrics=["benefit_test"],
            )
        ],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    scope = dict(runtime_root=tmp_path / "runtime", owner_goal_id="research-owner")
    registered = register_pack(
        **scope, input_path=input_path, source_root=sources, title="Case · 原始目标"
    )
    operation = {
        **scope,
        "pack_id": registered["pack_id"],
        "analysis_key": registered["analysis_key"],
    }
    output = {
        "assessments": [
            dict(
                episode_id="episode",
                metric="benefit_test",
                verdict="yes",
                reason="A negative native test still tests benefit · 负向测量。",
                citations=[dict(source_id="source", first_line=2, last_line=2)],
            )
        ],
        "limitations": ["Synthetic process; not model quality evidence"],
    }
    executable = tmp_path / "codex-fixture"
    executable.write_text(
        f"""#!{sys.executable}
import json,sys
from pathlib import Path
assert "\\u539f\\u59cb\\u76ee\\u6807" in sys.stdin.buffer.read().decode("utf-8")
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes({json.dumps(output, ensure_ascii=False).encode("utf-8")!r})
sys.stderr.buffer.write("\\u5408\\u6210\\u8bca\\u65ad".encode("utf-8"))
sys.stdout.buffer.write((json.dumps({{'type':'turn.completed','message':'\\u5206\\u6790\\u5b8c\\u6210','usage':{{'input_tokens':10,'output_tokens':2}}}},ensure_ascii=False)+'\\n').encode("utf-8"))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return scope, operation, registered, input_path, sources, str(executable)


def test_scope_discovery_execute_read_citation_and_changed_source_history(pack):
    scope, operation, registered, _, sources, provider = pack
    listing = discover(**scope)
    assert listing["default_pack_id"] == registered["pack_id"]
    assert (
        listing["packs"][0]["scope"]["goal_id"] == "solver"
    )  # never relabel owner as analyzed solver
    assert "sources" not in listing["packs"][0]
    before = inspect_pack(**scope)
    assert before["job"] is None and before["result"] is None
    assert not list(
        (scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob(
            "*/jobs/*"
        )
    )
    assert execute_pack(**operation, codex=provider)["status"] == "succeeded"
    view = inspect_pack(**scope)
    assert view["result"]["metrics"][1]["yes"] == 1
    assert "directory" not in view["job"] and "process_id" not in view["job"]
    assert execute_pack(**operation, codex="must-not-run")["reused"] is True
    citation = read_citation(**operation, source_id="source", first_line=2, last_line=2)
    assert (
        citation["text"]
        == "The native measurement did not improve utility · 原始测量。"
    )
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**operation, source_id="source", first_line=1, last_line=2)
    (sources / "result.txt").write_text("changed", encoding="utf-8")
    assert discover(**scope)["packs"][0]["reason_code"] == "source_changed"
    with pytest.raises(PackUnavailable, match="source_changed"):
        execute_pack(**operation, codex="must-not-run")
    historical = inspect_pack(**scope)
    assert (
        historical["applicability"] == "historical"
        and historical["result"] == view["result"]
    )
    assert (
        read_citation(**operation, source_id="source", first_line=2, last_line=2)[
            "text"
        ]
        == citation["text"]
    )


def test_native_results_register_through_real_cli_and_read_without_analysis(pack, tmp_path):
    scope, _, original, input_path, sources, _ = pack
    native = {
        "schema_version": "trajectory_native_outcomes_v1",
        "scope": json.loads(input_path.read_text())["scope"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z",
        "complete": True, "metric": {"name": "error", "unit": "points", "higher_is_better": False},
        "retained_result_id": "native-1",
        "samples": [{"submission_index": 1, "result_id": "native-1", "candidate_ref": "candidate-a",
            "submitted_at": "2026-01-01T00:01:00Z", "observed_at": "2026-01-01T00:02:00Z",
            "valid": True, "value": 2, "citation": {"source_id": "source", "first_line": 2, "last_line": 2}}],
    }
    native_path = sources / "native-outcomes.json"
    native_path.write_text(json.dumps(native))
    registry = tmp_path / "native-registry.json"
    registry.write_text(json.dumps({"goals": []}))
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(scope["runtime_root"]), "--format", "json", "benchmark", "trajectory-pack-register",
        "--goal-id", scope["owner_goal_id"], "--input-json", str(input_path), "--source-root", str(sources),
        "--title", "Native outcome window", "--native-outcomes-json", native_path.name],
        capture_output=True, text=True, check=True)
    registered = json.loads(result.stdout)
    assert registered["analysis_key"] == original["analysis_key"]
    assert "native_outcomes" not in original
    assert registered["native_outcomes"]["projection"]["initial"]["value"] == 2
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert view["job"] is None and view["result"] is None
    assert view["native_outcomes"]["projection"]["retained"]["candidate_ref"] == "candidate-a"
    citation = read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
        source_id="source", first_line=2, last_line=2)
    assert citation["text"] == "The native measurement did not improve utility · 原始测量。"
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
            source_id="source", first_line=1, last_line=2)
    native["samples"][0]["value"] = 3
    native_path.write_text(json.dumps(native))
    old = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert old["native_outcomes"]["applicability"] == "historical"
    assert old["native_outcomes"]["projection"]["initial"]["value"] == 2
    assert read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
        source_id="source", first_line=2, last_line=2)["applicability"] == "historical"
    assert not list((scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob("*/jobs/*"))


def test_native_capture_cannot_borrow_another_run_or_escape_source_root(pack):
    scope, _, _, input_path, sources, _ = pack
    path = sources / "wrong-native.json"
    raw = {"schema_version": "trajectory_native_outcomes_v1", "scope": {
        **json.loads(input_path.read_text())["scope"], "run_id": "foreign"}}
    path.write_text(json.dumps(raw))
    with pytest.raises(EffectRuntimeRejected, match="scope differs"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="wrong", native_outcomes_json=path.name)
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="wrong", native_outcomes_json="../input.json")


@pytest.mark.skipif(
    os.name != "posix", reason="C locale coercion is a POSIX regression"
)
def test_utf8_pack_and_child_roundtrip_with_ascii_default_locale(pack):
    scope, _, registered, input_path, sources, executable = pack
    script = r"""
import codecs, locale, sys
from pathlib import Path
from loopx.capabilities.benchmark_toolkit.trajectory_assessment import prepare, read_job
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable, discover, execute_pack, inspect_pack, read_citation, register_pack,
)
assert sys.flags.utf8_mode == 0
assert codecs.lookup(locale.getencoding()).name == "ascii"
input_path, sources, runtime = map(Path, sys.argv[1:4])
scope = dict(runtime_root=runtime, owner_goal_id="research-owner")
prepared = prepare(input_path, model="gpt-6.1-sol", effort="max", service_tier="fast")
assert prepared["input"]["objective"] == "Improve native utility · 原始目标"
registered = register_pack(**scope, input_path=input_path, source_root=sources, title="Case · 原始目标")
assert registered["pack_id"] == sys.argv[4]
assert registered["analysis_key"] == prepared["analysis_key"]
assert discover(**scope)["packs"][0]["title"] == "Case · 原始目标"
assert inspect_pack(**scope)["job"] is None
operation = dict(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"])
first = execute_pack(**operation, codex=sys.argv[5])
assert first["status"] == "succeeded" and first["usage"] == dict(input_tokens=10, output_tokens=2)
view = inspect_pack(**scope)
assert view["result"]["assessments"][0]["reason"] == "A negative native test still tests benefit · 负向测量。"
assert view["result"]["metrics"][1]["yes"] == 1
assert "directory" not in view["job"] and "process_id" not in view["job"]
assert execute_pack(**operation, codex="must-not-run")["reused"] is True
directory = next(runtime.glob("benchmark/trajectory-assessments/*/jobs/*"))
assert read_job(directory)["scope"]["window"] == "one episode · 观察窗口"
assert "原始目标" in (directory / "prompt.txt").read_text(encoding="utf-8")
assert "分析完成" in (directory / "events.jsonl").read_text(encoding="utf-8")
assert (directory / "stderr.txt").read_text(encoding="utf-8") == "合成诊断"
citation = dict(source_id="source", first_line=2, last_line=2)
assert read_citation(**operation, **citation)["text"] == "The native measurement did not improve utility · 原始测量。"
(sources / "result.txt").write_bytes(b"changed")
assert discover(**scope)["packs"][0]["reason_code"] == "source_changed"
try:
    execute_pack(**operation, codex="must-not-run")
except PackUnavailable as error:
    assert str(error) == "source_changed"
else:
    raise AssertionError("Changed sources must not run a new analysis")
assert inspect_pack(**scope)["result"] == view["result"]
assert read_citation(**operation, **citation)["applicability"] == "historical"
"""
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8=0",
            "-c",
            script.encode("ascii", "backslashreplace").decode("ascii"),
            str(input_path),
            str(sources),
            str(scope["runtime_root"]),
            registered["pack_id"],
            executable,
        ],
        env={
            **os.environ,
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONIOENCODING": "ascii",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_owner_isolation_unknown_ids_and_keys_are_rejected(pack):
    scope, operation, _, _, _, _ = pack
    other = {**scope, "owner_goal_id": "other-owner"}
    assert discover(**other)["packs"] == []
    with pytest.raises(PackUnavailable, match="pack_not_registered"):
        inspect_pack(**other, pack_id=operation["pack_id"])
    for value in ("../escape", "/tmp/arbitrary", "a" * 64):
        with pytest.raises(PackUnavailable):
            inspect_pack(**scope, pack_id=value)
    with pytest.raises(PackUnavailable, match="analysis_key_mismatch"):
        execute_pack(**{**operation, "analysis_key": "a" * 64}, codex="must-not-run")
    with pytest.raises(PackUnavailable, match="analysis_key_mismatch"):
        cancel_pack(**{**operation, "analysis_key": "a" * 64})


def test_digest_rejections_preserve_registration_and_create_no_job(pack):
    scope, operation, _, _, _, _ = pack
    root = scope["runtime_root"]
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    malformed = (
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "sha256:" + "a" * 64,
        " " + "a" * 64,
        "a" * 64 + " ",
        "\n" + "a" * 64,
        "a" * 64 + "\n",
        "a" * 64 + "\r\n",
        "a" * 64 + "\0",
        True,
        64,
        {"id": "a" * 64},
        ["a" * 64],
    )
    for value in malformed:
        with pytest.raises(PackUnavailable, match="^invalid_identifier$"):
            inspect_pack(**scope, pack_id=value)
    for value in (*malformed, "", None, False, 0, {}, []):
        with pytest.raises(PackUnavailable, match="^invalid_identifier$"):
            execute_pack(**{**operation, "analysis_key": value}, codex="must-not-run")
    for value in ("0" * 64, "0123456789abcdef" * 4, "f" * 64):
        with pytest.raises(PackUnavailable, match="^pack_not_registered$"):
            inspect_pack(**scope, pack_id=value)
    assert {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


def test_registration_requires_real_hash_literal_excerpt_and_contained_path(
    pack, tmp_path
):
    scope, _, _, input_path, sources, _ = pack
    original = json.loads(input_path.read_text(encoding="utf-8"))
    for change, message in (
        ({"text": "invented"}, "excerpt_mismatch"),
        ({"artifact_sha256": "a" * 64}, "source_changed"),
        ({"artifact": "../outside.txt"}, "invalid_source_path"),
    ):
        data = json.loads(json.dumps(original))
        data["sources"][0].update(change)
        input_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PackUnavailable, match=message):
            register_pack(
                **scope, input_path=input_path, source_root=sources, title="bad"
            )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (sources / "link.txt").symlink_to(outside)
    original["sources"][0]["artifact"] = "link.txt"
    input_path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="bad")
    assert len(discover(**scope)["packs"]) == 1


def test_missing_source_blocks_new_execution_and_leaves_registration_visible(pack):
    scope, operation, _, _, sources, _ = pack
    (sources / "result.txt").unlink()
    view = inspect_pack(**scope)
    assert (
        view["reason_code"] == "source_unavailable"
        and view["availability"] == "unavailable"
    )
    with pytest.raises(PackUnavailable, match="source_unavailable"):
        execute_pack(**operation, codex="must-not-run")


def test_method_upgrade_reads_verified_old_result_without_rejudging(pack, monkeypatch):
    from loopx.capabilities.benchmark_toolkit import (
        trajectory_assessment_store as store,
    )

    scope, operation, _, _, _, provider = pack
    execute_pack(**operation, codex=provider)
    current = inspect_pack(**scope)
    original_prepare = store._prepared

    def upgraded_method(record):
        # Simulate a new method identity at the typed-owner boundary. The real
        # old method, child process and stored receipt were exercised above.
        return {
            **original_prepare(record),
            "analysis_key": "b" * 64,
            "method_version": "future-method",
        }

    monkeypatch.setattr(store, "_prepared", upgraded_method)
    historical = inspect_pack(**scope)
    assert historical["reason_code"] == "method_changed"
    assert historical["applicability"] == "historical"
    assert historical["result"] == current["result"]
    assert historical["result"]["method_version"] != "future-method"
    assert (
        read_citation(**operation, source_id="source", first_line=2, last_line=2)[
            "applicability"
        ]
        == "historical"
    )
    with pytest.raises(PackUnavailable, match="method_changed"):
        execute_pack(**operation, codex="must-not-run")
    result_file = next(
        scope["runtime_root"].glob(
            "benchmark/trajectory-assessments/*/jobs/*/assessment.json"
        )
    )
    result_file.write_text("{}", encoding="utf-8")
    with pytest.raises(PackUnavailable, match="historical_result_unverified"):
        inspect_pack(**scope)


def _usage_fixture():
    def counts(i, c, o):
        return dict(input_tokens=i, cached_input_tokens=c, output_tokens=o)

    events = [
        dict(type="session_meta", payload=dict(id="solver-session")),
        dict(type="event_msg", payload=dict(type="task_started", turn_id="a")),
        dict(
            type="event_msg",
            payload=dict(
                type="token_count",
                info=dict(
                    total_token_usage=counts(100, 60, 10),
                    last_token_usage=counts(100, 60, 10),
                ),
            ),
        ),
        dict(type="event_msg", payload=dict(type="task_complete", turn_id="a")),
        dict(type="event_msg", payload=dict(type="task_started", turn_id="b")),
        dict(
            type="event_msg",
            payload=dict(
                type="token_count",
                info=dict(
                    total_token_usage=counts(140, 80, 15),
                    last_token_usage=counts(40, 20, 5),
                ),
            ),
        ),
        dict(type="event_msg", payload=dict(type="task_complete", turn_id="b")),
    ]
    for i, event in enumerate(events):
        event["timestamp"] = f"2026-01-01T00:00:0{i}Z"
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_optional_solver_usage_is_read_only_scoped_and_separate_from_model(pack):
    scope, _, old, input_path, sources, _ = pack
    assert "solver_usage" not in inspect_pack(**scope)
    (sources / "usage.jsonl").write_text(_usage_fixture())
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    registered = register_pack(**args)
    assert registered["pack_id"] != old["pack_id"]
    assert registered["analysis_key"] == old["analysis_key"]
    assert register_pack(**args) == registered
    view = inspect_pack(**scope)
    cost = view["solver_usage"]
    assert cost["projection"]["input_tokens"] == 140
    assert cost["projection"]["uncached_input_tokens"] == 60
    assert cost["projection"]["turns"][1]["usage"]["input_tokens"] == 40
    assert view["job"] is None and view["result"] is None
    assert discover(**scope)["packs"][-1]["solver_usage"] == cost
    assert not list(scope["runtime_root"].rglob("receipt.json"))
    (sources / "usage.jsonl").write_text("changed capture")
    stale = inspect_pack(**scope)["solver_usage"]
    assert stale["applicability"] == "historical"
    assert stale["reason_code"] == "source_changed"
    assert stale["projection"] == cost["projection"]
    (sources / "usage.jsonl").unlink()
    assert inspect_pack(**scope)["solver_usage"]["reason_code"] == "source_unavailable"
    assert "solver_usage" not in inspect_pack(**scope, pack_id=old["pack_id"])


def test_usage_registration_rejects_wrong_identity_paths_gaps_and_tampering(pack):
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected

    scope, _, old, input_path, sources, _ = pack
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    path = sources / "usage.jsonl"
    path.write_text(_usage_fixture())
    with pytest.raises(ValueError, match="supplied together"):
        register_pack(**{**args, "usage_session_id": None})
    with pytest.raises(EffectRuntimeRejected, match="session"):
        register_pack(**{**args, "usage_session_id": "wrong-session"})
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**{**args, "usage_jsonl": "../usage.jsonl"})
    outside = sources.parent / "outside.jsonl"
    outside.write_text(_usage_fixture())
    (sources / "escaping.jsonl").symlink_to(outside)
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**{**args, "usage_jsonl": "escaping.jsonl"})
    path.write_text(
        _usage_fixture().replace('"input_tokens": 140', '"input_tokens": 240')
    )
    with pytest.raises(EffectRuntimeRejected, match="gap"):
        register_pack(**args)
    path.write_text(_usage_fixture())
    registered = register_pack(**args)
    stored = next(scope["runtime_root"].rglob(registered["pack_id"] + ".json"))
    row = json.loads(stored.read_text())
    row["solver_usage"]["projection"]["input_tokens"] = 0
    stored.write_text(json.dumps(row))
    with pytest.raises(PackUnavailable, match="invalid_registration"):
        inspect_pack(**scope)


def test_large_usage_real_cli_register_readback_and_replay(pack, monkeypatch):
    from loopx.control_plane import effect_runtime

    scope, _, old, input_path, sources, _ = pack
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [dict(type="session_meta", payload=dict(id="solver-session"))]
    for turn in range(1, 4501):
        payloads = [
            dict(type="task_started", turn_id=str(turn)),
            dict(
                type="token_count",
                info=dict(
                    total_token_usage=dict(
                        input_tokens=100 * turn,
                        cached_input_tokens=60 * turn,
                        output_tokens=10 * turn,
                    ),
                    last_token_usage=dict(
                        input_tokens=100, cached_input_tokens=60, output_tokens=10
                    ),
                ),
            ),
            dict(type="task_complete", turn_id=str(turn)),
        ]
        for offset, payload in enumerate(payloads):
            rows.append(
                dict(
                    type="event_msg",
                    payload=payload,
                    timestamp=(
                        start + timedelta(seconds=3 * turn + offset)
                    ).isoformat(),
                )
            )
    capture = sources / "usage.jsonl"
    capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    # This is larger even after the adapter's content reduction, not merely a
    # large raw transcript made small by discarding model messages.
    params = dict(
        session_id="solver-session",
        events=[
            dict(line=i + 1, timestamp=None, **row)
            if "timestamp" not in row
            else dict(line=i + 1, **row)
            for i, row in enumerate(rows)
        ],
    )
    assert (
        len(json.dumps(params, separators=(",", ":")).encode())
        > effect_runtime.MAX_REQUEST_BYTES
    )
    registry = sources.parent / "registry.json"
    registry.write_text('{"projects":{}}')
    prefix = [
        sys.executable,
        "-m",
        "loopx.entrypoint",
        "--registry",
        str(registry),
        "--runtime-root",
        str(scope["runtime_root"]),
        "--format",
        "json",
        "benchmark",
    ]

    def cli(*args):
        result = subprocess.run([*prefix, *args], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    args = [
        "trajectory-pack-register",
        "--goal-id",
        scope["owner_goal_id"],
        "--input-json",
        str(input_path),
        "--source-root",
        str(sources),
        "--title",
        old["title"],
        "--usage-jsonl",
        "usage.jsonl",
        "--usage-session-id",
        "solver-session",
    ]
    registered = cli(*args)
    assert cli(*args) == registered
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    cost = view["solver_usage"]
    assert cost["artifact_sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
    facts = cost["projection"]
    assert facts["completed_turns"] == 4500
    assert facts["input_tokens"] == 450000
    assert facts["cached_input_tokens"] == 270000
    assert facts["uncached_input_tokens"] == 180000
    assert facts["output_tokens"] == 45000
    assert facts["turns"][0]["first_line"] == 2
    assert facts["turns"][-1]["last_line"] == 13501
    assert all(t["usage"]["input_tokens"] == 100 for t in facts["turns"])
    listing = cli("trajectory-packs", "--goal-id", scope["owner_goal_id"])
    assert (
        len(listing["packs"]) == 2
    )  # original optional-off pack plus one replayed pack
    assert (
        next(p for p in listing["packs"] if p["pack_id"] == registered["pack_id"])[
            "solver_usage"
        ]
        == cost
    )
    assert "solver_usage" not in inspect_pack(**scope, pack_id=old["pack_id"])
    assert view["job"] is None and view["result"] is None

    # The method opts into a bounded channel, not a larger ordinary RPC wire.
    with pytest.raises(
        effect_runtime.EffectRuntimeRejected, match="request is oversized"
    ):
        effect_runtime.effect_runtime_result("benchmark.trajectory.codex-usage", params)
    monkeypatch.setattr(
        effect_runtime, "MAX_LOCAL_SNAPSHOT_BYTES", effect_runtime.MAX_REQUEST_BYTES
    )
    with pytest.raises(
        effect_runtime.EffectRuntimeRejected, match="local snapshot is oversized"
    ):
        register_pack(
            **scope,
            input_path=input_path,
            source_root=sources,
            title="over limit",
            usage_jsonl="usage.jsonl",
            usage_session_id="solver-session",
        )
    assert len(discover(**scope)["packs"]) == 2
