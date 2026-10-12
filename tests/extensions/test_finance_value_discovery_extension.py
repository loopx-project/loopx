from __future__ import annotations

import io
import json
import os
import shutil
import site
import subprocess
import sys
import tomllib
import venv
from copy import deepcopy
from pathlib import Path

import pytest

from loopx.capabilities.catalog import build_capability_catalog_packet
from loopx.cli import main
from loopx.extensions.manifest import load_extension_manifest
from loopx.extensions.presentation import (
    default_extension_projection_root,
    publish_extension_projection,
    read_extension_projection,
)
from loopx.extensions.runtime import (
    default_extension_state_file,
    doctor_installed_extension,
    extension_catalog_entries,
    install_extension,
    disable_extension,
    enable_extension,
)

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = ROOT / "packages" / "loopx-finance-value-discovery"
EXTENSION_SRC = EXTENSION_ROOT / "src"
MANIFEST = EXTENSION_ROOT / "extension.toml"
EXAMPLE = EXTENSION_ROOT / "examples" / "paypal-debeta-discovery.json"
CASE_EXAMPLE = EXTENSION_ROOT / "examples" / "finance-case-gates-v1.json"
BETA_EXAMPLE = EXTENSION_ROOT / "examples" / "beta-attribution-v1.json"
PACK_EXAMPLE = EXTENSION_ROOT / "examples" / "software-metric-pack-v1.json"
RESEARCH_DASHBOARD_EXAMPLE = EXTENSION_ROOT / "examples" / "research-dashboard.json"
sys.path.insert(0, str(EXTENSION_SRC))

from loopx_finance_value_discovery.cli import run  # noqa: E402
from loopx_finance_value_discovery.dashboard import (  # noqa: E402
    build_finance_research_dashboard_packet,
)
from loopx_finance_value_discovery.reducer import (  # noqa: E402
    EVIDENCE_AXES,
    build_finance_value_discovery_packet,
)


