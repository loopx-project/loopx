"""In-process transport to the existing native command owners.

No stdout redirection or subprocess: original Chat workers can run concurrently.
Arguments are constructed by the host, never accepted from a model or HTTP body.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .steward_triage import (
    StewardFastPathError,
    build_portfolio_triage_projection,
    execute_steward_fast_path,
    parse_steward_fast_path_intent,
    resolve_all_registered_goal_ids,
)


class NativeStewardCommandError(ValueError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("the native steward operation was not committed: " + str(
            payload.get("error_code") or "unavailable") + ": " + str(
            payload.get("error") or payload.get("reason") or payload.get("message") or "inspect the native receipt"))


def _triage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward-triage", add_help=False)
    parser.add_argument("--goal-id", action="append", default=[])
    parser.add_argument("--all-goals", action="store_true")
    parser.add_argument("--focus", default="all")
    parser.add_argument("--max-gates", type=int, default=8)
    parser.add_argument("--max-todos", type=int, default=8)
    parser.add_argument("--max-progress", type=int, default=5)
    return parser


def native_fast_path_triage(
    controller: Any,
    *,
    text: str,
    binding_id: str,
    source_ref: str,
    sender_ref: str,
    bindings: Any = None,
    private_human_message: bool = True,
    origin: str | None = None,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a read-only Steward Fast-Path triage query without leases or Turns."""

    active_bindings = bindings if bindings is not None else getattr(controller, "conversation_bindings", None)
    if active_bindings is None:
        raise StewardFastPathError(
            "steward conversation bindings are unavailable",
            code="steward_bindings_unavailable",
        )
    return execute_steward_fast_path(
        text=text,
        bindings=active_bindings,
        binding_id=binding_id,
        source_ref=source_ref,
        sender_ref=sender_ref,
        registry_path=controller.registry_path,
        runtime_root_arg=str(controller.coordination_runtime_root),
        private_human_message=private_human_message,
        origin=origin,
        session_context=session_context,
    )


def native_command(controller: Any, argv: list[str]) -> dict[str, Any]:
    if argv and argv[0] == "triage":
        try:
            parsed = _triage_parser().parse_args(argv[1:])
            authorized = (
                resolve_all_registered_goal_ids(controller.registry_path)
                if parsed.all_goals
                else list(parsed.goal_id)
            )
            return build_portfolio_triage_projection(
                registry_path=controller.registry_path,
                runtime_root_arg=str(controller.coordination_runtime_root),
                authorized_goal_ids=authorized,
                requested_goal_ids=list(parsed.goal_id) or None,
                focus=parsed.focus,
                max_gates=parsed.max_gates,
                max_todos=parsed.max_todos,
                max_progress=parsed.max_progress,
            )
        except (StewardFastPathError, SystemExit, ValueError) as exc:
            error_code = getattr(exc, "code", "invalid_triage_command")
            raise NativeStewardCommandError(
                {"ok": False, "error_code": error_code, "error": str(exc)}
            ) from exc

    if not argv or argv[0] not in {"quota", "todo", "task-lease"}:
        raise ValueError("unsupported native steward operation")

    from ...cli_runtime import _build_selected_parser
    from ...cli_rollout import append_cli_rollout_event
    from ...cli_commands.quota import handle_quota_command
    from ...cli_commands.todo import handle_todo_command
    from ...cli_commands.task_lease import handle_task_lease_command

    args = _build_selected_parser(argv[0]).parse_args(["--format", "json", *argv])
    code: int | None
    result: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"registry_path": controller.registry_path,
        "runtime_root_arg": str(controller.coordination_runtime_root),
        "print_payload": lambda payload, *_args: result.update(payload)}
    if args.command == "todo":
        code = handle_todo_command(args, **kwargs, format_name="json",
            append_cli_rollout_event=append_cli_rollout_event)
    elif args.command == "quota":
        code = handle_quota_command(args, **kwargs,
            append_cli_rollout_event=append_cli_rollout_event)
    else:
        code = handle_task_lease_command(args, **kwargs, output_format=lambda *_a, **_kw: "json")
    if code or result.get("ok") is not True:
        raise NativeStewardCommandError(result)
    return result


