from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from enum import Enum
from importlib.resources import files
from pathlib import PurePosixPath
from typing import Any


class PageRole(str, Enum):
    COVER = "cover"
    ARGUMENT = "argument"
    MECHANISM = "mechanism"
    EVIDENCE = "evidence"
    BOUNDARY = "boundary"
    CLOSING = "closing"
    CTA = "cta"


class PageDensityOrder(str, Enum):
    FIRST_PAGE_MAXIMUM = "first_page_maximum"


_TEMPLATE_RESOURCE = "templates/layout-catalog-v0.json"


def _catalog() -> dict[str, Any]:
    payload = json.loads(
        files("loopx.capabilities.content_ops")
        .joinpath(_TEMPLATE_RESOURCE)
        .read_text(encoding="utf-8")
    )
    if payload.get("schema_version") != "content_ops_layout_template_catalog_v0":
        raise ValueError("layout template catalog has an unsupported schema_version")
    templates = payload.get("templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("layout template catalog must contain templates")
    return payload


def _template(template_id: str) -> dict[str, Any]:
    for template in _catalog()["templates"]:
        if template.get("template_id") == template_id:
            return dict(template)
    raise ValueError(f"unknown content-ops layout template: {template_id}")


def build_layout_template_catalog_packet() -> dict[str, object]:
    catalog = _catalog()
    return {
        "ok": True,
        "schema_version": "content_ops_layout_template_catalog_packet_v0",
        "templates": [
            {
                "template_id": template["template_id"],
                "title": template["title"],
                "intent": template["intent"],
                "canvas": template["canvas"],
                "page_archetypes": template["page_archetypes"],
            }
            for template in catalog["templates"]
        ],
        "template_count": len(catalog["templates"]),
        "external_reads_performed": False,
        "external_writes_performed": False,
    }


def build_layout_template_packet(template_id: str) -> dict[str, object]:
    return {
        "ok": True,
        "schema_version": "content_ops_layout_template_packet_v0",
        "template": _template(template_id),
        "external_reads_performed": False,
        "external_writes_performed": False,
    }


def _normalize_role(value: object) -> str:
    try:
        return PageRole(str(value)).value
    except ValueError as exc:
        allowed = ", ".join(role.value for role in PageRole)
        raise ValueError(f"unsupported page role {value!r}; choose one of: {allowed}") from exc


def _page_sequence(template: Mapping[str, Any]) -> tuple[str, PageDensityOrder, float]:
    sequence = template.get("page_sequence")
    if not isinstance(sequence, Mapping):
        raise ValueError("layout template requires page_sequence defaults")
    first_role = _normalize_role(sequence.get("first_role"))
    try:
        density_order = PageDensityOrder(str(sequence.get("density_order")))
    except ValueError as exc:
        allowed = ", ".join(order.value for order in PageDensityOrder)
        raise ValueError(
            f"unsupported page density order {sequence.get('density_order')!r}; "
            f"choose one of: {allowed}"
        ) from exc
    interior_min = float(sequence.get("interior_min", -1))
    if not 0.0 <= interior_min <= 1.0:
        raise ValueError(
            "layout template page_sequence.interior_min must be between 0 and 1"
        )
    return first_role, density_order, interior_min


def build_layout_plan_packet(
    *,
    item_id: str,
    template_id: str,
    pages: Iterable[Mapping[str, object]],
    required_roles: Iterable[str] = (),
    closing_role: str | None = None,
    generated_at: str,
) -> dict[str, object]:
    template = _template(template_id)
    normalized_pages: list[dict[str, object]] = []
    page_ids: set[str] = set()
    for index, raw_page in enumerate(pages, start=1):
        page_id = str(raw_page.get("page_id") or f"p{index:02d}").strip()
        subject_id = str(raw_page.get("subject_id") or "").strip()
        if not page_id or page_id in page_ids:
            raise ValueError("layout plan page_id values must be non-empty and unique")
        if not subject_id:
            raise ValueError(f"layout plan page {page_id} requires subject_id")
        page_ids.add(page_id)
        role = _normalize_role(raw_page.get("role"))
        normalized_page: dict[str, object] = {
            "page_id": page_id,
            "role": role,
            "subject_id": subject_id,
        }
        normalized_pages.append(normalized_page)
    if not normalized_pages:
        raise ValueError("layout plan requires at least one page")

    roles = [_normalize_role(role) for role in required_roles]
    if not roles:
        roles = list(template["default_required_roles"])
    normalized_closing_role = _normalize_role(
        closing_role or template["default_closing_role"]
    )

    return {
        "ok": True,
        "schema_version": "content_ops_layout_plan_packet_v0",
        "plan": {
            "schema_version": "content_ops_layout_plan_v0",
            "plan_id": f"layout:{item_id}:{template_id}",
            "item_id": item_id,
            "template_id": template_id,
            "generated_at": generated_at,
            "required_roles": roles,
            "closing_role": normalized_closing_role,
            "pages": normalized_pages,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
    }


def _safe_asset_ref(value: object) -> str:
    asset_ref = str(value or "").strip()
    path = PurePosixPath(asset_ref)
    if (
        not asset_ref
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in asset_ref
        or asset_ref.startswith("file:")
    ):
        raise ValueError("asset_ref must be a public-safe relative reference")
    return asset_ref


def _density_limits(template: Mapping[str, Any], role: str) -> tuple[float, float]:
    role_limits = template["density"].get("role_overrides", {}).get(role, {})
    minimum = float(role_limits.get("min", template["density"]["default_min"]))
    maximum = float(role_limits.get("max", template["density"]["default_max"]))
    return minimum, maximum


def _measurement_pages(measurement: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if measurement.get("schema_version") != "content_ops_layout_measurement_v0":
        raise ValueError("measurement must use content_ops_layout_measurement_v0")
    pages = measurement.get("pages")
    if not isinstance(pages, list):
        raise ValueError("measurement pages must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for page in pages:
        if not isinstance(page, Mapping):
            raise ValueError("each measurement page must be an object")
        page_id = str(page.get("page_id") or "").strip()
        if not page_id or page_id in result:
            raise ValueError("measurement page_id values must be non-empty and unique")
        result[page_id] = page
    return result


def check_layout_packet(
    plan_packet_or_plan: Mapping[str, Any],
    measurement: Mapping[str, Any],
) -> dict[str, object]:
    plan = plan_packet_or_plan.get("plan", plan_packet_or_plan)
    if not isinstance(plan, Mapping) or plan.get("schema_version") != "content_ops_layout_plan_v0":
        raise ValueError("plan must use content_ops_layout_plan_v0")
    template_id = str(plan.get("template_id") or "")
    template = _template(template_id)
    first_role, density_order, interior_min = _page_sequence(template)
    measured_by_id = _measurement_pages(measurement)
    planned_pages = plan.get("pages")
    if not isinstance(planned_pages, list) or not planned_pages:
        raise ValueError("plan pages must be a non-empty list")

    failures: list[dict[str, str]] = []
    page_results: list[dict[str, object]] = []
    if measurement.get("plan_id") != plan.get("plan_id"):
        failures.append(
            {
                "code": "plan_id_mismatch",
                "message": "measurement plan_id must match the layout plan",
            }
        )
    if measurement.get("template_id") != template_id:
        failures.append(
            {
                "code": "template_id_mismatch",
                "message": "measurement template_id must match the layout plan",
            }
        )
    expected_ids = [str(page["page_id"]) for page in planned_pages]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("plan page_id values must be unique")
    if set(expected_ids) != set(measured_by_id):
        failures.append(
            {
                "code": "page_set_mismatch",
                "message": "measurement page ids must exactly match the layout plan",
            }
        )

    roles = [_normalize_role(page["role"]) for page in planned_pages]
    if roles[0] != first_role:
        failures.append(
            {
                "code": "first_page_role_mismatch",
                "message": f"first page must use role {first_role}",
            }
        )
    required_roles = [
        _normalize_role(required_role) for required_role in plan.get("required_roles", [])
    ]
    for required_role in required_roles:
        if required_role not in roles:
            failures.append(
                {
                    "code": "required_role_missing",
                    "message": f"required page role is missing: {required_role}",
                }
            )
    closing_role = _normalize_role(plan.get("closing_role"))
    if roles[-1] != closing_role:
        failures.append(
            {
                "code": "closing_role_mismatch",
                "message": f"last page must use closing role {closing_role}",
            }
        )

    target_canvas = template["canvas"]
    safe_area = template["safe_area"]
    for page_index, planned_page in enumerate(planned_pages):
        page_id = str(planned_page["page_id"])
        role = str(planned_page["role"])
        measured = measured_by_id.get(page_id)
        page_failures: list[dict[str, str]] = []
        density: float | None = None
        asset_ref: str | None = None
        if measured is None:
            page_failures.append(
                {"code": "measurement_missing", "message": "page measurement is missing"}
            )
        else:
            asset_ref = _safe_asset_ref(measured.get("asset_ref"))
            canvas = measured.get("canvas")
            bounds = measured.get("meaningful_content_bounds")
            checks = measured.get("checks")
            if not isinstance(canvas, Mapping) or not isinstance(bounds, Mapping):
                raise ValueError(f"measurement page {page_id} requires canvas and bounds")
            if not isinstance(checks, Mapping):
                raise ValueError(f"measurement page {page_id} requires checks")
            width = int(canvas.get("width", 0))
            height = int(canvas.get("height", 0))
            if width != target_canvas["width"] or height != target_canvas["height"]:
                page_failures.append(
                    {
                        "code": "canvas_mismatch",
                        "message": (
                            f"canvas must be {target_canvas['width']}x"
                            f"{target_canvas['height']}"
                        ),
                    }
                )
            top = int(bounds.get("top", -1))
            bottom = int(bounds.get("bottom", -1))
            if top < 0 or bottom <= top or bottom > height:
                page_failures.append(
                    {"code": "invalid_content_bounds", "message": "content bounds are invalid"}
                )
            elif height > 0:
                usable_height = height * (
                    1.0 - float(safe_area["top_ratio"]) - float(safe_area["bottom_ratio"])
                )
                density = round((bottom - top) / usable_height, 3)
                minimum, maximum = _density_limits(template, role)
                if 0 < page_index < len(planned_pages) - 1:
                    minimum = max(minimum, interior_min)
                if density < minimum:
                    page_failures.append(
                        {
                            "code": "content_too_sparse",
                            "message": f"density {density:.3f} is below {minimum:.3f}; tighten the page",
                        }
                    )
                if density > maximum:
                    page_failures.append(
                        {
                            "code": "content_too_dense",
                            "message": f"density {density:.3f} exceeds {maximum:.3f}; split or reduce content",
                        }
                    )
            for check_name, code in (
                ("overflow", "content_overflow"),
                ("collision", "content_collision"),
                ("single_character_line", "single_character_line"),
            ):
                if checks.get(check_name) is not False:
                    page_failures.append(
                        {"code": code, "message": f"{check_name} must be explicitly false"}
                    )
        page_results.append(
            {
                "page_id": page_id,
                "asset_ref": asset_ref,
                "role": role,
                "density": density,
                "status": "pass" if not page_failures else "revise",
                "failures": page_failures,
            }
        )
        failures.extend(
            {"code": failure["code"], "message": f"{page_id}: {failure['message']}"}
            for failure in page_failures
        )

    if density_order is PageDensityOrder.FIRST_PAGE_MAXIMUM:
        first_result = page_results[0]
        first_density_value = first_result["density"]
        first_density = (
            float(first_density_value)
            if isinstance(first_density_value, (int, float))
            else None
        )
        denser_later_pages: list[tuple[dict[str, object], float]] = []
        if first_density is not None:
            for result in page_results[1:]:
                later_density = result["density"]
                if (
                    isinstance(later_density, (int, float))
                    and later_density > first_density
                ):
                    denser_later_pages.append((result, float(later_density)))
        if denser_later_pages:
            densest, densest_density = max(
                denser_later_pages,
                key=lambda result_with_density: result_with_density[1],
            )
            failure = {
                "code": "first_page_not_density_maximum",
                "message": (
                    f"first-page density {first_density:.3f} must be at least every "
                    f"later page; {densest['page_id']} is {densest_density:.3f}"
                ),
            }
            first_failures = first_result["failures"]
            if not isinstance(first_failures, list):
                raise TypeError("layout page result failures must be a list")
            first_failures.append(failure)
            first_result["status"] = "revise"
            failures.append(
                {
                    "code": failure["code"],
                    "message": f"{first_result['page_id']}: {failure['message']}",
                }
            )

    return {
        "ok": not failures,
        "schema_version": "content_ops_layout_check_packet_v0",
        "status": "pass" if not failures else "revise",
        "plan_id": plan.get("plan_id"),
        "template_id": template_id,
        "page_results": page_results,
        "failures": failures,
        "next_action": (
            "layout_acceptance_complete"
            if not failures
            else "revise_failed_pages_and_rerun_layout_check"
        ),
        "external_reads_performed": False,
        "external_writes_performed": False,
        "autopublish_allowed": False,
    }


def render_layout_packet_markdown(packet: Mapping[str, object]) -> str:
    if not packet.get("ok") and "error" in packet:
        return f"# Content-Ops Layout\n\nError: {packet.get('error', 'unknown error')}\n"
    if packet.get("schema_version") == "content_ops_layout_template_catalog_packet_v0":
        lines = ["# Content-Ops Layout Templates", ""]
        templates = packet.get("templates")
        if isinstance(templates, list):
            for template in templates:
                if isinstance(template, Mapping):
                    lines.append(f"- `{template.get('template_id')}` — {template.get('title')}")
        return "\n".join(lines) + "\n"
    if packet.get("schema_version") == "content_ops_layout_template_packet_v0":
        template = packet.get("template")
        if isinstance(template, Mapping):
            return (
                "# Content-Ops Layout Template\n\n"
                f"- id: `{template.get('template_id')}`\n"
                f"- title: {template.get('title')}\n"
                f"- intent: {template.get('intent')}\n"
            )
    if packet.get("schema_version") == "content_ops_layout_plan_packet_v0":
        plan = packet.get("plan")
        if isinstance(plan, Mapping):
            pages = plan.get("pages")
            page_count = len(pages) if isinstance(pages, list) else 0
            return (
                "# Content-Ops Layout Plan\n\n"
                f"- plan: `{plan.get('plan_id')}`\n"
                f"- template: `{plan.get('template_id')}`\n"
                f"- pages: {page_count}\n"
                f"- closing role: `{plan.get('closing_role')}`\n"
            )
    lines = [
        "# Content-Ops Layout Check",
        "",
        f"- status: `{packet.get('status')}`",
        f"- next action: `{packet.get('next_action')}`",
    ]
    failures = packet.get("failures")
    if isinstance(failures, list):
        for failure in failures:
            if isinstance(failure, Mapping):
                lines.append(f"- `{failure.get('code')}`: {failure.get('message')}")
    return "\n".join(lines) + "\n"
