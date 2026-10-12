import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx import chat_agent
from loopx.capabilities.native_chat import codex_auth
from loopx.capabilities.native_chat.project_context import ChatProjectContexts


def write_auth(home, access="synthetic-access", account="synthetic-account"):
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt",
        "tokens": {"access_token": access, "account_id": account,
                   "refresh_token": "synthetic-private-refresh", "id_token": "synthetic-private-id"},
        "unrelated_private_data": "never-forward"}))


def test_only_short_lived_model_credentials_cross_the_host_boundary(tmp_path):
    home, isolated = tmp_path / "account", tmp_path / "project-store"
    write_auth(home)
    broker = codex_auth.for_isolated_process(home, isolated, "synthetic-codex")
    assert broker.read() == {"accessToken": "synthetic-access", "chatgptAccountId": "synthetic-account"}
    assert "synthetic-access" not in repr(broker)
    assert not isolated.exists()
    assert codex_auth.for_isolated_process(tmp_path / "missing", isolated, "synthetic-codex") is None
    write_auth(isolated, account="separate-project-account")
    assert codex_auth.for_isolated_process(home, isolated, "synthetic-codex") is None


def test_concurrent_refresh_uses_native_account_owner_once(tmp_path, monkeypatch):
    write_auth(tmp_path)
    brokers = [codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex") for _ in range(6)]
    for broker in brokers:
        broker.read()
    calls = []
    def refresh(binary, home, **kwargs):
        calls.append((binary, home))
        write_auth(home, "synthetic-rotated")
    monkeypatch.setattr(codex_auth, "_native_refresh", refresh)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda b: b.read(refresh=True, previous_account_id="synthetic-account"), brokers))
    assert len(calls) == 1
    assert all(r["accessToken"] == "synthetic-rotated" for r in results)


def test_refresh_failure_is_redacted_and_can_retry_without_rebinding(tmp_path, monkeypatch):
    write_auth(tmp_path)
    broker = codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex")
    broker.read()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-refresh")
    monkeypatch.setattr(codex_auth, "_native_refresh", fail)
    with pytest.raises(codex_auth.CodexHostAuthUnavailable) as error:
        broker.read(refresh=True, previous_account_id="synthetic-account")
    assert "synthetic-private" not in str(error.value)
    assert error.value.__suppress_context__
    monkeypatch.setattr(codex_auth, "_native_refresh", lambda binary, home, **kw: write_auth(home, "synthetic-recovered"))
    assert broker.read(refresh=True, previous_account_id="synthetic-account")["accessToken"] == "synthetic-recovered"
    write_auth(tmp_path, "different-token", "different-account")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        broker.read(refresh=True, previous_account_id="synthetic-account")


@pytest.mark.parametrize("value", [None, {}, {"auth_mode": "apikey"},
    {"auth_mode": "chatgpt", "tokens": {}},
    {"auth_mode": "chatgpt", "tokens": {"access_token": False, "account_id": "account"}}])
def test_invalid_host_auth_never_uses_wider_or_fallback_credentials(tmp_path, value):
    (tmp_path / "auth.json").write_text(json.dumps(value))
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex").read()


def test_host_auth_symlink_is_rejected(tmp_path):
    other = tmp_path / "other"
    write_auth(other)
    (tmp_path / "auth.json").symlink_to(other / "auth.json")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex").read()


def test_broken_project_auth_symlink_cannot_switch_to_host_account(tmp_path):
    home, isolated = tmp_path / "host", tmp_path / "project"
    write_auth(home)
    isolated.mkdir()
    (isolated / "auth.json").symlink_to(tmp_path / "missing-private-file")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.for_isolated_process(home, isolated, "synthetic-codex")


def test_native_callback_refresh_recovers_but_preserves_account_and_request_gates(tmp_path, monkeypatch):
    from tests.test_chat_agent import _FakeAppServerProcess
    import queue

    write_auth(tmp_path)
    broker = codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex")
    broker.read()
    monkeypatch.setattr(codex_auth, "_native_refresh", lambda binary, home, **kw: write_auth(home, "synthetic-rotated"))
    process = _FakeAppServerProcess()
    session = chat_agent.CodexChatAgentSession(process=process, messages=queue.Queue(),
        thread_id="same-thread", work_dir=tmp_path, _host_model_auth=broker)
    request = {"id": 9, "method": "account/chatgptAuthTokens/refresh",
               "params": {"reason": "unauthorized", "previousAccountId": "synthetic-account"}}
    assert session._check_server_gate(request)
    assert json.loads(process.stdin.getvalue().splitlines()[-1])["result"] == {
        "accessToken": "synthetic-rotated", "chatgptAccountId": "synthetic-account"}
    assert session.thread_id == "same-thread"
    for params in ([], {"reason": "other", "previousAccountId": "synthetic-account"},
                   {"reason": "unauthorized"}):
        assert session._check_server_gate({**request, "params": params})
        assert "error" in json.loads(process.stdin.getvalue().splitlines()[-1])
    with pytest.raises(chat_agent.CodexChatAgentError):
        session._check_server_gate({"id": 10, "method": "item/commandExecution/requestApproval", "params": {}})


