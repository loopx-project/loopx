"""Native Chat admission/recovery IO for explicitly bound external sources.

Sessions and Turns remain in ChatSessionStore. This journal only correlates a
provider request with those canonical objects; it is not a second queue or model
runner. Write it before admission so a crash can replay the same client identity.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from ...chat_store import _atomic_write_json, _read_json
from ...chat_attachments import normalize_chat_image_attachments
from ...file_lock import exclusive_file_lock


def _commission_goal_identity(goal: dict[str, Any]) -> dict[str, str]:
    identity: dict[str, str] = {}
    for field in ("goal_instance_id", "creation_operation_id"):
        value = str(goal.get(field) or "").strip()
        if value:
            identity[field] = value
    return identity


class ChatExternalConversations:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.bindings = controller.project_contexts.conversation_bindings
        self.bindings.controller = controller
        self.root = controller.store.root / "external-requests"
        self.actions: Any | None = None

    def admit(self, *, binding_id: str, source: dict[str, Any], request_ref: str,
              message: str, command: str | None = None,
              attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        import re
        if not re.fullmatch(r"[a-f0-9]{24}", request_ref):
            raise ValueError("invalid external request reference")
        if command not in {None, "agents", "select_agent", "select_project", "status", "help", "new", "stop", "unsupported", "commission", "confirm_commission", "cancel_commission", "stop_commission", "resume_commission"}:
            raise ValueError("unsupported external conversation command")
        # These references enter the lock path before authority is resolved.
        # Check path-safe shape here; the typed owner checks current grants
        # and provider identity after both fences are acquired.
        for ref in (binding_id, source.get("source_ref")):
            if not isinstance(ref, str) or not re.fullmatch(r"[a-f0-9]{24}", ref):
                raise ValueError("invalid external conversation source reference")
        path = self.root / f"{request_ref}.json"
        with exclusive_file_lock(self.root / "source-fences" / f"{binding_id}.{source['source_ref']}.json", operation="route_external_chat_request"), exclusive_file_lock(path, operation="admit_external_chat_request"):
            # Resolve after waiting for both fences, including exact replay.
            # A lock-external provider probe cannot authorize the write and
            # would duplicate this fresh authority check on every request.
            selected = self.bindings.resolve(binding_id=binding_id, **source)
            expected = {"binding_id": binding_id, "source": source, "message": message, "command": command,
                        "attachments": normalize_chat_image_attachments(attachments) or None}
            if path.exists():
                row = _read_json(path)
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("external request identity was reused with different content")
                if row.get("status") != "prepared":
                    return row
            else:
                row = {"schema_version": "loopx_chat_external_request_v0", "request_ref": request_ref,
                       **expected, "status": "prepared", "session_id": None, "turn_id": None,
                       "created_at": datetime.now(timezone.utc).isoformat()}
                _atomic_write_json(path, row)
            try:
                return self._admit_prepared(path, row, selected)
            except ValueError:
                # Invalid commands and definitive grant denials are terminal
                # admission outcomes; recovery must not retry them forever.
                row.update(status="rejected", response="原授权或命令格式不可用，本条未进入执行队列；请用 /agents 或 /project 查看可用入口。")
                _atomic_write_json(path, row)
                raise

    def _admit_prepared(self, path: Path, row: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
        controller = self.controller
        self.bindings.ensure_delivery_scope(selected)
        if not row.get("routing_recorded"):
            choices = [item for item in self.pending() if item["binding_id"] == row["binding_id"]
                and item["source"]["source_ref"] == row["source"]["source_ref"]
                and item.get("selection_recorded")]
            previous = max(choices, key=lambda item: item.get("selection_order", 0)) if choices else None
            row.update(routing_recorded=True, agent_target=previous.get("selection_target") if previous else None)
            _atomic_write_json(path, row)
        if row["command"] in {"agents", "select_agent", "select_project"}:
            row["agent_target"] = None
        target = row.get("agent_target")
        if target:
            authority = self.bindings.resolve_agent_target(selected, target)
            row["agent_audience"] = authority["audience"]
            # Observation must retain failed or closed originals, so read the
            # exact frozen target Session without lifecycle filtering.
            current = next((candidate for candidate in controller.store.session_candidates(
                goal_id=None, agent_id=selected["binding"]["executor_endpoint_id"],
                channel_id=selected["channel_id"])
                if candidate.get("session_id") == target["session_id"]),
                controller.store.load_session(target["session_id"]))
        else:
            current = controller.store.latest_session(goal_id=None,
            agent_id=selected["binding"]["executor_endpoint_id"], channel_id=selected["channel_id"])
        if row["command"] in {"status", "help"} and not target:
            # Observation must retain failed/closed originals. Admission still
            # uses the resumable selector and never resumes from this snapshot.
            current = max(controller.store.session_candidates(goal_id=None,
                agent_id=selected["binding"]["executor_endpoint_id"], channel_id=selected["channel_id"]),
                key=lambda candidate: str(candidate.get("updated_at") or ""), default=None)
        if row.get("session_id") and row["command"] is None:
            current = controller.store.load_session(row["session_id"])
            if current is None:
                raise ValueError("the original request Session is unavailable")
            client_id = f"external-{row['request_ref']}"
            if controller.store.turn_for_client(current["session_id"], client_id) is not None:
                # The canonical store validates exact replay before its closed
                # Session check. Never move an accepted request to a new Session.
                self._record_steward_ingress(row, selected, current, client_id)
                turn, _ = controller.store.create_queued_turn(current["session_id"],
                    client_turn_id=client_id, message=row["message"], origin="lark",
                    attachments=row.get("attachments"),
                    external_agent_target={"target": target, "context": selected["context"]} if target else None)
                row.update(status="accepted", turn_id=turn["turn_id"])
                _atomic_write_json(path, row)
                return row
        from ...control_plane.effect_runtime import effect_runtime_result
        observations = {}
        if row["command"] in {"status", "help"}:
            active_id = current.get("active_turn_id") if current else None
            observations = {"context": selected["context"],
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "queued_count": len(controller.store.queued_turns(current["session_id"])) if current else 0,
                "active_turn": ({key: value for key, value in controller.store.load_turn(current["session_id"], active_id).items()
                    if key != "attachments"} if active_id else None)}
        # Routing needs presence, not private image bytes. Persisted attachments
        # remain in the native request/Turn and never enter the effect bridge.
        plan = effect_runtime_result("collaboration.conversation.request", {
            "request": {key: value for key, value in row.items() if key != "attachments"},
            "attachment_count": len(row.get("attachments") or []),
            "current_session": current, "binding": selected["binding"],
            "agent_target": target, **observations})
        operation = plan["operation"]
        if operation == "select_recipient":
            if selected["binding"]["context_kind"] != "project":
                raise ValueError("Agent selection requires the project assistant")
            granted = selected["binding"].get("agent_targets", [])
            choices = []
            for candidate in granted:
                try:
                    self.bindings.resolve_agent_target(selected, candidate)
                    choices.append(candidate)
                except (ValueError, OSError, KeyError):
                    continue
            if row["command"] == "select_agent":
                import re
                match = re.fullmatch(r"/agent ([a-f0-9]{24})", row["message"].strip())
                chosen = next((item for item in choices if match and item["target_ref"] == match[1]), None)
                if chosen is None:
                    raise ValueError("select the exact currently authorized Agent reference")
                row.update(selection_recorded=True, selection_target=chosen, response=f"已选择 Agent：{chosen['agent_id']}。后续文字进入原宿主持久队列；/project 返回普通项目对话。")
            elif row["command"] == "select_project":
                row.update(selection_recorded=True, selection_target=None, response="已返回普通项目对话；保留原项目会话。此前受理的 Agent 消息仍返回原私聊。")
            else:
                row["response"] = "当前没有已授权且可用的 Agent。请在本机 Chat → 设置 → Lark，为此 App 明确授权已有 attached Session；名称相似不会获得授权。" if not choices else "可选 Agent（使用确切命令）：\n" + "\n".join(f"{item['agent_id']} · {item['goal_id']}\n/agent {item['target_ref']}" for item in choices)
            if row.get("selection_recorded"):
                row["selection_order"] = 1 + max((item.get("selection_order", 0) for item in self.pending()
                    if item["binding_id"] == row["binding_id"] and item["source"]["source_ref"] == row["source"]["source_ref"]), default=0)
            row.update(status="command_completed", session_id=None, turn_id=None)
        elif operation == "steward_action":
            parsed = self.bindings._core("collaboration.steward.command", {"command": row["command"], "message": row["message"]})
            if row["command"] in {"stop_commission", "resume_commission"} and not row.get("target_recorded"):
                if self.actions is None:
                    raise ValueError("the native commission owner is unavailable")
                proposal = self.actions.load(parsed["argument"])
                self.bindings._core("collaboration.steward.authorize_creation", {"context": selected["context"],
                    "proposal": proposal, "operation": "stop" if row["command"] == "stop_commission" else "resume"})
                resources = proposal["receipt"]["resource_ids"]
                target = controller.store.load_session(resources["session_id"])
                if not target or target["goal_id"] != resources["goal_id"]:
                    raise ValueError("the exact commission Session is unavailable")
                row.update(target_recorded=True, target_session_id=target["session_id"],
                           target_turn_id=target.get("active_turn_id"), target_goal_id=resources["goal_id"])
                _atomic_write_json(path, row)
            row.update(status="command_queued", command_argument=parsed, session_id=plan["session_id"],
                       response="已持久受理此管家操作；正在核验原生操作与回执。")
        elif operation == "reply":
            row.update(status="command_completed", session_id=plan["session_id"], response_code=plan["response_code"])
            if "status_snapshot" in plan:
                row["status_snapshot"] = plan["status_snapshot"]
        elif operation in {"new", "stop"}:
            row.update(target_recorded=True, session_id=plan["session_id"], turn_id=plan["turn_id"])
            _atomic_write_json(path, row)
            if operation == "stop" and row["session_id"] and row["turn_id"]:
                controller.interrupt_turn(session_id=row["session_id"], turn_id=row["turn_id"])
            elif operation == "new" and row["session_id"]:
                controller.close_session(row["session_id"])
            row.update(status="command_completed", response_code=plan["response_code"])
        else:
            # Opening uses the shared route fence, canonical grant and native
            # adapter. It never creates a Goal or waits for a terminal answer.
            if current is None:
                current, _ = controller.open_session(goal_id=None,
                    agent_id=selected["binding"]["executor_endpoint_id"], work_dir=Path("."), objective="",
                    mode="resume_latest", conversation_binding_id=row["binding_id"], source_context=row["source"])
            row["session_id"] = current["session_id"]
            _atomic_write_json(path, row)
            self._record_steward_ingress(row, selected, current, plan["client_turn_id"])
            try:
                turn, _ = controller.enqueue_turn(session_id=current["session_id"],
                    client_turn_id=plan["client_turn_id"], message=row["message"],
                    attachments=row.get("attachments"),
                    work_dir=Path("."), objective="", origin="lark",
                    external_agent_target={"target": target, "context": selected["context"]} if target else None)
                row.update(status="accepted", turn_id=turn["turn_id"])
            except RuntimeError as exc:
                if str(exc) != "session_queue_full":
                    raise
                row.update(status="rejected", response="队列已满，本条没有被受理；请稍后重新发送。")
        _atomic_write_json(path, row)
        return row

    def _record_steward_ingress(self, row: dict[str, Any], selected: dict[str, Any],
                               session: dict[str, Any], client_turn_id: str) -> None:
        if selected["binding"]["context_kind"] != "steward":
            return
        if session.get("goal_id") != "loopx-manager" or session.get("channel_id") != selected["channel_id"]:
            raise ValueError("the verified steward source belongs to another Session")
        from ..manager_context import register_ingress
        from ...control_plane.collaboration.inbox import normalize_source_context

        try:
            normalize_source_context(row["message"])
        except ValueError:
            # Inbox bounds are not ordinary Chat admission bounds. Preserve the
            # full native message; no provenance means no context delivery.
            return

        # Persist provenance before enqueue can launch the model. This is the
        # existing inbox owner, not a delivery grant: its separate sender/target
        # policy still decides which registered recipient may receive context.
        # The independently verified App-scoped owner reference is deliberately
        # used instead of copying another profile's raw provider identity.
        register_ingress(getattr(self.controller, "coordination_runtime_root", self.controller.store.root.parent),
            session_id=session["session_id"], client_turn_id=client_turn_id,
            channel=selected["channel_id"], sender_id=selected["context"]["operator_ref"],
            message=row["message"], source_id=row["request_ref"])

    def pending(self) -> list[dict[str, Any]]:
        return [_read_json(path) for path in sorted(self.root.glob("*.json"))]

    def read_request(self, request_ref: str) -> dict[str, Any]:
        return _read_json(self.root / f"{request_ref}.json")

    def commission_evidence(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """Read original Core execution facts for this exact bound audience.

        Native terminal status/result delivery is separate from Goal/Todo
        acceptance. A portfolio reader must not infer one from the other.
        """
        saved = session["steward_context"]
        allowed = set(self.bindings.steward_scope(session) or [])
        latest: dict[str, dict[str, Any]] = {}
        for row in sorted(self.pending(), key=lambda row: row.get("created_at", "")):
            resources = row.get("commission_resources") or {}
            if (row["binding_id"] == saved["binding_id"] and row["source"]["source_ref"] == saved["source_ref"]
                    and resources.get("goal_id") in allowed):
                latest[resources["goal_id"]] = row
        facts = []
        for goal_id, row in latest.items():
            resources = row["commission_resources"]
            current = self.controller.store.load_session(resources["session_id"])
            turn = self.controller.store.load_turn(resources["session_id"], resources["turn_id"])
            if self.actions is None:
                continue
            try:
                goal_identity = _commission_goal_identity(
                    self.actions._goal(goal_id)
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if (
                not goal_identity
                or any(resources.get(key) != value for key, value in goal_identity.items())
                or not current
                or not turn
                or current.get("goal_id") != goal_id
                or (
                    "goal_instance_id" in goal_identity
                    and current.get("goal_instance_id")
                    != goal_identity["goal_instance_id"]
                )
            ):
                continue
            facts.append({"goal_id": goal_id, **goal_identity, "turn_status": turn["status"],
                "native_execution": current.get("native_goal"),
                "result_delivery_verified": row.get("delivery_verified") is True,
                "result_excerpt": str((turn.get("response") or {}).get("message") or "")[:500],
                "canonical_acceptance_attested": False})
        return facts

    def _run_steward_action(self, row: dict[str, Any]) -> None:
        """The existing service worker advances a journaled native operation.

        No provider owns Goal authority or a second scheduler. Confirmation is
        persisted before the canonical action service performs any write.
        """
        if self.actions is None:
            return
        path = self.root / f"{row['request_ref']}.json"
        with exclusive_file_lock(path, operation="advance_steward_action"):
            row = _read_json(path)
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
                    expiry = (datetime.fromisoformat(row["created_at"]) + timedelta(minutes=15)).isoformat()
                    audience = {key: context[key] for key in ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"]}
                    proposal = existing or self.actions.preview({"action_kind": "goal.create",
                        "summary": f"新管家委托：{argument[:180]}", "idempotency_key": key,
                        "context": {"kind": "manager", "goal_id": "loopx-manager", **audience, "expires_at": expiry},
                        "normalized_parameters": {"goal_id": f"steward-{row['request_ref']}", "title": argument[:180],
                            "objective": argument, "completion_criteria": "按明确委托返回可核验结果；原生执行结束不代表 LoopX 验收。",
                            "execution_boundary": "只读所选工作区；不继承旧目标，不自动调度，不扩大宿主策略。",
                            "agent_id": selected["binding"]["executor_endpoint_id"],
                            "workspace_ref": "workspace-" + hashlib.sha256(workspace.encode()).hexdigest()[:12],
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
                    from ...control_plane.effect_runtime import effect_runtime_result
                    effect_runtime_result("collaboration.steward.authorize_creation", {
                        "context": context, "proposal": proposal, "now": row["created_at"],
                        "operation": "stop" if row["command"] == "stop_commission" else
                                     "resume" if row["command"] == "resume_commission" else "confirm"})
                    # An authenticated, canonical source selected this exact
                    # preview; crash replay retains the same confirmation.
                    row.update(proposal_id=argument, confirmation_recorded=True)
                    _atomic_write_json(path, row)
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
                        row["response"] = "已取消这份新委托预览；没有创建或启动 Goal。"
                    else:
                        result = self.actions.apply(argument, steward_context=context, steward_confirmed_at=row["created_at"])
                        applied = result["proposal"]
                        if applied.get("status") != "applied":
                            raise ValueError("the creation preview became stale; prepare a new /delegate request")
                        resources = applied["receipt"]["resource_ids"]
                        # Canonical creation has committed. Retain its exact
                        # receipt before fallible adoption or notification.
                        row.update(commission_resources=resources, commission_adoption_pending=True,
                                   commission_gate=bool(result.get("gate")))
                        _atomic_write_json(path, row)
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
            _atomic_write_json(path, row)

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

    def record_delivery(self, request_ref: str, *, session_id: str | None, turn_id: str | None) -> None:
        # This transport receipt does not alter a canonical Turn or grant.
        path = self.root / f"{request_ref}.json"
        with exclusive_file_lock(path, operation="record_external_chat_delivery"):
            row = _read_json(path)
            if row.get("commission_adoption_pending"):
                raise ValueError("commission adoption is pending; notification cannot settle the effect")
            if (row.get("session_id"), row.get("turn_id")) != (session_id, turn_id):
                raise ValueError("delivery correlation changed")
            row["delivery_verified"] = True
            _atomic_write_json(path, row)

    def recover(self, *, request_ref: str | None = None) -> None:
        rows = self.pending() if request_ref is None else [self.read_request(request_ref)]
        for row in rows:
            try:
                if row.get("delivery_verified") and not row.get("commission_adoption_pending"):
                    continue
                self.bindings.resolve(binding_id=row["binding_id"], **row["source"])
                if row["status"] == "prepared":
                    self.admit(binding_id=row["binding_id"], source=row["source"], request_ref=row["request_ref"],
                               message=row["message"], command=row["command"])
                elif row["status"] == "command_queued":
                    self._run_steward_action(row)
                elif row["status"] == "accepted":
                    if row.get("agent_target"):
                        self.bindings.resolve_agent_target(self.bindings.resolve(binding_id=row["binding_id"], **row["source"]), row["agent_target"])
                        continue  # Existing host owns claim and completion; no adapter is resumed.
                    session = self.controller.store.load_session(row["session_id"])
                    if session and session.get("status") != "closed":
                        context = self.controller.project_contexts.session_context(session)
                        self.controller.resume_session_queue(session_id=row["session_id"],
                            work_dir=context["project"], objective=context["objective"])
            except (KeyError, ValueError, RuntimeError, OSError):
                # Revoked/missing grants never fall back to a new model thread.
                continue
