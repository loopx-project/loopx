from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

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
        assert ("type:" + " ignore") not in source, f"{rel_path} contains ignore directive"
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


def test_mapping_proxy_inputs_match_dict_behavior(tmp_path: Path) -> None:
    cfg = kanban.LarkKanbanConfig(base_token="bas123", table_id="tbl456")

    parsed_from_proxy = kanban.lark_kanban_config_from_payload(
        MappingProxyType(
            {
                "board": MappingProxyType(
                    {"base_token": "bas123", "table_id": "tbl456", "view_id": "Worker Queue"}
                )
            }
        )
    )
    assert parsed_from_proxy == cfg

    saved_from_proxy = kanban.save_lark_kanban_board_config(
        tmp_path / "proxy-kanban.json",
        base_token="bas123",
        table_id="tbl456",
        view_ids=MappingProxyType({"Worker Queue": "vew1"}),
    )
    assert saved_from_proxy["ok"] is True

    proj_dict = {
        "goal_id": "goal_proxy",
        "source_id": "src-proxy",
        "items": [{"todo_id": "todo_proxy", "text": "Sync proxy projection", "status": "open"}],
    }
    proj_proxy = MappingProxyType(
        {
            "goal_id": "goal_proxy",
            "source_id": "src-proxy",
            "items": [
                MappingProxyType(
                    {"todo_id": "todo_proxy", "text": "Sync proxy projection", "status": "open"}
                )
            ],
        }
    )
    res_dict = kanban.sync_loopx_projection_to_lark_kanban(cfg, projection=proj_dict)
    res_proxy = kanban.sync_loopx_projection_to_lark_kanban(cfg, projection=proj_proxy)
    assert res_proxy == res_dict

    with pytest.raises(ValueError, match="projection must be a JSON object"):
        kanban.sync_loopx_projection_to_lark_kanban(
            cfg,
            projection=cast(dict[str, object], ["not-a-mapping"]),
        )

    fixture_dict = {
        "data": {
            "fields": ["Task", "Status", "Claim", "Claimed By", "Run History", "Worker Command"],
            "data": [["Check proxy", kanban.STATUS_TODO, kanban.CLAIM_UNCLAIMED, "", "", "echo ok"]],
            "record_id_list": ["rec_proxy_1"],
        }
    }
    fixture_proxy = MappingProxyType(
        {
            "data": MappingProxyType(
                {
                    "fields": ["Task", "Status", "Claim", "Claimed By", "Run History", "Worker Command"],
                    "data": [["Check proxy", kanban.STATUS_TODO, kanban.CLAIM_UNCLAIMED, "", "", "echo ok"]],
                    "record_id_list": ["rec_proxy_1"],
                }
            )
        }
    )
    assert kanban.lark_record_rows(fixture_proxy) == kanban.lark_record_rows(fixture_dict)
    hb_dict = kanban.lark_kanban_heartbeat(cfg, fixture=fixture_dict)
    hb_proxy = kanban.lark_kanban_heartbeat(cfg, fixture=fixture_proxy)
    assert hb_proxy["decision"] == hb_dict["decision"] == "task_processed"
    assert hb_proxy["selected_record_id"] == hb_dict["selected_record_id"] == "rec_proxy_1"

    task_proxy = MappingProxyType({"Task": "Proxy task", "Status": kanban.STATUS_TODO})
    seeded_one = kanban.seed_lark_kanban_task(cfg, task=task_proxy)
    seeded_many = kanban.seed_lark_kanban_records(cfg, records=(task_proxy,))
    assert seeded_one["ok"] is True
    assert seeded_many["record_count"] == 1

    receipt_proxy = MappingProxyType(
        {
            "ok": True,
            "schema_version": "loopx_lark_kanban_sync_todos_v0",
            "records": [
                MappingProxyType(
                    {
                        "todo_id": "t1",
                        "record_id": "rec1",
                        "command": MappingProxyType({"ok": True, "executed": False}),
                        "values": MappingProxyType({"Work Item Type": "Todo"}),
                    }
                )
            ],
            "commands": [MappingProxyType({"ok": True, "executed": False, "command": ["cmd"]})],
        }
    )
    compact = kanban.compact_lark_kanban_sync_receipt(receipt_proxy)
    assert compact["record_success_count"] == 1
    assert "###" not in kanban.render_lark_kanban_markdown(compact)


def test_render_lark_kanban_markdown_preserves_summary_headers() -> None:
    todos_receipt = {
        "ok": True,
        "schema_version": "loopx_lark_kanban_sync_todos_v0",
        "execute": False,
        "goal_id": "g-md-probe",
        "base_token": "bas1234567",
        "table_id": "tbl1234567",
        "view_id": "Worker Queue",
        "todo_count": 2,
    }
    todos_md = kanban.render_lark_kanban_markdown(todos_receipt)
    assert todos_md.splitlines()[:6] == [
        "# LoopX Lark Kanban",
        "",
        "- ok: `True`",
        "- schema_version: `loopx_lark_kanban_sync_todos_v0`",
        "- base_token: `bas1234567`",
        "- table_id: `tbl1234567`",
    ]
    assert "- execute:" not in todos_md
    assert "- goal_id:" not in todos_md

    proj_receipt = {
        "ok": True,
        "schema_version": kanban.LARK_KANBAN_SYNC_PROJECTION_VERSION,
        "execute": True,
        "goal_id": "g-md-probe",
        "source_id": "src-probe",
        "detail_level": "compact",
        "record_count": 1,
    }
    proj_md = kanban.render_lark_kanban_markdown(proj_receipt)
    assert proj_md.splitlines()[:6] == [
        "# LoopX Lark Kanban",
        "",
        "- ok: `True`",
        f"- schema_version: `{kanban.LARK_KANBAN_SYNC_PROJECTION_VERSION}`",
        "- detail_level: `compact`",
        "- record_count: `1`",
    ]
    assert "- execute:" not in proj_md
    assert "- goal_id:" not in proj_md
    assert "- source_id:" not in proj_md