def acquire_steward_task_lease(
    controller: Any,
    request: Mapping[str, Any],
    turn_id: str,
    *,
    recovery: bool,
    admission_retry: bool,
    claim_args: list[str] | None,
) -> dict[str, Any]:
    """Use the original claim or lease for native Steward work."""
    actor = request["actor"]
    goal_args = ["--goal-id", actor["goal_id"]]
    todo_args = [*goal_args, "--todo-id", request["todo_id"]]
    if claim_args is not None:
        operation_id = f"todo-claim:native-turn:{turn_id}"
        claim = native_command(controller, [*claim_args, "--claim-operation-id", operation_id])
        if claim.get("claim_operation_id") != operation_id:
            raise ValueError("native task preparation did not return its original claim")
        receipts = native_command(controller, ["todo", "receipt", *goal_args, "--operation-id", operation_id])["receipts"]
        lease = receipts[0]["result"].get("lease") if len(receipts) == 1 else None
        if not isinstance(lease, dict):
            raise ValueError("native task preparation could not read back its original lease")
        return lease

    observed = native_command(controller, ["task-lease", "inspect", *todo_args])
    retained = observed.get("lease")
    if request.get("resume_turn_key") and not recovery:
        # The launch guard independently verifies the original generation.
        if (observed.get("active") is not True or not isinstance(retained, dict)
                or retained.get("owner") != actor["agent_id"]
                or retained.get("idempotency_key") != request["turn_instance_id"]):
            raise ValueError("the original report launch lease is no longer active")
        return retained

    version = retained["version"] if retained else 0
    suffix = (".validation-recovery." + str(version) if recovery else
        ".admission-retry." + turn_id if admission_retry else "")
    return native_command(controller, ["task-lease", "acquire", *todo_args, "--owner", actor["agent_id"],
        "--idempotency-key", request["turn_instance_id"] + suffix, "--ttl-seconds", "3600",
        "--expected-version", str(version)])["lease"]


_AUDIENCE_KEYS = ("binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref")


def _commission_goal_identity(goal: Mapping[str, Any]) -> dict[str, str]:
    identity: dict[str, str] = {}
    for field in ("goal_instance_id", "creation_operation_id"):
        value = str(goal.get(field) or "").strip()
        if value:
            identity[field] = value
    return identity


def _steward_proposal_context(context: Mapping[str, Any], created_at: str, *, dt_cls: Any) -> dict[str, Any]:
    expiry = (dt_cls.fromisoformat(created_at) + timedelta(minutes=15)).isoformat()
    audience = {key: context[key] for key in _AUDIENCE_KEYS}
    return {"kind": "manager", "goal_id": "loopx-manager", **audience, "expires_at": expiry}


