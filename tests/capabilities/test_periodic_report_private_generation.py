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
def test_frozen_report_keeps_original_source_links_across_renderer_upgrade(tmp_path, monkeypatch, tamper):
    from loopx.presentation.renderers import periodic_report_html as html
    from loopx.presentation.renderers import periodic_report_markdown as markdown

    root, window, response = _prepared_report(tmp_path)
    folder = attempt_directory(root, window)

    def previous_html_source(value, **_):
        raw = markdown._safe(value, maximum=500)
        parsed = urlparse(raw)
        return escape(raw, quote=True) if parsed.scheme in {"http", "https"} and parsed.netloc else None

    # Seed through the real freezer with the previous independently specified
    # source rules, not through the new validator's compatibility switch.
    with monkeypatch.context() as previous:
        previous.setattr(markdown, "_markdown_source", lambda value, labels, **_:
            f"[{labels['source']}]({markdown._safe(value, maximum=500)})")
        previous.setattr(html, "_safe_http_url", previous_html_source)
        receipt = freeze_private_report(root=root, window=window, response=response)
    original = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    assert "[来源](sha256:" in original["report.md"].decode("utf-8")
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