def _example() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _research_dashboard_input() -> dict[str, object]:
    return {
        "schema_version": "finance_research_dashboard_input_v0",
        "surface_id": "investment-research",
        "goal_id": "synthetic-research-goal",
        "source_id": "synthetic-research-2026-01-15",
        "source_version": 1,
        "supersedes": [],
        "point_in_time": "2026-01-15T12:00:00+00:00",
        "evidence_cutoff": "2026-01-15",
        "review_due_at": "2026-02-15T12:00:00+00:00",
        "title": "Synthetic Technology Research",
        "subtitle": "Evidence-gated decision review",
        "adjudication": {
            "status": "insufficient_evidence",
            "label": "Insufficient Evidence",
            "summary": "The frozen gates do not yet support a selected conclusion.",
            "confidence": "medium",
        },
        "metrics": [
            {
                "id": "validated-alpha",
                "label": "Validated company alpha",
                "value": "0",
                "detail": "No company-specific residual passed every frozen gate.",
                "tone": "warning",
            },
            {
                "id": "method-state",
                "label": "Active method",
                "value": "unchanged",
                "detail": "The active method was not promoted or replaced.",
                "tone": "neutral",
            },
        ],
        "dashboard_summaries": [
            {
                "id": "adjudication-summary",
                "label": "Current adjudication",
                "title": "Evidence remains insufficient",
                "summary": "Continue monitoring the frozen event gates.",
                "tone": "warning",
                "destination_anchor": "executive-adjudication",
            }
        ],
        "research_layers": [
            {
                "id": "beta",
                "order": 1,
                "label": "Beta",
                "status": "supported",
                "summary": "Discount-rate exposure explains part of the move.",
                "supporting_evidence": [
                    {
                        "classification": "current_fact",
                        "statement": "Broad synthetic peers moved in the same direction.",
                    }
                ],
                "counterevidence": [
                    {
                        "classification": "agent_inference",
                        "statement": "The entity retained modest relative strength.",
                    }
                ],
                "thesis_breakers": [
                    "Peer dispersion rises while rate sensitivity disappears."
                ],
            },
            {
                "id": "cycle",
                "order": 2,
                "label": "Cycle",
                "status": "partial",
                "summary": "The cycle signal remains mixed.",
                "supporting_evidence": [
                    {
                        "classification": "management_guidance",
                        "statement": "Demand guidance remained inside the frozen range.",
                    }
                ],
                "counterevidence": [
                    {
                        "classification": "analyst_estimate",
                        "statement": "Consensus expects slower sequential growth.",
                    }
                ],
                "thesis_breakers": ["Official guidance turns sequentially negative."],
            },
            {
                "id": "company-value",
                "order": 3,
                "label": "Company value",
                "status": "insufficient_evidence",
                "summary": "Quality is visible but valuation is not yet decisive.",
                "supporting_evidence": [
                    {
                        "classification": "historical_fact",
                        "statement": "Cash conversion stayed positive in the frozen period.",
                    }
                ],
                "counterevidence": [
                    {
                        "classification": "current_fact",
                        "statement": "Capital intensity remains elevated.",
                    }
                ],
                "thesis_breakers": [
                    "Cash conversion deteriorates for two consecutive periods."
                ],
            },
            {
                "id": "residual-alpha",
                "order": 4,
                "label": "Residual alpha",
                "status": "rejected",
                "summary": "Validated company alpha = 0.",
                "supporting_evidence": [
                    {
                        "classification": "agent_inference",
                        "statement": "A candidate residual was observed.",
                    }
                ],
                "counterevidence": [
                    {
                        "classification": "current_fact",
                        "statement": "The residual failed the frozen persistence gate.",
                    }
                ],
                "thesis_breakers": [
                    "No breaker can revive a residual that failed persistence."
                ],
            },
        ],
        "entities": [
            {
                "entity_id": "synthetic-cloud",
                "symbol": "SYN",
                "display_name": "Synthetic Cloud",
                "classification": "Watchlist",
                "status": "insufficient_evidence",
                "confidence": "medium",
                "inference": "A quality business is not yet a validated mispricing.",
                "supporting_evidence": [
                    {
                        "classification": "current_fact",
                        "statement": "Official revenue remained inside the frozen range.",
                    }
                ],
                "counterevidence": [
                    {
                        "classification": "agent_inference",
                        "statement": "Capital intensity may remain above the assumption.",
                    }
                ],
                "thesis_breakers": [
                    "Two periods of slower growth and weaker cash conversion."
                ],
                "observations": [
                    {
                        "id": "observation-range",
                        "label": "Observation range",
                        "classification": "current_fact",
                        "kind": "observation_range",
                        "value": "90-100 synthetic units",
                        "as_of": "2026-01-15T12:00:00+00:00",
                        "source_ref": "filing:syn-q4",
                        "source_type": "company_filing",
                        "confidence": "high",
                        "invalidation": "A verified close below 90 with weaker fundamentals.",
                    }
                ],
                "scenarios": [
                    {
                        "scenario": "bull",
                        "label": "Bull scenario estimate",
                        "value": "140 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.25,
                        "assumptions": [
                            "Growth reaccelerates.",
                            "Free cash flow conversion improves.",
                        ],
                    },
                    {
                        "scenario": "base",
                        "label": "Base scenario estimate",
                        "value": "112 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.5,
                        "assumptions": [
                            "Growth remains durable.",
                            "The valuation multiple is stable.",
                        ],
                    },
                    {
                        "scenario": "bear",
                        "label": "Bear scenario estimate",
                        "value": "72 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.25,
                        "assumptions": [
                            "Growth slows.",
                            "The valuation multiple compresses.",
                        ],
                    },
                ],
                "next_events": [
                    "Next official earnings release.",
                    "Updated capital expenditure guidance.",
                ],
            }
        ],
        "research_ledger": [
            {
                "case_id": "case-rejected-residual",
                "label": "Rejected residual-alpha case",
                "gate_states": [
                    {
                        "gate_id": "persistence",
                        "label": "Persistence",
                        "status": "failed",
                        "summary": "The residual did not persist.",
                    }
                ],
                "decision": "rejected",
                "summary": "The company-specific alpha claim was rejected.",
                "evidence_refs": ["market:synthetic-peer-control"],
            },
            {
                "case_id": "case-insufficient-value",
                "label": "Insufficient company-value case",
                "gate_states": [
                    {
                        "gate_id": "valuation",
                        "label": "Valuation",
                        "status": "insufficient_evidence",
                        "summary": "The evidence does not establish a margin of safety.",
                    }
                ],
                "decision": "insufficient_evidence",
                "summary": "The company-value conclusion remains open.",
                "evidence_refs": ["filing:syn-q4"],
            },
        ],
        "artifacts": [
            {
                "artifact_id": "synthetic-research-packet",
                "kind": "research_packet",
                "label": "Synthetic research packet",
                "summary": "Frozen adjudication, scenarios, and evidence lineage.",
                "artifact_ref": "artifact:synthetic-research-packet",
                "evidence_refs": [
                    "filing:syn-q4",
                    "market:synthetic-peer-control",
                ],
            }
        ],
        "event_gates": [
            {
                "event_id": "E1",
                "label": "Synthetic cloud earnings",
                "status": "pending",
                "frozen_at": "2026-01-15T12:00:00+00:00",
                "observation_window": "Next official reporting window",
                "frozen_hypothesis": "Returns depend on monetization, not capex alone.",
                "observables": [
                    "Cloud growth versus frozen guidance.",
                    "Capital expenditure and free cash flow direction.",
                ],
                "current_evidence": [],
                "supports": [
                    "Growth above the frozen range with stable cash conversion."
                ],
                "refutes": [
                    "Higher capital expenditure without measurable cloud return."
                ],
                "thesis_breakers": [
                    "Official guidance shows deteriorating returns on investment."
                ],
                "next_review": "After the official filing is available.",
            }
        ],
        "method_state": {
            "revision": "candidate-v1",
            "lifecycle_state": "active_method_unchanged",
            "active_method_changed": False,
            "summary": "Active method unchanged.",
        },
        "boundary": {
            "research_aid_only": True,
            "investment_advice": False,
            "trading_allowed": False,
            "raw_provider_payload_recorded": False,
            "private_source_content_read": False,
        },
    }


