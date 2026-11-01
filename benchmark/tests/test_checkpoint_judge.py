"""Native owner semantics; real grading/transport is qualified by the Docker smoke."""
import json
import hashlib
import math
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def native_config(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    from sforge.harness import judge_server
    from sforge.harness.config import SForgeConfig
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "BENCHMARK.yaml").write_text("name: fixture\nbase_images: {python: {}}\n")
    (tasks / "fixture.json").write_text(json.dumps({
        "task_id": "fixture", "name": "Synthetic checkpoint", "base_image": "python",
        "platform": "linux/arm64", "cwd": "/tmp", "internet": False,
        "submit_paths": ["solver.py"], "submit_exclude": [],
        "work": {"image_tag": "fixture", "agent_query": "Synthetic fixture"},
        "judge": {"image_tag": "fixture", "eval_timeout": 15, "parser": "structured_json",
                  "selection": "score_first", "score_direction": "maximize", "eval_cmd": "true"},
    }))
    monkeypatch.setattr(judge_server, "create_backend_from_config", lambda config: SimpleNamespace())
    return SForgeConfig(tasks_dir=tasks, log_dir=tmp_path)


def test_disabled_extension_preserves_the_native_route_surface(native_config):
    from benchmark.edgebench.checkpoint_judge import create_app
    from sforge.harness.judge_server import create_app as native_app
    first, second = native_app(native_config), create_app(native_config)
    try:
        assert [(r.path, r.methods) for r in first.routes] == [(r.path, r.methods) for r in second.routes]
    finally:
        first.state.judge.close()
        second.state.judge.close()


def test_capture_retains_consumed_round_history_and_cooldown_without_mutation(native_config):
    from fastapi.testclient import TestClient
    from benchmark.edgebench.checkpoint_judge import create_app, ROUTE
    app = create_app(native_config, checkpoint_capture=True)
    state = app.state.judge
    with TestClient(app) as client:
        token = state.register_session("fixture", "source", max_agent_submissions=12, submission_cooldown=3600)
        state.consume_round(token)
        state._record_submission("source", "accepted-error", "fixture", "agent-1", None,
                                 "synthetic evaluation failure", status="error")
        state.submissions["accepted-error"] = {"run_id": "source", "task_id": "fixture", "round": "agent-1",
            "status": "error", "report": None, "error": "synthetic evaluation failure"}
        before = state.resolve_token(token)
        result = client.post(ROUTE, json={"token": token})
        assert result.status_code == 200
        point = result.json()
        assert point["next_agent_round"] == 2 and point["next_auto_round"] == 1
        assert point["max_agent_submissions"] == 12
        assert 0 < point["remaining_cooldown_seconds"] <= 3600
        assert point["last_agent_submit_at_epoch"] == before["last_agent_submit_at"]
        assert point["released_history"] == state.get_run_history("source", "fixture")["entries"]
        assert point["released_history"][0]["status"] == "error"
        assert point["released_results"]["accepted-error"]["error"] == "synthetic evaluation failure"
        assert point["execution_authority_granted"] is False
        assert point["native_incumbent"] is None  # Failure does not fabricate a candidate.
        assert token not in json.dumps(point) and "admin_secret" not in json.dumps(point)
        assert state.resolve_token(token) == before
        assert client.post(ROUTE, json={"token": token}).json()["judge_epoch"] == point["judge_epoch"]


@pytest.mark.parametrize("gap", ["pending", "unreconciled", "automatic", "closed"])
def test_incomplete_opportunity_state_cannot_be_a_checkpoint(native_config, gap):
    from fastapi.testclient import TestClient
    from benchmark.edgebench.checkpoint_judge import create_app, ROUTE
    app = create_app(native_config, checkpoint_capture=True)
    state = app.state.judge
    with TestClient(app) as client:
        token = state.register_session("fixture", "source", submission_cooldown=3600)
        if gap == "pending":
            state._outstanding_by_run[("source", "fixture")] = 1
        elif gap == "unreconciled":
            state.consume_round(token)
        elif gap == "automatic":
            state.consume_round(token, "auto")
        else:
            state._closed = True
        assert client.post(ROUTE, json={"token": token}).status_code == 409
        assert client.post(ROUTE, json={"token": "unregistered"}).status_code == 403
        assert client.post(ROUTE, json={"token": token, "restore": True}).status_code == 422


