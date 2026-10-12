from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import re
from typing import Any

from .presentation_compat import require_presentation_api

require_presentation_api()

from .presentation_view import validate_decision_research_view  # noqa: E402
from .source_coverage import SOURCE_PERIOD_METRIC_FIELDS  # noqa: E402


FINANCE_RESEARCH_DASHBOARD_INPUT_SCHEMA_VERSION = "finance_research_dashboard_input_v0"
FINANCE_RESEARCH_DASHBOARD_PACKET_SCHEMA_VERSION = (
    "finance_research_dashboard_packet_v0"
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MARKUP_RE = re.compile(r"<[^>]*>|javascript:", re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s(])(?:~[/\\]|/+(?:Users|home|tmp|private|var|etc|opt)/|"
    r"[A-Za-z]:[\\/])"
)
_CREDENTIAL_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:api[_ -]?key|access[_ -]?token|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_TARGET_PRICE_RE = re.compile(r"\btarget[\s-]?price\b", re.IGNORECASE)
_FORBIDDEN_KEY_NAMES = {
    "account_id",
    "account_number",
    "access_token",
    "api_key",
    "credential",
    "cookie",
    "order_id",
    "order_request",
    "portfolio",
    "portfolio_holdings",
    "position_size",
    "raw_provider",
    "raw_request",
    "raw_response",
    "secret",
    "token",
}
_BOUNDARY_EXCEPTIONS = {
    "raw_provider_payload_recorded",
    "private_source_content_read",
}
_EVIDENCE_CLASSIFICATIONS = {
    "current_fact",
    "historical_fact",
    "management_guidance",
    "analyst_estimate",
    "agent_inference",
}
_ADJUDICATION_LABELS = {
    "selected": "Selected",
    "rejected": "Rejected",
    "insufficient_evidence": "Insufficient Evidence",
    "blocked": "Blocked",
    "superseded": "Superseded",
}


def _record(
    value: Any,
    *,
    context: str,
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        raise ValueError(f"{context} contains unsupported keys {unsupported}")
    return value


def _list(
    value: Any,
    *,
    context: str,
    minimum: int = 0,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{context} must be an array")
    result = list(value)
    if not minimum <= len(result) <= maximum:
        raise ValueError(
            f"{context} must contain between {minimum} and {maximum} items"
        )
    return result


def _text(
    value: Any,
    *,
    context: str,
    maximum: int = 700,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be plain text")
    text = " ".join(value.split())
    if not text:
        raise ValueError(f"{context} must be non-empty plain text")
    if len(text) > maximum:
        raise ValueError(f"{context} must contain at most {maximum} characters")
    if _MARKUP_RE.search(text):
        raise ValueError(f"{context} must not contain markup")
    if _LOCAL_PATH_RE.search(text):
        raise ValueError(f"{context} must not contain a local path")
    if _CREDENTIAL_RE.search(text):
        raise ValueError(f"{context} must not contain credential material")
    return text


def _identifier(value: Any, *, context: str) -> str:
    text = _text(value, context=context, maximum=128)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{context} must be a stable identifier")
    return text


def _integer(value: Any, *, context: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{context} must be an integer >= {minimum}")
    return value


def _iso(
    value: Any,
    *,
    context: str,
    require_time: bool,
) -> str:
    text = _text(value, context=context, maximum=40)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        if "T" in normalized:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                raise ValueError
        elif not require_time:
            date.fromisoformat(normalized)
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{context} must be a timezone-aware ISO-8601 value") from exc
    return text


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )


def _assert_finance_truth(view: Mapping[str, Any]) -> None:
    adjudication = view["adjudication"]
    expected_label = _ADJUDICATION_LABELS[adjudication["status"]]
    if adjudication["label"] != expected_label:
        raise ValueError(
            "input.adjudication label must match its canonical status "
            f"(`{expected_label}`)"
        )

    metrics = {metric["id"]: metric for metric in view["metrics"]}
    if len(metrics) != len(view["metrics"]):
        raise ValueError("input.metrics ids must be unique")
    validated_alpha = metrics.get("validated-alpha")
    if validated_alpha is None:
        raise ValueError("input.metrics requires canonical `validated-alpha`")
    if validated_alpha["value"] != "0" or validated_alpha["tone"] != "warning":
        raise ValueError(
            "input.metrics `validated-alpha` must preserve value 0 with warning tone"
        )
    method_metric = metrics.get("method-state")
    if method_metric is None:
        raise ValueError("input.metrics requires canonical `method-state`")
    if method_metric["value"] != "unchanged" or method_metric["tone"] != "neutral":
        raise ValueError(
            "input.metrics `method-state` must preserve unchanged with neutral tone"
        )

    method_state = view["method_state"]
    if (
        method_state["active_method_changed"]
        or method_state["lifecycle_state"] != "active_method_unchanged"
    ):
        raise ValueError("input.method_state must preserve active method unchanged")


def _reject_forbidden_material(value: Any, *, context: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                any(
                    normalized == forbidden or normalized.startswith(f"{forbidden}_")
                    for forbidden in _FORBIDDEN_KEY_NAMES
                )
                and normalized not in _BOUNDARY_EXCEPTIONS
            ):
                raise ValueError(f"{context} contains forbidden key `{key}`")
            _reject_forbidden_material(child, context=f"{context}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_material(child, context=f"{context}[{index}]")
    elif isinstance(value, str):
        _text(value, context=context, maximum=2_000)


def _simple_records(
    value: Any,
    *,
    context: str,
    allowed: set[str],
    minimum: int = 0,
    maximum: int,
) -> list[dict[str, Any]]:
    return [
        dict(_record(item, context=f"{context}[{index}]", allowed=allowed))
        for index, item in enumerate(
            _list(value, context=context, minimum=minimum, maximum=maximum)
        )
    ]


def _evidence_items(
    value: Any,
    *,
    context: str,
    minimum: int = 1,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, item in enumerate(
        _list(value, context=context, minimum=minimum, maximum=16)
    ):
        item_context = f"{context}[{index}]"
        record = _record(
            item,
            context=item_context,
            allowed={"classification", "statement"},
        )
        classification = _text(
            record.get("classification"),
            context=f"{item_context}.classification",
            maximum=40,
        )
        if classification not in _EVIDENCE_CLASSIFICATIONS:
            raise ValueError(
                f"{item_context}.classification must be a supported evidence class"
            )
        result.append(
            {
                "classification": classification,
                "statement": _text(
                    record.get("statement"),
                    context=f"{item_context}.statement",
                ),
            }
        )
    return result


def _evidence_lines(
    *,
    supporting: list[dict[str, str]],
    counterevidence: list[dict[str, str]],
    thesis_breakers: list[str],
) -> list[str]:
    lines = [
        f"Support ({item['classification']}): {item['statement']}"
        for item in supporting
    ]
    lines.extend(
        f"Counterevidence ({item['classification']}): {item['statement']}"
        for item in counterevidence
    )
    lines.extend(f"Thesis breaker: {item}" for item in thesis_breakers)
    return lines


def _text_list(
    value: Any,
    *,
    context: str,
    minimum: int = 0,
    maximum: int = 12,
) -> list[str]:
    return [
        _text(item, context=f"{context}[{index}]")
        for index, item in enumerate(
            _list(value, context=context, minimum=minimum, maximum=maximum)
        )
    ]


def _layers(value: Any) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for index, item in enumerate(
        _list(value, context="input.research_layers", minimum=1, maximum=12)
    ):
        context = f"input.research_layers[{index}]"
        record = _record(
            item,
            context=context,
            allowed={
                "id",
                "order",
                "label",
                "status",
                "summary",
                "supporting_evidence",
                "counterevidence",
                "thesis_breakers",
            },
        )
        supporting = _evidence_items(
            record.get("supporting_evidence"),
            context=f"{context}.supporting_evidence",
        )
        counterevidence = _evidence_items(
            record.get("counterevidence"),
            context=f"{context}.counterevidence",
        )
        breakers = _text_list(
            record.get("thesis_breakers"),
            context=f"{context}.thesis_breakers",
            minimum=1,
        )
        layers.append(
            {
                "id": _identifier(record.get("id"), context=f"{context}.id"),
                "order": _integer(
                    record.get("order"),
                    context=f"{context}.order",
                    minimum=1,
                ),
                "label": record.get("label"),
                "status": record.get("status"),
                "summary": record.get("summary"),
                "evidence_points": _evidence_lines(
                    supporting=supporting,
                    counterevidence=counterevidence,
                    thesis_breakers=breakers,
                ),
            }
        )
    return layers


def _observations(value: Any, *, entity_context: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    allowed = {
        "id",
        "label",
        "classification",
        "kind",
        "value",
        "as_of",
        "source_ref",
        "source_type",
        "confidence",
        "invalidation",
    }
    for index, record in enumerate(
        _simple_records(
            value,
            context=f"{entity_context}.observations",
            allowed=allowed,
            minimum=1,
            maximum=20,
        )
    ):
        context = f"{entity_context}.observations[{index}]"
        classification = _text(
            record.get("classification"),
            context=f"{context}.classification",
            maximum=40,
        )
        if classification not in _EVIDENCE_CLASSIFICATIONS:
            raise ValueError(
                f"{context}.classification must be a supported evidence class"
            )
        kind = _text(record.get("kind"), context=f"{context}.kind", maximum=40)
        if kind != "observation_range" and kind != classification:
            raise ValueError(
                f"{context}.kind must match classification or be observation_range"
            )
        record.pop("classification")
        observations.append(record)
    return observations


def _scenarios(value: Any, *, entity_context: str) -> list[dict[str, Any]]:
    scenarios = _simple_records(
        value,
        context=f"{entity_context}.scenarios",
        allowed={
            "scenario",
            "label",
            "value",
            "horizon",
            "probability",
            "assumptions",
        },
        minimum=3,
        maximum=3,
    )
    for index, scenario in enumerate(scenarios):
        context = f"{entity_context}.scenarios[{index}]"
        for field in ("label", "value"):
            text = _text(
                scenario.get(field),
                context=f"{context}.{field}",
                maximum=160,
            )
            if _TARGET_PRICE_RE.search(text):
                raise ValueError(f"{context}.{field} must not use target-price wording")
    return scenarios


def _entities(value: Any) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, context="input.entities", maximum=24)):
        context = f"input.entities[{index}]"
        record = _record(
            item,
            context=context,
            allowed={
                "entity_id",
                "symbol",
                "display_name",
                "classification",
                "status",
                "confidence",
                "inference",
                "supporting_evidence",
                "counterevidence",
                "thesis_breakers",
                "observations",
                "scenarios",
                "next_events",
            },
        )
        supporting = _evidence_items(
            record.get("supporting_evidence"),
            context=f"{context}.supporting_evidence",
        )
        counterevidence = _evidence_items(
            record.get("counterevidence"),
            context=f"{context}.counterevidence",
        )
        breakers = _text_list(
            record.get("thesis_breakers"),
            context=f"{context}.thesis_breakers",
            minimum=1,
        )
        inference = _text(record.get("inference"), context=f"{context}.inference")
        support_summary = " ".join(
            f"Support ({item['classification']}): {item['statement']}"
            for item in supporting
        )
        entities.append(
            {
                "entity_id": record.get("entity_id"),
                "symbol": record.get("symbol"),
                "display_name": record.get("display_name"),
                "classification": record.get("classification"),
                "status": record.get("status"),
                "confidence": record.get("confidence"),
                "inference": f"{inference} {support_summary}",
                "observations": _observations(
                    record.get("observations"),
                    entity_context=context,
                ),
                "scenario_estimates": _scenarios(
                    record.get("scenarios"),
                    entity_context=context,
                ),
                "counterevidence": [
                    f"{item['classification']}: {item['statement']}"
                    for item in counterevidence
                ],
                "thesis_breakers": breakers,
                "next_events": _text_list(
                    record.get("next_events"),
                    context=f"{context}.next_events",
                ),
            }
        )
    return entities


def _research_ledger(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(
        _list(value, context="input.research_ledger", maximum=40)
    ):
        context = f"input.research_ledger[{index}]"
        record = _record(
            item,
            context=context,
            allowed={
                "case_id",
                "label",
                "gate_states",
                "decision",
                "summary",
                "evidence_refs",
            },
        )
        rows.append(
            {
                "case_id": record.get("case_id"),
                "label": record.get("label"),
                "gate_states": _simple_records(
                    record.get("gate_states"),
                    context=f"{context}.gate_states",
                    allowed={"gate_id", "label", "status", "summary"},
                    minimum=1,
                    maximum=16,
                ),
                "decision": record.get("decision"),
                "summary": record.get("summary"),
                "evidence_refs": _text_list(
                    record.get("evidence_refs"),
                    context=f"{context}.evidence_refs",
                    minimum=1,
                    maximum=20,
                ),
            }
        )
    return rows


def _artifacts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, context="input.artifacts", maximum=20)):
        context = f"input.artifacts[{index}]"
        record = _record(
            item,
            context=context,
            allowed={
                "artifact_id",
                "kind",
                "label",
                "summary",
                "artifact_ref",
                "evidence_refs",
            },
        )
        rows.append(
            {
                "artifact_id": record.get("artifact_id"),
                "kind": record.get("kind"),
                "label": record.get("label"),
                "summary": record.get("summary"),
                "artifact_ref": record.get("artifact_ref"),
                "evidence_refs": _text_list(
                    record.get("evidence_refs"),
                    context=f"{context}.evidence_refs",
                    minimum=1,
                    maximum=20,
                ),
            }
        )
    return rows


def _event_evidence(
    value: Any,
    *,
    context: str,
    frozen_at: str,
    point_in_time: str,
    require_post_freeze: bool,
) -> list[str]:
    evidence: list[str] = []
    frozen = _parsed_datetime(frozen_at)
    cutoff = _parsed_datetime(point_in_time)
    for index, item in enumerate(_list(value, context=context, maximum=16)):
        item_context = f"{context}[{index}]"
        record = _record(
            item,
            context=item_context,
            allowed={"classification", "statement", "observed_at"},
        )
        classification = _text(
            record.get("classification"),
            context=f"{item_context}.classification",
            maximum=40,
        )
        if classification not in _EVIDENCE_CLASSIFICATIONS:
            raise ValueError(
                f"{item_context}.classification must be a supported evidence class"
            )
        observed_at = _iso(
            record.get("observed_at"),
            context=f"{item_context}.observed_at",
            require_time=True,
        )
        observed = _parsed_datetime(observed_at)
        if require_post_freeze and observed <= frozen:
            raise ValueError(
                f"{item_context}.observed_at must be after frozen_at "
                "for post-event adjudication"
            )
        if observed > cutoff:
            raise ValueError(
                f"{item_context}.observed_at must not be after point_in_time"
            )
        statement = _text(
            record.get("statement"),
            context=f"{item_context}.statement",
        )
        evidence.append(f"{classification} at {observed_at}: {statement}")
    return evidence


def _event_gates(value: Any, *, point_in_time: str) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, context="input.event_gates", maximum=32)):
        context = f"input.event_gates[{index}]"
        record = _record(
            item,
            context=context,
            allowed={
                "event_id",
                "label",
                "status",
                "frozen_at",
                "observation_window",
                "frozen_hypothesis",
                "observables",
                "current_evidence",
                "supports",
                "refutes",
                "thesis_breakers",
                "next_review",
            },
        )
        frozen_at = _iso(
            record.get("frozen_at"),
            context=f"{context}.frozen_at",
            require_time=True,
        )
        status = _text(
            record.get("status"),
            context=f"{context}.status",
            maximum=40,
        )
        current_evidence = _event_evidence(
            record.get("current_evidence"),
            context=f"{context}.current_evidence",
            frozen_at=frozen_at,
            point_in_time=point_in_time,
            require_post_freeze=status
            in {"partially_adjudicated", "selected", "rejected"},
        )
        if (
            status in {"partially_adjudicated", "selected", "rejected"}
            and not current_evidence
        ):
            raise ValueError(
                f"{context}.current_evidence is required for post-event adjudication"
            )
        gates.append(
            {
                "event_id": record.get("event_id"),
                "label": record.get("label"),
                "status": status,
                "observation_window": record.get("observation_window"),
                "frozen_hypothesis": record.get("frozen_hypothesis"),
                "observables": _text_list(
                    record.get("observables"),
                    context=f"{context}.observables",
                    minimum=1,
                    maximum=16,
                ),
                "current_evidence": current_evidence,
                "supports": _text_list(
                    record.get("supports"),
                    context=f"{context}.supports",
                    minimum=1,
                ),
                "refutes": _text_list(
                    record.get("refutes"),
                    context=f"{context}.refutes",
                    minimum=1,
                ),
                "thesis_breakers": _text_list(
                    record.get("thesis_breakers"),
                    context=f"{context}.thesis_breakers",
                    minimum=1,
                ),
                "next_review": record.get("next_review"),
            }
        )
    return gates


def build_finance_research_dashboard_packet(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate Finance semantics and map them to the generic research view."""

    _reject_forbidden_material(payload)
    from .funding_presentation import (
        FINANCE_FUNDING_RESEARCH_INPUT_SCHEMA_VERSION,
        build_funding_event_research_packet,
    )

    if payload.get("schema_version") == FINANCE_FUNDING_RESEARCH_INPUT_SCHEMA_VERSION:
        return build_funding_event_research_packet(payload)
    record = _record(
        payload,
        context="input",
        allowed={
            "schema_version",
            "surface_id",
            "goal_id",
            "source_id",
            "source_version",
            "supersedes",
            "point_in_time",
            "evidence_cutoff",
            "review_due_at",
            "title",
            "subtitle",
            "adjudication",
            "metrics",
            "source_period_metrics",
            "spot_market_identity",
            "dashboard_summaries",
            "research_layers",
            "entities",
            "research_ledger",
            "artifacts",
            "event_gates",
            "method_state",
            "boundary",
        },
    )
    if record.get("schema_version") != FINANCE_RESEARCH_DASHBOARD_INPUT_SCHEMA_VERSION:
        raise ValueError("input has unsupported schema_version")

    point_in_time = _iso(
        record.get("point_in_time"),
        context="input.point_in_time",
        require_time=True,
    )
    evidence_cutoff = _iso(
        record.get("evidence_cutoff"),
        context="input.evidence_cutoff",
        require_time=False,
    )
    review_due_at = _iso(
        record.get("review_due_at"),
        context="input.review_due_at",
        require_time=True,
    )
    supersedes = [
        _identifier(item, context=f"input.supersedes[{index}]")
        for index, item in enumerate(
            _list(record.get("supersedes"), context="input.supersedes", maximum=20)
        )
    ]
    view = validate_decision_research_view(
        {
            "identity": {
                "title": record.get("title"),
                "subtitle": record.get("subtitle"),
                "as_of": point_in_time,
                "evidence_cutoff": evidence_cutoff,
            },
            "adjudication": dict(
                _record(
                    record.get("adjudication"),
                    context="input.adjudication",
                    allowed={"status", "label", "summary", "confidence"},
                )
            ),
            "metrics": _simple_records(
                record.get("metrics"),
                context="input.metrics",
                allowed={"id", "label", "value", "detail", "tone"},
                minimum=1,
                maximum=12,
            ),
            **(
                {
                    "source_period_metrics": _simple_records(
                        record.get("source_period_metrics"),
                        context="input.source_period_metrics",
                        allowed=SOURCE_PERIOD_METRIC_FIELDS,
                        maximum=24,
                    )
                }
                if "source_period_metrics" in record
                else {}
            ),
            **(
                {"spot_market_identity": record.get("spot_market_identity")}
                if "spot_market_identity" in record
                else {}
            ),
            "dashboard_summaries": _simple_records(
                record.get("dashboard_summaries"),
                context="input.dashboard_summaries",
                allowed={
                    "id",
                    "label",
                    "title",
                    "summary",
                    "tone",
                    "destination_anchor",
                },
                maximum=3,
            ),
            "layers": _layers(record.get("research_layers")),
            "entities": _entities(record.get("entities")),
            "research_ledger": _research_ledger(record.get("research_ledger")),
            "artifacts": _artifacts(record.get("artifacts")),
            "event_gates": _event_gates(
                record.get("event_gates"),
                point_in_time=point_in_time,
            ),
            "method_state": dict(
                _record(
                    record.get("method_state"),
                    context="input.method_state",
                    allowed={
                        "revision",
                        "lifecycle_state",
                        "active_method_changed",
                        "summary",
                    },
                )
            ),
            "boundary": dict(
                _record(
                    record.get("boundary"),
                    context="input.boundary",
                    allowed={
                        "research_aid_only",
                        "investment_advice",
                        "trading_allowed",
                        "raw_provider_payload_recorded",
                        "private_source_content_read",
                    },
                )
            ),
        }
    )
    _assert_finance_truth(view)
    return {
        "schema_version": FINANCE_RESEARCH_DASHBOARD_PACKET_SCHEMA_VERSION,
        "presentation_projection": {
            "schema_version": "extension_presentation_projection_v0",
            "surface_id": _identifier(
                record.get("surface_id"),
                context="input.surface_id",
            ),
            "goal_id": _identifier(record.get("goal_id"), context="input.goal_id"),
            "generated_at": point_in_time,
            "review_due_at": review_due_at,
            "lineage": {
                "source_id": _identifier(
                    record.get("source_id"),
                    context="input.source_id",
                ),
                "version": _integer(
                    record.get("source_version"),
                    context="input.source_version",
                    minimum=1,
                ),
                "row_lifecycle": "active",
                "supersedes": supersedes,
                "superseded_by": None,
            },
            "view_schema": "decision_research_dashboard_v0",
            "view": view,
        },
    }