def _installed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    isolated_validator: bool = False,
) -> tuple[Path, Path]:
    interpreter = Path(sys.executable)
    if isolated_validator:
        # Core resolves the manifest's validator with -I, without PYTHONPATH.
        environment = tmp_path / "provider-env"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        purelib = subprocess.check_output([
            str(interpreter), "-I", "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ], text=True).strip()
        (Path(purelib) / "finance-source.pth").write_text(
            "\n".join([str(EXTENSION_SRC), str(ROOT), *site.getsitepackages()]) + "\n",
            encoding="utf-8",
        )
    provider = tmp_path / "finance-provider"
    provider.write_text(
        f"#!{interpreter}\n"
        "from loopx_finance_value_discovery.cli import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = tmp_path / "extension.toml"
    manifest.write_text(
        MANIFEST.read_text(encoding="utf-8").replace(
            'entrypoint = "loopx-finance-value-discovery"',
            f"entrypoint = {json.dumps(str(provider))}",
        ),
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            part for part in [str(EXTENSION_SRC), str(ROOT), existing] if part
        ),
    )
    runtime_root = tmp_path / "runtime"
    install_extension(
        manifest,
        state_file=default_extension_state_file(runtime_root),
        execute=True,
    )
    return manifest, runtime_root


def test_manifest_and_paypal_example_preserve_extension_boundary() -> None:
    manifest = load_extension_manifest(MANIFEST)
    assert manifest["capabilities"] == []
    assert manifest["implementations"] == []
    assert manifest["provider"]["permissions"] == []
    assert manifest["runtime"]["required_permissions"] == []
    assert manifest["runtime"]["protocol"] == "finance_value_discovery_extension_v0"

    catalog = build_capability_catalog_packet([MANIFEST])
    assert "finance-value-discovery" not in {
        item["id"] for item in catalog["capabilities"]
    }

    packet = build_finance_value_discovery_packet(_example())
    assert packet["projection"]["next_action"] == "select_at_most_one_b_successor"
    assert packet["projection"]["next_targets"] == ["PYPL"]
    assert packet["projection"]["screen_group_count"] == 5
    assert packet["projection"]["control_count"] == 3
    assert packet["cards"][0]["de_beta_control_count"] == 3
    assert packet["cards"][0]["de_beta_supported"] is True
    assert packet["boundary"]["investment_advice"] is False
    assert packet["boundary"]["trading_allowed"] is False
    assert packet["boundary"]["continuous_watch_allowed"] is False


def _research_source_period_metric() -> dict[str, object]:
    return {
        "metric_id": "synthetic-value-capture",
        "label": "Synthetic value capture",
        "event_namespace": "synthetic.period.metric",
        "event_id": "value-capture-2026w02",
        "event_at": "2026-01-14T23:00:00Z",
        "instrument_id": "SYNTH-USD",
        "scope_id": "synthetic-scope",
        "period_start": "2026-01-08",
        "period_end": "2026-01-14",
        "source_state": "ok",
        "value": 0.0,
        "unit": "USD",
        "metric_basis": "realized_cash",
        "metric_semantics": "cash_delta",
        "value_origin": "source_reported",
        "value_precision": "exact",
        "observation_authority": "source_reported_exact",
        "sign_basis": "account_cash_change",
        "fee_inclusion": "not_applicable",
        "account_scope": "not_applicable",
        "account_value_role": "not_applicable",
        "includes_isolated_margin": False,
        "expected_components": ["primary", "overlap"],
        "observed_components": ["primary", "overlap"],
        "double_counted_components": ["overlap"],
        "numerator_scope": ["primary"],
        "denominator_scope": [],
        "lineage_id": "synthetic-upstream-week",
        "source_ref": "source:synthetic-value-capture",
        "methodology_state": "verified",
        "anomaly_state": "clear",
    }


def _research_spot_market_identity() -> dict[str, object]:
    return {
        "pairs": [
            {
                "name": "SYNTH-PAIR",
                "asset_indexes": [4, 0],
                "is_canonical": False,
                "source_ref": "source:synthetic-pair",
            }
        ],
        "tokens": [
            {"index": 0, "symbol": "USDC", "source_ref": "source:synthetic-usdc"},
            {"index": 4, "symbol": "SYN", "source_ref": "source:synthetic-syn"},
        ],
        "contexts": [
            {
                "coin": "SYNTH-PAIR",
                "observed_at": "2026-01-15T12:00:00Z",
                "mark_price": 4.25,
                "source_ref": "source:synthetic-context",
            }
        ],
    }


def test_finance_research_dashboard_mapping_preserves_research_truth() -> None:
    packet = build_finance_research_dashboard_packet(_research_dashboard_input())
    assert packet == build_finance_research_dashboard_packet(
        _research_dashboard_input()
    )

    assert packet["schema_version"] == "finance_research_dashboard_packet_v0"
    projection = packet["presentation_projection"]
    assert projection["surface_id"] == "investment-research"
    assert projection["goal_id"] == "synthetic-research-goal"
    assert projection["generated_at"] == "2026-01-15T12:00:00+00:00"
    assert projection["review_due_at"] == "2026-02-15T12:00:00+00:00"
    assert projection["lineage"] == {
        "source_id": "synthetic-research-2026-01-15",
        "version": 1,
        "row_lifecycle": "active",
        "supersedes": [],
        "superseded_by": None,
    }
    assert projection["view_schema"] == "decision_research_dashboard_v0"
    view = projection["view"]
    assert view["adjudication"]["status"] == "insufficient_evidence"
    assert view["metrics"][0]["value"] == "0"
    assert view["metrics"][1]["value"] == "unchanged"
    assert [layer["id"] for layer in view["layers"]] == [
        "beta",
        "cycle",
        "company-value",
        "residual-alpha",
    ]
    assert view["layers"][-1]["status"] == "rejected"
    assert view["entities"][0]["scenario_estimates"][1]["probability"] == 0.5
    assert view["research_ledger"][0]["decision"] == "rejected"
    assert view["research_ledger"][1]["decision"] == "insufficient_evidence"
    assert view["artifacts"][0]["artifact_ref"] == (
        "artifact:synthetic-research-packet"
    )
    assert view["event_gates"][0]["status"] == "pending"
    assert view["method_state"]["active_method_changed"] is False
    assert view["boundary"]["trading_allowed"] is False
    serialized = json.dumps(packet, sort_keys=True)
    assert "supporting_evidence" not in serialized
    assert "frozen_at" not in serialized


def test_dashboard_cli_and_lark_card_share_period_metric_projection(
    tmp_path: Path,
    capsys,
) -> None:
    payload = _research_dashboard_input()
    payload["source_period_metrics"] = [_research_source_period_metric()]
    payload["spot_market_identity"] = _research_spot_market_identity()
    packet = build_finance_research_dashboard_packet(payload)
    metric = packet["presentation_projection"]["view"]["source_period_metrics"][0]
    assert metric["coverage_state"] == "complete"
    assert metric["value"] == 0.0
    assert metric["double_counted_components"] == ["overlap"]
    assert metric["ready_eligible"] is False
    market = packet["presentation_projection"]["view"]["spot_market_identity"][
        "markets"
    ][0]
    assert market["context_coin"] == "SYNTH-PAIR"
    assert market["base_asset"] == {"index": 4, "symbol": "SYN"}

    input_path = tmp_path / "research-dashboard.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    assert run(["render-lark-card", "--input-json", str(input_path)]) == 0
    card = json.loads(capsys.readouterr().out)
    markdown = card["elements"][0]["text"]["content"]
    assert "0 USD" in markdown
    assert "Excluded double-counted components: overlap" in markdown
    assert "Spot identity joins" in markdown
    assert "SYN / USDC" in markdown
    assert "evidence-only" in markdown


def test_published_card_reads_exact_native_envelope_without_renewing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, runtime_root = _installed_manifest(tmp_path, monkeypatch, isolated_validator=True)
    state_file = default_extension_state_file(runtime_root)
    request = _research_dashboard_input()
    request["source_version"] = 7
    request["supersedes"] = ["synthetic-prior-review"]
    request["metrics"].append({
        "id": "unknown-cost", "label": "Costs", "value": "unknown",
        "detail": "Missing evidence is not zero.", "tone": "warning",
    })
    receipt = publish_extension_projection(
        "loopx-finance-value-discovery", "investment-research",
        state_file=state_file, request=request, execute=True,
    )
    projection_file = default_extension_projection_root(state_file) / (
        "loopx-finance-value-discovery/investment-research.json"
    )
    original_state = state_file.read_bytes()
    original_projection = projection_file.read_bytes()
    envelope = read_extension_projection(
        state_file=state_file, extension_id="loopx-finance-value-discovery",
        surface_id="investment-research", extension_revision=receipt["revision"],
        payload_sha256=receipt["payload_sha256"],
    )
    assert run([
        "render-lark-card", "--published-state-file", str(state_file),
        "--goal-id", "synthetic-research-goal",
        "--extension-revision", receipt["revision"],
        "--payload-sha256", receipt["payload_sha256"],
    ]) == 0
    card = json.loads(capsys.readouterr().out)
    text = card["elements"][0]["text"]["content"]
    for expected in [
        "synthetic-research-goal", "synthetic-research-2026-01-15", "version: 7",
        "2026-01-15T12:00:00+00:00", "2026-02-15T12:00:00+00:00",
        receipt["revision"], receipt["payload_sha256"], "synthetic-prior-review",
        "Costs: unknown", "Counterevidence", "investment advice: false",
        "trading allowed: false", "does not renew it",
    ]:
        assert expected in text
    from loopx_finance_value_discovery.lark_projection import render_decision_research_markdown

    assert text.startswith(render_decision_research_markdown(envelope["view"]))
    assert state_file.read_bytes() == original_state
    assert projection_file.read_bytes() == original_projection


def test_published_card_rejects_stale_cross_goal_disabled_and_missing_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, runtime_root = _installed_manifest(tmp_path, monkeypatch, isolated_validator=True)
    state_file = default_extension_state_file(runtime_root)
    receipt = publish_extension_projection(
        "loopx-finance-value-discovery", "investment-research",
        state_file=state_file, request=_research_dashboard_input(), execute=True,
    )
    args = [
        "render-lark-card", "--published-state-file", str(state_file),
        "--goal-id", "synthetic-research-goal",
        "--extension-revision", receipt["revision"],
        "--payload-sha256", receipt["payload_sha256"],
    ]

    def rejected(arguments: list[str], message: str) -> None:
        assert run(arguments) == 1
        result = json.loads(capsys.readouterr().out)
        assert message in result["error"]
        assert result["external_writes_performed"] is False
        assert result["trading_allowed"] is False

    state_before = state_file.read_bytes()
    projection_file = default_extension_projection_root(state_file) / (
        "loopx-finance-value-discovery/investment-research.json"
    )
    published = projection_file.read_bytes()
    for flag, wrong, message in [
        ("--payload-sha256", "0" * 64, "does not match"),
        ("--extension-revision", "previous-revision", "does not match"),
        ("--goal-id", "another-goal", "does not belong"),
    ]:
        malformed = args.copy()
        malformed[malformed.index(flag) + 1] = wrong
        rejected(malformed, message)
    rejected(args[:-2], "published card requires")
    assert state_file.read_bytes() == state_before
    assert projection_file.read_bytes() == published
    disable_extension("loopx-finance-value-discovery", state_file=state_file, execute=True)
    rejected(args, "disabled")
    enable_extension("loopx-finance-value-discovery", state_file=state_file, execute=True)
    projection_file.unlink()
    rejected(args, "no published projection")
    projection_file.write_bytes(published)
    assert run(args) == 0
    assert "Published review reference" in json.loads(capsys.readouterr().out)["elements"][0]["text"]["content"]
    assert projection_file.read_bytes() == published


def test_input_card_cannot_claim_a_publication_reference(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "research.json"
    input_path.write_text(json.dumps(_research_dashboard_input()))
    assert run([
        "render-lark-card", "--input-json", str(input_path), "--goal-id", "synthetic-research-goal",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "publication reference flags require" in result["error"]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda payload: payload.update({"point_in_time": "not-an-iso-cutoff"}),
            "point_in_time",
        ),
        (
            lambda payload: payload.update({"evidence_cutoff": "2026-99-99"}),
            "evidence_cutoff",
        ),
        (
            lambda payload: payload.update({"unexpected": "field"}),
            "unsupported keys",
        ),
        (
            lambda payload: payload["entities"][0]["observations"][0].update(
                {"unexpected": "field"}
            ),
            "unsupported keys",
        ),
        (
            lambda payload: payload["research_layers"][0].update(
                {"supporting_evidence": []}
            ),
            "supporting_evidence",
        ),
        (
            lambda payload: payload["research_layers"][0].update(
                {"counterevidence": []}
            ),
            "counterevidence",
        ),
        (
            lambda payload: payload["research_layers"][0].update(
                {"thesis_breakers": []}
            ),
            "thesis_breakers",
        ),
        (
            lambda payload: payload["entities"][0]["scenarios"][0].update(
                {"probability": 0.5}
            ),
            "sum to 1",
        ),
        (
            lambda payload: payload["entities"][0]["scenarios"][0].update(
                {"assumptions": []}
            ),
            "assumptions",
        ),
        (
            lambda payload: payload["entities"][0]["scenarios"][0].update(
                {"horizon": ""}
            ),
            "horizon",
        ),
        (
            lambda payload: payload["entities"][0]["scenarios"][0].update(
                {"label": "Target price"}
            ),
            "target-price wording",
        ),
        (
            lambda payload: payload["entities"][0]["observations"][0].update(
                {"invalidation": ""}
            ),
            "invalidation",
        ),
        (
            lambda payload: payload["event_gates"][0].update(
                {
                    "status": "selected",
                    "current_evidence": [
                        {
                            "classification": "current_fact",
                            "statement": "The result was represented as post-event.",
                            "observed_at": "2026-01-14T12:00:00+00:00",
                        }
                    ],
                }
            ),
            "after frozen_at",
        ),
        (
            lambda payload: payload["adjudication"].update({"status": "buy"}),
            "status",
        ),
        (
            lambda payload: payload["research_ledger"][0]["gate_states"][0].update(
                {"status": "waived"}
            ),
            "status",
        ),
        (
            lambda payload: payload.update(
                {"raw_response_body": {"provider": "untrusted"}}
            ),
            "forbidden key",
        ),
        (
            lambda payload: payload["entities"][0].update(
                {"inference": "/tmp/private-research.json"}
            ),
            "local path",
        ),
        (
            lambda payload: payload["entities"][0].update(
                {"inference": "api_key=not-a-real-key"}
            ),
            "credential material",
        ),
        (
            lambda payload: payload["entities"][0].update(
                {"account_id": "synthetic-account"}
            ),
            "forbidden key",
        ),
        (
            lambda payload: payload["adjudication"].update(
                {"label": "Validated upside"}
            ),
            "adjudication label",
        ),
        (
            lambda payload: payload["metrics"][0].update({"tone": "success"}),
            "validated-alpha",
        ),
    ],
)
def test_finance_research_dashboard_rejects_weak_or_restricted_input(
    mutator,
    message: str,
) -> None:
    payload = deepcopy(_research_dashboard_input())
    mutator(payload)

    with pytest.raises(ValueError, match=message):
        build_finance_research_dashboard_packet(payload)


def test_finance_research_dashboard_runs_through_provider_stdin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(_research_dashboard_input())),
    )

    assert run([]) == 0

    packet = json.loads(capsys.readouterr().out)
    assert packet["schema_version"] == "finance_research_dashboard_packet_v0"
    assert packet["presentation_projection"]["view"]["metrics"][0]["value"] == "0"