@pytest.mark.parametrize("policy,direction,expected", [
    ("score_first", "maximize", "agent-1"), ("score_first", "minimize", "agent-2"),
    ("pass_rate_first", "maximize", "agent-2"), ("valid_then_score", "maximize", "agent-2"),
])
def test_native_selected_incumbent_archive_is_bound_to_its_result(native_config, policy, direction, expected):
    from fastapi.testclient import TestClient
    from benchmark.edgebench.checkpoint_judge import create_app, ROUTE, ARCHIVE_ROUTE

    task = json.loads((native_config.tasks_dir / "fixture.json").read_text())
    task["judge"].update(selection=policy, score_direction=direction)
    (native_config.tasks_dir / "fixture.json").write_text(json.dumps(task))
    app = create_app(native_config, checkpoint_capture=True)
    state = app.state.judge
    with TestClient(app) as client:
        token = state.register_session("fixture", "source")
        candidates = {"agent-1": b"earlier higher score", "agent-2": b"later candidate"}
        for index, (round_id, raw) in enumerate(candidates.items(), 1):
            state.consume_round(token)
            report = {"score": 9 if index == 1 else 3, "valid": index == 2,
                      "pass_rate": .5 if index == 1 else 1.0}
            identifier = "submission-" + str(index)
            state._record_submission("source", identifier, "fixture", round_id, report, None)
            state.submissions[identifier] = {"run_id": "source", "task_id": "fixture", "round": round_id,
                "status": "completed", "report": report, "error": None}
            path = native_config.log_dir / "runs/source/fixture/submissions" / round_id / "submission.tar.gz"
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
        before = state.resolve_token(token)
        point = client.post(ROUTE, json={"token": token}).json()
        native = point["native_incumbent"]
        assert native["round"] == expected
        assert native["archive_sha256"] == hashlib.sha256(candidates[expected]).hexdigest()
        assert native["archive_bytes"] == len(candidates[expected])
        request = {"token": token, "capture_binding_sha256": point["capture_binding_sha256"]}
        downloaded = client.post(ARCHIVE_ROUTE, json=request)
        assert downloaded.status_code == 200 and downloaded.content == candidates[expected]
        assert state.resolve_token(token) == before
        assert client.post(ARCHIVE_ROUTE, json={**request, "token": "unregistered"}).status_code == 403
        assert client.post(ARCHIVE_ROUTE, json={**request, "capture_binding_sha256": "wrong-epoch"}).status_code == 409
        selected = native_config.log_dir / "runs/source/fixture/submissions" / expected / "submission.tar.gz"
        selected.write_bytes(b"changed selected bytes")
        assert client.post(ARCHIVE_ROUTE, json=request).status_code == 409
        selected.unlink()
        assert client.post(ROUTE, json={"token": token}).status_code == 409
        selected.symlink_to(native_config.tasks_dir / "fixture.json")
        assert client.post(ROUTE, json={"token": token}).status_code == 409
        selected.unlink()
        selected.write_bytes(candidates[expected])
        state.consume_round(token)  # A new accepted, unreconciled round invalidates the old point.
        assert client.post(ARCHIVE_ROUTE, json=request).status_code == 409


@pytest.mark.parametrize('payload', [b'wrong bytes', b'truncated'])
def test_incumbent_transport_mismatch_never_publishes_a_participant(tmp_path, monkeypatch, payload):
    import io
    import time
    from benchmark.runtime import sforge_checkpoint

    expected = b'original accepted bytes'
    packet = {'schema_version': sforge_checkpoint.SCHEMA, 'execution_authority_granted': False,
              'outstanding_evaluations': 0, 'capture_binding_sha256': 'bound-state',
              'native_incumbent': {'archive_bytes': len(expected),
                                   'archive_sha256': hashlib.sha256(expected).hexdigest()}}
    calls = []
    def read(request, *, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps(packet).encode() if len(calls) == 1 else payload)
    monkeypatch.setattr(sforge_checkpoint, 'urlopen', read)
    env = {'SFORGE_JUDGE_URL': 'http://synthetic.invalid', 'SFORGE_TOKEN': 'synthetic',
           'LOOPX_PHASE_DEADLINE_EPOCH': str(time.time() + 30)}
    target = tmp_path / 'incumbent.tar.gz'
    with pytest.raises(ValueError, match='differ'):
        sforge_checkpoint.capture_opportunity(env, timeout=10, incumbent_path=target)
    assert len(calls) == 2 and not target.exists()
    assert json.loads(calls[1].data)['capture_binding_sha256'] == 'bound-state'
    target.write_bytes(b'prior participant')
    calls.clear()
    with pytest.raises(FileExistsError):
        sforge_checkpoint.capture_opportunity(env, timeout=10, incumbent_path=target)
    assert target.read_bytes() == b'prior participant'


