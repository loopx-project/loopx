from __future__ import annotations

import hashlib
import json
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from loopx.capabilities.periodic_report.cadence_journal import (
    admit_cadence_window,
    cadence_intent,
)
from loopx.capabilities.periodic_report.machine_defaults import (
    resolve_goal_periodic_report_subscription,
)
from loopx.capabilities.periodic_report.pending_intent import (
    _build_editorial_request,
    _canonical_digest,
)
from loopx.capabilities.periodic_report.private_generation import (
    attempt_directory,
    freeze_private_report,
    prepare_private_report,
    prepare_session_report,
    validate_private_generation,
)
from loopx.registry import atomic_write_json


ARTIFACT_NAMES = {
    "editorial-request.json",
    "private-delivery.json",
    "private-generation.json",
    "report.md",
    "source.json",
}


def _prepared_report(tmp_path: Path):
    root = tmp_path / "runtime"
    window = admit_cadence_window(
        runtime_root=root,
        goal_id="encoding-goal",
        agent_id="encoding-reporter",
        subscription=resolve_goal_periodic_report_subscription(
            {
                "id": "encoding-goal",
                "control_plane": {
                    "periodic_report": {
                        "enabled": True,
                        "profile_preset": "portfolio-progress",
                        "route_ref": "report-channel",
                        "timezone": "UTC",
                        "schedule": {
                            "schema_version": "periodic_report_schedule_v0",
                            "schedule_id": "daily-report",
                            "timezone": "UTC",
                            "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
                            "start_date": "2001-01-01",
                        },
                    }
                },
            },
            None,
        ),
        now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
    )["window"]
    fact = {
        "fact_id": "portfolio_coverage",
        "title": "本期来源覆盖",
        "summary": "已读取本期授权组合来源，缺失和未验证项在报告中单独披露。",
        "status": "read",
        "content_kind": "coverage",
        "source_ref": "sha256:" + "a" * 64,
    }
    request = _build_editorial_request(
        intent=cadence_intent(window),
        runtime_root=root,
        goal_id="encoding-goal",
        agent_id="encoding-reporter",
        completed_at=window["due_at"],
        facts=[fact],
    )
    folder = attempt_directory(root, window)
    folder.mkdir(parents=True, exist_ok=True)
    source = {"goal_ids": ["encoding-goal"], "rows": [], "gaps": []}
    atomic_write_json(folder / "source.json", source)
    request.update(
        authorization_scope_id="scope-example",
        authorization_goal_ids=["encoding-goal"],
        reporting="Use the established overview-to-depth report contract.",
        source_artifact=str(folder / "source.json"),
        source_revision="sha256:" + "b" * 64,
        source_artifact_sha256=_canonical_digest(source),
        window=window,
        coverage={"goals_in_inventory": 1},
        destination="original_owner_private_chat_full_body",
    )
    request["request_digest"] = _canonical_digest(
        {key: value for key, value in request.items() if key != "request_digest"}
    )
    atomic_write_json(folder / "editorial-request.json", request)

    narrative = (
        "本期授权组合已完成来源核查，当前证据仍需区分实际成果、来源缺口与后续行动。"
        "任务状态和私聊送达分别验证，不能根据单次观察宣告业务完成。"
    )
    section_titles = [
        ("overview", "全景判断"),
        ("problem_map", "问题版图"),
        ("causal_analysis", "重点因果下钻"),
        ("coverage_and_actions", "版本覆盖与处置"),
        ("next_actions", "下一步"),
    ]
    sections = []
    for section_id, title in section_titles:
        item = {
            "title": f"{title}与证据边界",
            "summary": narrative,
            "source_ref": fact["source_ref"],
        }
        if section_id == "causal_analysis":
            item["details"] = [
                {"label": "证据", "text": narrative},
                {"label": "边界", "text": narrative},
            ]
        items = [item]
        if section_id == "coverage_and_actions":
            items.append(
                {
                    "title": "后续核验",
                    "summary": narrative,
                    "source_ref": fact["source_ref"],
                }
            )
        sections.append({"section_id": section_id, "title": title, "items": items})
    response = {
        "schema_version": "periodic_report_editorial_response_v0",
        "request_digest": request["request_digest"],
        "language": "zh-CN",
        "title": "本期授权工作详细汇报",
        "highlights": [
            {
                "highlight_id": "coverage",
                "label": "读取范围",
                "value": "已核查",
                "detail": "保留来源缺口",
                "tone": "neutral",
            },
            {
                "highlight_id": "delivery",
                "label": "送达情况",
                "value": "待核验",
                "detail": "生成不代表送达",
                "tone": "attention",
            },
        ],
        "sections": sections,
    }
    return root, window, response


