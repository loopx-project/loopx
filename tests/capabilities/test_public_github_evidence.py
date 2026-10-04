from __future__ import annotations

import argparse
import copy
import hashlib
import json
from urllib.error import HTTPError

import pytest

from loopx.capabilities.external_research import cli
from loopx.capabilities.external_research.projection import readback, render_readback
from loopx.capabilities.deep_research.runtime import add_source, load_state, start_research
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.extensions import public_github_research as provider

REF = "https://github.com/example/public/blob/" + "a" * 40 + "/README.md"
SECOND = REF.replace("README.md", "missing.md")


def plan(refs=None):
    return effect_runtime_result("external_evidence.plan", {"request": {
        "objective": "Inspect public fixture", "user_activity": "Choose a source",
        "decision": "Whether the pinned source contains the fixture marker",
        "evidence_kinds": ["literal_match"], "source_refs": refs or [REF], "search_terms": ["fixture"]},
        "providers": [{"provider_id": provider.PROVIDER_ID, "provider_kind": "method",
            "protocol": "external_evidence_research_v0", "declared": True, "installed": True,
            "enabled": True, "ready": True, "unavailable_reason": None}]})


def reader(url):
    if url.startswith("https://api.github.com/"):
        return b'{"private": false}'
    if url.endswith("missing.md"):
        raise HTTPError(url, 404, "not found", {}, None)
    return b"fixture public data\n"


def admission(p, receipt, disposition="admit"):
    return effect_runtime_result("external_evidence.admit", {"plan": p, "receipt": receipt,
        "decision": {"disposition": disposition, "reason": "Fixture parent decision",
            "admitted_source_refs": [s["source_ref"] for s in receipt["sources"]] if disposition == "admit" else []}})


@pytest.mark.parametrize("ref", ["file:///private", REF.replace("https://", "http://"),
    REF.replace("github.com", "github.com.evil"), REF.replace("/" + "a"*40 + "/", "/main/"),
    REF + "?query=fixture", REF + "#L1", REF.replace("README.md", "../private"),
    REF.replace("README.md", "%2e%2e/private"), REF.replace("README.md", "%252e%252e/private")])
def test_provider_rejects_unpinned_or_out_of_scope_sources(ref, monkeypatch):
    monkeypatch.setattr(provider, "_read", lambda _: pytest.fail("invalid input made a HTTP call"))
    with pytest.raises(ValueError):
        provider.inspect_provider([ref])


def test_fresh_visibility_probe_does_not_trust_old_ready_plan(monkeypatch):
    monkeypatch.setattr(provider, "_read", lambda _: b'{"private": true}')
    assert provider.inspect_provider([REF])["ready"] is False
    output = provider.execute_public_github(plan())
    assert output["receipt"]["status"] == "failed"
    assert output["receipt"]["sources"] == []
    assert output["execution"]["automatic_admission"] is False


@pytest.mark.parametrize("content,status", [(b"", "no_evidence"), (b"\x00binary", "failed")])
def test_empty_or_binary_source_preserves_fallback(monkeypatch, content, status):
    monkeypatch.setattr(provider, "_read", lambda url: b'{"private":false}' if "api.github.com" in url else content)
    p = plan()
    output = provider.execute_public_github(p)
    assert output["receipt"]["status"] == status
    projected = readback(p, output["receipt"])
    assert projected["original_source_fallback_allowed"] is True
    assert projected["parent_admission"] is None
    assert REF in render_readback(projected)


def test_partial_reads_are_observed_not_admitted_or_complete(monkeypatch):
    monkeypatch.setattr(provider, "_read", reader)
    p = plan([REF, SECOND])
    output = provider.execute_public_github(p)
    receipt = output["receipt"]
    assert receipt["status"] == "succeeded" and len(receipt["sources"]) == 1
    source = receipt["sources"][0]
    assert source["basis"] == "observed"
    assert source["content_digest"] == "sha256:" + hashlib.sha256(b"fixture public data\n").hexdigest()
    assert "lines 1" in source["finding"]
    assert "fixture public data" not in json.dumps(output)
    before = readback(p, receipt)
    assert before["parent_admission"] is None and before["downstream_source_refs"] == []
    assert before["evidence_coverage_observed"] is False
    assert any("HTTP 404" in item for item in before["limitations"])
    rejected = readback(p, receipt, admission(p, receipt, "reject"))
    assert rejected["retirement"]["reason"] == "parent_rejected"


@pytest.mark.parametrize("mutate", ["source_refs", "search_terms", "objective"])
def test_execute_rejects_mutated_plan_before_provider_calls(tmp_path, monkeypatch, mutate):
    p = plan()
    p["request"][mutate] = [SECOND] if mutate == "source_refs" else ["different"] if mutate == "search_terms" else "different"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(p), encoding="utf-8")
    monkeypatch.setattr(cli, "execute_public_github", lambda _: pytest.fail("mutated plan reached provider"))
    payloads = []
    args = argparse.Namespace(command="external-evidence", external_evidence_action="execute",
        plan_json=str(path), execute=True)
    assert cli.handle_external_evidence_command(args, output_format=lambda _: "json",
        print_payload=lambda payload, *_: payloads.append(payload)) == 1
    assert payloads[0]["status"] == "invalid_request"


