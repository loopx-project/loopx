"""Offline smoke for the public-safe Codex provider routing extension."""

from __future__ import annotations

import copy
import importlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

cli = importlib.import_module("loopx_codex_provider_routing.cli")
contract = importlib.import_module("loopx_codex_provider_routing.contract")
_run_request = cli._run_request
REQUEST_SCHEMA_VERSION = contract.REQUEST_SCHEMA_VERSION
build_upgrade_plan = contract.build_upgrade_plan
compile_catalog = contract.compile_catalog
normalize_selector_request = contract.normalize_selector_request
project_runtime_status = contract.project_runtime_status
qualify_heartbeat_transport = contract.qualify_heartbeat_transport
qualify_host_control_recovery = contract.qualify_host_control_recovery
qualify_snapshot = contract.qualify_snapshot
reconcile_integration_candidate = contract.reconcile_integration_candidate


def _assert_layered_retry_templates() -> None:
    app = (PACKAGE_ROOT / "templates" / "legacy-cpa-config.toml").read_text()
    local_cpa = app.split("[model_providers.local-cpa]", maxsplit=1)[1]
    request_retries = re.search(r"(?m)^request_max_retries\s*=\s*(\d+)\s*$", local_cpa)
    stream_retries = re.search(r"(?m)^stream_max_retries\s*=\s*(\d+)\s*$", local_cpa)
    assert request_retries is not None and int(request_retries.group(1)) == 30
    assert stream_retries is not None and int(stream_retries.group(1)) == 30

    cpa = (PACKAGE_ROOT / "templates" / "cpa-config.public.yaml").read_text()
    interval = re.search(r"(?m)^max-retry-interval:\s*(\d+)\s*$", cpa)
    assert interval is not None and int(interval.group(1)) >= 60
    compat = re.search(
        r"(?ms)^openai-compatibility:\s*\n(?P<body>(?:^[ \t].*(?:\n|$))*)",
        cpa,
    )
    assert compat is not None
    provider_retry = re.search(
        r"(?m)^\s{4}request-retry:\s*(\d+)\s*$", compat.group("body")
    )
    assert provider_retry is not None and int(provider_retry.group(1)) == 1


def _source() -> dict[str, Any]:
    request = cast(
        dict[str, Any],
        json.loads((PACKAGE_ROOT / "examples" / "request.json").read_text()),
    )
    return cast(dict[str, Any], request["source"])