class ExternalStewardCommissionMixin:
    if TYPE_CHECKING:
        controller: Any
        bindings: Any
        root: Path
        actions: Any | None

        def _recovery_rows(self) -> list[dict[str, Any]]: ...
        def native_result_reply(self, request_ref: str) -> str | None: ...

    def commission_evidence(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """Read original Core execution facts for this exact bound audience.

        Native terminal status/result delivery is separate from Goal/Todo
        acceptance. A portfolio reader must not infer one from the other.
        """
        saved = session["steward_context"]
        allowed = set(self.bindings.steward_scope(session) or [])
        latest: dict[str, dict[str, Any]] = {}
        for row in sorted(self._recovery_rows(), key=lambda row: row.get("created_at", "")):
            resources = row.get("commission_resources") or {}
            if (resources.get("goal_id") in allowed
                    and row["binding_id"] == saved["binding_id"]
                    and row["source"]["source_ref"] == saved["source_ref"]):
                latest[resources["goal_id"]] = row
        facts = []
        for goal_id, row in latest.items():
            resources = row["commission_resources"]
            current = self.controller.store.load_session(resources["session_id"])
            turn = self.controller.store.load_turn(resources["session_id"], resources["turn_id"])
            if self.actions is None:
                continue
            try:
                goal_identity = _commission_goal_identity(self.actions._goal(goal_id))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if (not goal_identity or not current or not turn or current.get("goal_id") != goal_id
                    or any(resources.get(key) != value for key, value in goal_identity.items())
                    or ("goal_instance_id" in goal_identity and current.get("goal_instance_id") != goal_identity["goal_instance_id"])):
                continue
            facts.append({"goal_id": goal_id, **goal_identity, "turn_status": turn["status"],
                "native_execution": current.get("native_goal"),
                "result_delivery_verified": row.get("delivery_verified") is True,
                "result_excerpt": str((turn.get("response") or {}).get("message") or "")[:500],
                "canonical_acceptance_attested": False})
        return facts

    def todo_confirmation_reply(self, request_ref: str, response: dict[str, Any]) -> str:
        """Project the completed reply and optional Todo or PR-merge preview through Core.

        The original request journal freezes expiry and idempotency before IO.
        This creates an action preview only; the authenticated /confirm worker
        remains the sole route to canonical writes and protected actions.
        """
        from . import external_conversations as _ext, steward_pr_merge

        message = str(response.get("message") or "")
        if not message.strip():
            message = self.native_result_reply(request_ref) or ""
        proposals = [p for p in response.get("proposals") or [] if p.get("kind") == "todo"]
        protected = steward_pr_merge.merge_protected_action(response)
        if not proposals and protected is None:
            return message
        path = self.root / f"{request_ref}.json"
        with _ext.exclusive_file_lock(path, operation="offer_external_todo_confirmation"):
            row = _ext._read_json(path)
            selected = self.bindings.resolve(binding_id=row["binding_id"], **row["source"])
            if selected["binding"]["context_kind"] != "steward":
                return message
            if self.actions is None:
                return message + ("\n\n受保护操作尚未执行：当前宿主的确认入口不可用。" if protected and not proposals else "\n\n待办尚未写入：当前宿主的确认入口不可用。")
            if row.get("todo_confirmation_reply") is not None:
                return str(row["todo_confirmation_reply"])
            context = selected["context"]
            if "todo_confirmation_created_at" not in row:
                row["todo_confirmation_created_at"] = _ext.datetime.now(timezone.utc).isoformat()
                _ext._atomic_write_json(path, row)
            created = row["todo_confirmation_created_at"]
            proposal_context = _steward_proposal_context(context, created, dt_cls=_ext.datetime)
            lines = [message]
            for index, todo in enumerate(proposals):
                try:
                    request = {"action_kind": "todo.create", "summary": str(todo["text"]),
                        "idempotency_key": f"external-todo-{request_ref}-{index}",
                        "context": proposal_context,
                        "normalized_parameters": {"goal_id": todo.get("goal_id"), "text": todo["text"],
                                                  "priority": todo.get("priority", "P1")}}
                    self.bindings._core("collaboration.steward.authorize_creation", {
                        "context": context, "proposal": {**request, "created_at": created},
                        "now": _ext.datetime.now(timezone.utc).isoformat(), "operation": "preview"})
                except (OSError, ValueError, KeyError, RuntimeError):
                    lines.append("待办尚未写入：目标 Goal、当前授权或规范预览不可用；请在原对话核对目标后重新准备预览。")
                    continue
                # An uncertain preview-store write is safe to recover by this
                # same idempotency key. Do not cache a failure and strand a
                # successfully persisted preview without its confirmation route.
                proposal = self.actions.preview(request)
                goal = self.actions._goal(str(todo["goal_id"]))
                title = str(goal.get("title") or todo["goal_id"])
                lines.append(f"目标：{title}\n待记录：{todo['text']}\n尚未写入，也未启动执行。"
                    f"请在本私聊于预览生成后 15 分钟内发送 /confirm {proposal['proposal_id']}；"
                    f"取消：/cancel {proposal['proposal_id']}。")
            if protected is not None:
                lines.append(steward_pr_merge.preview_reply_block(
                    self, request_ref=request_ref, context=context,
                    audience={key: context[key] for key in _AUDIENCE_KEYS},
                    created=created, expiry=proposal_context["expires_at"], protected=protected))
            reply_text = "\n\n".join(lines)
            row["todo_confirmation_reply"] = reply_text
            _ext._atomic_write_json(path, row)
            return reply_text

    def _run_steward_action(self, row: dict[str, Any]) -> None:
        """The existing service worker advances a journaled native operation.

        No provider owns Goal authority or a second scheduler. Confirmation is
        persisted before the canonical action service performs any write.
        """
        from ...chat_actions import ProtectedActionGate, goal_workspace_ref
        from ...control_plane.effect_runtime import effect_runtime_result
        from . import external_conversations as _ext, steward_pr_merge

        if self.actions is None:
            return
        path = self.root / f"{row['request_ref']}.json"
        with _ext.exclusive_file_lock(path, operation="advance_steward_action"):
            row = _ext._read_json(path)
            if row["status"] != "command_queued":
                return
            try:
                selected = self.bindings.resolve(binding_id=row["binding_id"], **row["source"])
                context = selected["context"]
                argument = row["command_argument"]["argument"]
                if row.get("commission_adoption_pending"):
                    applied = self.actions.load(row["proposal_id"])
                    self.bindings._core("collaboration.steward.authorize_creation", {
                        "context": context, "proposal": applied, "now": row["created_at"], "operation": "confirm"})
                    if (applied.get("status") != "applied"
                            or applied["receipt"]["resource_ids"] != row["commission_resources"]):
                        raise ValueError("the applied commission receipt changed")
                    self._adopt_commission(row, applied)
                elif row["command"] == "commission":
                    key = f"steward-commission-{row['request_ref']}"
                    existing = next((p for p in self.actions.store.list() if p["idempotency_key"] == key), None)
                    workspace = context["workspace_path"]
                    proposal_context = _steward_proposal_context(context, row["created_at"], dt_cls=_ext.datetime)
                    proposal = existing or self.actions.preview({"action_kind": "goal.create",
                        "summary": f"新管家委托：{argument[:180]}", "idempotency_key": key,
                        "context": proposal_context,
                        "normalized_parameters": {"goal_id": f"steward-{row['request_ref']}", "title": argument[:180],
                            "objective": argument, "completion_criteria": "按明确委托返回可核验结果；原生执行结束不代表 LoopX 验收。",
                            "execution_boundary": "只读所选工作区；不继承旧目标，不自动调度，不扩大宿主策略。",
                            "agent_id": selected["binding"]["executor_endpoint_id"],
                            "workspace_ref": goal_workspace_ref(Path(workspace)),
                            "heartbeat": {"enabled": False},
                            "native_token_budget": row["command_argument"]["native_token_budget"]}})
                    row.update(proposal_id=proposal["proposal_id"], response=(
                        f"待确认的新委托：{argument}\n只读执行；总 token 上限 {row['command_argument']['native_token_budget']}，运行中请求可能超过该上限。"
                        "不自动设置长期调度；未继承旧目标。确认后创建 Goal 并启动原生持续执行；其完成状态不等于 LoopX 验收。"
                        f"\n15 分钟内发送 /confirm {proposal['proposal_id']}；取消请发送 /cancel {proposal['proposal_id']}。"))
                else:
                    proposal = self.actions.load(argument)
                    if proposal is None:
                        raise ValueError("the exact commission preview is unavailable")
                    if proposal.get("action_kind") == "pr.merge":
                        steward_pr_merge.run_steward_pr_merge_command(
                            self, path=path, row=row, context=context, proposal=proposal,
                            write_json=_ext._atomic_write_json, gate_cls=ProtectedActionGate)
                        return
                    effect_runtime_result("collaboration.steward.authorize_creation", {
                        "context": context, "proposal": proposal, "now": row["created_at"],
                        "operation": "stop" if row["command"] == "stop_commission" else
                                     "resume" if row["command"] == "resume_commission" else "confirm"})
                    # An authenticated, canonical source selected this exact
                    # preview; crash replay retains the same confirmation.
                    row.update(proposal_id=argument, confirmation_recorded=True)
                    _ext._atomic_write_json(path, row)
                    if row["command"] == "stop_commission":
                        if row.get("target_turn_id"):
                            self.controller.interrupt_turn(session_id=row["target_session_id"], turn_id=row["target_turn_id"])
                        row["response"] = "已对受理时记录的确切执行请求停止；没有停止其它委托或改变 Goal 验收状态。" if row.get("target_turn_id") else "受理此停止请求时，该委托没有正在执行的消息。"
                    elif row["command"] == "resume_commission":
                        goal = self.actions._goal(row["target_goal_id"])
                        turn, _ = self.controller.submit_turn(session_id=row["target_session_id"],
                            client_turn_id=f"commission-resume-{row['request_ref']}",
                            message=f"/goal resume --tokens {row['command_argument']['native_token_budget']}",
                            work_dir=Path(goal["repo"]), objective=str(goal.get("objective") or ""))
                        row.update(commission_resources={
                            "goal_id": row["target_goal_id"],
                            **_commission_goal_identity(goal),
                            "session_id": row["target_session_id"],
                            "turn_id": turn["turn_id"],
                        }, response=f"已受理原委托的恢复，保留原生线程、目标及累计用量。总 token 上限 {row['command_argument']['native_token_budget']}；结果会返回此私聊。\n停止：/stop-commission {argument}")
                    elif row["command"] == "cancel_commission":
                        self.actions.cancel(argument)
                        row["response"] = ("已取消待办预览；没有写入待办或启动执行。"
                            if proposal["action_kind"] == "todo.create" else
                            "已取消这份新委托预览；没有创建或启动 Goal。")
                    else:
                        result = self.actions.apply(argument, steward_context=context, steward_confirmed_at=row["created_at"])
                        applied = result["proposal"]
                        if applied.get("status") != "applied":
                            raise ValueError("the creation preview became stale; prepare a new preview")
                        resources = applied["receipt"]["resource_ids"]
                        if applied["action_kind"] == "todo.create":
                            row.update(todo_receipt=applied["receipt"], response=(
                                f"已记录待办：{applied['normalized_parameters']['text']}\n"
                                f"目标：{resources['goal_id']}；规范写入回执已读回。本次没有启动执行。"))
                        else:
                            # Persist a committed creation before fallible adoption.
                            row.update(commission_resources=resources, commission_adoption_pending=True,
                                       commission_gate=bool(result.get("gate")))
                            _ext._atomic_write_json(path, row)
                            self._adopt_commission(row, applied)
                row["status"] = "command_completed"
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                # No automatic wider permission, regenerated preview or new
                # native thread is used to hide an unavailable operation.
                if row["command"] == "confirm_commission" and row.get("commission_resources"):
                    row.update(status="command_queued", commission_adoption_pending=True,
                               failure_kind=type(exc).__name__,
                               response="已持久受理此管家操作；正在核验原生操作与回执。")
                else:
                    row.update(status="command_completed", failure_kind=type(exc).__name__,
                        response="管家操作未完成：原工作区、授权、预览有效期或原生执行入口没有通过核验。原操作已保留；请在本机核对回执后再决定是否重试。")
            _ext._atomic_write_json(path, row)

    def _adopt_commission(self, row: dict[str, Any], applied: dict[str, Any]) -> None:
        if self.actions is None:
            raise ValueError("the native commission owner is unavailable")
        resources = row["commission_resources"]
        goal = self.actions._goal(resources["goal_id"])
        self.bindings.adopt_created_goal(binding_id=row["binding_id"], source=row["source"], proposal=applied, goal=goal)
        row.update(commission_adoption_pending=False, response=(
            f"原生委托已创建：{resources['goal_id']}。操作回执已读回；处理结果会回到此私聊。没有自动设置调度。"
            f"\n停止此执行：/stop-commission {row['proposal_id']}"))
        row.pop("failure_kind", None)
        if row.get("commission_gate"):
            row["response"] += "首轮执行有待处理 Gate；请在本机查看该 Goal 的操作回执。"


__all__ = [
    "ExternalStewardCommissionMixin",
    "NativeStewardCommandError",
    "execute_steward_fast_path",
    "native_command",
    "native_fast_path_triage",
    "parse_steward_fast_path_intent",
]
