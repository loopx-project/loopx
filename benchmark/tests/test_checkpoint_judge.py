"""Native owner semantics; real grading/transport is qualified by the Docker smoke."""
import json
import hashlib
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
