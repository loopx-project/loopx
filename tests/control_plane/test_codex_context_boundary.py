"""Opt-in qualification of the existing native host's context-delivery seam.

Only inference is static and local. No real Goal, benchmark task, credentials,
installed configuration or LoopX default changes. A successful primitive test
does not qualify a product adapter or model effectiveness.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time

import pytest

from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport
from tests.control_plane.codex_checkpoint_fixture import local_inference_provider

pytestmark = pytest.mark.skipif(
    not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
    reason="Explicit native Codex binary required; inference uses loopback only",
)

# Deliberately literal before/after documents and a tiny change. This fixture
# tests native delivery, not a second implementation of the planned TS diff.
SOURCE = "\n".join(f"Requirement {i:04d}: preserve the exact original text." for i in range(900))
BASE = json.dumps({"requirements": SOURCE, "stop": "Stop before publishing."})
TARGET = json.dumps({"requirements": SOURCE, "stop": "Stop before publishing or deploying."})
DELTA = json.dumps({"changed": {"stop": "Stop before publishing or deploying."}})
HOOK = '''import json, pathlib, sys
root = pathlib.Path(__file__).parent
p = json.load(sys.stdin)
with (root / "observed.jsonl").open("a") as f:
    f.write(json.dumps({"event": p["hook_event_name"], "source": p.get("source")}) + "\\n")
event = p["hook_event_name"]
if p.get("source") == "compact":
    if (root / "fail-recovery").exists():
        sys.exit(1)
    body = (root / "target.json").read_text()
elif event == "SessionStart":
    body = (root / "base.json").read_text()
else:
    body = (root / "delta.json").read_text()
print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
    "additionalContext": "Synthetic required context data:\\n" + body}}))
'''


def _response(request, number, message):
    if number <= 2:
        return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                "name": "exec_command", "arguments": json.dumps({
                    "cmd": "printf synthetic-tool-result", "max_output_tokens": 100})}
    # The native host's third request is compaction. Remove the exact source
    # from the summary so recovery cannot pass by accidentally retaining it.
    message["content"][0]["text"] = "Synthetic summary without the original requirement body."
    return message


def _usage(request, number):
    count = 30000 if number == 2 else 1
    return {"input_tokens": count, "output_tokens": 1, "total_tokens": count + 1}


def _input_text(request):
    """Read actual model-input text; tools/instructions are not source delivery."""
    return "\n".join(part.get("text", "") for item in request["input"]
                     for part in item.get("content", []) if isinstance(part, dict))


def _app_server(binary, env, workspace, home, trusted, additional_context=None):
    with (home / "stderr.txt").open("w") as stderr, StdioNativeGoalTransport.spawn(
        [binary, "app-server", "--listen", "stdio://"], cwd=str(workspace), env=env,
        stderr=stderr, response_timeout_sec=20,
    ) as native:
        native.request("initialize", {"clientInfo": {"name": "context_boundary_fixture", "version": "0.1.0"},
                                      "capabilities": {"experimentalApi": True}})
        native.notify("initialized", {})
        params = {"cwd": str(workspace), "sandbox": "read-only", "approvalPolicy": "never"}
        if trusted:
            # Trust only the exact synthetic handlers created by this test.
            # Never read/alter the operator's hook trust or treat a global CLI
            # bypass flag as app-server trust propagation.
            listed = native.request("hooks/list", {"cwds": [str(workspace)]})
            handlers = [h for entry in listed["data"] for h in entry["hooks"]]
            assert len(handlers) == 2
            assert all(h["sourcePath"] == str((home / "hooks.json").resolve()) for h in handlers)
            params["config"] = {"hooks.state": {
                h["key"]: {"trusted_hash": h["currentHash"]} for h in handlers}}
        source = native.request("thread/start", params)["thread"]["id"]
        turn_params = {"threadId": source,
            "input": [{"type": "text", "text": "Synthetic context-delivery fixture."}]}
        if additional_context is not None:
            turn_params["additionalContext"] = additional_context
        turn = native.request("turn/start", turn_params)["turn"]["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            event = native.next_event(timeout_sec=1)
            if event and event.get("method") == "turn/completed":
                assert event["params"]["turn"]["id"] == turn
                assert event["params"]["turn"]["status"] == "completed"
                return
        raise AssertionError("Native context fixture did not finish")


def _native_fixture(tmp_path):
    binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env["CODEX_HOME"] = str(home)
    return binary, env, workspace, home


def _configure_provider(home, url):
    (home / "config.toml").write_text(
        'model="fixture"\nmodel_provider="fixture"\napproval_policy="never"\nsandbox_mode="read-only"\n'
        'model_auto_compact_token_limit=20000\nmodel_context_window=100000\n'
        '[features]\ngoals=false\nplugins=false\nremote_plugin=false\nplugin_sharing=false\n'
        'responses_websockets_v2=false\n'
        f'[model_providers.fixture]\nname="Synthetic local"\nbase_url="{url}"\n'
        'wire_api="responses"\nrequires_openai_auth=false\n')


@pytest.mark.parametrize("host", ["exec", "app_server"])
@pytest.mark.parametrize("case", ["intact", "spill", "untrusted", "failed_recovery"])
def test_native_hook_context_boundary(tmp_path, host, case):
    """Full -> warm change -> automatic compaction -> same-source full recovery."""
    binary, env, workspace, home = _native_fixture(tmp_path)
    for name, body in (("base", BASE), ("target", TARGET), ("delta", DELTA)):
        (home / f"{name}.json").write_text(body)
    hook = home / "hook.py"
    hook.write_text(HOOK)
    if case == "failed_recovery":
        (home / "fail-recovery").touch()
    handler = {"type": "command", "command": shlex.join([sys.executable, str(hook)]), "timeout": 10}
    if case != "spill":
        # The fixture has a strict known cap. Default spilling is deliberately
        # retained in its negative case, never mistaken for intact delivery.
        handler["additionalContextLimit"] = 0
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        name: [{"hooks": [handler]}] for name in ("SessionStart", "PostToolUse")}}))
    with local_inference_provider(_response, _usage) as (url, calls, requests):
        _configure_provider(home, url)
        if host == "exec":
            flags = [] if case == "untrusted" else ["--dangerously-bypass-hook-trust"]
            result = subprocess.run([binary, "exec", *flags, "--skip-git-repo-check", "--json",
                "-C", str(workspace), "Synthetic context-delivery fixture."], env=env,
                capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr
        else:
            _app_server(binary, env, workspace, home, case != "untrusted")
    assert len(calls) == 4  # initial, warm continuation, compaction, recovery
    first, warm, _, recovered = map(_input_text, requests)
    if case == "untrusted":
        assert not (home / "observed.jsonl").exists()
        assert all(SOURCE not in text for text in (first, warm, recovered))
        return  # A configured handler is not evidence of delivery.
    events = [json.loads(line) for line in (home / "observed.jsonl").read_text().splitlines()]
    assert events[-1] == {"event": "SessionStart", "source": "compact"}
    if case == "spill":
        assert BASE not in first and TARGET not in recovered
        assert "Requirement 0000" in first and "Requirement 0899" in first
        return  # Head/tail presence must not confirm a warm baseline.
    assert first.count(BASE) == 1
    assert warm.count(BASE) == 1 and warm.count(DELTA) == 1
    assert TARGET not in warm  # warm primitive sent a change, not a hidden full resend
    if case == "failed_recovery" and TARGET not in recovered:
        pytest.xfail("Native host continues after failed compact hook without complete source; "
                     "hooks alone do not qualify automatic full fallback")
    assert recovered.count(TARGET) == 1
    assert BASE not in recovered
    # The two immutable source captures survive; no fresh admission/Goal write.
    assert (home / "base.json").read_text() == BASE
    assert (home / "target.json").read_text() == TARGET


@pytest.mark.parametrize("kind", ["untrusted", "application"])
@pytest.mark.parametrize("body", ["Complete synthetic source; stop before publishing.", BASE],
                         ids=["small", "large"])
def test_native_additional_context_boundary(tmp_path, kind, body):
    """A keyed context fragment must not be mistaken for retained source state.

    Test the native alternative to command hooks, with no recovery hook present.
    Keep failed completeness/retention qualification visible rather than asserting
    a particular preview size or freezing the current host's lossy behavior.
    """
    binary, env, workspace, home = _native_fixture(tmp_path)
    with local_inference_provider(_response, _usage) as (url, calls, requests):
        _configure_provider(home, url)
        _app_server(binary, env, workspace, home, False,
                    {"synthetic_source": {"kind": kind, "value": body}})
    assert len(calls) == 4
    first, warm, _, recovered = map(_input_text, requests)
    if body not in first:
        pytest.xfail("Native additionalContext is incomplete before first model input; "
                     "it cannot establish an intact baseline")
    assert first.count(body) == 1 and warm.count(body) == 1
    if body not in recovered:
        pytest.xfail("Native additionalContext is lost on automatic compaction; "
                     "a stable source key does not prove retained context")
    assert recovered.count(body) == 1
