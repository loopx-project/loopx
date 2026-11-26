from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import cast
import pytest

from loopx.capabilities.explore import harness_frontier, harness_queue, harness_runtime
from loopx.capabilities.explore.harness_runtime import (
    DEFAULT_OBSERVATION_WEIGHT,
    FRONTIER_REQUESTS_SCHEMA_VERSION,
    HARNESS_ARM_SCHEMA_VERSION,
    HARNESS_RUNTIME_POLICY_SCHEMA_VERSION,
    ITEM_FAILURE_POLICIES,
    ITEM_FAILURE_POLICY_FATAL,
    ITEM_FAILURE_POLICY_RECORD,
    VARIANT_CATALOG_SCHEMA_VERSION,
    ItemFailurePolicy,
    NoveltyLedger,
    SeedAdapter,
    VariantCatalog,
    _MIN_DURATION_MINUTES,
    _adapter_error_record,
    _frontier_gain,
    _normalize_item_failure_policy,
    _now_perf_minutes,
    _read_json,
    _write_json,
    aggregate_arms,
    emit_frontier_requests,
    plan_round_robin,
    plan_router_no_prune,
    run_budget_arm,
    run_queue_epoch,
    select_frontier_work,
    step_auc,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ast_any_nodes(source: str, filename: str) -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            hits.append((node.lineno, "Name"))
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            hits.append((node.lineno, "Attribute"))
        elif isinstance(node, ast.alias) and node.name == "Any":
            hits.append((node.lineno, "alias"))
    return hits


def test_harness_modules_satisfy_zero_any_and_loc_budgets() -> None:
    root = _repo_root()
    budgets = {
        "loopx/capabilities/explore/harness_runtime.py": 650,
        "loopx/capabilities/explore/harness_frontier.py": 749,
        "loopx/capabilities/explore/harness_queue.py": 749,
        "tests/capabilities/test_explore_harness_runtime_contract.py": 749,
    }
    for rel_path, max_lines in budgets.items():
        source = (root / rel_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) <= max_lines, f"{rel_path} exceeded {max_lines} LOC: {len(lines)}"
        assert _ast_any_nodes(source, rel_path) == [], f"{rel_path} uses Any"
        type_ignore_marker = "type" + ": ignore"
        ignores = [
            idx + 1 for idx, line in enumerate(lines) if type_ignore_marker in line
        ]
        assert ignores == [], f"{rel_path} has type-ignore comments on lines {ignores}"


def test_harness_runtime_reexports_all_public_and_shared_helper_symbols() -> None:
    expected_public = {
        "DEFAULT_OBSERVATION_WEIGHT",
        "FRONTIER_REQUESTS_SCHEMA_VERSION",
        "HARNESS_ARM_SCHEMA_VERSION",
        "HARNESS_RUNTIME_POLICY_SCHEMA_VERSION",
        "ITEM_FAILURE_POLICIES",
        "ITEM_FAILURE_POLICY_FATAL",
        "ITEM_FAILURE_POLICY_RECORD",
        "VARIANT_CATALOG_SCHEMA_VERSION",
        "HarnessArmResult",
        "HarnessArmSummary",
        "HarnessCheckpointPoint",
        "HarnessEpochRecord",
        "HarnessEpisodePolicy",
        "HarnessLaneRecord",
        "HarnessResumeSummary",
        "HarnessRuntimePolicy",
        "HarnessScoreSummary",
        "ItemFailurePolicy",
        "NoveltyLedger",
        "SeedAdapter",
        "VariantCatalog",
        "aggregate_arms",
        "emit_frontier_requests",
        "plan_round_robin",
        "plan_router_no_prune",
        "run_budget_arm",
        "run_queue_epoch",
        "select_frontier_work",
        "step_auc",
    }
    assert set(harness_runtime.__all__) == expected_public
    assert DEFAULT_OBSERVATION_WEIGHT == 1.0
    assert HARNESS_ARM_SCHEMA_VERSION == harness_queue.HARNESS_ARM_SCHEMA_VERSION
    assert (
        HARNESS_RUNTIME_POLICY_SCHEMA_VERSION
        == harness_queue.HARNESS_RUNTIME_POLICY_SCHEMA_VERSION
    )
    assert FRONTIER_REQUESTS_SCHEMA_VERSION == harness_frontier.FRONTIER_REQUESTS_SCHEMA_VERSION
    assert VARIANT_CATALOG_SCHEMA_VERSION == harness_frontier.VARIANT_CATALOG_SCHEMA_VERSION
    assert ITEM_FAILURE_POLICIES == {"record", "fatal"}
    assert ITEM_FAILURE_POLICY_FATAL == "fatal"
    assert ITEM_FAILURE_POLICY_RECORD == "record"
    assert ItemFailurePolicy is harness_queue.ItemFailurePolicy
    assert NoveltyLedger is harness_frontier.NoveltyLedger
    assert SeedAdapter is harness_queue.SeedAdapter
    assert VariantCatalog is harness_frontier.VariantCatalog
    assert select_frontier_work is harness_frontier.select_frontier_work
    assert emit_frontier_requests is harness_frontier.emit_frontier_requests
    assert plan_round_robin is harness_frontier.plan_round_robin
    assert plan_router_no_prune is harness_frontier.plan_router_no_prune
    assert run_queue_epoch is harness_queue.run_queue_epoch
    assert step_auc is harness_queue.step_auc
    assert aggregate_arms is harness_queue.aggregate_arms
    assert _frontier_gain is harness_frontier._frontier_gain
    assert _adapter_error_record is harness_queue._adapter_error_record
    assert _normalize_item_failure_policy is harness_queue._normalize_item_failure_policy
    assert _now_perf_minutes is harness_queue._now_perf_minutes
    assert _read_json is harness_queue._read_json
    assert _write_json is harness_queue._write_json
    assert _MIN_DURATION_MINUTES == harness_queue._MIN_DURATION_MINUTES


def test_empty_container_compatibility_and_fail_closed_type_guards(tmp_path: Path) -> None:
    ledger = NoveltyLedger()
    # Legacy callers sometimes serialize empty observation_keys as {} or weighted_flags as [].
    assert ledger.raw_value({"observation_keys": {}, "weighted_flags": []}) == 0.0
    assert ledger.credit({"family": "alpha", "observation_keys": {}, "weighted_flags": []}) == {
        "novel_value": 0.0,
        "novel_observation_keys": 0,
    }

    with pytest.raises(TypeError, match="observation_keys"):
        ledger.raw_value({"observation_keys": "scalar-str"})
    with pytest.raises(TypeError, match="observation_keys"):
        ledger.credit({"observation_keys": 42})
    with pytest.raises(TypeError, match="weighted_flags"):
        ledger.credit({"weighted_flags": ["flag-a"]})

    catalog_path = tmp_path / "catalog.json"
    consumption_path = tmp_path / "consumption.json"
    _write_json(catalog_path, {"specs": {}})
    _write_json(consumption_path, {})
    catalog = VariantCatalog(catalog_path, consumption_path)
    assert catalog.pending_count() == 0

    _write_json(catalog_path, {"specs": "not-a-sequence"})
    with pytest.raises(TypeError, match="specs"):
        catalog.pending_count()

    _write_json(consumption_path, "not-a-sequence")
    with pytest.raises(TypeError, match="variant_consumption"):
        VariantCatalog(catalog_path, consumption_path)

    empty_ckpt_agg = aggregate_arms(
        {"arm-a": {"checkpoints": {}, "novel_value_total": 3.0, "execution_metrics": {}}},
        budget_minutes=5.0,
        worker_count=1,
    )
    assert empty_ckpt_agg["arms"]["arm-a"]["novel_value_auc"] == 0.0
    assert (
        empty_ckpt_agg["arms"]["arm-a"]["novel_value_per_effective_compute_minute"] is None
    )

    with pytest.raises(TypeError, match="checkpoints"):
        aggregate_arms(
            {"arm-a": {"checkpoints": "not-a-list"}},
            budget_minutes=5.0,
            worker_count=1,
        )


def test_mapping_proxy_key_presence_and_zero_float_frontier_gain() -> None:
    seed_item = MappingProxyType({"item_id": "seed-1", "family": "alpha", "text": "check alpha"})
    router_state = MappingProxyType(
        {
            "families": MappingProxyType(
                {
                    "scope:artifacts/alpha": MappingProxyType(
                        {
                            "runs": 2,
                            "novelty_rate": 0.0,
                            "accept_rate_ema": 0.0,
                        }
                    )
                }
            )
        }
    )
    router_state_none_accept = MappingProxyType(
        {
            "families": MappingProxyType(
                {
                    "scope:artifacts/alpha": MappingProxyType(
                        {
                            "runs": 2,
                            "novelty_rate": 0.0,
                            "accept_rate_ema": None,
                        }
                    )
                }
            )
        }
    )
    # accept_rate_ema=0.0 qualifies for the <=0.5 bonus (+0.25), unlike None.
    assert _frontier_gain(router_state, "alpha", epoch=5) == pytest.approx(1.37)
    assert _frontier_gain(router_state_none_accept, "alpha", epoch=5) == pytest.approx(1.12)

    ledger = NoveltyLedger()
    credit = ledger.credit(
        MappingProxyType(
            {
                "family": "alpha",
                "observation_keys": ("k1", "k2"),
                "weighted_flags": MappingProxyType({"passed": 2.5}),
            }
        )
    )
    assert credit == {"novel_value": 4.5, "novel_observation_keys": 2}

    planned = plan_router_no_prune(
        [seed_item],
        goal_id="g1",
        agent_id="a1",
        worker_count=1,
        router_state=router_state,
        load_profile=MappingProxyType({"source": "ro", "branch_count": 1}),
    )
    assert [item["item_id"] for item in planned] == ["seed-1"]

    err_record = _adapter_error_record(
        MappingProxyType({"item_id": None, "family": "alpha", "is_variant": True}),
        RuntimeError("boom"),
        duration_minutes=0.01,
    )
    assert "item_id" in err_record and err_record["item_id"] is None
    assert err_record["is_variant"] is True
    assert err_record["duration_minutes"] == _MIN_DURATION_MINUTES
    assert err_record["execution_status"] == "adapter_error"

    class _DummyAdapter:
        pass

    sig = harness_queue.arm_runtime_signature(
        _DummyAdapter(),
        arm_key="k",
        worker_count=2,
        use_router=True,
        frontier_max_lanes=1,
        variant_catalog_enabled=False,
        goal_id="g",
        agent_id="a",
        item_failure_policy="record",
        episode_mode=None,
    )
    assert "episode_mode" in sig and sig["episode_mode"] is None


def test_select_and_emit_frontier_avoid_redundant_catalog_reads_and_gain_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    families = [f"fam-{idx}" for idx in range(6)]
    catalog_path = tmp_path / "variant_catalog.json"
    specs: list[dict[str, object]] = []
    for fam in families:
        specs.append(
            {
                "spec_id": f"{fam}-low",
                "seed_family": fam,
                "intent": "low",
                "priority": 1.0,
            }
        )
        specs.append(
            {
                "spec_id": f"{fam}-high",
                "seed_family": fam,
                "intent": "high",
                "priority": 5.0,
            }
        )
    _write_json(
        catalog_path,
        {"schema_version": VARIANT_CATALOG_SCHEMA_VERSION, "specs": specs},
    )

    class MultiFamilyAdapter:
        def list_seed_items(self) -> list[dict[str, object]]:
            return [
                {
                    "item_id": f"seed-{fam}",
                    "family": fam,
                    "text": f"explore {fam}",
                    "concurrency_key": fam,
                }
                for fam in families
            ]

        def compile_variant(
            self, spec: Mapping[str, object], seed_item: Mapping[str, object]
        ) -> dict[str, object]:
            return {
                "item_id": f"var-{spec['spec_id']}",
                "family": seed_item["family"],
            }

        def execute(
            self, item: Mapping[str, object], **_: object
        ) -> dict[str, object]:
            return {
                "item_id": item["item_id"],
                "family": item["family"],
                "observation_keys": [f"obs:{item['item_id']}"],
                "weighted_flags": {"pass": 1.0} if item.get("is_variant") else {},
                "accepted": True,
                "duration_minutes": 0.1,
            }

    catalog_reads = 0
    real_read_json = _read_json

    def counting_read_json(path: Path) -> object:
        nonlocal catalog_reads
        if path == catalog_path:
            catalog_reads += 1
        return real_read_json(path)

    gain_calls = 0
    real_frontier_gain = harness_frontier._frontier_gain

    def counting_frontier_gain(
        router_state: Mapping[str, object] | None,
        family: str,
        *,
        epoch: int,
    ) -> float:
        nonlocal gain_calls
        gain_calls += 1
        return real_frontier_gain(router_state, family, epoch=epoch)

    monkeypatch.setattr(harness_frontier, "_read_json", counting_read_json)
    monkeypatch.setattr(harness_frontier, "_frontier_gain", counting_frontier_gain)

    result = run_budget_arm(
        MultiFamilyAdapter(),
        arm_key="frontier-io",
        run_root=tmp_path,
        budget_minutes=10.0,
        worker_count=2,
        max_epochs=1,
        use_router=True,
        frontier_max_lanes=3,
        variant_catalog_path=catalog_path,
        duration_guard_factor=0.0,
    )
    assert result["epoch_count"] == 1
    assert result["variant_records_total"] == 3
    # One catalog read in select_frontier_work + one in emit_frontier_requests (previously 2*6+2 = 14).
    assert catalog_reads == 2
    # Computed once per family in select_frontier_work (6) + once per family in emit_frontier_requests (6).
    assert gain_calls == 12
    admitted = result["epochs"][0]["frontier"]
    assert admitted is not None
    assert admitted["catalog_pending_after"] == len(specs) - 3


def test_select_frontier_work_persists_partial_consumption_and_supports_mock_catalog(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    consumption_path = tmp_path / "consumption.json"
    _write_json(
        catalog_path,
        {
            "schema_version": VARIANT_CATALOG_SCHEMA_VERSION,
            "specs": [
                {"spec_id": "s1", "seed_family": "alpha", "intent": "first", "priority": 2},
                {"spec_id": "s2", "seed_family": "beta", "intent": "second", "priority": 1},
            ],
        },
    )
    catalog = VariantCatalog(catalog_path, consumption_path)
    seeds = [
        {"item_id": "seed-a", "family": "alpha", "text": "a"},
        {"item_id": "seed-b", "family": "beta", "text": "b"},
    ]

    class ExplodingSecondVariantAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def compile_variant(
            self, spec: Mapping[str, object], seed_item: Mapping[str, object]
        ) -> dict[str, object]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("compile crashed on second spec")
            return {"item_id": f"v-{spec['spec_id']}", "family": seed_item["family"]}

    with pytest.raises(RuntimeError, match="compile crashed on second spec"):
        select_frontier_work(
            seeds,
            catalog=catalog,
            adapter=ExplodingSecondVariantAdapter(),
            router_state=None,
            epoch=1,
            max_lanes=2,
        )
    persisted = _read_json(consumption_path)
    assert isinstance(persisted, list) and len(persisted) == 1

    # Mock VariantCatalog without pending_snapshot must still work via fallback path.
    class LegacyMockCatalog:
        def __init__(self) -> None:
            self.consumed: set[str] = set()
            self.pending_calls: list[str] = []

        def pending_for(self, family: str) -> list[dict[str, object]]:
            self.pending_calls.append(family)
            if family == "alpha" and "legacy-1" not in self.consumed:
                return [{"spec_id": "legacy-1", "seed_family": "alpha", "intent": "ok"}]
            return []

        def pending_count(self) -> int:
            return 0 if "legacy-1" in self.consumed else 1

        def consume(self, spec_id: str) -> None:
            self.consumed.add(spec_id)

    mock_catalog = LegacyMockCatalog()

    class MockAdapter:
        def compile_variant(
            self, spec: Mapping[str, object], seed_item: Mapping[str, object]
        ) -> dict[str, object]:
            return {"item_id": f"v-{spec['spec_id']}", "family": seed_item["family"]}

    mock_catalog_typed = cast(VariantCatalog, mock_catalog)
    selected, audit = select_frontier_work(
        seeds,
        catalog=mock_catalog_typed,
        adapter=MockAdapter(),
        router_state=None,
        epoch=1,
        max_lanes=1,
    )
    assert [item["item_id"] for item in selected] == ["v-legacy-1"]
    assert audit["catalog_pending_after"] == 0
    assert set(mock_catalog.pending_calls) == {"alpha", "beta"}

    emit_frontier_requests(
        tmp_path / "frontier_requests.json",
        arm_key="mock",
        epoch=1,
        seed_items=seeds,
        ledger=NoveltyLedger(),
        catalog=mock_catalog_typed,
        router_state=None,
    )
    req_payload = _read_json(tmp_path / "frontier_requests.json")
    assert isinstance(req_payload, dict)
    assert req_payload["catalog_pending_total"] == 0


def test_step_auc_and_fatal_policy_contracts(tmp_path: Path) -> None:
    auc = step_auc(
        [
            {"elapsed_minutes": 1.0, "novel_value_cum": 2.0},
            {"elapsed_minutes": 3.0, "novel_value_cum": 5.0},
        ],
        value_key="novel_value_cum",
        horizon_minutes=4.0,
    )
    # 0..1 -> 0*1; 1..3 -> 2*2=4; 3..4 -> 5*1=5 => 9.0
    assert auc == 9.0

    with pytest.raises(ValueError, match="item_failure_policy must be one of"):
        _normalize_item_failure_policy("ignore")

    class MissingExecuteAdapter:
        def list_seed_items(self) -> Sequence[Mapping[str, object]]:
            return [{"item_id": "seed-1", "family": "alpha"}]

    with pytest.raises(AttributeError, match="execute"):
        run_budget_arm(
            MissingExecuteAdapter(),
            arm_key="missing-exec",
            run_root=tmp_path,
            budget_minutes=1.0,
            worker_count=1,
            max_epochs=1,
            item_failure_policy=ITEM_FAILURE_POLICY_FATAL,
        )

    class TupleBatchAdapter:
        def list_seed_items(self) -> list[dict[str, object]]:
            return [{"item_id": "s1", "family": "alpha"}]

        def execute(
            self, item: Mapping[str, object], **_: object
        ) -> tuple[dict[str, object], ...]:
            return (
                {
                    "item_id": item["item_id"],
                    "family": item["family"],
                    "observation_keys": ["k1"],
                    "weighted_flags": {},
                    "accepted": True,
                    "duration_minutes": 0.2,
                },
            )

    batch_res = run_budget_arm(
        TupleBatchAdapter(),
        arm_key="seq-arm",
        run_root=tmp_path,
        budget_minutes=1.0,
        worker_count=1,
        max_epochs=1,
    )
    assert batch_res["novel_value_total"] == 1.0

    class GeneratorOutcomeAdapter:
        def list_seed_items(self) -> list[dict[str, object]]:
            return [{"item_id": "gen-1", "family": "alpha"}]

        def execute(
            self, item: Mapping[str, object], **_: object
        ) -> Iterator[dict[str, object]]:
            yield {"item_id": item["item_id"], "family": item["family"]}

    gen_res = run_budget_arm(
        GeneratorOutcomeAdapter(),
        arm_key="gen-arm",
        run_root=tmp_path,
        budget_minutes=1.0,
        worker_count=1,
        max_epochs=1,
    )
    assert gen_res["novel_value_total"] == 0.0
    gen_rec = gen_res["epochs"][0]["lanes"][0]["results"][0]
    assert gen_rec["execution_status"] == "adapter_error"
    assert isinstance(gen_rec["adapter_error"], dict)
    assert gen_rec["adapter_error"]["type"] == "TypeError"
    assert plan_round_robin([MappingProxyType({"item_id": "x"})]) == [{"item_id": "x"}]


def test_cross_family_duplicate_spec_id_executes_once_and_counts_exact_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "variant_catalog.json"
    _write_json(
        catalog_path,
        {
            "schema_version": VARIANT_CATALOG_SCHEMA_VERSION,
            "specs": [
                {"spec_id": "dup-1", "seed_family": "beta", "intent": "b", "priority": 3},
                {"spec_id": "dup-1", "seed_family": "alpha", "intent": "a", "priority": 3},
                {"spec_id": "dup-1", "seed_family": "unseeded", "intent": "u", "priority": 1},
                {"spec_id": "uniq-2", "seed_family": "gamma", "intent": "g", "priority": 1},
            ],
        },
    )

    class CrossFamilyDupAdapter:
        def list_seed_items(self) -> list[dict[str, object]]:
            return [
                {"item_id": "seed-beta", "family": "beta", "text": "b", "concurrency_key": "beta"},
                {"item_id": "seed-alpha", "family": "alpha", "text": "a", "concurrency_key": "alpha"},
                {"item_id": "seed-gamma", "family": "gamma", "text": "g", "concurrency_key": "gamma"},
            ]

        def compile_variant(
            self, spec: Mapping[str, object], seed_item: Mapping[str, object]
        ) -> dict[str, object]:
            return {
                "item_id": f"v-{spec['spec_id']}-{seed_item['family']}",
                "family": seed_item["family"],
            }

        def execute(
            self, item: Mapping[str, object], **_: object
        ) -> dict[str, object]:
            return {
                "item_id": item["item_id"],
                "family": item["family"],
                "observation_keys": [f"obs:{item['item_id']}"],
                "weighted_flags": {},
                "accepted": True,
                "duration_minutes": 0.1,
            }

    catalog_reads = 0
    real_read_json = _read_json

    def counting_read_json(path: Path) -> object:
        nonlocal catalog_reads
        if path == catalog_path:
            catalog_reads += 1
        return real_read_json(path)

    monkeypatch.setattr(harness_frontier, "_read_json", counting_read_json)

    res = run_budget_arm(
        CrossFamilyDupAdapter(),
        arm_key="dup-arm",
        run_root=tmp_path,
        budget_minutes=10.0,
        worker_count=2,
        max_epochs=1,
        use_router=True,
        frontier_max_lanes=2,
        variant_catalog_path=catalog_path,
        duration_guard_factor=0.0,
    )
    assert catalog_reads == 2
    assert res["variant_records_total"] == 2
    frontier = res["epochs"][0]["frontier"]
    assert frontier is not None
    assert frontier["admitted"] == 2
    assert frontier["catalog_pending_after"] == 0
    admitted_specs = frontier["specs"]
    assert isinstance(admitted_specs, list)
    assert sorted(str(row["spec_id"]) for row in admitted_specs) == ["dup-1", "uniq-2"]
    assert _read_json(tmp_path / "variant_consumption_dup-arm.json") == ["dup-1", "uniq-2"]
    variant_ids = [
        rec["item_id"]
        for lane in res["epochs"][0]["lanes"]
        for rec in lane["results"]
        if rec.get("is_variant")
    ]
    assert len(variant_ids) == 2
    assert "v-uniq-2-gamma" in variant_ids
    assert sum(1 for vid in variant_ids if str(vid).startswith("v-dup-1-")) == 1


@pytest.mark.parametrize(
    ("first_min", "resume_min", "budget_min", "curve_min", "expect_stop", "expect_epochs"),
    [
        (0.0004, 0.0004, 0.0007, 0.0, "budget_exhausted", 1),
        (0.0006, 0.0001, 0.001, 0.001, "max_epochs", 2),
    ],
)
def test_checkpoint_resume_preserves_unrounded_elapsed_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_min: float,
    resume_min: float,
    budget_min: float,
    curve_min: float,
    expect_stop: str,
    expect_epochs: int,
) -> None:
    ran_epoch = False
    pre_resume = 0.0
    epoch_min = first_min

    class TinySeedAdapter:
        def list_seed_items(self) -> list[dict[str, object]]:
            return [{"item_id": "s1", "family": "fam", "text": "x", "concurrency_key": "k"}]

        def execute(self, item: Mapping[str, object], **_: object) -> dict[str, object]:
            nonlocal ran_epoch
            ran_epoch = True
            return {"item_id": item["item_id"], "family": "fam", "observation_keys": ["o1"], "weighted_flags": {}, "accepted": True, "duration_minutes": 0.0}

    def fake_perf_minutes(_start: float) -> float:
        return pre_resume + (epoch_min if ran_epoch else 0.0)

    monkeypatch.setattr(harness_runtime, "_now_perf_minutes", fake_perf_minutes)
    run1 = run_budget_arm(
        TinySeedAdapter(),
        arm_key="clk",
        run_root=tmp_path,
        budget_minutes=budget_min,
        worker_count=1,
        max_epochs=1,
        use_router=False,
        resumable=True,
        duration_guard_factor=0.0,
    )
    assert run1["checkpoints"][0]["elapsed_minutes"] == curve_min
    saved = _read_json(tmp_path / "arm_checkpoint_clk.json")
    assert isinstance(saved, dict) and isinstance(saved["state"], dict)
    assert saved["state"]["elapsed_minutes"] == pytest.approx(first_min, abs=1e-9)

    ran_epoch = False
    pre_resume = resume_min
    epoch_min = 0.0
    run2 = run_budget_arm(
        TinySeedAdapter(),
        arm_key="clk",
        run_root=tmp_path,
        budget_minutes=budget_min,
        worker_count=1,
        max_epochs=2,
        use_router=False,
        resume=True,
        duration_guard_factor=0.0,
    )
    assert run2["stop_reason"] == expect_stop
    assert run2["epoch_count"] == expect_epochs
