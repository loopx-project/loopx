from __future__ import annotations

import json
import mimetypes
import time
import uuid
from collections.abc import Mapping
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import chat_configuration_api as config_api
from .attached_session_api import AttachedSessionRequestMixin
from .chat import (
    TodoReviewPreviewConflict,
    apply_todo_review_preview,
    build_todo_review_preview,
    redact_local_paths,
)
from .chat_agent import CodexChatAgentError
from .chat_attachments import normalize_chat_image_attachments
from .chat_actions import ChatActionService, ProtectedActionGate
from .chat_action_store import ACTION_KINDS, ActionConflictError, ChatActionStore
from .chat_goal_subagent_api import (
    GoalSubagentConfigurationRequestMixin,
    add_goal_subagent_capability,
    add_goal_subagent_routes,
)
from .chat_status_api import ChatStatusRequestMixin
from .chat_runtime import ChatRuntimeController, TERMINAL_TURN_STATES
from .chat_manager import (
    MANAGER_AGENT_GOAL_ID, MANAGER_AGENT_OBJECTIVE, is_manager_channel,
    manager_capabilities_projection, manager_workspace,
)
from .chat_session_open import open_chat_session
from .chat_ssh_source_api import SshSourceRequestMixin
from .chat_store import ChatSessionStore
from .chat_loopx_mode import handle_loopx_request
from .capabilities.manager_context.roundtrip import project_chat_session_snapshot
from .control_plane.goals.active_state_metadata import active_state_section_text
from .control_plane.status.ssh_host_catalog import (
    SSH_HOST_CATALOG_PATH,
    ssh_host_catalog_payload,
)
from .chat_lark_api import (
    LarkChatRequestMixin,
    build_goal_repository_contexts as build_goal_repository_contexts,
    build_lark_goal_topic_runtime_snapshot,
    reconcile_lark_manager_route,
)
from .extensions.lark import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .extensions.lark.app_setup import LarkAppSetupManager
from .extensions.lark.cli_resolution import (
    LarkCliResolution,
    build_lark_command_runner,
    resolve_lark_cli,
)
from .extensions.lark.goal_channel import (
    configure_lark_goal_channel_automation,
    default_goal_channel_target_path,
    goal_channel_target_for_name,
    list_goal_channel_targets,
    read_goal_channel_targets,
    setup_lark_goal_channel,
)
from .extensions.lark.goal_topic_connections import list_lark_apps
from .extensions.lark.goal_topic_runtime import LarkGoalTopicRuntimeService
from .extensions.lark.manager_routing import authorized_manager_goal_ids
from .extensions.lark.presentation.kanban import CommandRunner
from .extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from .history import load_registry
from .chat_completed_todos import CompletedTodoPages, CompletedTodoRequestMixin
from .kiro_cli_goal_mode import KIRO_CLI_BIN
from .paths import resolve_runtime_root
from .release_manifest import release_runtime_identity
from .registry import registry_goals, resolve_state_file
from .state_projection import build_active_state_structured_projection
from .status_server import (
    cors_response_headers,
    is_loopback_host,
    is_loopback_origin,
    parse_strict_json_object,
)


DEFAULT_CHAT_HOST = "127.0.0.1"
DEFAULT_CHAT_PORT = 8767
DEFAULT_CHAT_PATH = "/chat/"
DEFAULT_CHAT_STATUS_PATH = "/status.json"
CHAT_CAPABILITIES_PATH = "/api/chat/capabilities"
CHAT_ENDPOINTS_PATH = "/api/chat/endpoints"
CHAT_SESSIONS_PATH = "/api/chat/sessions"
CHAT_ATTACH_SESSION_PATH = f"{CHAT_SESSIONS_PATH}/attach"
CHAT_PROJECTION_MESSAGES_PATH = "/api/chat/projection-messages"
CHAT_TODO_DRY_RUN_PATH = "/api/chat/todo/dry-run"
CHAT_TODO_APPLY_PATH = "/api/chat/todo/apply"
CHAT_GOAL_CHANNEL_TARGETS_PATH = "/api/chat/goal-channel/targets"
CHAT_GOAL_CHANNEL_SETUP_PATH = "/api/chat/goal-channel/setup"
CHAT_GOAL_CHANNEL_CONFIGURE_PATH = "/api/chat/goal-channel/configure"
CHAT_GOAL_CONTEXTS_PATH = "/api/chat/goals/contexts"
CHAT_LARK_APPS_PATH = "/api/chat/lark/apps"
CHAT_LARK_APP_SETUPS_PATH = "/api/chat/lark/app-setups"
CHAT_LARK_CHATS_PATH = "/api/chat/lark/chats"
CHAT_LARK_CONNECTIONS_PATH = "/api/chat/lark/connections"
CHAT_ACTIONS_PATH = "/api/actions"
CHAT_ACTION_PREVIEW_PATH = f"{CHAT_ACTIONS_PATH}/preview"


def chat_cors_response_headers(origin: str | None) -> dict[str, str]:
    headers = cors_response_headers(origin)
    if headers:
        headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return headers


def default_chat_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "web" / "chat"


