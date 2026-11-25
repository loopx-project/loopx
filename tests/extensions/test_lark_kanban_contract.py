from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from loopx.extensions.lark.presentation import (
    kanban,
    kanban_board,
    kanban_config,
    kanban_records,
    kanban_sync,
    record_io,
    sink_visibility,
    sync_receipt,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SLIM_MODULES = (
    "loopx/extensions/lark/presentation/record_io.py",
    "loopx/extensions/lark/presentation/sync_receipt.py",
    "loopx/extensions/lark/presentation/kanban_config.py",
    "loopx/extensions/lark/presentation/kanban_records.py",
    "loopx/extensions/lark/presentation/kanban_sync.py",
    "loopx/extensions/lark/presentation/kanban.py",
    "loopx/cli_commands/lark_kanban.py",
)


def test_kanban_split_modules_stay_under_budget_with_zero_any() -> None:
    for rel_path in SLIM_MODULES:
        source = (REPOSITORY_ROOT / rel_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) < 700, f"{rel_path} exceeded 700 LOC ({len(lines)})"
        assert "type: ignore" not in source, f"{rel_path} must not use type: ignore"
        tree = ast.parse(source, filename=rel_path)
        any_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert any_nodes == [], f"{rel_path} contains forbidden AST Any nodes"


def test_kanban_facade_preserves_canonical_export_identities() -> None:
    assert (
        kanban.SINK_VISIBILITY_OWNER_ONLY
        is sink_visibility.SINK_VISIBILITY_OWNER_ONLY
    )
    assert kanban.SINK_VISIBILITY_SHARED is sink_visibility.SINK_VISIBILITY_SHARED
    assert kanban.SINK_VISIBILITIES is sink_visibility.SINK_VISIBILITIES

    proj_default = inspect.signature(
        kanban.sync_loopx_projection_to_lark_kanban
    ).parameters["sink_visibility"].default
    todo_default = inspect.signature(
        kanban.sync_loopx_todos_to_lark_kanban
    ).parameters["sink_visibility"].default
    assert proj_default is sink_visibility.SINK_VISIBILITY_OWNER_ONLY
    assert todo_default is sink_visibility.SINK_VISIBILITY_SHARED

    assert kanban.LarkKanbanConfig is kanban_config.LarkKanbanConfig
    assert (
        kanban.save_lark_kanban_board_config
        is kanban_config.save_lark_kanban_board_config
    )
    assert (
        kanban.sync_loopx_projection_to_lark_kanban
        is kanban_sync.sync_loopx_projection_to_lark_kanban
    )
    assert (
        kanban.sync_loopx_todos_to_lark_kanban
        is kanban_sync.sync_loopx_todos_to_lark_kanban
    )
    assert (
        kanban._lark_record_from_todo_block
        is kanban_records._lark_record_from_todo_block
    )
    assert (
        kanban.compact_lark_kanban_sync_receipt
        is sync_receipt.compact_lark_kanban_sync_receipt
    )
    assert kanban.render_lark_kanban_markdown is sync_receipt.render_lark_kanban_markdown
    assert kanban.build_record_delete_command is record_io.build_record_delete_command
    assert kanban.use_lark_kanban_board is kanban_board.use_lark_kanban_board


def test_kanban_config_is_frozen_and_builds_record_delete_command(
    tmp_path: Path,
) -> None:
    cfg = kanban.LarkKanbanConfig(base_token="bas123", table_id="tbl456")
    assert is_dataclass(cfg)
    with pytest.raises(FrozenInstanceError):
        setattr(cfg, "table_id", "tbl999")

    delete_cmd = kanban.build_record_delete_command(cfg, record_ids=["recA", "recB"])
    assert delete_cmd == [
        cfg.cli_bin,
        "base",
        "+record-delete",
        "--as",
        cfg.identity,
        "--base-token",
        "bas123",
        "--table-id",
        "tbl456",
        "--record-id",
        "recA",
        "--record-id",
        "recB",
        "--yes",
    ]

    config_path = tmp_path / "lark-kanban.json"
    saved = kanban.save_lark_kanban_board_config(
        config_path,
        base_token="bas123",
        table_id="tbl456",
        view_ids={"Worker Queue": "vew1"},
    )
    assert saved["ok"] is True
    loaded = kanban.read_lark_kanban_local_config(config_path)
    assert loaded["ok"] is True
    parsed_cfg = kanban.lark_kanban_config_from_payload(loaded)
    assert parsed_cfg is not None
    assert parsed_cfg.base_token == "bas123"
    assert parsed_cfg.table_id == "tbl456"


def test_choose_heartbeat_task_and_fail_closed_reconcile() -> None:
    selected = kanban.choose_heartbeat_task(
        [
            {"Status": kanban.STATUS_DONE, "Claim": kanban.CLAIM_AGENT, "Claimed By": "worker-a"},
            {"Status": kanban.STATUS_TODO, "Claim": kanban.CLAIM_UNCLAIMED, "Task": "unclaimed"},
            {
                "Status": kanban.STATUS_CLAIMED,
                "Claim": kanban.CLAIM_AGENT,
                "Claimed By": "worker-a",
                "Task": "own-claim",
            },
        ],
        agent_id="worker-a",
    )
    assert selected is not None
    assert selected["Task"] == "own-claim"

    cfg = kanban.LarkKanbanConfig(base_token="bas123", table_id="tbl456")
    with pytest.raises(ValueError, match="sink_visibility must be one of"):
        kanban.sync_loopx_projection_to_lark_kanban(
            cfg,
            projection={"goal_id": "g1", "items": []},
            sink_visibility="invalid-mode",
        )

    with pytest.raises(ValueError, match="complete source snapshot"):
        kanban.sync_loopx_projection_to_lark_kanban(
            cfg,
            projection={"goal_id": "g1", "source_id": "src-1", "items": []},
            reconcile_source=True,
            source_snapshot_complete=False,
        )
