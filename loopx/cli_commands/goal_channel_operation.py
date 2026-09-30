"""Registration and dispatch for Goal Channel operation commands."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Self

from ..chat_action_store import ActionConflictError, ChatActionStore
from ..chat_actions import ChatActionService
from ..control_plane.effect_runtime import (
    EffectRuntimeConflict,
    EffectRuntimeRejected,
    effect_runtime_result,
)
from ..extensions.lark.goal_channel import (
    default_goal_channel_target_path,
    deliver_goal_channel_operation_card,
    goal_channel_target_for_name,
    read_goal_channel_binding,
    read_goal_channel_targets,
)
from ..extensions.lark.goal_channel_contracts import binding_for_goal, operation_packet
from ..extensions.lark.goal_channel_message_delivery import (
    GoalChannelDeliveryStageError,
)
from ..extensions.lark.goal_channel_operation import (
    OperationExecutorDriftError,
    confirmed_operation_executor,
)


@dataclass(frozen=True, slots=True)
class GoalChannelOperationContext:
    invoked_runtime_root: Path
    source_registry_path: Path
    source_runtime_root: Path
    binding_path: Path


class _GoalChannelOperationCommand(str, Enum):
    PREPARE = "prepare-operation"
    DELIVER = "deliver-operation"
    INSPECT = "inspect-operation"
    CONSUME = "consume-operation"
    REPORT = "report-operation"

    @classmethod
    def parse(cls, value: object) -> Self | None:
        try:
            return cls(str(value))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class _PrepareOperation:
    goal_id: str
    agent_id: str
    summary: str
    idempotency_key: str
    request_path: Path
    execute: bool
    target_path_override: Path | None
    command: ClassVar[_GoalChannelOperationCommand] = (
        _GoalChannelOperationCommand.PREPARE
    )


@dataclass(frozen=True, slots=True)
class _DeliverOperation:
    goal_id: str
    proposal_id: str
    execute: bool
    target_path_override: Path | None
    command: ClassVar[_GoalChannelOperationCommand] = (
        _GoalChannelOperationCommand.DELIVER
    )


@dataclass(frozen=True, slots=True)
class _AgentOperation:
    command: _GoalChannelOperationCommand
    goal_id: str
    agent_id: str
    proposal_id: str
    host_surface: str
    thread_id: str
    execute: bool
    consumption_id: str | None = None
    outcome_path: Path | None = None
    target_path_override: Path | None = None


_OperationRequest = _PrepareOperation | _DeliverOperation | _AgentOperation


def register_goal_channel_operation_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
    add_common_args: Callable[[argparse.ArgumentParser], None],
) -> None:
    prepare = subparsers.add_parser(
        "prepare-operation",
        help=(
            "Validate and persist one canonical typed-operation proposal. "
            "Dry-run unless --execute."
        ),
    )
    add_subcommand_format(prepare)
    add_common_args(prepare)
    prepare.add_argument("--agent-id", required=True)
    prepare.add_argument("--summary", required=True)
    prepare.add_argument("--idempotency-key", required=True)
    prepare.add_argument("--request-json", required=True)
    prepare.add_argument("--execute", action="store_true")

    deliver = subparsers.add_parser(
        "deliver-operation",
        help=(
            "Deliver one canonical typed-operation confirmation card through "
            "the bound project Bot. Dry-run unless --execute."
        ),
    )
    add_subcommand_format(deliver)
    add_common_args(deliver)
    deliver.add_argument("--proposal-id", required=True)
    deliver.add_argument("--execute", action="store_true")

    for name, help_text in (
        (
            "inspect-operation",
            "Read the original confirmed operation; never grants execution.",
        ),
        (
            "consume-operation",
            "Atomically consume the original Agent's authorization once. Requires --execute.",
        ),
        (
            "report-operation",
            "Record original external outcome evidence. Does not execute the operation.",
        ),
    ):
        parser = subparsers.add_parser(name, help=help_text)
        add_subcommand_format(parser)
        add_common_args(parser)
        parser.add_argument("--agent-id", required=True)
        parser.add_argument("--proposal-id", required=True)
        parser.add_argument("--host-surface", required=True)
        parser.add_argument("--thread-id", required=True)
        if name != "inspect-operation":
            parser.add_argument("--execute", action="store_true")
        if name == "consume-operation":
            parser.add_argument("--consumption-id", required=True)
        if name == "report-operation":
            parser.add_argument("--outcome-json", required=True)


def _parse_operation_request(args: argparse.Namespace) -> _OperationRequest | None:
    command = _GoalChannelOperationCommand.parse(
        getattr(args, "goal_channel_command", None)
    )
    if command is None:
        return None
    target_path_arg = getattr(args, "target_path", None)
    target_path_override = (
        Path(str(target_path_arg)).expanduser() if target_path_arg else None
    )
    if command in {
        _GoalChannelOperationCommand.INSPECT,
        _GoalChannelOperationCommand.CONSUME,
        _GoalChannelOperationCommand.REPORT,
    }:
        outcome_path = getattr(args, "outcome_json", None)
        return _AgentOperation(
            command=command,
            goal_id=str(args.goal_id),
            agent_id=str(args.agent_id),
            proposal_id=str(args.proposal_id),
            host_surface=str(args.host_surface),
            thread_id=str(args.thread_id),
            execute=bool(getattr(args, "execute", False)),
            consumption_id=getattr(args, "consumption_id", None),
            outcome_path=Path(str(outcome_path)).expanduser() if outcome_path else None,
        )
    if command is _GoalChannelOperationCommand.PREPARE:
        return _PrepareOperation(
            goal_id=str(args.goal_id),
            agent_id=str(args.agent_id),
            summary=str(args.summary),
            idempotency_key=str(args.idempotency_key),
            request_path=Path(str(args.request_json)).expanduser(),
            execute=bool(getattr(args, "execute", False)),
            target_path_override=target_path_override,
        )
    return _DeliverOperation(
        goal_id=str(args.goal_id),
        proposal_id=str(args.proposal_id),
        execute=bool(getattr(args, "execute", False)),
        target_path_override=target_path_override,
    )


def _operation_target_path(
    request: _OperationRequest,
    context: GoalChannelOperationContext,
) -> Path:
    if request.target_path_override is not None:
        return request.target_path_override
    runtime_root = (
        context.invoked_runtime_root
        if request.command is _GoalChannelOperationCommand.PREPARE
        else context.source_runtime_root
    )
    return default_goal_channel_target_path(runtime_root)


def _operation_error_packet(
    *,
    request: _OperationRequest,
    blocker: str,
    summary: str,
    external_write_performed: bool = False,
    failure_stage: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packet = operation_packet(
        ok=False,
        goal_id=request.goal_id,
        operation=request.command.value.replace("-", "_"),
        execute=request.execute,
        status="blocked",
        blocker=blocker,
        public_summary=summary,
        external_write_performed=external_write_performed,
        details=details,
    )
    if failure_stage:
        packet["failure_stage"] = failure_stage
    return packet


def run_goal_channel_operation(
    args: argparse.Namespace,
    *,
    context: GoalChannelOperationContext,
) -> dict[str, Any] | None:
    request = _parse_operation_request(args)
    if request is None:
        return None
    try:
        if isinstance(request, _AgentOperation):
            # No independent host identity producer is connected here. Ask the
            # TS owner for the explicit gate before reading private parameters,
            # outcome files or canonical operation state. Never promote an
            # environment id, --thread-id or a local "verified" flag to identity.
            try:
                effect_runtime_result(
                    "operation.agent_handoff.actor",
                    {
                        "requested": {
                            "goal_id": request.goal_id,
                            "agent_id": request.agent_id,
                            "host_surface": request.host_surface,
                            "thread_id": request.thread_id,
                        },
                    },
                )
            except (EffectRuntimeConflict, EffectRuntimeRejected) as exc:
                return _operation_error_packet(
                    request=request,
                    blocker=exc.diagnostic_code,
                    summary=str(exc),
                    details={"execution_allowed": False},
                )
            # An unexpected success from a mismatched/older runtime still may
            # not bypass this adapter's missing authenticated transport.
            raise ActionConflictError("no authenticated host transport is connected")
        target_path = _operation_target_path(request, context)
        binding = (
            binding_for_goal(
                read_goal_channel_binding(context.binding_path),
                request.goal_id,
            )
            or {}
        )
        target_name = str(binding.get("target_ref") or "")
        provider_target = (
            goal_channel_target_for_name(
                read_goal_channel_targets(target_path),
                target_name,
            )
            if target_name
            else None
        )
        if target_name and provider_target is None:
            return _operation_error_packet(
                request=request,
                blocker="provider_target_missing",
                summary="configure the named shared provider target first",
            )
        if isinstance(request, _PrepareOperation):
            return _prepare_goal_channel_operation(
                registry_path=context.source_registry_path,
                runtime_root=context.source_runtime_root,
                goal_id=request.goal_id,
                agent_id=request.agent_id,
                summary=request.summary,
                idempotency_key=request.idempotency_key,
                request_path=request.request_path,
                execute=request.execute,
            )
        return deliver_goal_channel_operation_card(
            proposal_id=request.proposal_id,
            action_store_root=context.source_runtime_root / "chat" / "actions",
            runtime_root=context.source_runtime_root,
            binding_path=context.binding_path,
            target_path=target_path,
            expected_goal_id=request.goal_id,
            execute=request.execute,
        )
    except OperationExecutorDriftError as exc:
        return _operation_error_packet(
            request=request,
            blocker=exc.blocker,
            summary=str(exc),
            external_write_performed=exc.external_write_performed,
            failure_stage=exc.failure_stage,
            details=exc.details,
        )
    except GoalChannelDeliveryStageError as exc:
        outcome = exc.external_write_performed
        return _operation_error_packet(
            request=request,
            blocker=exc.blocker,
            summary=str(exc),
            # Unknown provider outcomes cannot be projected as clean no-writes.
            external_write_performed=True if outcome is None else outcome,
            failure_stage=exc.failure_stage,
            details=(
                {"external_write_outcome": "unknown"} if outcome is None else None
            ),
        )
    except ActionConflictError as exc:
        return _operation_error_packet(
            request=request,
            blocker="operation_handoff_conflict",
            summary=str(exc),
        )
    except ValueError:
        return _operation_error_packet(
            request=request,
            blocker="invalid_configuration",
            summary="the Goal Channel configuration is invalid",
        )
    except Exception:
        return _operation_error_packet(
            request=request,
            blocker="provider_api_failed",
            summary=(
                "the Goal Channel operation failed before a verified provider receipt"
            ),
        )


def _prepare_goal_channel_operation(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    summary: str,
    idempotency_key: str,
    request_path: Path,
    execute: bool,
    executor_binding_resolver: Callable[[Mapping[str, Any], Path], Mapping[str, Any]]
    | None = None,
) -> dict[str, Any]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("operation request JSON must be an object")
    parameters = {**request, "goal_id": goal_id, "agent_id": agent_id}
    if isinstance(parameters.get("executor"), Mapping):
        # Reject executor drift before a durable proposal or idempotency row exists.
        confirmed_operation_executor(
            parameters, runtime_root, executor_binding_resolver
        )

    def preview(store_root: Path) -> dict[str, Any]:
        return ChatActionService(
            store=ChatActionStore(store_root),
            registry_path=registry_path,
        ).preview(
            {
                "action_kind": "operation.execute",
                "summary": summary,
                "idempotency_key": idempotency_key,
                "context": {"kind": "goal", "goal_id": goal_id},
                "normalized_parameters": parameters,
            }
        )

    durable_store_root = runtime_root / "chat" / "actions"
    if execute:
        proposal = preview(durable_store_root)
        readback = ChatActionStore(durable_store_root).load(
            str(proposal["proposal_id"])
        )
        readback_verified = bool(
            readback is not None
            and readback.get("request_digest") == proposal.get("request_digest")
            and readback.get("operation") == proposal.get("operation")
        )
        if not readback_verified:
            raise ValueError("operation proposal durable readback did not match")
    else:
        with tempfile.TemporaryDirectory(prefix="loopx-operation-preview-") as root:
            proposal = preview(Path(root) / "actions")
        readback_verified = False
    operation = proposal.get("operation")
    if not isinstance(operation, Mapping):
        raise ValueError("operation preview did not produce a canonical envelope")
    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="prepare_operation",
        execute=execute,
        status="awaiting_confirmation" if execute else "preview_ready",
        public_summary=(
            "persisted one canonical operation awaiting card delivery"
            if execute
            else "validated one canonical operation proposal without persistence"
        ),
        external_write_performed=False,
        readback_verified=readback_verified,
        idempotency_key=idempotency_key,
        receipt_id=str(proposal["proposal_id"]) if execute else None,
        details={
            "operation_id": str(proposal["proposal_id"]) if execute else None,
            "lifecycle_state": operation["lifecycle_state"],
            "confirmation_digest": operation["confirmation_digest"],
            "payload_digest": operation["payload_digest"],
            "projection_digest": operation["projection_digest"],
            "durable_proposal_written": execute,
        },
    )
