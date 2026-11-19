"""Chat storage is not another registered work/status authority."""
from types import SimpleNamespace
import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat.status_api import ChatStatusRequestMixin
from loopx.chat_store import ChatSessionStore


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("registered_root", [True, False])
def test_status_reads_registered_work_while_chat_keeps_its_own_store(tmp_path, monkeypatch, provider, registered_root):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, common, _ = promoted_create_fixture(tmp_path, provider=provider)
    if not registered_root:
        configuration = json.loads(registry.read_text())
        del configuration["common_runtime_root"]
        registry.write_text(json.dumps(configuration))
    chat_root = tmp_path / "private-chat" if registered_root else common
    store = ChatSessionStore(chat_root)
    runtime = ChatRuntimeController(store=store, registry_path=registry, codex_bin="must-not-run")
    session = store.create_session(goal_id="loopx-manager", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="original", channel_id="manager")
    store.append_message(session["session_id"], role="agent", text="Keep the original private history.")
    before = {path: path.read_bytes() for path in store.root.rglob("*.json")}
    # External host activity is an optional observer, not the status authority.
    monkeypatch.setattr("loopx.chat.status_api.attach_host_thread_activity", lambda *a, **k: None)
    monkeypatch.setattr("loopx.chat.status_api.attach_host_delivery_windows", lambda *a, **k: None)

    class Reader(ChatStatusRequestMixin):
        server = SimpleNamespace(registry_path=registry, runtime_root=store.root.parent,
            runtime_root_override=str(store.root.parent), runtime_controller=runtime,
            selected_goal_id=None, scan_roots=[tmp_path / "project"], limit=80,
            goal_subagent_configuration_enabled=False)
        path = "/status.json?goal_id=goal-a"

        def _send_json(self, value, *, status=200):
            self.result = status, value

        def _send_error(self, message, **kwargs):
            self.result = kwargs.get("status", 500), {"error": message, **kwargs}

    try:
        assert runtime.coordination_runtime_root == common
        reader = Reader()
        reader._status()
        code, result = reader.result
        assert code == 200, result
        assert not any("canonical authority" in error.casefold() for error in result.get("contract_errors", []))
        assert result["run_history"]["goals"][0]["id"] == "goal-a"
        assert str(common) not in json.dumps(result)
        assert {path: path.read_bytes() for path in before} == before
        assert runtime.store.root == chat_root / "chat"
    finally:
        runtime.close()