def _source_prepared_report(tmp_path: Path, *, local: bool):
    _, window, response = _prepared_report(tmp_path / "template")
    root = tmp_path / "bound-runtime"
    source = {"rows": [{"todo_id": "todo_source", "text": "保留原始证据与未完成工作", "status": "open"}]}
    source["source_revision"] = _canonical_digest(source)

    def read(arguments):
        rows = ([{"goal_id": "encoding-goal", "activation_state": "active"}]
            if arguments["view"] == "portfolio" else source["rows"]
            if arguments["view"] == "todos" else [])
        return {"ok": True, "rows": rows, "matched": len(rows), "next_offset": None,
            "source": {"source_revision": source["source_revision"], "status": "read"}}

    def prepare():
        if local:
            return prepare_session_report(root=root, window=window,
                actor={"session_id": "original-session"}, read=lambda: source,
                reporting="保留实际来源。")
        return prepare_private_report(root=root, window=window, read=read,
            authorization_scope_id="scope-example", authorization_goal_ids=["encoding-goal"],
            reporting="保留实际来源。")

    request = prepare()
    response["request_digest"] = request["request_digest"]
    for section in response["sections"]:
        for item in section["items"]:
            item["source_ref"] = request["facts"][0]["source_ref"]
    return root, window, response, prepare


@pytest.mark.parametrize("local", [False, True])
def test_source_bound_report_keeps_complete_facts_and_original_artifacts(tmp_path, local):
    root, window, response, prepare = _source_prepared_report(tmp_path, local=local)
    folder = attempt_directory(root, window)
    before = {name: (folder / name).read_bytes() for name in ["source.json", "editorial-request.json"]}
    request = prepare()
    assert len(request["facts"]) == len(json.loads(before["source.json"])["rows"]) + 1
    receipt = freeze_private_report(root=root, window=window, response=response)
    assert validate_private_generation(folder) == receipt
    assert {name: (folder / name).read_bytes() for name in before} == before
    bundle = json.loads((folder / "generation-bundle.json").read_text())
    assert all(item["source_ref"] == request["facts"][0]["source_ref"]
        for section in bundle["document"]["sections"] for item in section["items"])


