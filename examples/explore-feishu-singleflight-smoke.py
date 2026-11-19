#!/usr/bin/env python3
"""Prove overlapping Explore Feishu deliveries fail before remote writes."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.cli_commands import explore as explore_cli  # noqa: E402
from loopx.cli_commands import explore_feishu_commands as explore_feishu  # noqa: E402
from loopx.extensions.lark.presentation.explore_singleflight import (  # noqa: E402
    explore_feishu_sync_singleflight,
    singleflight_issue_fix_material_sync,
)
from loopx.extensions.runtime import (  # noqa: E402
    default_extension_state_file,
    install_extension,
)


def _child_attempt(config_path: str, queue: multiprocessing.Queue) -> None:
    with explore_feishu_sync_singleflight(
        config_path=Path(config_path), execute=True
    ) as acquired:
        queue.put(acquired)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-explore-singleflight-") as tmp:
        root = Path(tmp)
        config_path = root / ".loopx" / "lark-explore.json"
        registry_path = root / ".loopx" / "registry.json"
        config_path.parent.mkdir(parents=True)
        sentinel = '{"sentinel":"unchanged"}\n'
        config_path.write_text(sentinel, encoding="utf-8")
        install_extension(
            ROOT / "loopx" / "extensions" / "lark" / "extension.toml",
            state_file=default_extension_state_file(root),
            execute=True,
        )

        with explore_feishu_sync_singleflight(
            config_path=config_path, execute=True
        ) as acquired:
            assert acquired is True

            ctx = multiprocessing.get_context("spawn")
            queue = ctx.Queue()
            child = ctx.Process(
                target=_child_attempt,
                args=(str(config_path), queue),
            )
            child.start()
            child.join(timeout=10)
            assert child.exitcode == 0, child.exitcode
            assert queue.get(timeout=2) is False

            with explore_feishu_sync_singleflight(
                config_path=config_path, execute=False
            ) as dry_run_acquired:
                assert dry_run_acquired is True

            with explore_feishu_sync_singleflight(
                config_path=config_path, execute=True
            ) as nested_acquired:
                assert nested_acquired is True

            nested_calls: list[str] = []

            @singleflight_issue_fix_material_sync(
                lambda _path, _goal_id, _runtime_override: config_path
            )
            def nested_material(**_kwargs):
                nested_calls.append("material")
                return {"ok": True, "status": "synced"}

            material = nested_material(
                registry_path=registry_path,
                goal_id="fixture-goal",
                execute=True,
            )
            assert material == {"ok": True, "status": "synced"}, material
            assert nested_calls == ["material"], nested_calls

            captured: list[dict[str, object]] = []
            original_load_registry = explore_cli.load_registry
            original_resolve_runtime_root = explore_cli.resolve_runtime_root
            original_projection_for = explore_cli._projection_for
            original_target_config = explore_feishu._target_config
            original_sync_results = explore_feishu.sync_explore_results_to_lark
            original_read_config = explore_feishu.read_lark_explore_local_config
            original_sync_visual = explore_feishu.sync_explore_visual_to_lark
            direct_calls: list[str] = []
            explore_cli.load_registry = lambda _path: {}
            explore_cli.resolve_runtime_root = lambda _registry, _arg: root
            explore_cli._projection_for = lambda _args, **_kwargs: {
                "schema_version": "loopx_explore_result_projection_v0",
                "goal_id": "fixture-goal",
                "nodes": [],
                "edges": [],
                "findings": [],
            }
            explore_feishu._target_config = lambda _args, **_kwargs: object()
            explore_feishu.sync_explore_results_to_lark = lambda *_args, **_kwargs: (
                direct_calls.append("rows")
                or {"ok": True, "status": "synced", "execute": True}
            )
            explore_feishu.read_lark_explore_local_config = lambda _path: {}
            explore_feishu.sync_explore_visual_to_lark = lambda *_args, **_kwargs: (
                direct_calls.append("visual")
                or {"ok": True, "status": "published", "execute": True}
            )
            try:
                result = explore_cli.handle_explore_command(
                    argparse.Namespace(
                        command="explore",
                        explore_command="feishu-sync",
                        config_path=str(config_path),
                        execute=True,
                        goal_id="",
                        sink_visibility="owner-only",
                    ),
                    registry_path=registry_path,
                    runtime_root_arg=str(root),
                    print_payload=lambda payload, _fmt, _renderer: captured.append(
                        payload
                    ),
                    output_format=lambda _args: "json",
                )
            finally:
                explore_cli.load_registry = original_load_registry
                explore_cli.resolve_runtime_root = original_resolve_runtime_root
                explore_cli._projection_for = original_projection_for
                explore_feishu._target_config = original_target_config
                explore_feishu.sync_explore_results_to_lark = original_sync_results
                explore_feishu.read_lark_explore_local_config = original_read_config
                explore_feishu.sync_explore_visual_to_lark = original_sync_visual
            assert result == 0, result
            assert captured[0]["status"] == "synced", captured
            assert captured[0]["visual_sync"]["status"] == "published", captured
            assert direct_calls == ["rows", "visual"], direct_calls
            assert config_path.read_text(encoding="utf-8") == sentinel

        with explore_feishu_sync_singleflight(
            config_path=config_path, execute=True
        ) as acquired_after_release:
            assert acquired_after_release is True

    print("explore-feishu-singleflight-smoke: ok")


if __name__ == "__main__":
    main()
