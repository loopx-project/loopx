"""Native HTTP authorization with synthetic completed grades and worker transport."""
import json
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("sforge")
from fastapi.testclient import TestClient
from requests import Response
from requests.exceptions import HTTPError
from sforge.harness import judge_server
from sforge.harness.config import SForgeConfig
from sforge.harness.grading import EvalReport

from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_FILE
from benchmark.edgebench.online_judge import create_app
from benchmark.tests.test_edgebench_feedback import Admissions, Transport, archive


@pytest.mark.parametrize("registration,status", [("run", 200), ("other-run", 404), ("invalid", 401)])
def test_native_result_authorization_controls_improvement_delivery(tmp_path, monkeypatch, registration, status):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "BENCHMARK.yaml").write_text("name: fixture\nbase_images: {python: {}}\n")
    (tasks / "fixture.json").write_text(json.dumps({
        "task_id": "fixture", "name": "Synthetic feedback", "base_image": "python",
        "platform": "linux/arm64", "cwd": "/tmp", "internet": False,
        "submit_paths": ["solver.py"], "submit_exclude": [],
        "work": {"image_tag": "fixture", "agent_query": "Synthetic fixture"},
        "judge": {"image_tag": "fixture", "eval_timeout": 15, "parser": "structured_json",
                  "selection": "score_first", "score_direction": "maximize", "eval_cmd": "true"},
    }))
    # Preserve the native result/history owners and online registration routes;
    # no container, grading job or model is needed for the HTTP auth contract.
    monkeypatch.setattr(judge_server, "create_backend_from_config",
                        lambda config: SimpleNamespace(backend_name="docker"))
    app = create_app(SForgeConfig(tasks_dir=tasks, log_dir=tmp_path / "judge"),
                     slots=2, reservation={"slots": 2})
    with TestClient(app) as client:
        state = app.state.judge
        tokens = {}
        for run_id in ("run", "other-run"):
            response = client.post("/api/v1/register", json={
                "task_id": "fixture", "run_id": run_id, "max_agent_submissions": 0,
                "admin_secret": state.admin_secret,
            })
            assert response.status_code == 200
            tokens[run_id] = response.json()["token"]

        reports = {}
        for n, score in enumerate((1., 3.), 1):
            round_id = state.consume_round(tokens["run"], "auto")[2]
            submission_id = f"s{n}"
            report = EvalReport(task_id="fixture", submission_id=submission_id,
                                score=score, pass_rate=1., summary="Synthetic completed grade").to_dict()
            state._record_submission("run", submission_id, "fixture", round_id, report, None)
            state.submissions[submission_id] = {
                "run_id": "run", "task_id": "fixture", "round": round_id,
                "status": "completed", "report": report, "error": None,
            }
            reports[submission_id] = report
        before = state.resolve_token(tokens["run"])
        response = client.get("/api/v1/best-only/admission", params={"admin_secret": state.admin_secret})
        response.raise_for_status()
        sampler = Admissions()
        sampler.epoch, sampler.evaluators = response.json()["epoch"], response.json()["evaluators"]
        publisher = BestOnlyFeedback(trial=tmp_path, run_id="run", task_id="fixture", direction="maximize",
            judge_url="http://testserver", admin_secret=state.admin_secret,
            logger=logging.getLogger("fixture"), sampler=sampler)
        publisher.bind({"SFORGE_TOKEN": tokens.get(registration, "synthetic-invalid-registration")})

        def native_get(url, *, params=None, timeout):
            # Adapt requests' timeout to the in-process client; route, query
            # parameters, auth enforcement and result serialization stay real.
            # Convert the TestClient response to the production requests contract;
            # Starlette may use httpx2 rather than httpx under the hood.
            native = client.get(url, params=params)
            response = Response()
            response.status_code = native.status_code
            response.url = str(native.url)
            response._content = native.content
            return response

        monkeypatch.setattr(publisher.session, "get", native_get)
        transport = Transport()
        archive(publisher, 2)
        response = client.get("/api/v1/history", params={
            "token": tokens["run"], "admin_secret": state.admin_secret,
        })
        response.raise_for_status()
        values = response.json()
        try:
            if status != 200:
                with pytest.raises(HTTPError) as failure:
                    publisher.update(values, transport, None)
                assert failure.value.response.status_code == status
                assert not transport.files
                assert publisher.score == 1. and publisher.notifications == 0
                assert not (publisher.directory / "delivered.jsonl").exists()
            else:
                publisher.update(values, transport, None)
                packet = json.loads(transport.files[str(FEEDBACK_FILE)])
                assert packet["latest"]["snapshot_id"] == "auto-2"
                assert packet["latest"]["official_result"]["report"] == reports["s2"]
                assert transport.files[packet["latest"]["source_archive"]] == b"agent source only"
                assert publisher.score == 3. and publisher.notifications == 1
                for secret in (*tokens.values(), state.admin_secret):
                    assert secret not in json.dumps(packet)
                    assert secret not in (publisher.directory / "delivered.jsonl").read_text()
                previous = dict(transport.files)
                publisher.update(values, transport, None)
                assert transport.files == previous and publisher.notifications == 1
            assert state.resolve_token(tokens["run"]) == before
        finally:
            publisher.close()
