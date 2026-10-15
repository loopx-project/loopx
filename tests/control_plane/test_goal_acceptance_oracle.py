"""Real File/SQLite oracle and owner HTTP journeys; no model or active Goal."""

import hashlib
import json
import shutil
import sys
import pytest
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal


def test_owner_pinned_oracle_binds_actual_versions_and_withdraws_stale_evidence(
    acceptance_goal,
):
    project, document, cli, _ = acceptance_goal
    checker = project / "check.py"
    checker.write_text(
        "from pathlib import Path\nassert Path('artifact.txt').read_text() == Path('input.txt').read_text()\n"
    )
    (project / "input.txt").write_text("accepted")
    (project / "artifact.txt").write_text("accepted")
    raw = json.loads(document.read_text())
    row = raw["criteria"][0]
    row["validation_argv"] = [sys.executable, "check.py"]
    row["validation_files"] = [
        {"path": "check.py", "sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}
    ]
    row["oracle"] = {
        "kind": "owner_pinned_oracle",
        "source_id": "export-checker",
        "input_refs": ["input.txt"],
        "output_refs": ["artifact.txt"],
    }
    document.write_text(json.dumps(raw))
    initial = cli("inspect")[1]
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        initial["provider_revision"],
        "--execute",
    )
    assert code == 0, configured
    assert (
        configured["goal_acceptance_contract"]["criteria"][0]["oracle"]["state"]
        == "unverified"
    )
    code, checked = cli("verify", "--execute")
    assert code == 0, checked
    receipt = checked["goal_acceptance_contract"]["verification"]["results"][0][
        "oracle_evidence"
    ]
    assert receipt["source_id"] == "export-checker"
    assert receipt["input_versions"] == [
        {"ref": "input.txt", "sha256": hashlib.sha256(b"accepted").hexdigest()}
    ]
    assert receipt["output_versions"] == [
        {"ref": "artifact.txt", "sha256": hashlib.sha256(b"accepted").hexdigest()}
    ]
    assert receipt["checked_at"].endswith("Z")
    current = cli("inspect")[1]["goal_acceptance_contract"]
    assert current["criteria"][0]["oracle"]["state"] == "current"
    (project / "artifact.txt").write_text("a later unverified version")
    stale = cli("inspect")[1]["goal_acceptance_contract"]
    assert stale["criteria"][0]["oracle"]["state"] == "stale"
    assert stale["verification"]["results"][0]["oracle_evidence"] == receipt
    (project / "artifact.txt").write_text("accepted")
    checker.write_text("raise SystemExit(0)\n")
    assert (
        cli("inspect")[1]["goal_acceptance_contract"]["criteria"][0]["oracle"]["state"]
        == "stale"
    )
    code, rejected = cli("verify", "--execute")
    assert code == 1 and rejected["checks_passed"] is False


def test_oracle_http_owner_journey_uses_same_real_authority(acceptance_goal):
    import http.client
    import threading
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.paths import effective_runtime_root

    project, document, cli, _ = acceptance_goal
    # Fixture's source registry is adjacent to the document; this is an isolated
    # File/SQLite provider, never the caller's active Goal.
    registry = document.parent / "registry.json"
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path = registry
    server.runtime_root = effective_runtime_root(registry, None)
    server.runtime_root_override = str(server.runtime_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(body=None, *, origin=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=60)
        try:
            connection.request(
                "GET" if body is None else "POST",
                "/api/chat/goal-acceptance?goal_id=goal-acceptance"
                if body is None
                else "/api/chat/goal-acceptance",
                None
                if body is None
                else json.dumps({"goal_id": "goal-acceptance", **body}),
                {
                    "Content-Type": "application/json",
                    "Origin": origin or f"http://127.0.0.1:{server.server_port}",
                },
            )
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    try:
        status, before = request()
        assert status == 200 and before["goal_acceptance_contract"] == {
            "enabled": False
        }
        checker = project / "check.py"
        checker.write_text(
            "from pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
        )
        owner_document = json.loads(document.read_text())
        owner_document["criteria"][0].update(
            validation_argv=[sys.executable, "check.py"],
            validation_files=[
                {
                    "path": "check.py",
                    "sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
                }
            ],
            oracle={
                "kind": "owner_pinned_oracle",
                "source_id": "http-owner-checker",
                "input_refs": [],
                "output_refs": ["artifact.txt"],
            },
        )
        config = {
            "action": "configure",
            "document": owner_document,
            "expected_provider_revision": before["provider_revision"],
            "operation_id": "http-contract",
        }
        status, saved = request(config)
        assert status == 200 and saved["goal_id"] == "goal-acceptance"
        assert (
            saved["goal_acceptance_contract"]
            == cli("inspect")[1]["goal_acceptance_contract"]
        )
        assert "validation_argv" not in json.dumps(saved)
        assert request({**config, "operation_id": "stale-config"})[0] == 409
        assert request({**config, "actor_agent_id": "agent-a"})[0] == 409
        assert (
            request(
                {
                    "action": "verify",
                    "expected_provider_revision": saved["provider_revision"],
                    "results": [{"passed": True}],
                }
            )[0]
            == 409
        )
        assert (
            request(
                {
                    "action": "verify",
                    "expected_provider_revision": saved["provider_revision"],
                },
                origin="https://foreign.example",
            )[0]
            == 403
        )
        (project / "artifact.txt").write_text("accepted")
        status, checked = request(
            {
                "action": "verify",
                "expected_provider_revision": saved["provider_revision"],
            }
        )
        assert status == 200 and checked["checks_passed"] is True, checked
        assert (
            checked["goal_acceptance_contract"]["criteria"][0]["oracle"]["state"]
            == "current"
        )
        assert (
            checked["goal_acceptance_contract"]
            == cli("inspect")[1]["goal_acceptance_contract"]
        )
        current = request()[1]
        status, disabled = request(
            {
                "action": "disable",
                "expected_provider_revision": current["provider_revision"],
                "operation_id": "http-disable",
            }
        )
        assert status == 200 and disabled["goal_acceptance_contract"] == {
            "enabled": False
        }
        assert cli("inspect")[1]["goal_acceptance_contract"] == {"enabled": False}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "change",
    ["snapshot_output", "live_input", "live_output", "missing_source", "timeout"],
)
def test_oracle_rejects_changed_or_missing_files_and_timeout(acceptance_goal, change):
    project, document, cli, _ = acceptance_goal
    (project / "input.txt").write_text("expected")
    (project / "artifact.txt").write_text("expected")
    live = project / ("input.txt" if change == "live_input" else "artifact.txt")
    script = "from pathlib import Path\n"
    if change == "snapshot_output":
        script += "Path('artifact.txt').write_text('changed by checker')\n"
    elif change.startswith("live_"):
        script += f"Path({str(live)!r}).write_text('changed while checker ran')\n"
    elif change == "timeout":
        script += "import time\ntime.sleep(5)\n"
    else:
        script += "Path('artifact.txt').read_text()\n"
    checker = project / "check.py"
    checker.write_text(script)
    doc = json.loads(document.read_text())
    criterion = doc["criteria"][0]
    criterion.update(
        validation_argv=[sys.executable, "check.py"],
        validation_timeout_seconds=1,
        validation_files=[
            {
                "path": "check.py",
                "sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
            }
        ],
        oracle={
            "kind": "owner_pinned_oracle",
            "source_id": "owner-checker",
            "input_refs": ["input.txt"],
            "output_refs": ["artifact.txt"],
        },
    )
    document.write_text(json.dumps(doc))
    before = cli("inspect")[1]
    code, result = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        before["provider_revision"],
        "--execute",
    )
    assert code == 0, result
    if change == "missing_source":
        checker.unlink()
    code, result = cli("verify", "--execute")
    assert code == 1 and result["checks_passed"] is False, result
    check = result["goal_acceptance_contract"]["verification"]["results"][0]
    assert check["passed"] is False and "oracle_evidence" not in check
    assert (
        cli("inspect")[1]["goal_acceptance_contract"]["criteria"][0]["oracle"]["state"]
        != "current"
    )