def test_finance_presentation_surface_and_example_are_public_safe() -> None:
    manifest = load_extension_manifest(MANIFEST)
    assert manifest["presentation_surfaces"] == [
        {
            "id": "investment-research",
            "kind": "decision_research_dashboard",
            "title": "Investment Research",
            "view_schema": "decision_research_dashboard_v0",
            "view_validator": (
                "loopx_finance_value_discovery.presentation_view:"
                "validate_decision_research_view"
            ),
            "visibility": "public-safe",
            "empty_state_title": "No validated research yet",
            "empty_state_detail": "Publish a validated projection.",
        }
    ]

    payload = json.loads(RESEARCH_DASHBOARD_EXAMPLE.read_text(encoding="utf-8"))
    packet = build_finance_research_dashboard_packet(payload)
    assert packet["presentation_projection"]["surface_id"] == "investment-research"
    assert packet["presentation_projection"]["view"]["metrics"][0]["value"] == "0"
    serialized = RESEARCH_DASHBOARD_EXAMPLE.read_text(encoding="utf-8")
    for forbidden in (
        "finance-method-improvement",
        "/Users/",
        "bytedance",
        "Bearer ",
        "api_key",
        "account_id",
        "order_id",
        "position_size",
    ):
        assert forbidden not in serialized


