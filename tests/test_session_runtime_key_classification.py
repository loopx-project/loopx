"""Typed word-level raw-material classification for the session-runtime projection."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator, Mapping
import io
from pathlib import Path
import tokenize
from types import MappingProxyType
from typing import cast

import pytest

from loopx.canary.maintainability_ratchet import (
    DECISION_POINT_LIMIT,
    DECISION_STATEMENT_LIMIT,
    _FunctionMetrics,
    _iter_functions,
)
from loopx.control_plane.runtime.session_runtime import (
    compact_session_runtime_readonly_projection,
)
from loopx.session_runtime import (
    KeyClassification,
    KeyState,
    RawMaterialCategory,
    SOURCE_ID_KEYS,
    build_session_runtime_readonly_projection,
    classify_session_runtime_key,
)


@pytest.mark.parametrize(
    "key",
    [
        # projection inputs
        "kind", "status", "actor", "summary", "recommended_action",
        "created_at", "session_id", "artifact_id",
        # pointers and counts that merely contain a raw-looking word
        "trace_id", "message_id", "catalog_id", "dialog_id", "login_at", "log_count",
        "conversation_id",
        # `message` is an exact raw key, not a raw word: its pointer/count neighbours stay compact
        "message_count", "message_ref",
        # usage metrics
        "token_count", "tokens_used", "max_tokens", "input_tokens", "output_tokens",
        "prompt_tokens", "prompt_token_count", "completion_tokens",
    ],
)
def test_compact_keys(key: str) -> None:
    assert classify_session_runtime_key(key).state is KeyState.COMPACT


@pytest.mark.parametrize(
    "key", ["logical_clock", "backlog", "changelog", "drawer", "content_type", "message_text", ""]
)
def test_unclassified_keys_are_neither_compact_nor_raw(key: str) -> None:
    assert classify_session_runtime_key(key) == (KeyState.UNCLASSIFIED, None)


@pytest.mark.parametrize(
    ("key", "category"),
    [
        ("token", RawMaterialCategory.CREDENTIAL),
        ("access_token", RawMaterialCategory.CREDENTIAL),
        ("auth_token", RawMaterialCategory.CREDENTIAL),
        ("api_token", RawMaterialCategory.CREDENTIAL),
        ("bearer_token", RawMaterialCategory.CREDENTIAL),
        ("refresh_token", RawMaterialCategory.CREDENTIAL),
        ("id_token", RawMaterialCategory.CREDENTIAL),
        ("accessToken", RawMaterialCategory.CREDENTIAL),
        ("api_key", RawMaterialCategory.CREDENTIAL),
        ("password", RawMaterialCategory.CREDENTIAL),
        ("secret", RawMaterialCategory.CREDENTIAL),
        ("credential_hint", RawMaterialCategory.CREDENTIAL),
        ("raw_transcript", RawMaterialCategory.TRANSCRIPT),
        ("transcript_path", RawMaterialCategory.TRANSCRIPT),
        ("messages", RawMaterialCategory.TRANSCRIPT),
        ("prompt", RawMaterialCategory.TRANSCRIPT),
        ("content", RawMaterialCategory.TRANSCRIPT),
        ("body", RawMaterialCategory.TRANSCRIPT),
        ("message", RawMaterialCategory.TRANSCRIPT),
        ("log", RawMaterialCategory.LOG),
        ("logs", RawMaterialCategory.LOG),
        ("log_path", RawMaterialCategory.LOG),
        ("raw_log", RawMaterialCategory.LOG),
        ("trace", RawMaterialCategory.LOG),
        ("traces", RawMaterialCategory.LOG),
        ("stack_trace", RawMaterialCategory.LOG),
        ("trace_path", RawMaterialCategory.LOG),
        ("raw_trace", RawMaterialCategory.LOG),
        ("local_path", RawMaterialCategory.LOCAL_PATH),
        ("file_path", RawMaterialCategory.LOCAL_PATH),
        ("output_text", RawMaterialCategory.RAW_OUTPUT),
        ("stdout_tail", RawMaterialCategory.RAW_OUTPUT),
        ("stderr_tail", RawMaterialCategory.RAW_OUTPUT),
        ("diff", RawMaterialCategory.RAW_OUTPUT),
        ("patch", RawMaterialCategory.RAW_OUTPUT),
    ],
)
def test_raw_material_keys_carry_a_typed_category(key: str, category: RawMaterialCategory) -> None:
    assert classify_session_runtime_key(key) == (KeyState.RAW_MATERIAL, category)


def test_substrings_never_match() -> None:
    # Every hint of the retired substring denylist embedded in a longer word.
    for key in ("catalog", "dialogue", "backlog", "tokenizer", "tracer", "drawn", "rawhide"):
        assert classify_session_runtime_key(key).state is not KeyState.RAW_MATERIAL, key


@pytest.mark.parametrize(
    ("key", "category"),
    [
        ("tokens_password", RawMaterialCategory.CREDENTIAL),
        ("tokens_transcript", RawMaterialCategory.TRANSCRIPT),
        ("raw_tokens", RawMaterialCategory.RAW_OUTPUT),
    ],
)
def test_raw_words_outrank_the_tokens_metric_rule(
    key: str,
    category: RawMaterialCategory,
) -> None:
    assert classify_session_runtime_key(key) == (KeyState.RAW_MATERIAL, category)


@pytest.mark.parametrize(
    ("key", "category"),
    [
        ("secret_id", RawMaterialCategory.CREDENTIAL),
        ("password_id", RawMaterialCategory.CREDENTIAL),
        ("transcript_id", RawMaterialCategory.TRANSCRIPT),
        ("raw_id", RawMaterialCategory.RAW_OUTPUT),
        ("stdout_id", RawMaterialCategory.RAW_OUTPUT),
        ("api_key_id", RawMaterialCategory.CREDENTIAL),
        ("access_token_ref", RawMaterialCategory.CREDENTIAL),
        ("tool_result_ref", RawMaterialCategory.RAW_OUTPUT),
        # neighbours of the explicit safe collisions stay fail-closed
        ("prompt_id", RawMaterialCategory.TRANSCRIPT),
        ("prompt_text_tokens", RawMaterialCategory.TRANSCRIPT),
        ("conversation_ref", RawMaterialCategory.TRANSCRIPT),
        ("conversation_log_count", RawMaterialCategory.TRANSCRIPT),
        ("messages_count", RawMaterialCategory.TRANSCRIPT),
    ],
)
def test_raw_evidence_outranks_generic_pointer_suffix(
    key: str,
    category: RawMaterialCategory,
) -> None:
    assert classify_session_runtime_key(key) == (KeyState.RAW_MATERIAL, category)


@pytest.mark.parametrize(
    ("key", "category"),
    [
        ("secret_id", RawMaterialCategory.CREDENTIAL),
        ("api_key_id", RawMaterialCategory.CREDENTIAL),
        ("transcript_id", RawMaterialCategory.TRANSCRIPT),
        ("raw_id", RawMaterialCategory.RAW_OUTPUT),
    ],
)
def test_pointer_shaped_raw_key_blocks_projection_without_copying_value(
    key: str,
    category: RawMaterialCategory,
) -> None:
    marker = f"RAW_POINTER_MARKER_{key}"
    payload = build_session_runtime_readonly_projection(
        goal_id="g",
        sessions=[
            {
                "session_id": "s",
                "next_action": "advance",
                key: marker,
            }
        ],
    )

    assert payload["boundary"]["raw_material_detected"] is True
    assert payload["boundary"]["raw_material_key_names"] == [key]
    assert payload["boundary"]["raw_material_categories"] == [category.value]
    assert payload["first_screen"]["agent_can_continue"] is False
    assert payload["work_lane_contract"]["must_attempt_work"] is False
    assert marker not in repr(payload)


def test_message_material_is_flagged_and_never_copied_to_first_screen() -> None:
    marker = "RAW_TRANSCRIPT_MARKER full conversation material"
    payload = build_session_runtime_readonly_projection(
        goal_id="g",
        events=[
            {
                "event_id": "e1",
                "kind": "blocker",
                "status": "blocked",
                "message": marker,
            }
        ],
    )
    assert payload["boundary"]["raw_material_detected"] is True
    assert payload["boundary"]["raw_material_key_names"] == ["message"]
    assert payload["boundary"]["raw_material_categories"] == ["transcript"]
    assert marker not in repr(payload)
    assert payload["first_screen"]["latest_blocker"] is None


def test_unclassified_keys_are_reported_but_do_not_block() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="g",
        sessions=[{"session_id": "s", "next_action": "advance", "backlog": "x", "drawer": "y"}],
    )
    assert payload["boundary"]["raw_material_detected"] is False
    assert payload["boundary"]["unclassified_key_names"] == ["backlog", "drawer"]
    assert payload["first_screen"]["agent_can_continue"] is True
    assert payload["work_lane_contract"]["must_attempt_work"] is True


def test_unclassified_key_report_is_bounded() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="g",
        sessions=[{f"opaque{index:03d}": index for index in range(40)}],
    )
    assert len(payload["boundary"]["unclassified_key_names"]) == 24


def test_compaction_keeps_typed_boundary_fields_bounded() -> None:
    payload = build_session_runtime_readonly_projection(
        goal_id="g",
        sessions=[
            {
                "session_id": "s",
                "api_key": "k",
                "raw_transcript": "t",
                "backlog": "b",
                **{f"opaque{index:02d}": index for index in range(12)},
            }
        ],
    )
    compact = compact_session_runtime_readonly_projection(payload)
    assert compact is not None
    boundary = compact["boundary"]
    assert boundary["raw_material_key_names"] == ["api_key", "raw_transcript"]
    assert boundary["raw_material_categories"] == ["credential", "transcript"]
    assert len(boundary["unclassified_key_names"]) == 8


def _ast_any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    )


def _comment_ignore_or_noqa_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT
        and ("type: ignore" in tok.string or "noqa" in tok.string)
    )


def test_session_runtime_zero_any_size_and_complexity_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sr_path = repo_root / "loopx" / "session_runtime.py"
    for path in (sr_path, Path(__file__)):
        source = path.read_text(encoding="utf-8")
        assert _ast_any_count(source) == 0, path
        assert _comment_ignore_or_noqa_count(source) == 0, path

    lines = sr_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 550
    assert "session_id" in SOURCE_ID_KEYS
    assert classify_session_runtime_key("status") == KeyClassification(KeyState.COMPACT)

    tree = ast.parse(sr_path.read_text(encoding="utf-8"))
    for qname, fn in _iter_functions(tree.body):
        metrics = _FunctionMetrics(fn)
        metrics.visit(fn)
        assert metrics.statement_count <= DECISION_STATEMENT_LIMIT, qname
        assert metrics.decision_points <= DECISION_POINT_LIMIT, qname


def test_projection_filters_non_mappings_reads_mappingproxy_and_dedups_repeated_keys() -> None:
    proxy_session = MappingProxyType(
        {
            "session_id": "s1",
            "created_at": "2026-01-01T00:00:00Z",
            "next_action": "advance step",
            "opaque_extra": "v1",
        }
    )
    proxy_event_a = MappingProxyType(
        {
            "event_id": "e1",
            "event_at": "2026-01-01T00:01:00Z",
            "opaque_extra": "v2",
        }
    )
    proxy_event_b = MappingProxyType(
        {
            "event_id": "e2",
            "updated_at": "2026-01-01T00:02:00Z",
            "opaque_extra": "v3",
        }
    )
    payload = build_session_runtime_readonly_projection(
        goal_id="g-proxy",
        sessions=[cast(Mapping[str, object], "malformed-entry"), proxy_session],
    )
    assert payload["source"]["source_refs"]["sessions"] == [
        {"kind": "session", "session_id": "s1"}
    ]
    assert payload["boundary"]["unclassified_key_names"] == ["opaque_extra"]
    assert payload["first_screen"]["agent_can_continue"] is True

    repeated = build_session_runtime_readonly_projection(
        goal_id="g-repeat",
        sessions=[proxy_session],
        events=[proxy_event_a, proxy_event_b],
    )
    assert repeated["source"]["latest_fact_at"] == "2026-01-01T00:02:00Z"
    assert repeated["boundary"]["unclassified_key_names"] == ["opaque_extra"]


def test_gate_blocker_validation_and_empty_text_precedence() -> None:
    blocked_without_todo = build_session_runtime_readonly_projection(
        goal_id="g-blocker",
        gates=[
            {"gate_id": "g-adv", "status": "open", "actor": "human", "advisory": True},
            {"gate_id": "g-nonblock", "status": "pending", "actor": "user", "blocking": False},
            {"gate_id": "g-blocker", "kind": "blocker", "status": "failed", "blocker": "disk full"},
        ],
    )
    assert blocked_without_todo["first_screen"]["waiting_on"] == "runtime"
    assert blocked_without_todo["first_screen"]["user_action_required"] is False
    assert blocked_without_todo["first_screen"]["latest_blocker"] == "disk full"
    assert blocked_without_todo["work_lane_contract"]["lane"] == "blocker"
    assert blocked_without_todo["attention_item"]["priority"] == "P0"

    empty_text_gate = build_session_runtime_readonly_projection(
        goal_id="g-empty-gate",
        gates=[
            {
                "gate_id": "g-open",
                "status": "pending",
                "requires_human_decision": True,
                "actor": "bot_router",
            }
        ],
        decision_results=[{"recommended_action": "continue"}],
    )
    assert empty_text_gate["first_screen"]["waiting_on"] == "operator"
    assert empty_text_gate["first_screen"]["user_action_required"] is True
    assert empty_text_gate["first_screen"]["first_user_todo"] is None
    assert empty_text_gate["first_screen"]["first_agent_todo"] is None
    assert empty_text_gate["first_screen"]["recommended_action"] is None
    assert empty_text_gate["attention_item"]["title"] is None
    assert empty_text_gate["work_lane_contract"]["lane"] == "user_gate"


class _LazyCachingSessionMapping(Mapping[str, object]):
    """Test Mapping that materializes missing action keys on lookup if probed directly."""

    def __init__(self, initial: Mapping[str, object]) -> None:
        self._data: dict[str, object] = dict(initial)
        self.probed_keys: list[str] = []

    def __getitem__(self, key: str) -> object:
        self.probed_keys.append(key)
        if key not in self._data and key in ("recommended_action", "agent_next_action", "next_action", "handoff", "summary"):
            self._data[key] = f"synthesized:{key}"
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(tuple(self._data))

    def __len__(self) -> int:
        return len(self._data)


class _LazyGetDict(dict[str, object]):
    """Test dict subclass whose .get() lazily caches recommended_action if called."""

    def get(self, key: str, default: object = None) -> object:
        if key not in self and key == "recommended_action":
            self[key] = "synthesized:get"
        return super().get(key, default)


def test_lazy_mapping_preserves_enumerated_key_snapshot_and_input_invariance() -> None:
    base_record: dict[str, object] = {"session_id": "s-idle"}
    lazy_session = _LazyCachingSessionMapping(base_record)
    lazy_proxy = MappingProxyType(_LazyCachingSessionMapping(base_record))
    lazy_subdict: defaultdict[str, object] = defaultdict(lambda: "synthesized", base_record)
    lazy_get_dict = _LazyGetDict(base_record)
    lazy_decision = _LazyCachingSessionMapping({"ref_id": "d-idle"})
    static_proxy = MappingProxyType({"session_id": "s-idle"})
    static_decision_proxy = MappingProxyType({"ref_id": "d-idle"})

    expected = build_session_runtime_readonly_projection(
        goal_id="g-lazy",
        sessions=[static_proxy],
    )
    assert expected["first_screen"]["recommended_action"] == "monitor for a material transition"
    assert expected["first_screen"]["agent_can_continue"] is False
    assert expected["work_lane_contract"]["lane"] == "monitor"
    assert expected["work_lane_contract"]["must_attempt_work"] is False

    for candidate in (base_record, lazy_session, lazy_proxy, lazy_subdict, lazy_get_dict):
        projected = build_session_runtime_readonly_projection(
            goal_id="g-lazy",
            sessions=[candidate],
        )
        assert projected == expected

    multi_group_expected = build_session_runtime_readonly_projection(
        goal_id="g-lazy",
        sessions=[static_proxy],
        decision_results=[static_decision_proxy],
    )
    multi_group_actual = build_session_runtime_readonly_projection(
        goal_id="g-lazy",
        sessions=[_LazyCachingSessionMapping(base_record)],
        decision_results=[lazy_decision],
    )
    assert multi_group_actual == multi_group_expected
    assert lazy_session.probed_keys == ["session_id"]
    assert lazy_decision.probed_keys == ["ref_id"]
    assert dict(lazy_session) == {"session_id": "s-idle"}
    assert dict(lazy_decision) == {"ref_id": "d-idle"}
    assert dict(lazy_subdict) == {"session_id": "s-idle"}
    assert dict(lazy_get_dict) == {"session_id": "s-idle"}
    assert list(lazy_proxy.keys()) == ["session_id"]
