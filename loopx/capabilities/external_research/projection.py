"""Shared external evidence readback for CLI and existing conversation surfaces.

Typed effect-runtime reductions remain the sole admission/retirement authority.
Deep-research owns its existing durable source ledger; no parallel evidence store.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...control_plane.effect_runtime import effect_runtime_result
from ..deep_research.runtime import add_source, load_state


def validate_receipt(plan: dict, receipt: dict) -> dict:
    return effect_runtime_result("external_evidence.receipt", {"plan": plan, "receipt": receipt})


def _validate_admission(plan: dict, receipt: dict, admission: dict) -> dict:
    normalized = effect_runtime_result("external_evidence.admit", {"plan": plan, "receipt": receipt,
        "decision": {"disposition": admission.get("disposition"), "reason": admission.get("reason"),
            "admitted_source_refs": admission.get("admitted_source_refs", [])}})
    if normalized != admission:
        raise ValueError("admission does not match its exact plan and receipt")
    return normalized


def _lineage(admission: dict, source: dict) -> dict:
    return {"plan_id": admission["plan_id"], "admission_id": admission["admission_id"],
        "receipt_digest": admission["receipt_digest"], "content_digest": source["content_digest"]}


def readback(plan: dict, receipt: dict, admission: dict | None = None,
    *, project: Path | None = None, execute: bool = False) -> dict:
    observation = validate_receipt(plan, receipt)
    receipt = observation["receipt"]
    if execute and (admission is None or project is None):
        raise ValueError("downstream write requires an explicit parent admission and research project")
    normalized = _validate_admission(plan, receipt, admission) if admission is not None else None
    write_blockers = []
    if execute and normalized["disposition"] == "admit":
        for source in normalized["downstream_projection"]["sources"]:
            try:
                add_source(project, url_or_path=source["source_ref"], tool="external-evidence",
                    title="Public evidence", claims=[{"text": source["finding"], "stance": "neutral"}],
                    external_evidence=_lineage(normalized, source), expected_question=plan["request"]["objective"],
                    source_accessed_at=source["accessed_at"], source_publication_date=source["publication_date"])
            except ValueError as error:
                # A bounded partial projection is retained. Retrying is idempotent
                # for the same identity; failed sources never count as covered.
                write_blockers.append(str(error))
                break
    covered = []
    if project is not None and normalized is not None:
        state = load_state(project)
        if state is not None and state["question"] == plan["request"]["objective"]:
            for source in normalized["downstream_projection"]["sources"]:
                for row in state["sources"]:
                    if (row["url_or_path"] == source["source_ref"] and
                        row.get("external_evidence") == _lineage(normalized, source) and
                        any(claim["id"] in row["claims"] and claim["text"] == source["finding"]
                            for claim in state["claims"])):
                        covered.append(source["source_ref"])
                        break
    retirement = effect_runtime_result("external_evidence.retire", {"admission": normalized,
        "downstream_source_refs": covered}) if normalized is not None else None
    return {"schema_version": "loopx_external_evidence_readback_v0", "plan_id": plan["plan_id"],
        "request": plan["request"], "provider_id": receipt["provider_id"], "receipt_status": receipt["status"],
        "sources": receipt["sources"], "summary": receipt["summary"], "limitations": receipt["limitations"],
        "parent_admission": None if normalized is None else {"disposition": normalized["disposition"],
            "reason": normalized["reason"], "admission_id": normalized["admission_id"],
            "admitted_source_refs": normalized["admitted_source_refs"]},
        "downstream_source_refs": covered, "retirement": retirement, "write_blockers": write_blockers,
        "original_source_fallback_allowed": True, "automatic_admission": False,
        "evidence_coverage_observed": False}


def _text(value: object) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", str(value)).replace("\n", " ")


def render_readback(payload: dict) -> str:
    admission = payload["parent_admission"]
    retirement = payload["retirement"]
    lines = ["## External evidence / 外部证据", "", _text(payload["request"]["objective"]), "",
        "- Provider / 来源：" + _text(payload["provider_id"]),
        "- Receipt / 读取结果：" + payload["receipt_status"],
        "- Parent decision / 父 Agent 决定：" + ("pending / 待采纳" if admission is None else _text(admission["disposition"])),
        f"- Downstream coverage / 下游覆盖：{len(payload['downstream_source_refs'])} admitted sources / 已采纳来源",
        "- Retirement / 退休：" + ("pending / 待决定" if retirement is None else retirement["status"]),
        "- Original-source fallback remains available / 可继续使用原始来源。",
        "- Evidence completeness is unverified / 证据完整性尚未验证。", ""]
    if admission is not None:
        lines.extend(["Parent reason / 采纳理由：" + _text(admission["reason"]), ""])
    for ref in payload["request"].get("source_refs", []):
        lines.extend(["Original source / 原始来源：" + _text(ref), ""])
    admitted = set(admission["admitted_source_refs"]) if admission is not None else set()
    covered = set(payload["downstream_source_refs"])
    for index, source in enumerate(payload["sources"], 1):
        ref = source["source_ref"]
        lines.extend([f"### Source {index} / 来源 {index}", "", _text(ref), "",
            _text(source["finding"]), "",
            "- Evidence basis / 依据：" + _text(source["basis"]),
            "- Accessed / 读取时间：" + _text(source["accessed_at"]),
            "- Digest / 摘要：" + _text(source["content_digest"]),
            "- Admission / 采纳：" + ("admitted" if ref in admitted else "not admitted"),
            "- Downstream / 下游：" + ("observed" if ref in covered else "not observed"),
            "- Limitation / 限制：" + _text(source.get("limitation") or "unspecified"), ""])
    for limitation in [*payload["limitations"], *payload["write_blockers"]]:
        lines.append("- " + _text(limitation))
    lines.extend(["", "Plan identity / 计划身份：" + _text(payload["plan_id"])])
    return "\n".join(lines) + "\n"