def test_native_refresh_never_opens_a_thread_or_passes_credentials_in_argv(tmp_path, monkeypatch):
    from tests.test_chat_agent import _FakeAppServerProcess
    process = _FakeAppServerProcess(thread_response={"account": {"type": "chatgpt"}})
    launches = []
    monkeypatch.setattr(codex_auth.subprocess, "Popen", lambda command, **kw: launches.append((command, kw)) or process)
    codex_auth._native_refresh("synthetic-codex", tmp_path)
    packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [p["method"] for p in packets] == ["initialize", "initialized", "account/read"]
    assert packets[-1]["params"] == {"refreshToken": True}
    assert launches[0][1]["env"]["CODEX_HOME"] == str(tmp_path)
    assert "synthetic-access" not in str(launches[0][0])
    assert process.poll() == 0


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("native_provider", ["loopx_host_chatgpt_http", None, "unexpected-provider", "collision"])
def test_isolated_start_and_resume_supply_auth_only_over_private_rpc(tmp_path, monkeypatch, resume, native_provider):
    from tests.test_chat_agent import _FakeAppServerProcess
    home = tmp_path / "host"
    write_auth(home)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ChatProjectContexts([workspace], filesystem_scope="workspace_only").available()[0]
    profile = "loopx_workspace_only_write"
    config = {"model_providers": {"loopx_host_chatgpt_http": {
        "base_url": "https://example.invalid", "env_key": "PRIVATE_KEY"}}} if native_provider == "collision" else {}
    process = _FakeAppServerProcess(config_response={"config": config}, thread_response={
        "thread": {"id": "thread-loopx-chat"}, "activePermissionProfile": {"id": profile},
        "runtimeWorkspaceRoots": [str(workspace)], "modelProvider": native_provider})
    process.stdout = io.StringIO(json.dumps({"id": 1, "result": {}}) + "\n" +
        json.dumps({"id": 4, "result": {"type": "chatgptAuthTokens"}}) + "\n" + process.stdout.getvalue().split("\n", 1)[1])
    real_which = chat_agent.shutil.which
    monkeypatch.setattr(chat_agent.shutil, "which", lambda name: str(tmp_path / "native-codex") if name == "synthetic-codex" else real_which(name))
    real_popen = chat_agent.subprocess.Popen
    launches = []
    def launch(command, **kwargs):
        if command[0] != str(tmp_path / "native-codex"):
            return real_popen(command, **kwargs)
        launches.append((command, kwargs))
        return process
    monkeypatch.setattr(chat_agent.subprocess, "Popen", launch)
    def start():
        return chat_agent.CodexChatAgentSession.start(codex_bin="synthetic-codex", work_dir=workspace,
            goal_id=None, objective="project", project_context=context, codex_home=home,
            resume_thread_id="thread-loopx-chat" if resume else None, model="synthetic-model")
    if native_provider != "loopx_host_chatgpt_http":
        collision = native_provider == "collision"
        with pytest.raises(chat_agent.CodexChatAgentError, match=(
                "conflicts with project configuration" if collision else "requested conversation provider")):
            start()
        assert process.poll() is not None
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert not any(p.get("method") == "turn/start" for p in packets)
        assert sum(p.get("method") in {"thread/start", "thread/resume"} for p in packets) == (0 if collision else 1)
        return
    session = start()
    try:
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        login = next(p for p in packets if p["method"] == "account/login/start")
        assert login["params"] == {"type": "chatgptAuthTokens", "accessToken": "synthetic-access", "chatgptAccountId": "synthetic-account"}
        thread = next(p for p in packets if p["method"] in {"thread/start", "thread/resume"})
        assert thread["params"]["permissions"] == profile
        assert thread["params"]["modelProvider"] == "loopx_host_chatgpt_http"
        provider = thread["params"]["config"]["model_providers"]["loopx_host_chatgpt_http"]
        assert provider == {"name": "LoopX host ChatGPT HTTP", "wire_api": "responses",
                            "requires_openai_auth": True, "supports_websockets": False}
        assert thread["params"]["config"]["permissions"][profile]["network"]["enabled"] is False
        assert thread["params"]["config"]["skills"]["include_instructions"] is False
        assert "LOOPX_CHAT_CODEX_MODEL_PROVIDER" not in launches[0][1]["env"]
        assert "synthetic-access" not in str(thread)
        assert "synthetic-access" not in str(launches)
        isolated = Path(launches[0][1]["env"]["CODEX_HOME"])
        assert isolated != home and not (isolated / "auth.json").exists()
        assert session.next_request_id == 5
        session._check_server_gate({"id": 9, "method": "account/chatgptAuthTokens/refresh", "params": {"reason": "unauthorized", "previousAccountId": "wrong-account"}})
        assert json.loads(process.stdin.getvalue().splitlines()[-1])["error"]["message"] == "Trusted-host model authentication unavailable."
    finally:
        session.close()