@pytest.mark.parametrize("score", [-2.5, float("inf"), float("-inf"), float("nan")])
def test_nonfinite_opportunity_crosses_real_http_client_and_retains_binding(native_config, score):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from sforge.harness.grading import EvalReport
    from benchmark.edgebench.checkpoint_judge import create_app
    from benchmark.runtime import sforge_checkpoint, sforge_json
    from benchmark.tests.test_checkpoint_controller import server

    app = create_app(native_config, checkpoint_capture=True)
    state = app.state.judge
    with server(app) as url:
        token = state.register_session("fixture", "source", max_agent_submissions=3, submission_cooldown=3600)
        state.consume_round(token)
        literal = {"sforge_nonfinite_json": 1, "value": {"score": None},
                   "nonfinite_values": [{"path": ["score"], "kind": "nan"}]}
        report = EvalReport(task_id="fixture", submission_id="accepted", pass_rate=1,
                            score=score, metrics={"nested": [score, None], "literal": literal}).to_dict()
        state._record_submission("source", "accepted", "fixture", "agent-1", report, None)
        state.submissions["accepted"] = {"run_id": "source", "task_id": "fixture", "round": "agent-1",
                                         "status": "completed", "report": report, "error": None}
        archive = native_config.log_dir / "runs/source/fixture/submissions/agent-1/submission.tar.gz"
        archive.parent.mkdir(parents=True)
        original = b"opaque accepted synthetic candidate"
        archive.write_bytes(original)
        before = state.resolve_token(token)
        history_before = json.dumps(state.get_run_history("source", "fixture"), sort_keys=True)

        def request(route, payload):
            return urlopen(Request(url + route, data=json.dumps(payload).encode(),
                                   headers={"Content-Type": "application/json"}, method="POST"), timeout=10)

        with request(sforge_checkpoint.ROUTE, {"token": token}) as response:
            raw = response.read()
        # The actual FastAPI response is standards-compliant JSON; the client,
        # rather than a helper-only round trip, restores its numeric semantics.
        def unmarked(value):
            raise AssertionError("Non-JSON numeric constant in HTTP response")
        wire = json.loads(raw, parse_constant=unmarked)
        assert ("sforge_nonfinite_json" in wire) is not math.isfinite(score)
        env = {"SFORGE_JUDGE_URL": url, "SFORGE_TOKEN": token,
               "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 60)}
        point = sforge_checkpoint.capture_opportunity(env, timeout=10,
                                                      incumbent_path=native_config.log_dir / "incumbent.tar.gz")
        assert point["schema_version"] == sforge_checkpoint.SCHEMA
        measured = point["released_results"]["accepted"]["report"]["metrics"]["nested"][0]
        assert math.isnan(measured) if math.isnan(score) else measured == score
        selected_score = point["released_history"][0]["score"]
        assert math.isnan(selected_score) if math.isnan(score) else selected_score == score
        assert point["released_results"]["accepted"]["report"]["metrics"]["literal"] == literal
        assert point["native_incumbent"]["round"] == "agent-1"
        assert (native_config.log_dir / "incumbent.tar.gz").read_bytes() == original
        assert point["next_agent_round"] == 2 and point["max_agent_submissions"] == 3
        assert point["submission_cooldown_seconds"] == 3600
        assert 0 < point["remaining_cooldown_seconds"] <= 3600
        assert point["execution_authority_granted"] is False and point["continuation_qualified"] is False
        assert token not in raw.decode() and "admin_secret" not in raw.decode()

        # Repeated NaN reads and JSON-equivalent object ordering retain the same
        # binding. A changed released metric must invalidate the archive read.
        report["metrics"] = dict(reversed(list(report["metrics"].items())))
        with request(sforge_checkpoint.ROUTE, {"token": token}) as response:
            repeated = sforge_json.loads(response.read(), fields=sforge_checkpoint.RELEASED_FIELDS)
        assert repeated["capture_binding_sha256"] == point["capture_binding_sha256"]
        assert state.resolve_token(token) == before
        assert json.dumps(state.get_run_history("source", "fixture"), sort_keys=True) == history_before
        report["metrics"]["nested"][0] = 42.0
        with pytest.raises(HTTPError) as moved:
            request(sforge_checkpoint.ARCHIVE_ROUTE, {"token": token,
                    "capture_binding_sha256": point["capture_binding_sha256"]})
        assert moved.value.code == 409
        assert archive.read_bytes() == original and state.resolve_token(token) == before


def test_malformed_http_nonfinite_paths_cannot_download_an_incumbent(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from benchmark.runtime import sforge_checkpoint, sforge_json
    from benchmark.tests.test_checkpoint_controller import server

    app = FastAPI()
    downloads = []
    packet = sforge_json.encode_document({"schema_version": sforge_checkpoint.SCHEMA,
        "execution_authority_granted": False, "outstanding_evaluations": 0,
        "capture_binding_sha256": "synthetic-binding", "released_results": {"score": float("inf")},
        "native_incumbent": {"archive_bytes": 1, "archive_sha256": "a" * 64}})
    packet["nonfinite_values"].append(packet["nonfinite_values"][0])

    @app.post(sforge_checkpoint.ROUTE)
    def malformed():
        return packet

    @app.post(sforge_checkpoint.ARCHIVE_ROUTE)
    def archive():
        downloads.append(True)

    with server(app) as url:
        target = tmp_path / "incumbent.tar.gz"
        with pytest.raises(ValueError, match="Duplicate"):
            sforge_checkpoint.capture_opportunity({"SFORGE_JUDGE_URL": url, "SFORGE_TOKEN": "synthetic",
                "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 60)}, timeout=10, incumbent_path=target)
        assert not target.exists() and not downloads