def _valid_snapshot() -> dict[str, Any]:
    return {
        "visible_models": [
            "auto/gpt-5.6-sol",
            "fast/auto/gpt-5.6-sol",
            "codex-a/gpt-5.6-sol",
            "fast/codex-a/gpt-5.6-sol",
            "codex-b/gpt-5.6-sol",
            "fast/codex-b/gpt-5.6-sol",
            "gpt-5.6-luna",
            "ark/deepseek-v4-flash",
        ],
        "hidden_models": ["gpt-5.6-sol"],
        "input_modalities": {
            "auto/gpt-5.6-sol": ["text", "image"],
            "fast/auto/gpt-5.6-sol": ["text", "image"],
            "codex-a/gpt-5.6-sol": ["text", "image"],
            "fast/codex-a/gpt-5.6-sol": ["text", "image"],
            "codex-b/gpt-5.6-sol": ["text", "image"],
            "fast/codex-b/gpt-5.6-sol": ["text", "image"],
            "gpt-5.6-luna": ["text", "image"],
            "ark/deepseek-v4-flash": ["text"],
        },
        "fast_models": [
            "fast/auto/gpt-5.6-sol",
            "fast/codex-a/gpt-5.6-sol",
            "fast/codex-b/gpt-5.6-sol",
        ],
        "default_service_tier": "default",
        "selector_default_service_tiers": {
            "auto/gpt-5.6-sol": "default",
            "fast/auto/gpt-5.6-sol": "fast",
            "codex-a/gpt-5.6-sol": "default",
            "fast/codex-a/gpt-5.6-sol": "fast",
            "codex-b/gpt-5.6-sol": "default",
            "fast/codex-b/gpt-5.6-sol": "fast",
            "gpt-5.6-luna": "default",
            "ark/deepseek-v4-flash": "default",
        },
        "request_normalizer": {
            "active": True,
            "selector_prefix": "fast/",
            "fast_request_service_tier": "priority",
            "ordinary_selector_action": "preserve",
            "effective_priority_admission": "fast_capable_only",
        },
        "endpoint_host": "127.0.0.1",
        "affinity_policy": "hint_revalidated_per_attempt",
        "route_traversal": {
            "auto/gpt-5.6-sol": {
                "entrypoint": "affinity_then_first",
                "ordered_candidates": ["codex-a", "codex-b", "ark-text"],
                "fallback_tail": ["ark-text"],
                "max_cycles": 1,
            },
            "fast/auto/gpt-5.6-sol": {
                "entrypoint": "affinity_then_first",
                "ordered_candidates": ["codex-a", "codex-b"],
                "fallback_tail": [],
                "max_cycles": 1,
            },
            "codex-a/gpt-5.6-sol": {
                "entrypoint": "codex-a",
                "ordered_candidates": ["codex-a", "codex-b", "ark-text"],
                "fallback_tail": ["ark-text"],
                "max_cycles": 1,
            },
            "fast/codex-a/gpt-5.6-sol": {
                "entrypoint": "codex-a",
                "ordered_candidates": ["codex-a", "codex-b"],
                "fallback_tail": [],
                "max_cycles": 1,
            },
            "codex-b/gpt-5.6-sol": {
                "entrypoint": "codex-b",
                "ordered_candidates": ["codex-b", "codex-a", "ark-text"],
                "fallback_tail": ["ark-text"],
                "max_cycles": 1,
            },
            "fast/codex-b/gpt-5.6-sol": {
                "entrypoint": "codex-b",
                "ordered_candidates": ["codex-b", "codex-a"],
                "fallback_tail": [],
                "max_cycles": 1,
            },
            "gpt-5.6-luna": {
                "entrypoint": "affinity_then_first",
                "ordered_candidates": ["codex-a", "codex-b"],
                "fallback_tail": [],
                "max_cycles": 1,
            },
        },
        "settings_revision_durable": True,
        "turn_revision_matches": True,
        "commit_barrier": "before_first_visible_output_or_tool_call",
    }


def _runtime_status() -> dict[str, Any]:
    return {
        "catalog_source": _source(),
        "host_identity": {
            "state": "retained",
            "projection": "not_projected",
            "route_binding": "none",
        },
        "execution_observation": {
            "route_slug": "auto/gpt-5.6-sol",
            "modality": "text",
            "observed_at": "2026-09-01T08:00:00Z",
            "attempted_profiles": ["codex-b"],
            "selected_profile": "codex-b",
            "outcome": "success",
        },
        "account_observations": [
            {
                "profile_id": "codex-a",
                "state": "ready",
                "quota": {
                    "observed_at": "2026-09-01T08:00:00Z",
                    "windows": [
                        {
                            "id": "primary",
                            "used_percent": 25,
                            "window_minutes": 300,
                            "reset_at": "2026-09-01T10:00:00Z",
                        }
                    ],
                },
                "recent_activity": {
                    "success": 3,
                    "failed": 1,
                    "window_minutes": 200,
                },
            },
            {
                "profile_id": "codex-b",
                "state": "ready",
                "quota": None,
                "recent_activity": {
                    "success": 2,
                    "failed": 0,
                    "window_minutes": 200,
                },
            },
        ],
    }


def expect_error(action: Callable[[], Any], message: str) -> None:
    try:
        action()
    except (TypeError, ValueError):
        return
    raise AssertionError(message)