def _compact_text(value: Any, *, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _goal_public_context(registry: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
    goal_id = str(goal.get("id") or "")
    project = Path(str(goal.get("repo") or ".")).expanduser().resolve()
    objective = ""
    title = goal_id
    state_path = resolve_state_file(project, goal.get("state_file"))
    if state_path is not None and state_path.exists():
        try:
            state_text = state_path.read_text(encoding="utf-8")
            objective = _compact_text(active_state_section_text(state_text, "Objective"))
            title_line = next(
                (line[2:].strip() for line in state_text.splitlines() if line.startswith("# ")),
                "",
            )
            projected_title = _compact_text(title_line, limit=160)
            if projected_title.casefold() in {"active goal state", "active state"}:
                projected_title = goal_id
            title = projected_title or title
        except OSError:
            pass
    return {
        "goal_id": goal_id,
        "title": title,
        "objective": objective or _compact_text(goal.get("domain"), limit=200),
        "project": project,
        "runtime_root": resolve_runtime_root(registry),
    }


def _compact_todo(item: dict[str, Any], *, protected_paths: list[Path]) -> dict[str, Any]:
    return {
        "todo_id": _compact_text(item.get("todo_id"), limit=100) or None,
        "role": _compact_text(item.get("role"), limit=40) or None,
        "status": _compact_text(item.get("status"), limit=40) or ("done" if item.get("done") else "open"),
        "priority": _compact_text(item.get("priority"), limit=60) or None,
        "text": _compact_text(
            redact_local_paths(str(item.get("text") or item.get("title") or ""), protected_paths=protected_paths)
        ),
        "action_kind": _compact_text(item.get("action_kind"), limit=100) or None,
        "task_class": _compact_text(item.get("task_class"), limit=100) or None,
        "claimed_by": _compact_text(item.get("claimed_by"), limit=100) or None,
        "evidence": _compact_text(
            redact_local_paths(str(item.get("evidence") or item.get("note") or ""), protected_paths=protected_paths)
        )
        or None,
    }


def _goal_todos(queue_item: dict[str, Any], *, protected_paths: list[Path]) -> list[dict[str, Any]]:
    project_asset = queue_item.get("project_asset")
    asset = project_asset if isinstance(project_asset, dict) else {}
    todos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role in ("user", "agent"):
        group = queue_item.get(f"{role}_todos")
        if not isinstance(group, dict):
            group = asset.get(f"{role}_todos")
        items = group.get("items") if isinstance(group, dict) else []
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item.setdefault("role", role)
            compact = _compact_todo(item, protected_paths=protected_paths)
            key = str(compact.get("todo_id") or compact.get("text") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            todos.append(compact)
    return todos[:40]


def build_chat_status_projection(
    *,
    registry: dict[str, Any],
    status_payload: dict[str, Any],
    selected_goal_id: str | None,
) -> dict[str, Any]:
    queue = status_payload.get("attention_queue")
    queue_items = queue.get("items") if isinstance(queue, dict) else []
    queue_by_goal = {
        str(item.get("goal_id") or ""): item
        for item in queue_items if isinstance(queue_items, list) and isinstance(item, dict)
    }
    goals: list[dict[str, Any]] = []
    for raw_goal in registry_goals(registry):
        goal_id = str(raw_goal.get("id") or "")
        context = _goal_public_context(registry, raw_goal)
        protected_paths = [context["project"], context["runtime_root"]]
        item = queue_by_goal.get(goal_id, {})
        asset_raw = item.get("project_asset") if isinstance(item, dict) else {}
        asset = asset_raw if isinstance(asset_raw, dict) else {}
        todos = _goal_todos(item, protected_paths=protected_paths) if isinstance(item, dict) else []
        open_todos = [todo for todo in todos if todo.get("status") not in {"done", "completed", "closed"}]
        top_todo = next((todo for todo in open_todos if todo.get("role") == "agent"), None)
        if top_todo is None and open_todos:
            top_todo = open_todos[0]
        waiting_on = _compact_text(item.get("waiting_on"), limit=80) if isinstance(item, dict) else ""
        raw_gate = asset.get("gate") or item.get("status") if isinstance(item, dict) else ""
        raw_next_action = asset.get("next_action") or item.get("recommended_action") if isinstance(item, dict) else ""
        evidence = [
            str(todo.get("evidence"))
            for todo in todos
            if todo.get("evidence")
        ][:4]
        latest_validation = asset.get("latest_validation")
        if isinstance(latest_validation, dict) and latest_validation.get("summary"):
            evidence.insert(0, _compact_text(latest_validation.get("summary")))
        quota = item.get("quota") if isinstance(item, dict) and isinstance(item.get("quota"), dict) else {}
        goals.append(
            {
                "goal_id": goal_id,
                "title": context["title"],
                "objective": redact_local_paths(context["objective"], protected_paths=protected_paths),
                "status": _compact_text(item.get("status"), limit=100)
                if isinstance(item, dict)
                else _compact_text(raw_goal.get("status"), limit=100),
                "waiting_on": waiting_on or None,
                "severity": _compact_text(item.get("severity"), limit=60)
                if isinstance(item, dict)
                else None,
                "gate": _compact_text(
                    redact_local_paths(str(raw_gate or "clear"), protected_paths=protected_paths),
                    limit=240,
                ),
                "next_action": _compact_text(
                    redact_local_paths(
                        str(raw_next_action or "Review the next bounded proposal."),
                        protected_paths=protected_paths,
                    )
                ),
                "top_todo": top_todo,
                "todos": todos,
                "evidence": [
                    _compact_text(redact_local_paths(value, protected_paths=protected_paths))
                    for value in evidence
                    if value
                ],
                "quota": {
                    "state": _compact_text(quota.get("state"), limit=80) or None,
                    "spent_slots": quota.get("spent_slots"),
                    "allowed_slots": quota.get("allowed_slots"),
                    "reason": _compact_text(quota.get("reason"), limit=240) or None,
                },
            }
        )
    effective_selected = selected_goal_id or (goals[0]["goal_id"] if len(goals) == 1 else None)
    return {
        "ok": bool(status_payload.get("ok", True)),
        "schema_version": "loopx_chat_status_v0",
        "selected_goal_id": effective_selected,
        "goal_count": len(goals),
        "goals": goals,
    }


def build_bounded_chat_status_projection(
    *,
    registry: dict[str, Any],
    selected_goal_id: str | None,
) -> dict[str, Any]:
    """Build Chat's first-screen projection from bounded local state only."""

    items: list[dict[str, Any]] = []
    for raw_goal in registry_goals(registry):
        goal_id = str(raw_goal.get("id") or "")
        project = Path(str(raw_goal.get("repo") or ".")).expanduser().resolve()
        state_path = resolve_state_file(project, raw_goal.get("state_file"))
        state_text = ""
        if state_path is not None and state_path.is_file():
            try:
                state_text = state_path.read_text(encoding="utf-8")
            except OSError:
                state_text = ""
        structured = build_active_state_structured_projection(
            state_text,
            goal_id=goal_id,
        )
        frontmatter = structured.get("frontmatter")
        frontmatter = frontmatter if isinstance(frontmatter, dict) else {}
        todos = structured.get("todos")
        todos = todos if isinstance(todos, dict) else {}
        user_todos = todos.get("user")
        user_todos = user_todos if isinstance(user_todos, dict) else {"items": [], "open_count": 0}
        agent_todos = todos.get("agent")
        agent_todos = agent_todos if isinstance(agent_todos, dict) else {"items": [], "open_count": 0}
        user_open = int(user_todos.get("open_count") or 0)
        agent_open = int(agent_todos.get("open_count") or 0)
        next_action = structured.get("next_action")
        next_action = next_action if isinstance(next_action, dict) else {}
        next_action_text = _compact_text(next_action.get("first"))
        waiting_on = _compact_text(frontmatter.get("waiting_on"), limit=80)
        if not waiting_on:
            if user_open:
                waiting_on = "user_or_controller"
            elif agent_open:
                waiting_on = "codex"
        gate = _compact_text(frontmatter.get("gate"), limit=120)
        if not gate:
            gate = "user_review_required" if user_open else "clear"
        items.append(
            {
                "goal_id": goal_id,
                "status": _compact_text(frontmatter.get("status"), limit=100)
                or _compact_text(raw_goal.get("status"), limit=100),
                "waiting_on": waiting_on or None,
                "severity": "action" if user_open or agent_open else "info",
                "recommended_action": next_action_text or "Review the next bounded proposal.",
                "user_todos": user_todos,
                "agent_todos": agent_todos,
                "project_asset": {
                    "gate": gate,
                    "next_action": next_action_text or "Review the next bounded proposal.",
                    "user_todos": user_todos,
                    "agent_todos": agent_todos,
                },
            }
        )
    return build_chat_status_projection(
        registry=registry,
        status_payload={"ok": True, "attention_queue": {"items": items}},
        selected_goal_id=selected_goal_id,
    )


def configured_lark_cli_bin(target_payload: Mapping[str, Any]) -> str | None:
    """Return one deterministic private Target CLI reference for discovery."""

    targets = target_payload.get("targets")
    if not isinstance(targets, Mapping):
        return None
    for target_name in sorted(str(name) for name in targets):
        target = targets.get(target_name)
        identity = target.get("identity") if isinstance(target, Mapping) else None
        cli_bin = (
            str(identity.get("cli_bin") or "").strip()
            if isinstance(identity, Mapping)
            else ""
        )
        if cli_bin:
            return cli_bin
    return None


def resolve_lark_cli_for_runtime(
    *,
    runtime_root: Path,
    explicit: str | None,
) -> LarkCliResolution:
    """Resolve the shared CLI while preserving configured Target priority."""

    target_cli_bin = configured_lark_cli_bin(
        read_goal_channel_targets(default_goal_channel_target_path(runtime_root))
    )
    return resolve_lark_cli(
        explicit=explicit,
        target_cli_bin=target_cli_bin,
    )


class ChatHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    registry_path: Path
    runtime_root_override: str | None
    scan_roots: list[Path]
    limit: int
    selected_goal_id: str | None
    codex_bin: str
    assets_dir: Path
    verbose: bool
    chat_store: ChatSessionStore
    action_store: ChatActionStore
    action_service: ChatActionService
    runtime_controller: ChatRuntimeController
    lark_runner: CommandRunner
    lark_cli_resolution: LarkCliResolution
    lark_app_setup_manager: LarkAppSetupManager
    lark_goal_topic_runtime: LarkGoalTopicRuntimeService
    ssh_config_path: Path | None
    goal_subagent_configuration_enabled: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.completed_todo_pages = CompletedTodoPages()

    def server_close(self) -> None:
        if hasattr(self, "lark_app_setup_manager"):
            self.lark_app_setup_manager.close()
        if hasattr(self, "manager_return_service"):
            self.manager_return_service.close()
        if hasattr(self, "lark_goal_topic_runtime"):
            self.lark_goal_topic_runtime.close()
        if hasattr(self, "runtime_controller"):
            self.runtime_controller.close()
        super().server_close()


class ChatRequestHandler(
    CompletedTodoRequestMixin,
    AttachedSessionRequestMixin,
    SshSourceRequestMixin,
    GoalSubagentConfigurationRequestMixin,
    LarkChatRequestMixin,
    config_api.ChatConfigurationRequestMixin,
    ChatStatusRequestMixin,
    BaseHTTPRequestHandler,
):
    server: ChatHTTPServer

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in chat_cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        message: str,
        *,
        status: int = 400,
        gate: dict[str, str] | None = None,
        error_code: str | None = None,
        session_invalidated: bool = False,
        turn_replay_safe: bool = False,
        todo_receipt: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"ok": False, "error": _compact_text(message)}
        if error_code:
            payload["error_code"] = _compact_text(error_code, limit=80)
        if gate:
            payload["gate"] = gate
        if session_invalidated:
            payload["session_invalidated"] = True
        if turn_replay_safe:
            payload["turn_replay_safe"] = True
        if todo_receipt:
            payload["todo_receipt"] = todo_receipt
        self._send_json(payload, status=status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise ValueError("request body is empty")
        if length > 64_000:
            raise ValueError("request body is too large")
        return parse_strict_json_object(self.rfile.read(length))

    def _require_loopback_origin(self) -> bool:
        if is_loopback_origin(self.headers.get("Origin")):
            return True
        self._send_error("LoopX Chat only accepts loopback browser origins.", status=403)
        return False

    def _ssh_hosts(self) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_error(
                "SSH Host discovery requires a loopback LoopX Chat server.",
                status=403,
            )
            return
        if not self._require_loopback_origin():
            return
        self._send_json(
            ssh_host_catalog_payload(getattr(self.server, "ssh_config_path", None))
        )

    def _registry_and_goal(self, goal_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = load_registry(self.server.registry_path)
        goal = next((item for item in registry_goals(registry) if str(item.get("id") or "") == goal_id), None)
        if goal is None:
            raise ValueError("goal_id was not found in the active LoopX registry")
        return registry, goal

    def _session_context(self, session: dict[str, object]) -> dict[str, object]:
        if is_manager_channel(session.get("channel_id")):
            return {"project": manager_workspace(self.server.chat_store.root, str(session["channel_id"])),
                    "objective": MANAGER_AGENT_OBJECTIVE, "title": "LoopX global manager"}
        registry, goal = self._registry_and_goal(str(session["goal_id"]))
        return _goal_public_context(registry, goal)

    def _serve_asset(self, path: str) -> None:
        relative = "index.html" if path in {"/chat", "/chat/"} else path.removeprefix("/chat/")
        root = self.server.assets_dir.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send_error("unknown asset", status=404)
            return
        if not target.is_file():
            self._send_error("unknown asset", status=404)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"{content_type}; charset=utf-8"
            if content_type.startswith("text/")
            else content_type,
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _create_session(self) -> None:
        try:
            body = self._read_json()
            unknown = set(body) - {"goal_id", "agent_id", "mode", "context_kind"}
            if unknown:
                raise ValueError("unknown session field")
            goal_id = _compact_text(body.get("goal_id"), limit=160) or self.server.selected_goal_id or ""
            # ``agent_id`` is the caller's explicit executor pick and stays empty
            # when the caller does not make one. Each channel then resolves its
            # own default through its own owner instead of inheriting whatever
            # executor this client happens to ship with.
            requested_endpoint = _compact_text(body.get("agent_id"), limit=80)
            mode = _compact_text(body.get("mode"), limit=40) or "resume_latest"
            context_kind = _compact_text(body.get("context_kind"), limit=40) or "goal"
            if context_kind not in {"goal", "manager"}:
                raise ValueError("context_kind must be goal or manager")
            if context_kind == "manager":
                goal_id = MANAGER_AGENT_GOAL_ID
                context = self._session_context({"channel_id": "manager"})
            else:
                registry, goal = self._registry_and_goal(goal_id)
                context = _goal_public_context(registry, goal)
            runtime_objective = str(context["objective"] or context["title"])
            project = context["project"]
            if not project.is_dir():
                raise CodexChatAgentError(
                    "The Goal project root is unavailable.",
                    gate={
                        "kind": "host_tool_gate",
                        "summary": "The Goal project root is unavailable on this host.",
                        "next_action": "Reconnect the Goal from its project root, then retry.",
                    },
                )
            session, resumed = open_chat_session(
                controller=self.server.runtime_controller,
                context_kind=context_kind,
                goal_id=goal_id,
                work_dir=project,
                objective=runtime_objective,
                mode=mode,
                requested_endpoint=requested_endpoint,
            )
        except CodexChatAgentError as exc:
            self._send_error(str(exc), status=424, gate=exc.gate, error_code=exc.error_code)
            return
        except Exception as exc:  # noqa: BLE001 - compact local validation error.
            self._send_error(str(exc))
            return
        public = self.server.chat_store.public_session(session)
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_session_v1",
                "session_id": public["session_id"],
                "goal_id": public["goal_id"],
                "agent_id": public.get("executor_endpoint_id") or requested_endpoint,
                "context_kind": context_kind,
                "resumed": resumed,
                "session": public,
            },
            status=200 if resumed else 201,
        )

    def _record_projection_exchange(self) -> None:
        """Persist a status-only exchange in the same owner-local message store."""
        try:
            body = self._read_json()
            unknown = set(body) - {"goal_id", "context_kind", "question", "answer"}
            if unknown:
                raise ValueError("unknown projection message field")
            context_kind = _compact_text(body.get("context_kind"), limit=40) or "manager"
            if context_kind not in {"goal", "manager"}:
                raise ValueError("context_kind must be goal or manager")
            goal_id = _compact_text(body.get("goal_id"), limit=160)
            if context_kind == "goal" and not goal_id:
                raise ValueError("goal_id is required for Goal projection messages")
            question = str(body.get("question") or "").strip()
            answer = str(body.get("answer") or "").strip()
            if not question or not answer:
                raise ValueError("question and answer are required")
            if len(question) > 20_000 or len(answer) > 100_000:
                raise ValueError("projection message is too large")
            channel_id = "manager" if context_kind == "manager" else f"goal.{goal_id}"
            session = self.server.chat_store.latest_session(
                goal_id=None if context_kind == "manager" else goal_id,
                agent_id="status-only",
                channel_id=channel_id,
            )
            if session is None:
                session = self.server.chat_store.create_session(
                    goal_id=goal_id or MANAGER_AGENT_GOAL_ID,
                    agent_id="status-only",
                    adapter_kind="status_projection",
                    upstream_thread_id="local-status-projection",
                    upstream_mode="projection",
                    channel_id=channel_id,
                )
            session_id = str(session["session_id"])
            self.server.chat_store.append_message(session_id, role="user", text=question)
            self.server.chat_store.append_message(session_id, role="agent", text=answer)
            self.server.chat_store.update_session(session_id, last_activity_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        except Exception as exc:  # noqa: BLE001 - local validation response.
            self._send_error(str(exc))
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_projection_exchange_v1",
                "session_id": session_id,
            },
            status=201,
        )

    def _session_turn(self, session_id: str) -> None:
        session = self.server.chat_store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            self._send_error(
                "chat session was not found",
                status=404,
                session_invalidated=True,
                turn_replay_safe=True,
            )
            return
        try:
            body = self._read_json()
            if set(body) - {"message", "client_turn_id", "attachments"}:
                raise ValueError("unknown turn field")
            message = str(body.get("message") or "").strip()
            if not message:
                raise ValueError("message is required")
            attachments = normalize_chat_image_attachments(body.get("attachments"))
            client_turn_id = _compact_text(body.get("client_turn_id"), limit=160) or uuid.uuid4().hex
            context = self._session_context(session)
            runtime_objective = str(context["objective"] or context["title"])
            turn, created = self.server.runtime_controller.submit_turn(
                session_id=session_id,
                client_turn_id=client_turn_id,
                message=message,
                attachments=attachments,
                work_dir=context["project"],
                objective=runtime_objective,
            )
            if body.get("client_turn_id"):
                turn_id = str(turn["turn_id"])
                self._send_json(
                    {
                        "ok": True,
                        "schema_version": "loopx_chat_turn_accepted_v1",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "created": created,
                        "status": turn["status"],
                        "events_url": f"{CHAT_SESSIONS_PATH}/{session_id}/turns/{turn_id}/events",
                    },
                    status=202,
                )
                return
            completed = self.server.runtime_controller.wait_for_turn(
                session_id=session_id,
                turn_id=str(turn["turn_id"]),
            )
            if completed.get("status") != "completed":
                self._send_error(
                    str(completed.get("error") or "Agent turn failed."),
                    status=424,
                    gate=(
                        completed.get("gate")
                        if isinstance(completed.get("gate"), dict)
                        else None
                    ),
                )
                return
            response = completed.get("response")
        except CodexChatAgentError as exc:
            self._send_error(
                str(exc),
                status=424,
                gate=exc.gate,
            )
            return
        except RuntimeError as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": "another turn is already running for this session",
                    "active_turn_id": str(exc),
                },
                status=409,
            )
            return
        except KeyError:
            self._send_error("chat session was not found", status=404, session_invalidated=True)
            return
        except Exception as exc:  # noqa: BLE001 - compact local validation error.
            self._send_error(str(exc))
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_turn_v0",
                "session_id": session_id,
                "goal_id": session["goal_id"],
                "response": response,
            }
        )

    def _session_snapshot(self, session_id: str) -> None:
        try:
            self._send_json(project_chat_session_snapshot(
                self.server.runtime_root, self.server.chat_store, session_id))
        except KeyError:
            self._send_error("chat session was not found", status=404)

    def _list_sessions(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        goal_id = _compact_text((query.get("goal_id") or [""])[0], limit=160) or None
        agent_id = _compact_text((query.get("agent_id") or [""])[0], limit=80) or None
        channel_id = _compact_text((query.get("channel_id") or [""])[0], limit=160) or None
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_session_list_v1",
                "sessions": self.server.chat_store.list_sessions(
                    goal_id=goal_id,
                    agent_id=agent_id,
                    channel_id=channel_id,
                ),
            }
        )

    def _turn_events(self, session_id: str, turn_id: str) -> None:
        if self.server.chat_store.load_turn(session_id, turn_id) is None:
            self._send_error("chat turn was not found", status=404)
            return
        last_event_id = self.headers.get("Last-Event-ID")
        query = parse_qs(urlparse(self.path).query)
        if not last_event_id:
            last_event_id = (query.get("after") or [None])[0]
        if str(last_event_id or "0") != "0":
            turn = self.server.chat_store.load_turn(session_id, turn_id) or {}
            self.server.chat_store.update_turn(
                session_id,
                turn_id,
                sse_reconnect_count=int(turn.get("sse_reconnect_count") or 0) + 1,
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in chat_cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()
        cursor = str(last_event_id or "0")
        heartbeat_at = time.monotonic()
        try:
            while True:
                events = self.server.chat_store.events_after(session_id, turn_id, cursor)
                for event in events:
                    cursor = str(event["event_id"])
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(
                        f"id: {cursor}\nevent: {event['kind']}\ndata: {data}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                turn = self.server.chat_store.load_turn(session_id, turn_id)
                if turn is None or (turn.get("status") in TERMINAL_TURN_STATES and not events):
                    self.close_connection = True
                    break
                if time.monotonic() - heartbeat_at >= 15.0:
                    heartbeat = json.dumps({"kind": "heartbeat", "created_at": time.time()})
                    self.wfile.write(f"event: heartbeat\ndata: {heartbeat}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    heartbeat_at = time.monotonic()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _interrupt_turn(self, session_id: str, turn_id: str) -> None:
        try:
            turn = self.server.runtime_controller.interrupt_turn(session_id=session_id, turn_id=turn_id)
        except KeyError:
            self._send_error("chat turn was not found", status=404)
            return
        except CodexChatAgentError as exc:
            self._send_error(str(exc), status=424, error_code=exc.error_code, gate=exc.gate)
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_turn_interrupt_v1",
                "session_id": session_id,
                "turn_id": turn_id,
                "status": turn["status"],
            }
        )

    def _resume_session(self, session_id: str) -> None:
        try:
            session = self.server.chat_store.load_session(session_id)
            if session is None:
                raise KeyError("chat session was not found")
            context = self._session_context(session)
            objective = str(context["objective"] or context["title"])
            restored = self.server.runtime_controller.resume_session(
                session_id=session_id,
                work_dir=context["project"],
                objective=objective,
            )
        except KeyError:
            self._send_error("chat session was not found", status=404)
            return
        except CodexChatAgentError as exc:
            self._send_error(str(exc), status=424, error_code=exc.error_code, gate=exc.gate)
            return
        except Exception as exc:  # noqa: BLE001 - compact local validation error.
            self._send_error(str(exc), status=424, error_code="resume_failed")
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_session_resume_v1",
                "session": self.server.chat_store.public_session(restored),
            }
        )

    def _todo(self, *, apply: bool) -> None:
        try:
            body = self._read_json()
            allowed = {"goal_id", "text", "preview_id"} if apply else {"goal_id", "text"}
            if set(body) - allowed:
                raise ValueError("unknown Todo review field")
            goal_id = _compact_text(body.get("goal_id"), limit=160)
            text = str(body.get("text") or "")
            if not goal_id:
                raise ValueError("goal_id is required")
            self._registry_and_goal(goal_id)
            if apply:
                payload = apply_todo_review_preview(
                    registry_path=self.server.registry_path,
                    goal_id=goal_id,
                    text=text,
                    preview_id=str(body.get("preview_id") or ""),
                )
            else:
                payload = build_todo_review_preview(
                    registry_path=self.server.registry_path,
                    goal_id=goal_id,
                    text=text,
                )
        except TodoReviewPreviewConflict as exc:
            self._send_error(str(exc), status=409, todo_receipt=exc.receipt)
            return
        except ValueError as exc:
            self._send_error(str(exc), status=400)
            return
        except Exception:
            self._send_error("Todo review could not be completed.", status=400)
            return
        self._send_json(payload)

    def _goal_channel_extension_ready(self) -> str | None:
        try:
            registry = load_registry(self.server.registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                self.server.runtime_root_override,
                registry_path=self.server.registry_path,
            )
            resolve_extension_activation(
                LARK_EXTENSION_ID,
                state_file=default_extension_state_file(runtime_root),
                required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,),
            )
        except Exception:
            return "install, enable, and doctor the bundled LoopX Lark extension"
        return None

    def _goal_channel_targets(self) -> None:
        try:
            packet = list_goal_channel_targets(target_path=self._goal_channel_target_path())
        except Exception:
            self._send_error("Goal Channel targets could not be listed.", status=400)
            return
        details = packet.get("details")
        items = details.get("items") if isinstance(details, dict) else None
        self._send_json({"ok": True, "targets": items if isinstance(items, list) else []})

    def _goal_channel_setup(self) -> None:
        try:
            body = self._read_json()
            if set(body) - {"goal_id", "target", "execute"}:
                raise ValueError("unknown Goal Channel setup field")
            goal_id = _compact_text(body.get("goal_id"), limit=160)
            target_name = _compact_text(body.get("target"), limit=120)
            execute = body.get("execute") is True
            if not goal_id or not target_name:
                raise ValueError("goal_id and target are required")
            source_registry, binding_path = self._goal_channel_context(goal_id)
            extension_blocker = self._goal_channel_extension_ready()
            if extension_blocker is not None:
                self._send_error(extension_blocker, status=400, error_code="extension_unavailable")
                return
            provider_target = goal_channel_target_for_name(
                read_goal_channel_targets(self._goal_channel_target_path()),
                target_name,
            )
            if provider_target is None:
                self._send_error(
                    "configure the named shared provider target first",
                    status=400,
                    error_code="provider_target_missing",
                )
                return
            packet = setup_lark_goal_channel(
                registry=source_registry,
                registry_path=self.server.registry_path,
                goal_id=goal_id,
                binding_path=binding_path,
                target_name=target_name,
                provider_target=provider_target,
                execute=execute,
            )
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_goal_channel_setup")
            return
        except Exception:
            self._send_error(
                "the Goal Channel operation failed before a verified provider receipt",
                status=400,
                error_code="provider_api_failed",
            )
            return
        if not packet.get("ok"):
            packet["error"] = _compact_text(
                packet.get("public_summary") or packet.get("blocker") or "Goal Channel setup failed"
            )
        self._send_json(packet, status=200 if packet.get("ok") else 400)

    def _goal_channel_configure(self) -> None:
        try:
            body = self._read_json()
            if set(body) - {"goal_id", "auto_notify_human_gates"}:
                raise ValueError("unknown Goal Channel configure field")
            goal_id = _compact_text(body.get("goal_id"), limit=160)
            auto_notify = body.get("auto_notify_human_gates")
            if not goal_id or not isinstance(auto_notify, bool):
                raise ValueError("goal_id and auto_notify_human_gates are required")
            source_registry, binding_path = self._goal_channel_context(goal_id)
            if auto_notify:
                extension_blocker = self._goal_channel_extension_ready()
                if extension_blocker is not None:
                    self._send_error(extension_blocker, status=400, error_code="extension_unavailable")
                    return
            packet = configure_lark_goal_channel_automation(
                registry=source_registry,
                goal_id=goal_id,
                binding_path=binding_path,
                human_gate_auto_notify=auto_notify,
                execute=True,
            )
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_goal_channel_configure")
            return
        except Exception:
            self._send_error(
                "the Goal Channel automation setting could not be updated",
                status=400,
                error_code="provider_api_failed",
            )
            return
        if not packet.get("ok"):
            packet["error"] = _compact_text(
                packet.get("public_summary") or packet.get("blocker") or "Goal Channel configure failed"
            )
        self._send_json(packet, status=200 if packet.get("ok") else 400)

    def _action_preview(self) -> None:
        try:
            proposal = self.server.action_service.preview(self._read_json())
        except ProtectedActionGate as exc:
            self._send_json(
                {
                    "ok": False,
                    "schema_version": "loopx_chat_action_preview_gate_v1",
                    "error": "This preview needs an explicit public-safe selection.",
                    "error_code": "action_preview_gate",
                    "gate": exc.gate,
                    "write_attempted": False,
                },
                status=409,
            )
            return
        except ActionConflictError as exc:
            self._send_error(str(exc), status=409, error_code="action_conflict")
            return
        except (KeyError, ValueError) as exc:
            self._send_error(str(exc), status=400, error_code="invalid_action_preview")
            return
        except Exception:
            self._send_error(
                "Typed action preview could not be created.",
                status=400,
                error_code="action_preview_failed",
            )
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_preview_v1",
                "proposal": proposal,
            },
            status=201,
        )

    def _action_not_found(self) -> None:
        self._send_error(
            "typed Chat action proposal was not found",
            status=404,
            error_code="action_not_found",
        )

    def _action_snapshot(self, proposal_id: str) -> None:
        try:
            proposal = self.server.action_service.load(proposal_id)
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_proposal_id")
            return
        if proposal is None:
            self._action_not_found()
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_v1",
                "proposal": proposal,
            }
        )

    def _action_list(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        goal_id = _compact_text((query.get("goal_id") or [""])[0], limit=200) or None
        context_kind = _compact_text(
            (query.get("context_kind") or [""])[0], limit=200
        ) or None
        status = _compact_text((query.get("status") or [""])[0], limit=80) or None
        try:
            proposals = self.server.action_store.list(
                goal_id=goal_id,
                context_kind=context_kind,
                status=status,
            )
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_action_query")
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_list_v1",
                "proposals": proposals,
            }
        )

    def _action_cancel(self, proposal_id: str) -> None:
        try:
            body = self._read_json()
            if body:
                raise ValueError("action cancel request must be empty")
            proposal = self.server.action_service.cancel(proposal_id)
        except KeyError:
            self._action_not_found()
            return
        except ActionConflictError as exc:
            self._send_error(str(exc), status=409, error_code="action_conflict")
            return
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_action_cancel")
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_cancel_v1",
                "proposal": proposal,
            }
        )

    def _action_transition(self, proposal_id: str, transition: str) -> None:
        try:
            body = self._read_json()
            if body:
                raise ValueError("action transition request must be empty")
            if transition == "regenerate":
                proposal = self.server.action_service.regenerate(proposal_id)
                status = 201
            elif transition == "reject":
                proposal = self.server.action_service.reject(proposal_id)
                status = 200
            elif transition == "defer":
                proposal = self.server.action_service.defer(proposal_id)
                status = 200
            else:
                raise ValueError("unsupported action transition")
        except KeyError:
            self._action_not_found()
            return
        except ActionConflictError as exc:
            self._send_error(str(exc), status=409, error_code="action_conflict")
            return
        except ValueError as exc:
            self._send_error(str(exc), status=400, error_code="invalid_action_transition")
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_transition_v1",
                "proposal": proposal,
            },
            status=status,
        )

    def _action_apply(self, proposal_id: str) -> None:
        try:
            body = self._read_json()
            if body:
                raise ValueError("action apply request must be empty")
            result = self.server.action_service.apply(proposal_id)
        except ProtectedActionGate as exc:
            try:
                proposal = self.server.action_store.mark_gated(proposal_id, gate=exc.gate)
            except (ActionConflictError, KeyError, ValueError):
                proposal = self.server.action_store.load(proposal_id)
            self._send_json(
                {
                    "ok": False,
                    "schema_version": "loopx_chat_action_gate_v1",
                    "error": "This action requires a protected canonical LoopX transition.",
                    "error_code": "protected_action",
                    "gate": exc.gate,
                    "proposal": proposal,
                    "write_attempted": False,
                },
                status=409,
            )
            return
        except KeyError:
            self._action_not_found()
            return
        except ActionConflictError as exc:
            self._send_error(str(exc), status=409, error_code="action_conflict")
            return
        except ValueError as exc:
            try:
                self.server.action_store.mark_failed(
                    proposal_id,
                    error_code="invalid_canonical_transition",
                    message=str(exc),
                )
            except (ActionConflictError, KeyError, ValueError):
                pass
            self._send_error(str(exc), status=400, error_code="invalid_action_apply")
            return
        except Exception:
            try:
                self.server.action_store.mark_failed(
                    proposal_id,
                    error_code="canonical_action_failed",
                    message="Canonical LoopX service did not complete the transition.",
                )
            except (ActionConflictError, KeyError, ValueError):
                pass
            self._send_error(
                "Typed action could not be applied through its canonical LoopX service.",
                status=424,
                error_code="canonical_action_failed",
            )
            return
        proposal = result["proposal"]
        if proposal.get("status") == "stale":
            self._send_json(
                {
                    "ok": False,
                    "schema_version": "loopx_chat_action_stale_v1",
                    "error": "The source state changed; regenerate the action preview.",
                    "error_code": "action_stale",
                    "proposal": proposal,
                    "write_attempted": False,
                },
                status=409,
            )
            return
        self._send_json(
            {
                "ok": True,
                "schema_version": "loopx_chat_action_apply_v1",
                "proposal": proposal,
                "turn": result.get("turn"),
                "gate": result.get("gate"),
            },
            status=202 if result.get("turn") else 200,
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        if path == CHAT_CAPABILITIES_PATH:
            # The steward section is composed by its own owner so the model
            # arguments, the availability verdict and the readback cannot
            # disagree about which executor and credential they describe.
            capabilities = {
                "ok": True,
                "schema_version": "loopx_chat_capabilities_v1",
                "manager": manager_capabilities_projection(
                    self.server.runtime_controller, self.server.chat_store
                ),
                "runtime_identity": release_runtime_identity(),
                "agent_backend": "multi_adapter",
                "sandbox": "read-only",
                "approval_policy": "never",
                "todo_write": "preview_locked",
                "goal_id": self.server.selected_goal_id,
                "streaming": True,
                "resume": True,
                "interrupt": True,
                "typed_actions": True,
                "attached_session_broker": True,
                "action_kinds": sorted(ACTION_KINDS),
                "adapters": self.server.runtime_controller.capabilities(),
                "lark_cli": self.server.lark_cli_resolution.public_snapshot(),
            }
            add_goal_subagent_capability(capabilities, server=self.server)
            self._send_json(capabilities)
            return
        if path == CHAT_ENDPOINTS_PATH:
            return self._send_json(
                {
                    "ok": True,
                    "schema_version": "loopx_chat_endpoint_list_v1",
                    "endpoints": self.server.runtime_controller.capabilities(),
                }
            )
        get_dispatch = {
            "/api/chat/completed-todos": self._completed_todos,
            CHAT_SESSIONS_PATH: self._list_sessions,
            CHAT_ACTIONS_PATH: self._action_list,
            CHAT_GOAL_CONTEXTS_PATH: self._goal_contexts,
            CHAT_LARK_APPS_PATH: self._lark_apps,
            CHAT_LARK_CHATS_PATH: self._lark_chats,
            CHAT_LARK_CONNECTIONS_PATH: self._lark_connections,
            CHAT_GOAL_CHANNEL_TARGETS_PATH: self._goal_channel_targets,
            **self._configuration_get_routes(),
            DEFAULT_CHAT_STATUS_PATH: self._status,
            "/api/chat/delivery-review": self._delivery_review,
            SSH_HOST_CATALOG_PATH: self._ssh_hosts,
        }
        if path in get_dispatch:
            return get_dispatch[path]()
        setup_parts = path.strip("/").split("/")
        if len(setup_parts) == 5 and setup_parts[:4] == ["api", "chat", "lark", "app-setups"]:
            return self._lark_setup_snapshot(setup_parts[4])
        action_parts = path.strip("/").split("/")
        if len(action_parts) == 3 and action_parts[:2] == ["api", "actions"]:
            return self._action_snapshot(action_parts[2])
        session_parts = path.strip("/").split("/")
        if len(session_parts) == 5 and session_parts[:3] == ["api", "chat", "sessions"] and session_parts[4] == "loopx":
            return handle_loopx_request(self, session_parts[3])
        if len(session_parts) == 4 and session_parts[:3] == ["api", "chat", "sessions"]:
            return self._session_snapshot(session_parts[3])
        if len(session_parts) == 7 and session_parts[:3] == ["api", "chat", "sessions"] and session_parts[4] == "turns" and session_parts[6] == "events":
            return self._turn_events(session_parts[3], session_parts[5])
        if path == "/":
            self.send_response(302)
            self.send_header("Location", DEFAULT_CHAT_PATH)
            return self.end_headers()
        if path == "/chat" or path.startswith("/chat/"):
            return self._serve_asset(path)
        self._send_error("unknown path", status=404)

    def do_POST(self) -> None:
        if not self._require_loopback_origin():
            return
        path = urlparse(self.path).path
        post_dispatch = {
            CHAT_SESSIONS_PATH: self._create_session,
            CHAT_ATTACH_SESSION_PATH: self._attach_session,
            CHAT_PROJECTION_MESSAGES_PATH: self._record_projection_exchange,
            CHAT_ACTION_PREVIEW_PATH: self._action_preview,
            CHAT_TODO_DRY_RUN_PATH: lambda: self._todo(apply=False),
            CHAT_TODO_APPLY_PATH: lambda: self._todo(apply=True),
            CHAT_GOAL_CHANNEL_SETUP_PATH: self._goal_channel_setup,
            CHAT_GOAL_CHANNEL_CONFIGURE_PATH: self._goal_channel_configure,
            CHAT_LARK_APP_SETUPS_PATH: self._lark_setup_start,
            CHAT_LARK_CONNECTIONS_PATH: self._lark_connect,
            **self._configuration_post_routes(),
            **self._ssh_source_post_routes(),
        }
        add_goal_subagent_routes(post_dispatch, handler=self)
        if path in post_dispatch:
            return post_dispatch[path]()
        session_action_parts = path.strip("/").split("/")
        if len(session_action_parts) == 5 and session_action_parts[:3] == ["api", "chat", "sessions"] and session_action_parts[4] == "loopx":
            return handle_loopx_request(self, session_action_parts[3], apply=True)
        if len(session_action_parts) == 5 and session_action_parts[:3] == ["api", "chat", "sessions"] and session_action_parts[4] == "resume":
            return self._resume_session(session_action_parts[3])
        action_parts = path.strip("/").split("/")
        if len(action_parts) == 4 and action_parts[:2] == ["api", "actions"] and action_parts[3] in {"apply", "cancel", "regenerate", "reject", "defer"}:
            if action_parts[3] == "apply":
                return self._action_apply(action_parts[2])
            if action_parts[3] == "cancel":
                return self._action_cancel(action_parts[2])
            return self._action_transition(action_parts[2], action_parts[3])
        prefix = f"{CHAT_SESSIONS_PATH}/"
        if path.startswith(prefix) and path.endswith("/turns"):
            session_id = path[len(prefix) : -len("/turns")].strip("/")
            return self._session_turn(session_id)
        parts = path.strip("/").split("/")
        if len(parts) == 7 and parts[:3] == ["api", "chat", "sessions"] and parts[4] == "turns" and parts[6] == "interrupt":
            return self._interrupt_turn(parts[3], parts[5])
        self._send_error("unknown path", status=404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        for key, value in chat_cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()

    def do_DELETE(self) -> None:
        if not self._require_loopback_origin():
            return
        path = urlparse(self.path).path
        setup_parts = path.strip("/").split("/")
        if len(setup_parts) == 5 and setup_parts[:4] == ["api", "chat", "lark", "app-setups"]:
            return self._lark_setup_cancel(setup_parts[4])
        if path == CHAT_LARK_CONNECTIONS_PATH:
            return self._lark_disconnect()
        prefix = f"{CHAT_SESSIONS_PATH}/"
        if not path.startswith(prefix):
            return self._send_error("unknown path", status=404)
        session_id = path[len(prefix) :].strip("/")
        self._close_session(session_id)

    def log_message(self, format: str, *args: object) -> None:
        if self.server.verbose:
            super().log_message(format, *args)


def serve_chat(
    *,
    registry_path: Path | None = None,
    runtime_root_override: str | Path | None = None,
    scan_roots: list[Path] | None = None,
    limit: int = 20,
    host: str = DEFAULT_CHAT_HOST,
    port: int = DEFAULT_CHAT_PORT,
    goal_id: str | None = None,
    codex_bin: str = "codex",
    claude_bin: str = "claude",
    kiro_cli_bin: str = KIRO_CLI_BIN,
    lark_cli_bin: str | None = None,
    startup_timeout_sec: float = 30.0,
    idle_timeout_sec: float = 180.0,
    hard_timeout_sec: float = 900.0,
    assets_dir: Path | None = None,
    open_browser: bool = False,
    verbose: bool = False,
    enable_goal_subagent_configuration: bool = False,
) -> None:
    if not is_loopback_host(host):
        raise ValueError("loopx chat requires a loopback --host such as 127.0.0.1")
    resolved_assets = (assets_dir or default_chat_assets_dir()).expanduser().resolve()
    if not (resolved_assets / "index.html").is_file():
        raise FileNotFoundError("LoopX Chat web assets are unavailable; reinstall LoopX or rebuild the chat bundle")
    resolved_registry_path = registry_path or (Path.home() / ".loopx" / "registry.json")
    resolved_runtime_root_override = str(runtime_root_override) if runtime_root_override else None
    resolved_scan_roots = scan_roots if scan_roots is not None else [Path.cwd()]
    registry = load_registry(resolved_registry_path) if resolved_registry_path.exists() else {}
    runtime_root = resolve_runtime_root(
        registry,
        resolved_runtime_root_override,
        registry_path=resolved_registry_path,
    )
    lark_cli_resolution = resolve_lark_cli_for_runtime(
        runtime_root=runtime_root,
        explicit=lark_cli_bin,
    )
    server = ChatHTTPServer((host, port), ChatRequestHandler)
    server.registry_path = resolved_registry_path
    server.runtime_root = runtime_root
    server.runtime_root_override = resolved_runtime_root_override
    server.scan_roots = resolved_scan_roots
    server.limit = limit
    server.selected_goal_id = goal_id
    server.codex_bin = codex_bin
    server.assets_dir = resolved_assets
    server.verbose = verbose
    server.ssh_config_path = None
    server.goal_subagent_configuration_enabled = (
        enable_goal_subagent_configuration
    )
    server.lark_cli_resolution = lark_cli_resolution
    server.lark_runner = build_lark_command_runner(server.lark_cli_resolution)
    server.lark_app_setup_manager = LarkAppSetupManager(
        cli_resolution=server.lark_cli_resolution,
        profile_verifier=lambda app_ref: any(
            app.get("app_ref") == app_ref and app.get("ready") is True
            for app in list_lark_apps(
                runner=server.lark_runner,
                cli_bin=server.lark_cli_resolution.command or "lark-cli",
            )
        )
    )
    server.chat_store = ChatSessionStore(runtime_root)
    server.action_store = ChatActionStore(runtime_root / "chat" / "actions")
    server.runtime_controller = ChatRuntimeController(
        store=server.chat_store,
        registry_path=resolved_registry_path,
        manager_scope_resolver=lambda session: authorized_manager_goal_ids(
            build_lark_goal_topic_runtime_snapshot(
                registry_path=server.registry_path, runtime_root_override=server.runtime_root_override,
            ), session, runtime_root=runtime_root,
        ),
        codex_bin=codex_bin,
        claude_bin=claude_bin,
        kiro_cli_bin=kiro_cli_bin,
        startup_timeout_sec=startup_timeout_sec,
        idle_timeout_sec=idle_timeout_sec,
        hard_timeout_sec=hard_timeout_sec,
    )
    server.action_service = ChatActionService(
        store=server.action_store,
        registry_path=resolved_registry_path,
        chat_store=server.chat_store,
        runtime_controller=server.runtime_controller,
        workspace_roots=resolved_scan_roots,
    )
    # An admitted steward team preview is projected into the typed action store,
    # because that store is what the product surfaces list: the chat action
    # service owns it, so the channel hands the preview to that owner instead of
    # teaching the runtime a second place where cards live.
    server.runtime_controller.team_plan_projector = (
        server.action_service.project_team_plan_preview
    )
    server.lark_goal_topic_runtime = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: build_lark_goal_topic_runtime_snapshot(
            registry_path=server.registry_path,
            runtime_root_override=server.runtime_root_override,
        ),
        runtime_root=runtime_root,
        runtime_controller=server.runtime_controller,
        action_service=server.action_service,
        manager_route_reconciler=lambda route: reconcile_lark_manager_route(
            route=route,
            registry_path=server.registry_path,
            runtime_root_override=server.runtime_root_override,
            runtime_controller=server.runtime_controller,
        ),
    )
    server.lark_goal_topic_runtime.start()
    from .extensions.lark.manager_returns import start_return_service
    server.manager_return_service = start_return_service(server, runtime_root)
    url = f"http://{host}:{port}{DEFAULT_CHAT_PATH}"
    print(f"Serving LoopX Chat at {url}", flush=True)
    print("Agent boundary: local adapters, read-only sandbox, approval policy never", flush=True)
    print("Todo writes: preview-locked on loopback", flush=True)
    if enable_goal_subagent_configuration:
        print("Goal sub-agent configuration: preview-locked opt-in enabled", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping LoopX Chat", flush=True)
    finally:
        server.server_close()