def test_pinned_file_argument_is_not_an_executed_checker(acceptance_goal):
    non_interpreter = shutil.which("true")
    if non_interpreter is None:
        pytest.skip("POSIX true executable is required for this invocation regression")
    project, document, cli, _ = acceptance_goal
    checker = project / "reject.py"
    checker.write_text("raise SystemExit(17)\n")
    (project / "artifact.txt").write_text("unchanged output")
    raw = json.loads(document.read_text())
    criterion = raw["criteria"][0]
    criterion.update(
        validation_argv=[sys.executable, "reject.py"],
        validation_files=[
            {
                "path": "reject.py",
                "sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
            }
        ],
        oracle={
            "kind": "owner_pinned_oracle",
            "source_id": "rejecting-checker",
            "input_refs": [],
            "output_refs": ["artifact.txt"],
        },
    )
    document.write_text(json.dumps(raw))
    before = cli("inspect")[1]
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        before["provider_revision"],
        "--execute",
    )
    assert code == 0, configured
    code, rejected = cli("verify", "--execute")
    assert code == 1 and rejected["checks_passed"] is False, rejected
    result = rejected["goal_acceptance_contract"]["verification"]["results"][0]
    assert result["exit_code"] == 17 and "oracle_evidence" not in result
    before = cli("inspect")[1]
    criterion["validation_argv"] = [non_interpreter, "reject.py"]
    document.write_text(json.dumps(raw))
    code, refused = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        before["provider_revision"],
        "--execute",
    )
    assert code == 1, refused
    assert refused["error"] == "goal_acceptance_invalid_request"
    assert cli("inspect")[1] == before


def test_pinned_direct_executable_checker_runs_from_snapshot(acceptance_goal):
    if sys.platform == "win32":
        pytest.skip(
            "Direct POSIX shebang execution is not a Windows invocation contract"
        )
    project, document, cli, _ = acceptance_goal
    checker = project / "checks" / "verify"
    checker.parent.mkdir()
    checker.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    checker.chmod(0o755)
    (project / "artifact.txt").write_text("accepted")
    raw = json.loads(document.read_text())
    raw["criteria"][0].update(
        validation_argv=["checks/verify"],
        validation_files=[
            {
                "path": "checks/verify",
                "sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
            }
        ],
        oracle={
            "kind": "owner_pinned_oracle",
            "source_id": "direct-checker",
            "input_refs": [],
            "output_refs": ["artifact.txt"],
        },
    )
    document.write_text(json.dumps(raw))
    before = cli("inspect")[1]
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        before["provider_revision"],
        "--execute",
    )
    assert code == 0, configured
    code, checked = cli("verify", "--execute")
    assert code == 0 and checked["checks_passed"] is True, checked
    assert (
        "oracle_evidence"
        in checked["goal_acceptance_contract"]["verification"]["results"][0]
    )
    assert (
        cli("inspect")[1]["goal_acceptance_contract"]["criteria"][0]["oracle"]["state"]
        == "current"
    )