@pytest.mark.parametrize("legacy", [False, True])
def test_recorded_citation_reaches_frozen_facts_and_both_artifacts_without_public_access_claim(
        tmp_path, legacy):
    from loopx.capabilities.manager_context.report_source import collect_manager_report_source
    from loopx.chat_manager_history import _recorded_details

    root, window, response = _prepared_report(tmp_path / "template")
    root = tmp_path / "citation-runtime"
    reference = "https://example.org/results/42"
    details = _recorded_details({"agent_vision": {"path_delta": {"evidence_refs": [reference]}}}, complete=True)

    def read(arguments):
        rows = ([{"goal_id": "encoding-goal"}] if arguments["view"] == "portfolio" else
            [{"source_ref": "sha256:" + "d" * 64, "recorded_details": details}]
            if arguments["view"] == "deliveries" else [])
        return {"ok": True, "rows": rows, "matched": len(rows), "next_offset": None,
            "source": {"status": "read", "source_revision": "sha256:" + "c" * 64}}

    request = prepare_private_report(root=root, window=window, read=read,
        authorization_scope_id="scope-example", authorization_goal_ids=["encoding-goal"], reporting="保留原出处。")
    folder = attempt_directory(root, window)
    # The shipped v1 input has no additional citation facts. Specify that
    # compatibility delta directly rather than deriving it from a v1 helper.
    if legacy:
        source = json.loads((folder / "source.json").read_text())
        request["facts"] = [fact for fact in request["facts"] if fact["source_ref"] != reference]
        source["source_fact_binding"] = request["source_fact_binding"] = "frozen_source_v1"
        request["source_artifact_sha256"] = _canonical_digest(source)
        request["request_digest"] = _canonical_digest({k: v for k, v in request.items() if k != "request_digest"})
        atomic_write_json(folder / "source.json", source)
        atomic_write_json(folder / "editorial-request.json", request)
    citation_facts = [fact for fact in request["facts"] if fact["source_ref"] == reference]
    assert len(citation_facts) == (0 if legacy else 1)
    if not legacy:
        assert citation_facts[0]["status"] == "unverified"
        assert "可达性和公开许可均未核验" in citation_facts[0]["summary"]
        assert "sha256:" + "d" * 64 in citation_facts[0]["summary"]
    response["request_digest"] = request["request_digest"]
    for section in response["sections"]:
        for item in section["items"]:
            item["source_ref"] = request["facts"][0]["source_ref"] if legacy else reference
    receipt = freeze_private_report(root=root, window=window, response=response)
    before = {path.name: path.read_bytes() for path in folder.iterdir() if path.suffix in {".json", ".md"}}
    assert validate_private_generation(folder) == receipt
    assert prepare_private_report(root=root, window=window, read=lambda _: pytest.fail("frozen input reread"),
        authorization_scope_id="scope-example", authorization_goal_ids=["encoding-goal"],
        reporting="保留原出处。") == request
    assert {name: (folder / name).read_bytes() for name in before} == before
    bundle = json.loads((folder / "generation-bundle.json").read_text())
    artifacts = {item["renderer_kind"]: item["content"] for item in bundle["artifacts"]}
    assert (f"]({reference})" in artifacts["markdown"]) is not legacy
    assert (f'href="{reference}"' in artifacts["html"]) is not legacy
    snapshot = collect_manager_report_source(read, {"window_start": window["start_at"], "window_end": window["due_at"]})
    assert not snapshot["coverage"]["referenced_artifacts_read"]
    # A different audience cannot adopt the same frozen input.
    with pytest.raises(ValueError, match="another scope"):
        prepare_private_report(root=root, window=window, read=read,
            authorization_scope_id="other-scope", authorization_goal_ids=["encoding-goal"], reporting="保留原出处。")


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("entrypoint", ["prepare", "freeze"])
@pytest.mark.parametrize("substitution", ["url", "summary", "omission", "revision", "binding_removed"])
def test_source_bound_report_rejects_rehashed_input_substitution(tmp_path, local, entrypoint, substitution):
    root, window, response, prepare = _source_prepared_report(tmp_path, local=local)
    folder = attempt_directory(root, window)
    original_source = (folder / "source.json").read_bytes()
    request = json.loads((folder / "editorial-request.json").read_text())
    if substitution == "url":
        request["facts"][0]["source_ref"] = "https://example.org/not-in-the-frozen-evidence"
    elif substitution == "summary":
        request["facts"][0]["summary"] = "原始来源没有提供的成功结论。"
    elif substitution == "omission":
        request["facts"].pop()
    elif substitution == "revision":
        request["source_revision"] = "sha256:" + "e" * 64
    else:
        request.pop("source_fact_binding", None)
        request["facts"][0]["source_ref"] = "https://example.org/removed-input-binding"
    request["request_digest"] = _canonical_digest({k: v for k, v in request.items() if k != "request_digest"})
    atomic_write_json(folder / "editorial-request.json", request)
    response["request_digest"] = request["request_digest"]
    for section in response["sections"]:
        for item in section["items"]:
            item["source_ref"] = request["facts"][0]["source_ref"]
    with pytest.raises(ValueError, match="facts do not match"):
        if entrypoint == "prepare":
            prepare()
        else:
            freeze_private_report(root=root, window=window, response=response)
    assert (folder / "source.json").read_bytes() == original_source
    assert not (folder / "editorial-response.json").exists()
    assert not (folder / "private-generation.json").exists()
    assert not (folder / "report.md").exists()


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("remove_binding", [False, True])
def test_generation_validator_rejects_rehashed_facts_with_self_consistent_receipts(tmp_path, local, remove_binding):
    from loopx.capabilities.periodic_report.incremental import build_periodic_report_publication_candidate
    from loopx.capabilities.periodic_report.post_writeback_hook import evaluate_periodic_report_trigger_evaluation_intent

    root, window, response, _ = _source_prepared_report(tmp_path, local=local)
    folder = attempt_directory(root, window)
    receipt = freeze_private_report(root=root, window=window, response=response)
    request = json.loads((folder / "editorial-request.json").read_text())
    request["facts"][0]["summary"] = "替换了原始来源不支持的事实。"
    if remove_binding:
        request.pop("source_fact_binding", None)
        receipt.pop("source_fact_binding", None)
    request["request_digest"] = _canonical_digest({k: v for k, v in request.items() if k != "request_digest"})
    response["request_digest"] = request["request_digest"]
    candidate = build_periodic_report_publication_candidate(goal_id=window["goal_id"],
        agent_id=window["agent_id"], generation_id=receipt["generation_id"],
        trigger_receipt=evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(window)),
        facts=request["facts"], baseline=None)
    # Every existing self-digest is consistent; the unchanged independent
    # source remains the oracle, rather than another hash of substituted facts.
    atomic_write_json(folder / "editorial-request.json", request)
    atomic_write_json(folder / "editorial-response.json", response)
    atomic_write_json(folder / "publication-candidate.json", candidate)
    atomic_write_json(folder / "private-generation.json", {**receipt,
        "request_digest": request["request_digest"], "publication_candidate_id": candidate["candidate_id"],
        "publication_candidate_sha256": _canonical_digest(candidate)})
    with pytest.raises(ValueError, match="facts do not match"):
        validate_private_generation(folder)