def test_projection_publisher_loads_finance_validator_from_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_packet = build_finance_research_dashboard_packet(
        _research_dashboard_input()
    )
    invalid_packet["presentation_projection"]["view"]["unsupported"] = (
        "must fail in the publisher process"
    )
    runtime_root = tmp_path / "finance-runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(runtime_root)
    runtime_python = runtime_root / "bin" / "python"
    purelib = subprocess.run(
        [
            str(runtime_python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (Path(purelib) / "finance-value-discovery-source.pth").write_text(
        str(EXTENSION_SRC) + "\n",
        encoding="utf-8",
    )
    provider = runtime_root / "bin" / "finance-provider"
    provider.write_text(
        f"""#!{runtime_python}
import json
import sys

if "--doctor" in sys.argv:
    raise SystemExit(0)

json.load(sys.stdin)
json.dump({invalid_packet!r}, sys.stdout)
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = tmp_path / "extension.toml"
    manifest.write_text(
        MANIFEST.read_text(encoding="utf-8").replace(
            'entrypoint = "loopx-finance-value-discovery"',
            f"entrypoint = {json.dumps(str(provider))}",
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.syspath_prepend(str(EXTENSION_SRC))
    sys.modules.pop("loopx_finance_value_discovery.presentation_view", None)
    state_file = default_extension_state_file(tmp_path / "runtime")
    install_extension(manifest, state_file=state_file, execute=True)

    with pytest.raises(ValueError, match="unsupported keys"):
        publish_extension_projection(
            "loopx-finance-value-discovery",
            "investment-research",
            state_file=state_file,
            request=_research_dashboard_input(),
            execute=True,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script shebang fixture")
def test_doctor_identity_binds_declared_validator_implementation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replaced validator implementation cannot reuse the earlier doctor proof."""

    runtime_source = tmp_path / "finance-source"
    shutil.copytree(EXTENSION_SRC, runtime_source)
    runtime_root = tmp_path / "finance-runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(runtime_root)
    runtime_python = runtime_root / "bin" / "python"
    purelib = subprocess.run(
        [
            str(runtime_python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (Path(purelib) / "finance-value-discovery-source.pth").write_text(
        str(runtime_source) + "\n",
        encoding="utf-8",
    )
    unacceptable_view = "must be accepted only by the replaced decision code"
    packet = build_finance_research_dashboard_packet(_research_dashboard_input())
    packet["presentation_projection"]["view"]["unsupported"] = unacceptable_view
    invocation_marker = tmp_path / "provider-called"
    provider = runtime_root / "bin" / "finance-provider"
    provider.write_text(
        f"""#!{runtime_python}
import json
import sys
from pathlib import Path

if "--doctor" in sys.argv:
    raise SystemExit(0)

Path({str(invocation_marker)!r}).write_text("called", encoding="utf-8")
json.load(sys.stdin)
json.dump({packet!r}, sys.stdout)
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = tmp_path / "extension.toml"
    manifest.write_text(
        MANIFEST.read_text(encoding="utf-8").replace(
            'entrypoint = "loopx-finance-value-discovery"',
            f"entrypoint = {json.dumps(str(provider))}",
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PYTHONPATH", raising=False)
    state_file = default_extension_state_file(tmp_path / "runtime")
    install_extension(manifest, state_file=state_file, execute=True)

    def publish() -> dict[str, object]:
        return publish_extension_projection(
            "loopx-finance-value-discovery",
            "investment-research",
            state_file=state_file,
            request=_research_dashboard_input(),
            execute=True,
        )

    def ready() -> bool:
        entries = extension_catalog_entries([manifest], state_file=state_file)
        return bool(entries[0]["provider"]["ready"])

    assert ready() is True
    with pytest.raises(ValueError, match="unsupported keys"):
        publish()
    invocation_marker.unlink()

    validator_module = (
        runtime_source / "loopx_finance_value_discovery" / "presentation_view.py"
    )
    validator_module.write_text(
        validator_module.read_text(encoding="utf-8")
        + "\n\ndef validate_decision_research_view(view):\n"
        '    """Replacement implementation that accepts any view structure."""\n'
        "    return view\n",
        encoding="utf-8",
    )

    assert ready() is False
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        publish()
    assert not invocation_marker.exists()

    doctor = doctor_installed_extension(
        "loopx-finance-value-discovery",
        state_file=state_file,
        execute=True,
    )
    assert doctor["verified"] is True
    assert ready() is True

    # Re-verifying the replaced implementation makes it runnable again, and the
    # published view proves the replaced decision code is what executed.
    assert publish()["status"] == "published"
    projection_file = (
        default_extension_projection_root(state_file)
        / "loopx-finance-value-discovery"
        / "investment-research.json"
    )
    persisted = json.loads(projection_file.read_text(encoding="utf-8"))
    assert persisted["view"]["unsupported"] == unacceptable_view


@pytest.mark.parametrize("legacy_command", ["source-map", "install-check"])
def test_legacy_connector_returns_extension_migration_packet(
    legacy_command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--format",
                "json",
                "value-connectors",
                legacy_command,
                "--connector",
                "finance_market_snapshot",
            ]
        )
        == 0
    )
    packet = json.loads(capsys.readouterr().out)
    item = (
        packet["source_profiles"][0]
        if legacy_command == "source-map"
        else packet["checks"][0]
    )
    migration = item["migration"]
    assert item["status"] == "migrated_to_extension"
    assert migration["replacement_extension_id"] == ("loopx-finance-value-discovery")
    assert migration["replacement_capability_id"] is None
    assert migration["automatic_provider_install_supported"] is False
    assert migration["packaged_loopx_only_start_supported"] is False
    assert migration["agent_start_mode"] == ("guided_when_provider_source_is_available")
    assert migration["source_checkout_paths"]["package"] == (
        "packages/loopx-finance-value-discovery"
    )
    assert migration["source_checkout_paths"]["manifest"] == (
        "packages/loopx-finance-value-discovery/extension.toml"
    )
    assert migration["truth_contract"]["legacy_connector_executes_finance"] is False


def test_legacy_plan_selector_returns_extension_migration_packet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--format",
                "json",
                "value-connectors",
                "plan",
                "--connector-id",
                "finance_market_snapshot",
            ]
        )
        == 0
    )
    migration = json.loads(capsys.readouterr().out)
    assert migration["schema_version"] == "value_connector_extension_migration_v0"
    assert migration["status"] == "migrated_to_extension"
    assert migration["replacement_extension_id"] == ("loopx-finance-value-discovery")
    assert migration["replacement_capability_id"] is None
    assert migration["truth_contract"]["legacy_connector_executes_finance"] is False


def test_group_wide_derating_or_missing_controls_cannot_advance() -> None:
    group_wide = _example()
    group_wide["cards"][0]["relative_signal"] = "group_wide"
    packet = build_finance_value_discovery_packet(group_wide)
    assert packet["projection"]["next_action"] == "close_no_validated_candidates"
    assert packet["projection"]["next_targets"] == []

    missing_controls = _example()
    missing_controls["cards"] = missing_controls["cards"][:2]
    packet = build_finance_value_discovery_packet(missing_controls)
    assert packet["cards"][0]["de_beta_control_count"] == 1
    assert packet["projection"]["next_action"] == "close_no_validated_candidates"


def test_a_claim_requires_complete_evidence_and_two_frozen_controls() -> None:
    payload = _example()
    candidate = payload["cards"][0]
    candidate["classification"] = "A"
    candidate["axes"] = {axis: "supported" for axis in EVIDENCE_AXES}
    candidate["missing_fields"] = []
    candidate["fully_diluted_valuation_status"] = "verified"
    candidate["terminal_risk_status"] = "bounded"
    payload["cards"] = payload["cards"][:2]

    with pytest.raises(ValueError, match="at least two frozen peer controls"):
        build_finance_value_discovery_packet(payload)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda payload: payload.update(
                {"screen_groups": ["legacy-payments", "packaging"]}
            ),
            "at least three unrelated screen groups",
        ),
        (
            lambda payload: payload["cards"][0].update({"counter_evidence": []}),
            "counter_evidence",
        ),
        (
            lambda payload: payload["cards"][0]["source_refs"][0].update(
                {"observed_at": "2026-07-07"}
            ),
            "after as_of",
        ),
        (
            lambda payload: payload.update({"raw_provider_payload": {"quote": 1}}),
            "forbidden key",
        ),
    ],
)
def test_reducer_fails_closed_on_weak_or_restricted_evidence(
    mutator,
    message: str,
) -> None:
    payload = deepcopy(_example())
    mutator(payload)
    with pytest.raises(ValueError, match=message):
        build_finance_value_discovery_packet(payload)


def test_declared_minimum_core_without_presentation_api_can_import_and_doctor(
    tmp_path: Path,
) -> None:
    metadata = tomllib.loads((EXTENSION_ROOT / "pyproject.toml").read_text())
    assert "loopx>=0.4.0" in metadata["project"]["dependencies"]

    legacy_core = tmp_path / "legacy-core" / "loopx"
    legacy_core.mkdir(parents=True)
    (legacy_core / "__init__.py").write_text('__version__ = "0.4.0"\n')
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(EXTENSION_SRC), str(legacy_core.parent))
    )

    imported = subprocess.run(
        [sys.executable, "-S", "-c", "import loopx_finance_value_discovery"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr

    doctor = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from loopx_finance_value_discovery.cli import main; raise SystemExit(main(['--doctor']))",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert doctor.returncode == 1
    assert (
        "does not provide the extension presentation API"
        in json.loads(doctor.stdout)["error"]
    )


def test_dashboard_requires_only_public_presentation_validator_api(
    tmp_path: Path,
) -> None:
    """A Core with the public resolver need not preserve Core-private helpers."""

    core_package = tmp_path / "minimal-core" / "loopx" / "extensions"
    core_package.mkdir(parents=True)
    (core_package.parent / "__init__.py").write_text('__version__ = "0.4.0"\n')
    (core_package / "__init__.py").write_text("")
    (core_package / "presentation.py").write_text(
        "def load_presentation_view_validator(_surface):\n    return lambda value: value\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(EXTENSION_SRC), str(core_package.parents[1]))
    )

    dashboard_import = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from loopx_finance_value_discovery.dashboard import "
            "build_finance_research_dashboard_packet",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dashboard_import.returncode == 0, dashboard_import.stderr


def test_provider_doctor_is_side_effect_free() -> None:
    assert run(["--doctor"]) == 0


def test_standalone_extension_runs_through_verified_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, runtime_root = _installed_manifest(tmp_path, monkeypatch)
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "run",
                "loopx-finance-value-discovery",
                "--input-json",
                str(EXAMPLE),
                "--execute",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "succeeded"
    packet = receipt["provider_result"]
    assert packet["schema_version"] == "finance_value_discovery_packet_v0"
    assert packet["projection"]["next_targets"] == ["PYPL"]


@pytest.mark.parametrize(
    "case_example",
    [CASE_EXAMPLE, EXTENSION_ROOT / "examples" / "finance-source-coverage-v1.json"],
)
def test_unified_gate_contract_runs_through_verified_runtime(
    case_example: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, runtime_root = _installed_manifest(tmp_path, monkeypatch)
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "run",
                "loopx-finance-value-discovery",
                "--input-json",
                str(case_example),
                "--execute",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "succeeded"
    evaluation = receipt["provider_result"]
    assert evaluation["schema_version"] == "finance_case_gate_evaluation_v1"
    assert evaluation["disposition"] == "insufficient_evidence"
    assert evaluation["replay"]["evaluation_sha256"]
    if case_example.name == "finance-source-coverage-v1.json":
        assert evaluation["gate_results"][0]["source_coverage"]["state"] == "complete"
        assert evaluation["first_blocking_gate"]["gate_id"] == "economic_evidence"


@pytest.mark.parametrize(
    ("example", "schema_version"),
    [
        (BETA_EXAMPLE, "finance_beta_attribution_v1"),
        (PACK_EXAMPLE, "finance_metric_pack_evaluation_v1"),
    ],
)
def test_p1_finance_layers_run_through_verified_runtime(
    example: Path,
    schema_version: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, runtime_root = _installed_manifest(tmp_path, monkeypatch)
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "run",
                "loopx-finance-value-discovery",
                "--input-json",
                str(example),
                "--execute",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "succeeded"
    assert receipt["provider_result"]["schema_version"] == schema_version
    assert receipt["provider_result"]["boundary"]["trading_allowed"] is False
