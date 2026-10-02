from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .core import _reject_raw_keys
from ...control_plane.digest_envelope import sha256_envelope

AUDIENCE_POLICY_SCHEMA = "periodic_report_audience_policy_v0"
AUDIENCE_RECIPIENT_SCHEMA = "periodic_report_audience_recipient_v0"
AUDIENCE_ROUTING_RULE_SCHEMA = "periodic_report_audience_routing_rule_v0"
ANNOUNCEMENT_PLAN_SCHEMA = "periodic_report_announcement_plan_v0"

_DOCUMENT_SCHEMA = "periodic_report_document_v0"
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_VISIBILITIES = {"primary", "supporting"}
_RULE_SELECTORS = {
    "source_ids",
    "section_ids",
    "content_kinds",
    "tags_any",
    "domains_any",
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 128) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _token(value: object, label: str) -> str:
    token = _text(value, label).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _reject_unknown_fields(
    value: Mapping[str, Any], *, allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _token_set(value: object, label: str, *, maximum: int = 64) -> list[str]:
    raw = _sequence(value, label)
    if len(raw) > maximum:
        raise ValueError(f"{label} must contain at most {maximum} items")
    return sorted({_token(item, f"{label}[]") for item in raw})


def _normalize_routing_rule(raw: object, *, label: str) -> dict[str, Any]:
    rule = _mapping(raw, label)
    _reject_raw_keys(rule, label)
    _reject_unknown_fields(
        rule,
        allowed={"schema_version", "rule_id", *_RULE_SELECTORS},
        label=label,
    )
    if rule.get("schema_version", AUDIENCE_ROUTING_RULE_SCHEMA) != (
        AUDIENCE_ROUTING_RULE_SCHEMA
    ):
        raise ValueError(f"{label} must use {AUDIENCE_ROUTING_RULE_SCHEMA}")
    normalized: dict[str, Any] = {
        "schema_version": AUDIENCE_ROUTING_RULE_SCHEMA,
        "rule_id": _token(rule.get("rule_id"), f"{label}.rule_id"),
    }
    for field in sorted(_RULE_SELECTORS):
        values = _token_set(rule.get(field, []), f"{label}.{field}")
        if values:
            normalized[field] = values
    if not any(field in normalized for field in _RULE_SELECTORS):
        raise ValueError(f"{label} requires at least one typed selector")
    return normalized


def normalize_periodic_report_audience_policy(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize profile-owned recipient relevance without provider identities."""

    policy = _mapping(raw, "audience_policy")
    _reject_raw_keys(policy, "audience_policy")
    _reject_unknown_fields(
        policy,
        allowed={"schema_version", "eligible_visibilities", "recipients"},
        label="audience_policy",
    )
    if policy.get("schema_version") != AUDIENCE_POLICY_SCHEMA:
        raise ValueError(f"audience_policy must use {AUDIENCE_POLICY_SCHEMA}")
    visibilities = _token_set(
        policy.get("eligible_visibilities", ["primary"]),
        "audience_policy.eligible_visibilities",
        maximum=2,
    )
    if not visibilities or any(value not in _VISIBILITIES for value in visibilities):
        raise ValueError(
            "audience_policy.eligible_visibilities must contain primary or supporting"
        )

    raw_recipients = _sequence(policy.get("recipients"), "audience_policy.recipients")
    if not raw_recipients:
        raise ValueError("audience_policy.recipients must not be empty")
    if len(raw_recipients) > 64:
        raise ValueError("audience_policy.recipients must contain at most 64 items")
    recipients: list[dict[str, Any]] = []
    seen_recipients: set[str] = set()
    for index, raw_recipient in enumerate(raw_recipients):
        label = f"audience_policy.recipients[{index}]"
        recipient = _mapping(raw_recipient, label)
        _reject_raw_keys(recipient, label)
        _reject_unknown_fields(
            recipient,
            allowed={
                "schema_version",
                "recipient_id",
                "owned_domains",
                "routing_rules",
            },
            label=label,
        )
        if recipient.get("schema_version", AUDIENCE_RECIPIENT_SCHEMA) != (
            AUDIENCE_RECIPIENT_SCHEMA
        ):
            raise ValueError(f"{label} must use {AUDIENCE_RECIPIENT_SCHEMA}")
        recipient_id = _token(recipient.get("recipient_id"), f"{label}.recipient_id")
        if recipient_id in seen_recipients:
            raise ValueError(f"duplicate audience recipient_id {recipient_id!r}")
        seen_recipients.add(recipient_id)
        owned_domains = _token_set(
            recipient.get("owned_domains", []), f"{label}.owned_domains"
        )
        raw_rules = _sequence(
            recipient.get("routing_rules", []), f"{label}.routing_rules"
        )
        if len(raw_rules) > 64:
            raise ValueError(f"{label}.routing_rules must contain at most 64 items")
        rules = [
            _normalize_routing_rule(value, label=f"{label}.routing_rules[{rule_index}]")
            for rule_index, value in enumerate(raw_rules)
        ]
        rule_ids = [str(rule["rule_id"]) for rule in rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError(f"{label}.routing_rules contains duplicate rule_id")
        if not owned_domains and not rules:
            raise ValueError(
                f"{label} requires at least one owned_domain or routing_rule"
            )
        recipients.append(
            {
                "schema_version": AUDIENCE_RECIPIENT_SCHEMA,
                "recipient_id": recipient_id,
                "owned_domains": owned_domains,
                "routing_rules": sorted(rules, key=lambda item: str(item["rule_id"])),
            }
        )
    return {
        "schema_version": AUDIENCE_POLICY_SCHEMA,
        "eligible_visibilities": visibilities,
        "recipients": sorted(recipients, key=lambda item: str(item["recipient_id"])),
    }


def _normalized_document_items(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != _DOCUMENT_SCHEMA:
        raise ValueError(f"document must use {_DOCUMENT_SCHEMA}")
    items: list[dict[str, Any]] = []
    for section_index, raw_section in enumerate(
        _sequence(document.get("sections", []), "document.sections")
    ):
        section_label = f"document.sections[{section_index}]"
        section = _mapping(raw_section, section_label)
        section_id = _token(section.get("section_id"), f"{section_label}.section_id")
        for item_index, raw_item in enumerate(
            _sequence(section.get("items", []), f"{section_label}.items")
        ):
            item_label = f"{section_label}.items[{item_index}]"
            item = _mapping(raw_item, item_label)
            visibility = _token(
                item.get("visibility", "primary"), f"{item_label}.visibility"
            )
            if visibility not in _VISIBILITIES:
                raise ValueError(f"{item_label}.visibility is invalid")
            items.append(
                {
                    "source_id": _token(
                        item.get("source_id"), f"{item_label}.source_id"
                    ),
                    "section_id": section_id,
                    "item_id": _token(item.get("item_id"), f"{item_label}.item_id"),
                    "content_kind": (
                        _token(item.get("content_kind"), f"{item_label}.content_kind")
                        if item.get("content_kind")
                        else None
                    ),
                    "visibility": visibility,
                    "tags": _token_set(item.get("tags", []), f"{item_label}.tags"),
                    "domains": _token_set(
                        item.get("domains", []), f"{item_label}.domains"
                    ),
                }
            )
    return items


def _rule_matches(rule: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    scalar_selectors = {
        "source_ids": str(item["source_id"]),
        "section_ids": str(item["section_id"]),
        "content_kinds": str(item.get("content_kind") or ""),
    }
    for field, item_value in scalar_selectors.items():
        if field in rule and item_value not in rule[field]:
            return False
    for field, item_field in (("tags_any", "tags"), ("domains_any", "domains")):
        if field in rule and not set(rule[field]).intersection(item[item_field]):
            return False
    return True


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_envelope(encoded)


def build_periodic_report_announcement_plan(
    *,
    document: Mapping[str, Any],
    audience_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Select recipient mentions from typed item routing facts only."""

    normalized_document = _mapping(document, "document")
    _reject_raw_keys(normalized_document, "document")
    policy = normalize_periodic_report_audience_policy(audience_policy)
    items = _normalized_document_items(normalized_document)
    eligible_visibilities = set(policy["eligible_visibilities"])
    eligible_items = [
        item for item in items if item["visibility"] in eligible_visibilities
    ]

    recipient_results: list[dict[str, Any]] = []
    mentioned_recipient_ids: list[str] = []
    matched_item_keys: set[tuple[str, str, str]] = set()
    for recipient in policy["recipients"]:
        refs: list[dict[str, Any]] = []
        owned_domains = set(recipient["owned_domains"])
        for item in eligible_items:
            reasons: list[dict[str, str]] = []
            for domain in sorted(owned_domains.intersection(item["domains"])):
                reasons.append({"kind": "owned_domain", "domain": domain})
            for rule in recipient["routing_rules"]:
                if _rule_matches(rule, item):
                    reasons.append(
                        {"kind": "routing_rule", "rule_id": str(rule["rule_id"])}
                    )
            if not reasons:
                continue
            ref = {
                "source_id": item["source_id"],
                "section_id": item["section_id"],
                "item_id": item["item_id"],
                "content_kind": item.get("content_kind"),
                "visibility": item["visibility"],
                "reasons": reasons,
            }
            refs.append(ref)
            matched_item_keys.add(
                (str(item["source_id"]), str(item["section_id"]), str(item["item_id"]))
            )
        mention = bool(refs)
        if mention:
            mentioned_recipient_ids.append(str(recipient["recipient_id"]))
        recipient_results.append(
            {
                "recipient_id": recipient["recipient_id"],
                "mention": mention,
                "matching_item_count": len(refs),
                "matching_item_refs": refs,
            }
        )

    return {
        "ok": True,
        "schema_version": ANNOUNCEMENT_PLAN_SCHEMA,
        "status": "mentions_selected"
        if mentioned_recipient_ids
        else "no_relevant_recipient",
        "policy_digest": _digest(policy),
        "document_digest": _digest(normalized_document),
        "mentioned_recipient_ids": mentioned_recipient_ids,
        "mentioned_recipient_count": len(mentioned_recipient_ids),
        "matched_item_count": len(matched_item_keys),
        "recipients": recipient_results,
        "boundary": {
            "default_action": "omit_mention",
            "normalized_items_only": True,
            "owned_domain_or_typed_rule_required": True,
            "title_or_summary_text_inference": False,
            "provider_identities_resolved": False,
            "external_writes_performed": False,
        },
    }


__all__ = [
    "ANNOUNCEMENT_PLAN_SCHEMA",
    "AUDIENCE_POLICY_SCHEMA",
    "AUDIENCE_RECIPIENT_SCHEMA",
    "AUDIENCE_ROUTING_RULE_SCHEMA",
    "build_periodic_report_announcement_plan",
    "normalize_periodic_report_audience_policy",
]
