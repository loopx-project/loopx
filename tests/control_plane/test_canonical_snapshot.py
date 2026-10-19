"""Cross-language transport admission, independent of Todo domain validation."""

from copy import deepcopy

import pytest

from loopx.control_plane.coordination.canonical_snapshot import (
    METHOD,
    RESULT_SCHEMA,
    read_canonical_snapshot,
)


def pages():
    snapshot = {
        "goal_id": "goal-a",
        "store_identity": "store-a",
        "provider_revision": "r1",
        "cursor": "1",
        "query_sha256": "query-a",
        "todo_count": 3,
        "lease_count": 1,
    }
    metadata = {
        "todo_read_model": {"todo_count": 3},
        "handoff_mode": "native",
        "goal_acceptance_contract": {"enabled": True},
    }
    base = {
        "schema_version": RESULT_SCHEMA,
        "status": "page",
        "snapshot": snapshot,
        "metadata": metadata,
        "source_authority": "sqlite_v0",
        "decision_read_from_provider": True,
        "legacy_fallback_used": False,
    }
    return deepcopy(
        [
            {
                **base,
                "todos": [
                    {"todo_id": "a", "text": "保留🙂"},
                    {"todo_id": "b", "archive_state": "archived"},
                ],
                "leases": [],
                "goal_acceptance_work_guards": {"a": {"allowed": False}},
                "next": {"snapshot": snapshot, "todo_offset": 2, "lease_offset": 0},
            },
            {
                **base,
                "todos": [{"todo_id": "c", "depends_on": ["b"]}],
                "leases": [{"todo_id": "a", "version": 4}],
                "goal_acceptance_work_guards": {"c": {"allowed": True}},
                "next": None,
            },
        ]
    )


def read(responses, *, include_leases=True):
    calls = []

    def rpc(method, request, *, timeout, large_local_snapshot):
        assert method == METHOD
        assert request["include_leases"] is include_leases
        assert timeout == 15
        assert large_local_snapshot is True
        assert request["after"] == (None if not calls else calls[-1]["next"])
        response = responses[len(calls)]
        calls.append(deepcopy(response))
        return response

    result = read_canonical_snapshot(
        rpc=rpc,
        runtime_root="/disposable",
        goal_id="goal-a",
        include_leases=include_leases,
        projection_readback=None,
        timeout=15,
    )
    return result, calls


def test_complete_snapshot_preserves_archives_guards_and_leases():
    source = pages()
    result, calls = read(source)
    assert len(calls) == 2
    assert result["status"] == "loaded"
    assert result["todos"] == source[0]["todos"] + source[1]["todos"]
    assert result["todo_ids"] == ["a", "b", "c"]
    assert result["leases"] == source[1]["leases"]
    assert result["goal_acceptance_work_guards"] == {
        "a": {"allowed": False},
        "c": {"allowed": True},
    }
    assert result["provider_revision"] == "r1"


@pytest.mark.parametrize("status", ["failed", "unavailable", "missing"])
def test_later_page_failure_discards_all_earlier_records(status):
    source = pages()
    source[1] = {
        "status": status,
        "reason_code": "canonical_snapshot_changed",
        "reason": "restart",
        "todos": [{"todo_id": "untrusted-partial"}],
    }
    result, calls = read(source)
    assert len(calls) == 2
    assert result == {
        "schema_version": "loopx_local_coordination_todo_list_result_v0",
        "status": status,
        "reason_code": "canonical_snapshot_changed",
        "reason": "restart",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "revision",
        "identity",
        "query",
        "metadata",
        "source",
        "duplicate",
        "out_of_order",
        "premature_end",
        "over_count",
        "non_integer_count",
        "skipped_offset",
        "boolean_offset",
        "no_progress",
        "leases_first",
        "foreign_guard",
        "missing_next",
        "fallback",
        "unpaged",
        "metadata_injection",
        "non_object",
        "missing_rows",
        "foreign_goal",
        "missing_snapshot",
    ],
)
def test_malformed_or_mixed_pages_never_escape_as_partial_success(mutation):
    source = pages()
    # Break aliasing deliberately: changing a later page must not mutate the first snapshot.
    source = [deepcopy(page) for page in source]
    first, last = source
    if mutation in {"revision", "identity", "query"}:
        last["snapshot"][
            {
                "revision": "provider_revision",
                "identity": "store_identity",
                "query": "query_sha256",
            }[mutation]
        ] = "other"
    elif mutation == "metadata":
        last["metadata"]["handoff_mode"] = "legacy"
    elif mutation == "source":
        last["source_authority"] = "file_v0"
    elif mutation == "duplicate":
        last["todos"][0]["todo_id"] = "b"
    elif mutation == "out_of_order":
        first["todos"].reverse()
    elif mutation == "premature_end":
        first["next"] = None
    elif mutation == "over_count":
        last["todos"].append({"todo_id": "d"})
    elif mutation == "non_integer_count":
        first["snapshot"]["todo_count"] = True
    elif mutation == "skipped_offset":
        first["next"]["todo_offset"] = 3
    elif mutation == "boolean_offset":
        first["next"]["lease_offset"] = False
    elif mutation == "no_progress":
        first["todos"] = []
        first["goal_acceptance_work_guards"] = {}
        first["next"]["todo_offset"] = 0
    elif mutation == "leases_first":
        first["leases"] = [{"todo_id": "a"}]
    elif mutation == "foreign_guard":
        last["goal_acceptance_work_guards"]["a"] = {"allowed": True}
    elif mutation == "missing_next":
        del last["next"]
    elif mutation == "fallback":
        last["legacy_fallback_used"] = True
    elif mutation == "unpaged":
        last["status"] = "loaded"
    elif mutation == "metadata_injection":
        last["metadata"]["todos"] = []
    elif mutation == "non_object":
        source[1] = []
    elif mutation == "missing_rows":
        del last["todos"]
    elif mutation == "foreign_goal":
        last["snapshot"]["goal_id"] = "goal-b"
    elif mutation == "missing_snapshot":
        del last["snapshot"]
    result, _ = read(source)
    assert result["status"] == "failed"
    assert result["reason_code"] == "canonical_snapshot_result_invalid"
    assert "todos" not in result and "leases" not in result


def test_empty_snapshot_is_complete_without_legacy_fallback():
    page = pages()[0]
    page["snapshot"].update(todo_count=0, lease_count=0)
    page.update(todos=[], leases=[], goal_acceptance_work_guards={}, next=None)
    page["metadata"]["todo_read_model"]["todo_count"] = 0
    result, calls = read([page])
    assert len(calls) == 1
    assert result["status"] == "loaded" and result["todos"] == []
    assert result["legacy_fallback_used"] is False


def test_todo_only_read_rejects_unrequested_lease_population():
    result, _ = read(pages(), include_leases=False)
    assert result["reason_code"] == "canonical_snapshot_result_invalid"


def test_unicode_order_is_code_point_order_across_runtimes():
    source = pages()
    source[0]["todos"][0]["todo_id"] = "z"
    source[0]["todos"][1]["todo_id"] = "\ue000"
    source[1]["todos"][0]["todo_id"] = "🙂"
    for page in source:
        page["goal_acceptance_work_guards"] = {}
    result, _ = read(source)
    assert result["todo_ids"] == ["z", "\ue000", "🙂"]
