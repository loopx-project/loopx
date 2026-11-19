from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from ....file_lock import try_exclusive_file_lock


def default_lark_explore_config_path(registry_path: Path | None = None) -> Path:
    if registry_path is not None:
        expanded = registry_path.expanduser()
        if expanded.parent.name == ".loopx":
            return expanded.parent / "lark-explore.json"
    return Path.cwd() / ".loopx" / "lark-explore.json"


def source_lark_explore_config_path(
    registry_path: Path | None, goal_id: str, runtime_root_override: str | None = None
) -> Path:
    """Resolve the sink config beside the goal's canonical source registry."""

    if registry_path is None or not goal_id:
        return default_lark_explore_config_path(registry_path)
    from .explore_source_guard import resolve_explore_source_registry

    source_registry_path, _ = resolve_explore_source_registry(
        registry_path=registry_path,
        goal_id=goal_id,
        runtime_root_override=runtime_root_override,
    )
    return default_lark_explore_config_path(source_registry_path)


_held_sync_targets: ContextVar[frozenset[str]] = ContextVar(
    "loopx_explore_feishu_sync_targets",
    default=frozenset(),
)


@contextmanager
def explore_feishu_sync_singleflight(
    *, config_path: Path, execute: bool
) -> Iterator[bool]:
    """Fail fast when another execution context is delivering this board.

    Dry-runs never contend because they perform no remote or config writes.
    Nested calls in the current context reuse an exact same-target lock; other
    contexts and processes still contend. The lock target is local-private and
    only the acquired/busy state enters public packets.
    """

    if not execute:
        yield True
        return
    target = config_path.with_name(f"{config_path.name}.feishu-sync")
    target_key = str(target.expanduser().resolve())
    held_targets = _held_sync_targets.get()
    if target_key in held_targets:
        yield True
        return
    with try_exclusive_file_lock(target, operation="lark_explore_sync") as lock_path:
        if lock_path is None:
            yield False
            return
        token = _held_sync_targets.set(held_targets | {target_key})
        try:
            yield True
        finally:
            _held_sync_targets.reset(token)


def explore_feishu_sync_busy_packet(
    *, schema_version: str = "loopx_lark_explore_sync_v0"
) -> dict[str, Any]:
    """Return the compact retryable contract for a rejected overlapping sync."""

    return {
        "ok": False,
        "schema_version": schema_version,
        "status": "sync_busy",
        "execute": True,
        "retryable": True,
        "reason_code": "explore_feishu_sync_in_progress",
        "external_write_performed": False,
        "row_readback_verified": False,
        "required_action": "retry after the active Explore Feishu sync exits",
        "error": "another Explore Feishu sync is already active for this config",
    }


def singleflight_issue_fix_material_sync(
    config_path_resolver: Callable[[Path | None, str, str | None], Path],
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Serialize an issue-fix material sync across row, visual, and checkpoint writes."""

    def decorate(
        function: Callable[..., dict[str, Any]],
    ) -> Callable[..., dict[str, Any]]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
            registry_path = kwargs.get("registry_path")
            if registry_path is None and args:
                registry_path = args[0]
            resolved_registry = (
                Path(registry_path) if registry_path is not None else None
            )
            goal_id = str(kwargs.get("goal_id") or "").strip()
            execute = bool(kwargs.get("execute", False))
            delivery_authorized = bool(
                kwargs.get("external_sink_delivery_authorized", True)
            )
            with explore_feishu_sync_singleflight(
                config_path=config_path_resolver(resolved_registry, goal_id, kwargs.get("runtime_root_override")),
                execute=bool(execute and delivery_authorized),
            ) as acquired:
                if not acquired:
                    return explore_feishu_sync_busy_packet(
                        schema_version="issue_fix_explore_lark_material_sync_v0"
                    )
                return function(*args, **kwargs)

        return wrapped

    return decorate