def test_execution_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "execute_public_github", lambda _: pytest.fail("default-off execution called provider"))
    args = argparse.Namespace(command="external-evidence", external_evidence_action="execute",
        plan_json=str(tmp_path / "absent.json"), execute=False)
    assert cli.handle_external_evidence_command(args, output_format=lambda _: "json",
        print_payload=lambda *_: None) == 1


def test_parent_admission_and_real_ledger_coverage_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "_read", reader)
    p = plan()
    receipt = provider.execute_public_github(p)["receipt"]
    accepted = admission(p, receipt)
    start_research(tmp_path, question=p["request"]["objective"], max_sources=8, max_subquestions=4)
    retained = readback(p, receipt, accepted, project=tmp_path)
    assert retained["retirement"]["status"] == "retained"
    projected = readback(p, receipt, accepted, project=tmp_path, execute=True)
    assert projected["downstream_source_refs"] == [REF]
    assert projected["retirement"]["status"] == "retire_ready"
    replay = readback(p, receipt, accepted, project=tmp_path, execute=True)
    assert replay == projected
    assert len(load_state(tmp_path)["sources"]) == 1
    assert REF in render_readback(replay) and "admitted" in render_readback(replay)
    corrupt = copy.deepcopy(accepted)
    corrupt["downstream_projection"]["sources"][0]["finding"] = "mutated"
    with pytest.raises(ValueError, match="exact plan"):
        readback(p, receipt, corrupt, project=tmp_path, execute=True)


def test_wrong_question_or_unrelated_source_never_proves_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "_read", reader)
    p = plan()
    receipt = provider.execute_public_github(p)["receipt"]
    accepted = admission(p, receipt)
    start_research(tmp_path, question="Unrelated question", max_sources=8, max_subquestions=4)
    result = readback(p, receipt, accepted, project=tmp_path, execute=True)
    assert result["write_blockers"] and result["retirement"]["status"] == "retained"
    assert not load_state(tmp_path)["sources"]
    other = tmp_path / "other"
    start_research(other, question=p["request"]["objective"], max_sources=8, max_subquestions=4)
    add_source(other, url_or_path=REF, tool="manual", title=None, claims=[{"text":"Unrelated observation"}])
    result = readback(p, receipt, accepted, project=other, execute=True)
    assert result["write_blockers"] and result["downstream_source_refs"] == []


def test_partial_downstream_projection_retains_until_all_sources_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "_read", lambda url: reader(url.replace("second.md", "README.md")))
    p = plan([REF, REF.replace("README.md", "second.md")])
    receipt = provider.execute_public_github(p)["receipt"]
    accepted = admission(p, receipt)
    start_research(tmp_path, question=p["request"]["objective"], max_sources=1, max_subquestions=4)
    result = readback(p, receipt, accepted, project=tmp_path, execute=True)
    assert result["downstream_source_refs"] == [REF]
    assert result["retirement"]["status"] == "retained"
    assert len(result["retirement"]["missing_downstream_source_refs"]) == 1
    assert result["write_blockers"] and result["original_source_fallback_allowed"]


def test_existing_lark_sink_preserves_shared_readback_facts(tmp_path, monkeypatch):
    from loopx.extensions.lark.presentation.message_card import build_lark_markdown_reply_card
    monkeypatch.setattr(provider, "_read", reader)
    p = plan()
    receipt = provider.execute_public_github(p)["receipt"]
    accepted = admission(p, receipt)
    start_research(tmp_path, question=p["request"]["objective"], max_sources=8, max_subquestions=4)
    result = readback(p, receipt, accepted, project=tmp_path, execute=True)
    card = build_lark_markdown_reply_card(render_readback(result))
    content = card["elements"][0]["text"]["content"]
    assert "retire_ready" in content and REF in content
    assert "Evidence completeness is unverified" in content
    assert result["plan_id"] in content
    assert "truncated" not in content


def test_long_readback_uses_existing_lossless_lark_transport(monkeypatch):
    from loopx.extensions.lark.outbound import split_lark_outbound_text
    from loopx.extensions.lark.presentation.message_card import build_lark_markdown_reply_card
    monkeypatch.setattr(provider, "_read", reader)
    p = plan()
    receipt = provider.execute_public_github(p)["receipt"]
    receipt["sources"][0]["finding"] = "Bounded public finding. " * 150
    result = readback(p, receipt, admission(p, receipt))
    markdown = render_readback(result)
    parts = split_lark_outbound_text(markdown, limit=3000, preserve_format=True)
    cards = [build_lark_markdown_reply_card(part) for part in parts]
    assert len(cards) > 1
    body = "\n".join(card["elements"][0]["text"]["content"] for card in cards)
    assert result["plan_id"] in body and REF in body
    assert "Evidence completeness is unverified" in body and "truncated" not in body


def test_pinned_paths_keep_case_sensitive_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "_read", reader)
    refs = [REF, REF.replace("README.md", "readme.md")]
    p = plan(refs)
    receipt = provider.execute_public_github(p)["receipt"]
    accepted = admission(p, receipt)
    start_research(tmp_path, question=p["request"]["objective"], max_sources=8, max_subquestions=4)
    result = readback(p, receipt, accepted, project=tmp_path, execute=True)
    assert result["downstream_source_refs"] == refs
    assert result["retirement"]["retire_ready"] is True