def main() -> int:
    _assert_layered_retry_templates()
    heartbeat = qualify_heartbeat_transport(
        {
            "turn_trigger": "automation_heartbeat",
            "payload_kind": "heartbeat_xml",
            "delivery_kind": "user_input",
            "message_role": "user",
        }
    )
    assert heartbeat["qualified"] is True
    assert heartbeat["prompt_or_model_remediation"] is False

    mislabeled_heartbeat = qualify_heartbeat_transport(
        {
            "turn_trigger": "automation_heartbeat",
            "payload_kind": "heartbeat_xml",
            "delivery_kind": "tool_output",
            "tool_name": "automation_update",
        }
    )
    assert mislabeled_heartbeat["qualified"] is False
    assert mislabeled_heartbeat["failure_code"] == (
        "heartbeat_mislabeled_as_automation_tool_output"
    )
    assert mislabeled_heartbeat["responsible_layer"] == (
        "codex_app_heartbeat_transport"
    )
    expect_error(
        lambda: qualify_heartbeat_transport(
            {
                "turn_trigger": "automation_heartbeat",
                "payload_kind": "heartbeat_xml",
                "delivery_kind": "user_input",
                "message_role": "assistant",
            }
        ),
        "heartbeat user input accepted a non-user role",
    )

    recovered_instruction = qualify_host_control_recovery(
        {
            "tool_name": "send_message_to_thread",
            "call_id_present": False,
            "matching_call_count": 0,
            "semantic_output_present": True,
            "observed_action": "project_as_user",
            "projection": {
                "type": "message",
                "role": "user",
                "tool_identity_removed": True,
                "semantic_output_preserved": True,
                "next_action_observed": True,
                "next_action_matches_instruction": True,
            },
        }
    )
    assert recovered_instruction["qualified"] is True
    # Scheduled inputs and sent host instructions share the same recovery owner.
    heartbeat_observation = json.loads(
        (PACKAGE_ROOT / "examples/host-control-recovery.json").read_text()
    )["host_control_recovery"]
    heartbeat_observation["tool_name"] = "automation_update"
    heartbeat = qualify_host_control_recovery(heartbeat_observation)
    assert heartbeat["qualified"] is True
    assert heartbeat["expected_action"] == "project_as_user"
    assert recovered_instruction["expected_action"] == "project_as_user"
    assert (
        recovered_instruction["required_contract"][
            "qualification_requires_instruction_follow_through"
        ]
        is True
    )

    silently_dropped_instruction = qualify_host_control_recovery(
        {
            "tool_name": "automation_update",
            "call_id_present": False,
            "matching_call_count": 0,
            "semantic_output_present": True,
            "observed_action": "project_as_user",
            "projection": {
                "type": "message",
                "role": "user",
                "tool_identity_removed": True,
                "semantic_output_preserved": False,
                "next_action_observed": True,
                "next_action_matches_instruction": False,
            },
        },
    )
    assert silently_dropped_instruction["qualified"] is False
    assert {
        item["id"]
        for item in silently_dropped_instruction["checks"]
        if not item["passed"]
    } == {"semantic_output_preserved", "instruction_follow_through"}

    for rejected_observation in (
        {
            "tool_name": "unknown_host_tool",
            "call_id_present": False,
            "matching_call_count": 0,
            "semantic_output_present": True,
            "observed_action": "fail_closed",
            "error_status": 409,
        },
        {
            "tool_name": "send_message_to_thread",
            "call_id_present": False,
            "matching_call_count": 1,
            "semantic_output_present": True,
            "observed_action": "fail_closed",
            "error_status": 409,
        },
        {
            "tool_name": "automation_update",
            "call_id_present": True,
            "matching_call_count": 0,
            "semantic_output_present": True,
            "observed_action": "fail_closed",
            "error_status": 409,
        },
        {
            "tool_name": "automation_update",
            "call_id_present": False,
            "matching_call_count": 0,
            "semantic_output_present": False,
            "observed_action": "fail_closed",
            "error_status": 409,
        },
    ):
        rejected = qualify_host_control_recovery(rejected_observation)
        assert rejected["qualified"] is True
        assert rejected["expected_action"] == "fail_closed"

    catalog = compile_catalog(_source())
    assert catalog["credential_free"] is True
    auto = next(
        route for route in catalog["routes"] if route["slug"].startswith("auto/")
    )
    assert auto["eligible_candidates"]["image"] == ["codex-a", "codex-b"]
    assert auto["eligible_candidates"]["text"] == ["codex-a", "codex-b", "ark-text"]
    assert next(
        profile for profile in catalog["profiles"] if profile["id"] == "ark-text"
    )["tool_transports"] == ["function_call"]
    legacy_source = copy.deepcopy(_source())
    for profile in legacy_source["profiles"]:
        profile.pop("tool_transports")
    legacy_catalog = compile_catalog(legacy_source)
    assert next(
        profile for profile in legacy_catalog["profiles"] if profile["id"] == "codex-a"
    )["tool_transports"] == ["function_call"]
    legacy_standard = normalize_selector_request(
        {
            "catalog_source": legacy_source,
            "model_selector": "codex-a/gpt-5.6-sol",
            "required_tool_transport": "function_call",
        }
    )
    assert legacy_standard["eligible_candidates"] == [
        "codex-a",
        "codex-b",
        "ark-text",
    ]
    expect_error(
        lambda: normalize_selector_request(
            {
                "catalog_source": legacy_source,
                "model_selector": "codex-a/gpt-5.6-sol",
                "required_tool_transport": "custom_tool_call",
            }
        ),
        "legacy catalog inferred unproven custom_tool_call support",
    )
    assert auto["fast_candidates"] == ["codex-a", "codex-b"]
    assert auto["routing_policy"]["session_affinity"] == "hint_revalidated_per_attempt"
    assert auto["ring_id"] == "codex-accounts"
    assert auto["entrypoint"] == "affinity_then_first"
    assert auto["routing_policy"]["max_cycles"] == 1
    prefer_a = next(
        route for route in catalog["routes"] if route["slug"].startswith("codex-a/")
    )
    prefer_b = next(
        route for route in catalog["routes"] if route["slug"].startswith("codex-b/")
    )
    assert prefer_a["candidates"] == ["codex-a", "codex-b", "ark-text"]
    assert prefer_b["candidates"] == ["codex-b", "codex-a", "ark-text"]
    assert prefer_a["routing_mode"] == prefer_b["routing_mode"] == "preferred"
    luna = next(route for route in catalog["routes"] if route["slug"] == "gpt-5.6-luna")
    assert luna["candidates"] == ["codex-a", "codex-b"]
    assert luna["eligible_candidates"]["image"] == ["codex-a", "codex-b"]
    assert luna["reasoning_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert luna["fast_candidates"] == ["codex-a", "codex-b"]
    selector_rows = {row["slug"]: row for row in catalog["selector_rows"]}
    assert (
        len([row for row in selector_rows.values() if row["visibility"] == "visible"])
        == 8
    )
    assert selector_rows["auto/gpt-5.6-sol"]["default_service_tier"] == "default"
    assert selector_rows["fast/auto/gpt-5.6-sol"]["default_service_tier"] == "fast"
    assert selector_rows["fast/auto/gpt-5.6-sol"]["candidates"] == [
        "codex-a",
        "codex-b",
    ]
    assert "fast/gpt-5.6-luna" not in selector_rows

    normalized_fast = normalize_selector_request(
        {
            "catalog_source": _source(),
            "model_selector": "fast/codex-b/gpt-5.6-sol",
            "service_tier": "default",
        }
    )
    assert normalized_fast["normalized_model_selector"] == "codex-b/gpt-5.6-sol"
    assert normalized_fast["service_tier"] == {
        "action": "force_priority",
        "value": "priority",
    }
    assert normalized_fast["eligible_candidates"] == ["codex-b", "codex-a"]
    normalized_standard = normalize_selector_request(
        {
            "catalog_source": _source(),
            "model_selector": "codex-b/gpt-5.6-sol",
        }
    )
    assert normalized_standard["normalized_model_selector"] == ("codex-b/gpt-5.6-sol")
    assert normalized_standard["service_tier"] == {"action": "preserve"}
    normalized_code_mode = normalize_selector_request(
        {
            "catalog_source": _source(),
            "model_selector": "codex-b/gpt-5.6-sol",
            "required_tool_transport": "custom_tool_call",
        }
    )
    assert normalized_code_mode["eligible_candidates"] == ["codex-b", "codex-a"]
    assert normalized_code_mode["fallback_policy"] == "required_tool_transport_only"
    custom_preserving_source = copy.deepcopy(_source())
    custom_preserving_source["profiles"][2]["tool_transports"].append(
        "custom_tool_call"
    )
    normalized_custom_adapter = normalize_selector_request(
        {
            "catalog_source": custom_preserving_source,
            "model_selector": "auto/gpt-5.6-sol",
            "required_tool_transport": "custom_tool_call",
        }
    )
    assert normalized_custom_adapter["eligible_candidates"] == [
        "codex-a",
        "codex-b",
        "ark-text",
    ]
    normalized_standard_priority = normalize_selector_request(
        {
            "catalog_source": _source(),
            "model_selector": "auto/gpt-5.6-sol",
            "service_tier": "priority",
        }
    )
    assert normalized_standard_priority["service_tier"] == {
        "action": "preserve",
        "value": "priority",
    }
    assert normalized_standard_priority["fallback_policy"] == "fast_capable_only"
    assert normalized_standard_priority["eligible_candidates"] == [
        "codex-a",
        "codex-b",
    ]

    runtime = project_runtime_status(_runtime_status())
    assert runtime["credential_free"] is True
    assert runtime["host_identity"]["route_binding"] == "none"
    assert runtime["execution"]["selected_profile"] == "codex-b"
    assert runtime["execution"]["fallback_used"] is False
    assert runtime["accounts"][0]["quota"]["windows"][0]["remaining_percent"] == 75

    preferred_fallback = _runtime_status()
    preferred_fallback["execution_observation"].update(
        {
            "route_slug": "codex-b/gpt-5.6-sol",
            "attempted_profiles": ["codex-b", "codex-a"],
            "selected_profile": "codex-a",
        }
    )
    runtime = project_runtime_status(preferred_fallback)
    assert runtime["execution"]["fallback_used"] is True

    code_mode_to_ark = _runtime_status()
    code_mode_to_ark["execution_observation"].update(
        {
            "required_tool_transport": "custom_tool_call",
            "attempted_profiles": ["codex-a", "codex-b", "ark-text"],
            "selected_profile": "ark-text",
        }
    )
    expect_error(
        lambda: project_runtime_status(code_mode_to_ark),
        "custom_tool_call request was allowed to fall back to function-only provider",
    )

    fast_fallback = _runtime_status()
    fast_fallback["execution_observation"].update(
        {
            "route_slug": "fast/codex-b/gpt-5.6-sol",
            "attempted_profiles": ["codex-b", "codex-a"],
            "selected_profile": "codex-a",
        }
    )
    runtime = project_runtime_status(fast_fallback)
    assert runtime["route_intent"]["fast"] is True
    assert runtime["route_intent"]["selector_slug"] == ("fast/codex-b/gpt-5.6-sol")
    assert runtime["route_intent"]["legal_attempt_orders"] == [["codex-b", "codex-a"]]

    fast_to_ark = _runtime_status()
    fast_to_ark["execution_observation"].update(
        {
            "route_slug": "fast/auto/gpt-5.6-sol",
            "attempted_profiles": ["codex-a", "codex-b", "ark-text"],
            "selected_profile": "ark-text",
        }
    )
    expect_error(
        lambda: project_runtime_status(fast_to_ark),
        "Fast selector was allowed to fall back to a non-Fast provider",
    )

    ordinary_priority = _runtime_status()
    ordinary_priority["execution_observation"].update(
        {
            "fast": True,
            "attempted_profiles": ["codex-a", "codex-b"],
            "selected_profile": "codex-b",
        }
    )
    runtime = project_runtime_status(ordinary_priority)
    assert runtime["route_intent"]["fast"] is True
    assert runtime["route_intent"]["legal_attempt_orders"] == [
        ["codex-a", "codex-b"],
        ["codex-b", "codex-a"],
    ]

    ordinary_priority_to_ark = _runtime_status()
    ordinary_priority_to_ark["execution_observation"].update(
        {
            "fast": True,
            "attempted_profiles": ["codex-a", "codex-b", "ark-text"],
            "selected_profile": "ark-text",
        }
    )
    expect_error(
        lambda: project_runtime_status(ordinary_priority_to_ark),
        "ordinary selector with priority was allowed to fall back to Ark",
    )

    fast_selector_reported_standard = _runtime_status()
    fast_selector_reported_standard["execution_observation"].update(
        {
            "route_slug": "fast/auto/gpt-5.6-sol",
            "fast": False,
        }
    )
    expect_error(
        lambda: project_runtime_status(fast_selector_reported_standard),
        "Fast selector was allowed to report a non-Fast execution",
    )

    image_affinity = _runtime_status()
    image_affinity["execution_observation"]["modality"] = "image"
    runtime = project_runtime_status(image_affinity)
    assert runtime["route_intent"]["legal_attempt_orders"] == [
        ["codex-a", "codex-b"],
        ["codex-b", "codex-a"],
    ]

    host_bound_to_route = _runtime_status()
    host_bound_to_route["host_identity"]["route_binding"] = "codex-a"
    expect_error(
        lambda: project_runtime_status(host_bound_to_route),
        "host identity was allowed to bind a route",
    )

    luna_to_ark = _runtime_status()
    luna_to_ark["execution_observation"].update(
        {
            "route_slug": "gpt-5.6-luna",
            "attempted_profiles": ["codex-a", "codex-b", "ark-text"],
            "selected_profile": "ark-text",
        }
    )
    expect_error(
        lambda: project_runtime_status(luna_to_ark),
        "Luna was allowed to select Ark",
    )

    failed_execution = _runtime_status()
    failed_execution["execution_observation"]["outcome"] = "failed"
    failed_execution["execution_observation"].pop("selected_profile")
    runtime = project_runtime_status(failed_execution)
    assert "selected_profile" not in runtime["execution"]

    failed_with_selection = _runtime_status()
    failed_with_selection["execution_observation"].update(
        {"outcome": "failed", "selected_profile": None}
    )
    expect_error(
        lambda: project_runtime_status(failed_with_selection),
        "failed execution declared a selected profile",
    )

    identified_runtime = _runtime_status()
    identified_runtime["account_observations"][0]["email"] = (
        "operator" + "@" + "example.invalid"
    )
    expect_error(
        lambda: project_runtime_status(identified_runtime),
        "account identity leaked through runtime status",
    )

    repeated_ring = copy.deepcopy(_source())
    repeated_ring["rings"][0]["max_cycles"] = 2
    expect_error(
        lambda: compile_catalog(repeated_ring), "multi-cycle fallback ring was accepted"
    )

    overlapping_tail = copy.deepcopy(_source())
    overlapping_tail["routes"][1]["fallback_tail"] = ["codex-b"]
    expect_error(
        lambda: compile_catalog(overlapping_tail),
        "fallback tail overlapping the account ring was accepted",
    )

    leaked = copy.deepcopy(_source())
    leaked["profiles"][0]["access_token"] = "example-sensitive-value"
    expect_error(
        lambda: compile_catalog(leaked), "credential-shaped field was accepted"
    )

    identified = copy.deepcopy(_source())
    identified["profiles"][0]["id"] = "operator" + "@" + "example.invalid"
    expect_error(lambda: compile_catalog(identified), "account identity was accepted")

    ambiguous_boolean = copy.deepcopy(_source())
    ambiguous_boolean["profiles"][0]["supports_fast"] = "false"
    expect_error(
        lambda: compile_catalog(ambiguous_boolean), "string boolean was accepted"
    )

    unsafe_fast_fallback = copy.deepcopy(_source())
    unsafe_fast_fallback["routes"][0]["fast_selector"]["fallback_policy"] = (
        "route_default"
    )
    expect_error(
        lambda: compile_catalog(unsafe_fast_fallback),
        "Fast selector was allowed to retain a non-Fast fallback",
    )

    colliding_fast_slug = copy.deepcopy(_source())
    colliding_fast_slug["routes"].append(
        {
            "candidates": ["ark-text"],
            "display_name": "Collision",
            "input_modalities": ["text"],
            "mode": "manual",
            "reasoning_levels": ["low"],
            "slug": "fast/auto/gpt-5.6-sol",
            "supports_fast": False,
            "visible": False,
        }
    )
    expect_error(
        lambda: compile_catalog(colliding_fast_slug),
        "generated Fast selector collision was accepted",
    )

    no_image_provider = copy.deepcopy(_source())
    no_image_provider["profiles"][0]["input_modalities"] = ["text"]
    no_image_provider["profiles"][1]["input_modalities"] = ["text"]
    expect_error(
        lambda: compile_catalog(no_image_provider),
        "image route without an eligible image provider was accepted",
    )

    divergent_alias = copy.deepcopy(_source())
    divergent_alias["routes"][-1]["supports_fast"] = False
    expect_error(
        lambda: compile_catalog(divergent_alias), "divergent alias was accepted"
    )

    snapshot = qualify_snapshot(_valid_snapshot())
    assert snapshot["qualified"] is True
    ordinary_row_forced_fast = _valid_snapshot()
    ordinary_row_forced_fast["selector_default_service_tiers"]["auto/gpt-5.6-sol"] = (
        "fast"
    )
    failed = qualify_snapshot(ordinary_row_forced_fast)
    assert "fast_default_off" in {
        item["id"] for item in failed["checks"] if not item["passed"]
    }

    inactive_normalizer = _valid_snapshot()
    inactive_normalizer["request_normalizer"]["active"] = False
    failed = qualify_snapshot(inactive_normalizer)
    assert "request_normalizer" in {
        item["id"] for item in failed["checks"] if not item["passed"]
    }

    unsafe_priority_admission = _valid_snapshot()
    unsafe_priority_admission["request_normalizer"]["effective_priority_admission"] = (
        "route_default"
    )
    failed = qualify_snapshot(unsafe_priority_admission)
    assert "request_normalizer" in {
        item["id"] for item in failed["checks"] if not item["passed"]
    }

    stale = _valid_snapshot()
    stale["affinity_policy"] = "sticky_without_revalidation"
    stale["turn_revision_matches"] = False
    failed = qualify_snapshot(stale)
    assert failed["qualified"] is False
    assert {item["id"] for item in failed["checks"] if not item["passed"]} == {
        "modality_aware_affinity",
        "settings_revision",
    }

    wrong_prefer_b = _valid_snapshot()
    wrong_prefer_b["route_traversal"]["codex-b/gpt-5.6-sol"]["ordered_candidates"] = [
        "codex-a",
        "codex-b",
        "ark-text",
    ]
    failed = qualify_snapshot(wrong_prefer_b)
    assert failed["qualified"] is False
    assert "preferred_route_order" in {
        item["id"] for item in failed["checks"] if not item["passed"]
    }

    luna_with_ark = _valid_snapshot()
    luna_with_ark["route_traversal"]["gpt-5.6-luna"]["ordered_candidates"] = [
        "codex-a",
        "codex-b",
        "ark-text",
    ]
    luna_with_ark["route_traversal"]["gpt-5.6-luna"]["fallback_tail"] = ["ark-text"]
    failed = qualify_snapshot(luna_with_ark)
    assert failed["qualified"] is False
    assert {
        "preferred_route_order",
        "terminal_fallback_tail",
    } <= {item["id"] for item in failed["checks"] if not item["passed"]}

    plan = build_upgrade_plan(
        {
            "current_ref": "public-current-ref",
            "target_ref": "public-target-ref",
            "changed_seams": [
                "transport_pool",
                "desktop_runtime_patch",
                "modality_routing",
                "request_normalizer",
                "quota_recovery",
                "settings_revision",
                "tool_transport",
            ],
        }
    )
    assert "h2_reuse" in plan["required_checks"]
    assert "asar_member_integrity" in plan["required_checks"]
    assert "no_eligible_fail_closed" in plan["required_checks"]
    assert "ordinary_selector_preserved" in plan["required_checks"]
    assert "effective_priority_admission" in plan["required_checks"]
    assert "turn_revision_match" in plan["required_checks"]
    assert "stale_cooldown_invalidation" in plan["required_checks"]
    assert "custom_tool_item_preserved" in plan["required_checks"]

    integration_request = json.loads(
        (PACKAGE_ROOT / "examples" / "integration-candidate.json").read_text()
    )
    integration = reconcile_integration_candidate(integration_request["integration"])
    assert integration["status"] == "in_sync"
    assert integration["sync_required"] is False
    assert integration["core_integration_plan"]["source_refs"] == [
        "fork/provider-history-normalization",
        "fork/reusable-http2-transport",
        "operator/modality-routing",
        "fork/route-specific-fallback",
        "operator/compat-stream-repair",
        "fork/openai-compat-bounded-rate-limit-waits",
    ]
    assert integration["deployment_contract"]["session_store_policy"] == (
        "preserve_in_place_never_copy_or_delete"
    )

    moved_source = copy.deepcopy(integration_request["integration"])
    moved_source["observed"]["source_heads"]["transport-pool"] = (
        "9999999999999999999999999999999999999999"
    )
    moved_source["sources"][1]["head_sha"] = "9999999999999999999999999999999999999999"
    integration = reconcile_integration_candidate(moved_source)
    assert integration["sync_required"] is True
    assert integration["drift_reasons"] == [
        {
            "kind": "source_moved",
            "source_id": "transport-pool",
            "last_sync_sha": "3333333333333333333333333333333333333333",
            "observed_sha": "9999999999999999999999999999999999999999",
        }
    ]

    uncovered = copy.deepcopy(integration_request["integration"])
    uncovered["required_seams"].append("retry_policy")
    expect_error(
        lambda: reconcile_integration_candidate(uncovered),
        "integration candidate without a required seam was accepted",
    )

    response = _run_request(
        {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "operation": "qualify_snapshot",
            "snapshot": _valid_snapshot(),
        }
    )
    assert response["ok"] is True and response["result"]["qualified"] is True
    for example_name in (
        "request.json",
        "normalize-request.json",
        "runtime-status.json",
        "qualification-snapshot.json",
        "desktop-patch.json",
        "heartbeat-transport.json",
        "host-control-recovery.json",
        "outage-recovery.json",
        "quota-recovery.json",
        "tool-transport.json",
        "integration-candidate.json",
        "upgrade-request.json",
    ):
        example_request = json.loads(
            (PACKAGE_ROOT / "examples" / example_name).read_text()
        )
        example_response = _run_request(example_request)
        assert example_response["ok"] is True, example_name
    expect_error(
        lambda: _run_request(
            {
                "schema_version": REQUEST_SCHEMA_VERSION,
                "operation": "qualify_snapshot",
                "snapshot": _valid_snapshot(),
                "source": {},
            }
        ),
        "operation accepted an unrelated payload field",
    )
    print("ok: codex provider routing extension smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
