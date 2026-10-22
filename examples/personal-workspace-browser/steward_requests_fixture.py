"""Disposable SQLite/Chat/Inbox journey; no model or external message execution.

Pass a public synthetic status file for whole-workspace visual review. Runtime
availability is display-only fixture metadata; POSTs cannot execute it. Requests,
decisions and return delivery below use the production owners in a temp runtime.
Commands on stdin: inspect, fault, recover, deliver, stop.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from urllib.parse import parse_qs, urlparse

from loopx.capabilities.manager_context import acknowledge, deliver
from loopx.capabilities.manager_context.roundtrip import drain, report
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_action_store import ChatActionStore
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir
from loopx.chat_store import ChatSessionStore
from loopx.cli import main as cli
from loopx.control_plane.collaboration.inbox import _root
from loopx.extensions.lark.cli_resolution import LarkCliResolution


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--status-file", type=Path, required=True)
    args.add_argument("--assets-dir", type=Path, default=default_chat_assets_dir())
    selected = args.parse_args()
    status = json.loads(selected.status_file.read_text())
    with TemporaryDirectory(prefix="loopx-steward-requests-") as directory:
        root = Path(directory).resolve()
        runtime, registry = root / "runtime", root / "project/.loopx/registry.json"
        with redirect_stdout(StringIO()):
            code = cli(["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime),
                "project", "register", "--project-id", "delivery-fixture", "--project-kind", "work",
                "--knowledge-root", str(root / "project"), "--goal-id", "billing-exports",
                "--objective", "Move billing exports to the ledger API", "--non-goal", "No external effects",
                "--acceptance", "Reviewed artifacts retain the correct versions", "--next-effect", "Inspect the artifact",
                "--stop-condition", "Stop before external execution", "--goal-instance-profile", "source_session_v1",
                "--operation-id", "synthetic-request-inventory", "--registered-agent", "builder",
                "--registered-agent", "reviewer", "--canonical-storage", "sqlite"])
        if code:
            raise RuntimeError("Could not create isolated fixture")
        store = ChatSessionStore(runtime)
        session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
            upstream_thread_id="synthetic-steward", channel_id="manager",
            agent_model_id="gpt-6.1-sol", agent_reasoning_effort="xhigh")
        titles = ["核对账单导出的字段映射", "检查重复导出是否会生成重复记录", "复核历史账单的金额差异",
            "整理迁移后的回滚步骤", "补齐取消导出时的错误提示", "核对欧洲地区的税额精度",
            "检查超时后的安全重试", "复核下载文件中的版本信息", "检查月底跨时区的账期边界",
            "整理旧接口的调用方清单", "核对缺失凭证时的处理方式", "检查手机上的导出入口", "复核迁移说明中的示例"]
        last_receipt = None
        for index, title in enumerate(titles):
            target = {"goal_id": "billing-exports", "agent_id": "reviewer" if index % 3 == 2 else "builder"}
            turn, _ = store.create_turn(session["session_id"], client_turn_id=f"synthetic-{index}", message=title, origin="web")
            brief = {"schema_version": "collaboration_brief_v0", "purpose": title,
                "context": "账单导出正在迁移到统一接口，请沿现有工作核对这项要求，并保留失败与未知。",
                "constraints": ["只检查本次产物；不发布、不联系外部人员。"], "inputs": [],
                "acceptance": ["给出可回看的核验结论与下一步。"], "return_requirement": "将实际结论返回原管家会话。"}
            receipt = deliver(runtime, registry, session=session, turn=turn, request={**target, "brief": brief}, source_store=store)
            store.append_message(session["session_id"], role="user", text=title, turn_id=turn["turn_id"], origin="web")
            store.append_message(session["session_id"], role="assistant", text="已交给负责的 Agent，等待它返回实际结论。",
                turn_id=turn["turn_id"], origin="web")
            store.update_turn(session["session_id"], turn["turn_id"], status="completed", response={"context_handoff_receipt": receipt})
            store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
            if index % 3 != 2:
                acknowledge(runtime, "billing-exports", target["agent_id"], receipt["request_id"], "adopt",
                    "已接到原工作，正在核对实际产物；尚未形成完成结论。", registry=registry, caller_goal_ref=receipt["goal_ref"])
            last_receipt = receipt
        report(runtime, "billing-exports", "builder", last_receipt["request_id"], "conclusion",
            "已核对本次迁移说明与示例，版本一致，原始检查结果已保留。", registry=registry,
            caller_goal_ref=last_receipt["goal_ref"])

        class Handler(ChatRequestHandler):
            def do_POST(self):
                self.server.posts += 1
                self._send_error("Read-only visual fixture", status=405)
            def _status(self):
                self._send_json(status)
            def _goal_contexts(self):
                self._send_json({"ok": True, "schema_version": "loopx_chat_goal_contexts_v0", "goals": []})
            def _request_inventory_read(self):
                self.server.inventory_reads += 1
                if self.server.fault:
                    return self._send_error("Synthetic inventory read failure", status=503)
                return super()._request_inventory_read()
            def do_GET(self):
                if urlparse(self.path).path == "/status.json":
                    query = parse_qs(urlparse(self.path).query)
                    if query.get("view") == ["workspace-directory"]:
                        return self._send_json({"ok": True, "schema_version": "loopx_workspace_directory_v1",
                            "registry_revision": "synthetic-requests", "goals": [
                                {"id": goal["id"], "display_name": goal.get("display_name", goal["id"]),
                                 "activation_state": "active", "registry_member": True} for goal in status["run_history"]["goals"]],
                            "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}, "errors": [], "warnings": [], "checks": []}})
                    snapshot = copy.deepcopy(status)
                    snapshot["workspace_registry_revision"] = "synthetic-requests"
                    requested = query.get("goal_id", [None])[0]
                    if requested:
                        snapshot["run_history"]["goals"] = [row for row in snapshot["run_history"]["goals"] if row["id"] == requested]
                        snapshot["attention_queue"]["items"] = [row for row in snapshot["attention_queue"]["items"] if row["goal_id"] == requested]
                    snapshot["goal_projection"] = {"schema_version": "loopx_goal_projection_scope_v0", "scope": "active",
                        "complete": True, "projected_goal_count": len(snapshot["run_history"]["goals"]),
                        "registry_goal_count": len(status["run_history"]["goals"]), "registry_revision": "synthetic-requests"}
                    return self._send_json(snapshot)
                return super().do_GET()

        server = ChatHTTPServer(("127.0.0.1", 0), Handler)
        server.chat_store, server.registry_path, server.runtime_root = store, registry, runtime
        server.action_store = ChatActionStore(runtime / "chat/actions")
        server.lark_cli_resolution = LarkCliResolution(None, False, "synthetic", None, "lark_cli_not_installed")
        server.runtime_root_override, server.selected_goal_id, server.verbose = str(runtime), None, False
        server.assets_dir, server.fault, server.posts, server.inventory_reads = selected.assets_dir, False, 0, 0
        server.runtime_controller = ChatRuntimeController(store=store, registry_path=registry, codex_bin="unused-fixture-codex")
        server.runtime_controller.capabilities = lambda: [{"agent_id": "codex", "display_name": "Codex",
            "adapter_kind": "codex_app_server", "available": True, "streaming": True, "resume": True, "interrupt": True}]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def source_hashes():
            return {str(path.relative_to(_root(runtime))): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in _root(runtime).rglob("*.json")}
        initial = source_hashes()
        print(json.dumps({"event": "ready", "origin": f"http://127.0.0.1:{server.server_port}", "session_id": session["session_id"]}), flush=True)
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "stop":
                    break
                if command == "fault":
                    server.fault = True
                elif command == "recover":
                    server.fault = False
                elif command == "deliver":
                    drain(runtime, registry, store, lambda *_: None)
                elif command != "inspect":
                    raise ValueError("Unknown fixture command")
                print(json.dumps({"event": command, "posts": server.posts, "inventory_reads": server.inventory_reads,
                    "source_unchanged": source_hashes() == initial}), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            server.runtime_controller.close()


if __name__ == "__main__":
    main()
