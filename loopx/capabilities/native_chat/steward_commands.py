"""Native command transport and confirmation readback for the steward.

No stdout redirection or subprocess: original Chat workers can run concurrently.
Arguments are constructed by the host, never accepted from a model or HTTP body.
"""
from __future__ import annotations

from typing import Any


class NativeStewardCommandError(ValueError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("the native steward operation was not committed: " + str(
            payload.get("error_code") or "unavailable") + ": " + str(
            payload.get("error") or payload.get("reason") or payload.get("message") or "inspect the native receipt"))


def native_command(controller: Any, argv: list[str]) -> dict[str, Any]:
    from ...cli import build_parser
    from ...cli_rollout import append_cli_rollout_event
    from ...cli_commands.quota import handle_quota_command
    from ...cli_commands.todo import handle_todo_command
    from ...cli_commands.task_lease import handle_task_lease_command

    args = build_parser().parse_args(["--format", "json", *argv])
    result: dict[str, Any] = {}
    kwargs = {"registry_path": controller.registry_path,
        "runtime_root_arg": str(controller.coordination_runtime_root),
        "print_payload": lambda payload, *_args: result.update(payload)}
    if args.command == "todo":
        code = handle_todo_command(args, **kwargs, format_name="json",
            append_cli_rollout_event=append_cli_rollout_event)
    elif args.command == "quota":
        code = handle_quota_command(args, **kwargs,
            append_cli_rollout_event=append_cli_rollout_event)
    elif args.command == "task-lease":
        code = handle_task_lease_command(args, **kwargs, output_format=lambda *_a, **_kw: "json")
    else:
        raise ValueError("unsupported native steward operation")
    if code or result.get("ok") is not True:
        raise NativeStewardCommandError(result)
    return result



def todo_confirmation_readback(proposal: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    failure = proposal.get("failure") or {}
    if (proposal.get("status") == "failed" and failure.get("error_code") == "canonical_create_projection_pending"
        and (failure.get("details") or {}).get("canonical_committed") is True):
        return {"response": ("待办已创建，展示尚未更新。请在原确认有效期内"
                            f"重试 /confirm {proposal_id} 以完成更新。")}
    if proposal.get("status") != "applied":
        raise ValueError("the creation preview became stale; prepare a new preview")
    receipt = proposal["receipt"]
    resources = receipt["resource_ids"]
    return {"todo_receipt": receipt, "response": (
        f"已记录待办：{proposal['normalized_parameters']['text']}\n"
        f"目标：{resources['goal_id']}；规范写入回执已读回。本次没有启动执行。")}
