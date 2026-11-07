"""Optional argv-only generic-cli adapter; secrets come from the process environment."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import signal

from arkruntime import AsyncArk

from .config import AdapterError, Config
from loopx.file_lock import exclusive_file_lock, LockAcquisitionPolicy

from .host import run, cleanup, config_digest
from .receipt import Receipt


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, help="Operator-owned JSON Config; exclusive with inline configuration.")
    p.add_argument("--model")
    p.add_argument("--environment-id")
    p.add_argument("--workspace", type=Path, default=Path.cwd())
    p.add_argument("--state-dir", type=Path, help="Private host receipts, outside the task workspace.")
    p.add_argument("--mcp-command-json", help="Operator-selected stdio server argv, never a shell command.")
    p.add_argument("--tool", action="append", default=[], help="Exact MCP tool to expose; repeat up to eight times.")
    p.add_argument("--mcp-env", action="append", default=[], help="Additional environment variable name to forward; never ARK_API_KEY.")
    p.add_argument("--timeout-seconds", type=float, default=180)
    p.add_argument("--tool-timeout-seconds", type=float, default=60)
    p.add_argument("--max-tool-calls", type=int, default=32)
    p.add_argument("--sandbox-builtins", action="store_true", help="Enable cloud sandbox tools with verified restricted networking and no injected resources.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true", help="Read configuration only; make no network calls.")
    mode.add_argument("--inspect-turn-key", help="Read a private host receipt without calling the provider.")
    mode.add_argument("--cleanup-turn-key", help="Retry deletion/readback for this attempt's known resources; never launch work.")
    return p


async def execute(args: argparse.Namespace) -> dict:
    if args.config:
        if (args.model or args.environment_id or args.state_dir or args.mcp_command_json or args.tool or args.mcp_env
                or args.workspace != Path.cwd() or args.timeout_seconds != 180
                or args.tool_timeout_seconds != 60 or args.max_tool_calls != 32 or args.sandbox_builtins):
            raise AdapterError("config_file_and_inline_options_are_exclusive")
        if args.config.stat().st_size > 32_000:
            raise AdapterError("config_file_exceeds_limit")
        raw = json.loads(args.config.read_text())
        if not isinstance(raw, dict):
            raise AdapterError("config_file_must_be_object")
        for field in ("workspace", "state_dir"):
            if not isinstance(raw.get(field), str) or not Path(raw[field]).is_absolute():
                raise AdapterError("config_paths_must_be_absolute")
            raw[field] = Path(raw[field])
        for field in ("mcp_command", "tool_names", "mcp_env"):
            value = raw.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise AdapterError("config_sequence_fields_require_strings")
            raw[field] = tuple(value)
        raw.setdefault("base_url", os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3")
        config = Config(**raw)
    else:
        if not args.model or not args.environment_id or not args.state_dir:
            raise AdapterError("model_environment_and_state_dir_required")
        command = json.loads(args.mcp_command_json) if args.mcp_command_json else []
        if not isinstance(command, list) or any(not isinstance(x, str) for x in command):
            raise AdapterError("mcp_command_must_be_string_argv")
        config = Config(
            model=args.model, environment_id=args.environment_id,
            workspace=args.workspace.resolve(), state_dir=args.state_dir.resolve(),
            base_url=os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3",
            mcp_command=tuple(command), tool_names=tuple(args.tool), mcp_env=tuple(args.mcp_env),
            timeout_seconds=args.timeout_seconds, tool_timeout_seconds=args.tool_timeout_seconds,
            max_tool_calls=args.max_tool_calls,
            sandbox_builtins=args.sandbox_builtins,
        )
    if args.doctor:
        return {"ok": True, "provider": "loopx-ark-turn", "context": "fresh", "model": config.model,
                "credential_present": bool(os.environ.get("ARK_API_KEY")), "selected_tools": list(config.tool_names),
                "continuation_owner": "loopx_turn", "network_checked": False,
                "sandbox_builtins": config.sandbox_builtins,
                **({"sandbox_networking": "unrestricted"} if config.sandbox_builtins else {}),
                "sandbox_enforcement_verified": False}
    receipt = None
    if args.inspect_turn_key or args.cleanup_turn_key:
        receipt = Receipt(config.state_dir, args.inspect_turn_key or args.cleanup_turn_key)
        receipt.read()
        if receipt.data.get("provider_config_digest") != config_digest(config):
            raise AdapterError("host_receipt_configuration_mismatch")
        if args.inspect_turn_key:
            return {"ok": True, **receipt.projection()}
    key = os.environ.get("ARK_API_KEY")
    if not key:
        raise AdapterError("ARK_API_KEY_required")
    options = {"api_key": key, "max_retries": 0, "timeout": 15.0, "base_url": config.base_url}
    async with AsyncArk.volc(**options) as client:
        if receipt is not None:
            with exclusive_file_lock(receipt.path, policy=LockAcquisitionPolicy.SINGLE_FLIGHT):
                receipt.read()
                if receipt.data.get("provider_config_digest") != config_digest(config):
                    raise AdapterError("host_receipt_configuration_mismatch")
                return {"ok": await cleanup(client, receipt), **receipt.projection()}
        text = sys.stdin.read(256_001)
        if len(text) > 256_000:
            raise AdapterError("request_exceeds_limit")
        request = json.loads(text)
        if not isinstance(request, dict):
            raise AdapterError("request_must_be_object")
        return await run(request, config, client)


async def interruptible(args: argparse.Namespace) -> dict:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed = False
    try:
        if task is not None:
            try:
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
                installed = True
            except NotImplementedError:
                pass
        return await execute(args)
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(interruptible(args))
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("ark_turn_failed: interrupted; inspect the private host receipt", file=sys.stderr)
        return 130
    except Exception as exc:
        reason = str(exc) if isinstance(exc, AdapterError) else type(exc).__name__
        print("ark_turn_failed: " + reason, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 1 if result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
