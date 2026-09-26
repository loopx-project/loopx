"""The Goal instance rollout keeps an explicit first-party owner census."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess

from loopx.semantics.project_registry_io import (
    PROJECT_REGISTRY_IO_MANIFEST,
    validate_project_registry_io_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = (
    REPO_ROOT / "loopx" / "semantics" / "goal_instance_binding_inventory_v1.json"
)
REQUIRED_OWNER_IDS = {
    "attached_host_chat_session",
    "first_party_host_runtime",
    "global_goal_projection",
    "goal_channel_lark",
    "handoff_inbox_outbox",
    "heartbeat_automation",
    "project_registry_goal",
    "project_session_binding",
    "quota_settlement",
    "todo_coordination_lease",
    "turn_journal",
}
REQUIRED_OWNER_FIELDS = {
    "owner_id",
    "locator",
    "revision_signal",
    "content_digest_signal",
    "observed_reference",
    "producer_sites",
    "consumer_sites",
    "effect_boundary",
    "authority_role",
    "current_identity_strength",
    "cleanup_support",
    "m1_disposition",
    "target_milestone",
}
OBSERVED_OWNER_IDS = {
    "global_goal_projection",
    "project_registry_goal",
}
QUALIFIED_OWNER_IDS = {"attached_host_chat_session"}
TYPESCRIPT_DECLARATION = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)


def _inventory() -> dict[str, object]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _python_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.add(node.name)
            symbols.update(
                f"{node.name}.{child.name}"
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    return symbols


def _typescript_symbols(path: Path) -> set[str]:
    return set(TYPESCRIPT_DECLARATION.findall(path.read_text(encoding="utf-8")))


def _assert_anchor(anchor: str, tracked_paths: set[str]) -> None:
    parts = anchor.split("::")
    assert len(parts) == 2, f"invalid path::symbol anchor: {anchor}"
    relative, symbol = parts
    assert relative in tracked_paths, f"inventory path is not tracked: {relative}"
    path = REPO_ROOT / relative
    assert path.is_file(), f"inventory path does not exist: {relative}"
    if path.suffix == ".py":
        symbols = _python_symbols(path)
    elif path.suffix == ".ts":
        symbols = _typescript_symbols(path)
    else:
        raise AssertionError(f"unsupported inventory source suffix: {relative}")
    assert symbol in symbols, f"inventory symbol does not exist: {anchor}"


def test_goal_instance_binding_inventory_is_complete_and_anchored() -> None:
    inventory = _inventory()
    assert inventory["schema_version"] == (
        "loopx_goal_instance_binding_inventory_v1"
    )
    scope = inventory["scope"]
    assert isinstance(scope, dict)
    assert scope["baseline"] == "M1 characterization"
    assert scope["claim"] == "first_party_goal_binding_owner_families"

    owners = inventory["owners"]
    assert isinstance(owners, list)
    owner_ids = [owner["owner_id"] for owner in owners]
    assert owner_ids == sorted(REQUIRED_OWNER_IDS)
    assert set(owner_ids) == REQUIRED_OWNER_IDS

    tracked = subprocess.run(
        ["git", "ls-files", "loopx"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_paths = set(tracked.stdout.splitlines())
    for owner in owners:
        assert isinstance(owner, dict)
        assert set(owner) == REQUIRED_OWNER_FIELDS
        for field in REQUIRED_OWNER_FIELDS - {"producer_sites", "consumer_sites"}:
            assert isinstance(owner[field], str) and owner[field].strip(), (
                owner["owner_id"],
                field,
            )
        for role in ("producer_sites", "consumer_sites"):
            sites = owner[role]
            assert isinstance(sites, list) and sites
            assert sites == sorted(set(sites)), (owner["owner_id"], role)
            for site in sites:
                _assert_anchor(site, tracked_paths)
        _assert_anchor(owner["effect_boundary"], tracked_paths)


def test_m1_observation_claims_are_bounded_to_the_selected_lifecycle() -> None:
    owners = _inventory()["owners"]
    observed = {
        owner["owner_id"]
        for owner in owners
        if owner["m1_disposition"] == "observed"
    }

    assert observed == OBSERVED_OWNER_IDS
    for owner in owners:
        if owner["owner_id"] in OBSERVED_OWNER_IDS:
            assert owner["current_identity_strength"] == (
                "optional_instance_observation"
            )
            assert owner["effect_boundary"] == (
                "loopx/control_plane/goals/operator_actions.py::"
                "build_goal_action_catalog"
            )
            assert owner["target_milestone"] == "M2"
        elif owner["owner_id"] in QUALIFIED_OWNER_IDS:
            assert owner["m1_disposition"] == "m3_qualified"
            assert owner["current_identity_strength"] == "exact_goal_ref_enforced"
            assert owner["target_milestone"] == "M3"
        else:
            assert owner["m1_disposition"] == "alias_only_inventory"
            assert owner["current_identity_strength"] == "goal_alias_only"
            assert owner["target_milestone"] in {"M2", "M3"}


def test_goal_instance_inventory_does_not_replace_the_registry_io_census() -> None:
    manifest = json.loads(
        (REPO_ROOT / PROJECT_REGISTRY_IO_MANIFEST).read_text(encoding="utf-8")
    )

    assert validate_project_registry_io_manifest(REPO_ROOT, manifest) == []