def test_private_report_artifacts_use_canonical_utf8_on_windows_text_mode(
    tmp_path: Path, monkeypatch
) -> None:
    root, window, response = _prepared_report(tmp_path)
    folder = attempt_directory(root, window)

    real_write_text = Path.write_text
    real_read_text = Path.read_text

    def windows_write_text(path: Path, data: str, *args, **kwargs):
        if path.name == "report.md" and kwargs.get("newline") is None:
            data = data.replace("\n", "\r\n")
            kwargs["newline"] = ""
        return real_write_text(path, data, *args, **kwargs)

    def host_codec_read_text(path: Path, *args, **kwargs):
        if path.name in ARTIFACT_NAMES and not args and "encoding" not in kwargs:
            kwargs["encoding"] = "ascii"
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    monkeypatch.setattr(Path, "read_text", host_codec_read_text)

    receipt = freeze_private_report(root=root, window=window, response=response)
    report_bytes = (folder / "report.md").read_bytes()
    assert b"\r\n" not in report_bytes
    assert receipt["body_sha256"] == hashlib.sha256(report_bytes).hexdigest()


@pytest.mark.parametrize("tamper", [None, "report", "html", "source", "response"])
@pytest.mark.parametrize("legacy_links", [False, True, None])
def test_frozen_report_keeps_prior_presentations_across_renderer_upgrade(tmp_path, monkeypatch, tamper, legacy_links):
    from loopx.presentation.renderers import periodic_report_html as html
    from loopx.presentation.renderers import periodic_report_markdown as markdown

    root, window, response = _prepared_report(tmp_path)
    folder = attempt_directory(root, window)

    def previous_html_source(value, **_):
        raw = markdown._safe(value, maximum=500)
        parsed = urlparse(raw)
        return escape(raw, quote=True) if parsed.scheme in {"http", "https"} and parsed.netloc else None

    prior_markdown = markdown.render_periodic_report_markdown
    prior_html = html.render_periodic_report_html
    # Seed through the real freezer, retaining all three shipped layouts.
    # The old source rules remain independently specified, not inferred from
    # the new link-eligibility helper or the validator's selected fallback.
    with monkeypatch.context() as previous:
        # Independently reproduce the shipped CSS. Its automatic side column
        # predates bounded prose highlights in all three presentations.
        previous.setattr(html, "_CSS", html._CSS.replace(
            "grid-template-columns: minmax(0, 1fr) minmax(240px, 360px)",
            "grid-template-columns: minmax(0, 1fr) auto", 1))
        if legacy_links is not None:
            previous.setattr(markdown, "render_periodic_report_markdown", lambda document, **kwargs:
                prior_markdown(document, **{**kwargs, "_legacy_layout": True}))
            previous.setattr(html, "render_periodic_report_html", lambda document, **kwargs:
                prior_html(document, **{**kwargs, "_legacy_layout": True}))
        if legacy_links:
            previous.setattr(markdown, "_markdown_source", lambda value, labels, **_:
                f"[{labels['source']}]({markdown._safe(value, maximum=500)})")
            previous.setattr(html, "_safe_http_url", previous_html_source)
        receipt = freeze_private_report(root=root, window=window, response=response)
    original = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    # Independent historical renderer oracles: parent of #7413 for old links,
    # e20e4b684 for safe inline references, be1dc37a0 for indexed sources.
    # The input/window above is fixed.
    expected_text_digest = (
        "509ff2e53bbce2f703049f1684e1a9f2048826ebb8c386891650b52841a1ed23"
        if legacy_links else
        "8d97d55e80f759e7eaadb73c5df93afea4d5cfbc071301a9726cb47ff37f9755"
        if legacy_links is False else
        "0041149af93d653431f5a70e0b98ad388462b1b93b81bc7a718a39f0b54dec8d"
    )
    assert hashlib.sha256(original["report.md"]).hexdigest() == expected_text_digest
    assert ("[来源](sha256:" in original["report.md"].decode("utf-8")) is bool(legacy_links)
    assert "[来源](sha256:" not in markdown.render_periodic_report_markdown(
        json.loads(original["generation-bundle.json"])["document"])["content"]

    if tamper == "report":
        body = original["report.md"] + b"\nA different unapproved claim.\n"
        (folder / "report.md").write_bytes(body)
        atomic_write_json(folder / "private-generation.json", {
            **receipt, "body_sha256": hashlib.sha256(body).hexdigest()})
    elif tamper == "html":
        bundle = json.loads(original["generation-bundle.json"])
        artifact = next(item for item in bundle["artifacts"] if item["renderer_kind"] == "html")
        artifact["content"] += "<p>A different unapproved claim.</p>"
        artifact["content_digest"] = "sha256:" + hashlib.sha256(artifact["content"].encode()).hexdigest()
        atomic_write_json(folder / "generation-bundle.json", bundle)
    elif tamper in {"source", "response"}:
        name = "source.json" if tamper == "source" else "editorial-response.json"
        changed = json.loads(original[name])
        changed["rows" if tamper == "source" else "title"] = [] if tamper == "source" else "Another title"
        if tamper == "source":
            changed["gaps"] = ["An unapproved source change"]
        atomic_write_json(folder / name, changed)
    if tamper:
        with pytest.raises(ValueError):
            validate_private_generation(folder)
        return

    assert validate_private_generation(folder) == receipt
    assert validate_private_generation(folder) == receipt  # Restart/readback is effect-free.
    assert {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()} == original
