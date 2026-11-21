from __future__ import annotations

import hashlib
import json
import shutil
from importlib.resources import files
from typing import Any


CONTENT_OPS_SOCIAL_BROWSER_X_PROVIDER_SCHEMA_VERSION = (
    "content_ops_social_browser_x_provider_v0"
)
SOCIAL_BROWSER_X_CONNECTOR_ID = "social_browser_x"
SOCIAL_BROWSER_X_PROVIDER_MODULE = "loopx.capabilities.content_ops.social_browser_x"


def build_social_browser_x_provider_packet() -> dict[str, Any]:
    # Public packaged guidance.
    seed_bytes = (
        files("loopx.capabilities.content_ops")
        .joinpath("experiences/x-composer-preflight-v1.json")
        .read_bytes()
    )
    experience = json.loads(seed_bytes)
    experience["content_digest"] = "sha256:" + hashlib.sha256(seed_bytes).hexdigest()
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_SOCIAL_BROWSER_X_PROVIDER_SCHEMA_VERSION,
        "connector_id": SOCIAL_BROWSER_X_CONNECTOR_ID,
        "outcome_capability_id": "content-ops",
        "provider_module": SOCIAL_BROWSER_X_PROVIDER_MODULE,
        "source_profile": {
            "operating_experience": experience,
            "connector_id": SOCIAL_BROWSER_X_CONNECTOR_ID,
            "status": "profile_ready_when_browser_available",
            "route_type": "browser-backed public/social channel",
            "boundary": "logged_in_read",
            "safe_uses": [
                "read public or account-visible X metadata through an owner-approved browser profile",
                "prepare source-mapped drafts for LoopX posts or replies",
                "monitor published posts after an audited publish record exists",
            ],
            "commands": [
                "loopx content-ops observe-public-handle --url https://x.com/<handle> --source-item-id <stable-source-id> --no-fetch --format json",
                "loopx value-connectors plan --connector-id social_browser_x --connector-kind browser_social_channel --channel 'X public post via browser' --stage external_write_request --target-ref '<exact post>' --target-url https://x.com/<handle> --external-write-requested --money-metric '<demand metric>' --success-metric '<success metric>' --kill-condition '<stop condition>' --format json",
            ],
            "evidence_schema": (
                "x_draft_packet_v0 or content_ops_public_handle_observation_packet_v0"
            ),
            "maturity_hint": (
                "Use repeated public signals and concrete workflow-owner replies; "
                "avoid platform drama as proof."
            ),
            "write_gate": (
                "exact account, final body, media/link plan, timing, source refs, "
                "and stop condition required"
            ),
            "stop_conditions": [
                "active account identity is unclear",
                "source requires private DMs, private lists, or hidden timeline expansion",
                "post/reply/media upload would execute without an audit record",
            ],
        },
        "install_check": {
            "operating_experience": experience,
            "connector_id": SOCIAL_BROWSER_X_CONNECTOR_ID,
            "status": "ready" if shutil.which("ego-browser") else "needs_ego_browser",
            "install": [
                "Install ego lite / ego-browser and log in to X in the user-owned browser profile.",
                "Use LoopX to plan X account setup, research, draft, publish, and reply-monitor calls before browser execution.",
                "loopx content-ops observe-public-handle --url https://x.com/loopxops --source-item-id source_x_loopx_public_handle --no-fetch --format json",
                "loopx value-connectors plan --connector-id social_browser_x --connector-kind browser_social_channel --channel 'X public post via ego-browser' --stage external_write_request --target-ref 'one approved LoopX post' --target-url https://x.com/loopxops --external-write-requested --money-metric 'qualified workflow owner asks for LoopX setup help' --success-metric 'one audit, demo, or setup request' --kill-condition 'spam hiding, account-health degradation, or no workflow owner signal' --format json",
            ],
            "optional_tools": [
                {
                    "tool": "ego-browser",
                    "installed": shutil.which("ego-browser") is not None,
                    "needed_for": (
                        "logged-in browser research, profile maintenance, uploads, "
                        "approved posts, and reply monitoring"
                    ),
                    "install_hint": (
                        "Install ego lite, then confirm `ego-browser nodejs` can "
                        "open the target site."
                    ),
                }
            ],
            "external_write_capability": True,
            "write_gate": (
                "exact account identity, body, image, link, mentions, and stop "
                "condition required before any X write"
            ),
        },
        "connector_trial": {
            "schema_version": "connector_trial_v0",
            "trial_id": "trial_x_ego_lite_browser",
            "surface": "x_public_feed",
            "tool_hint": "ego-lite browser",
            "access_mode": "public_metadata_only",
            "source_status": "public",
            "freshness": "unknown",
            "allowed_use": "metadata_only",
            "trial_state": "ready_for_metadata_trial",
            "proposed_source_item_id": "source_x_public_signal_001",
            "terms_note": (
                "public/terms-aware signal intake; no login, posting, or raw "
                "timeline capture in LoopX state"
            ),
            "promotion_target": "source_item_v0",
            "requires_user_gate": False,
            "external_write_allowed": False,
        },
        "truth_contract": {
            "compatibility_facade_may_delegate": True,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "autopublish_allowed": False,
            "raw_platform_data_recorded": False,
        },
    }
