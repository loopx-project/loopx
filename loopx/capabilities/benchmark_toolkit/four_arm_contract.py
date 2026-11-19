"""Provider-neutral four-arm benchmark experiment contract."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION = "benchmark_four_arm_spec_v0"
BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION = "benchmark_four_arm_contract_v0"
BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE = "factor_design_and_prompt_parity_only"
BENCHMARK_FOUR_ARM_ATTESTATIONS = ("attest_domain_hint_independent_of_loopx",)
BENCHMARK_FOUR_ARM_RUNNER_OBLIGATIONS = (
    "keep_loopx_startup_out_of_band",
    "match_runtime_task_goal_hash_to_selected_arm",
    "pin_all_non_factor_inputs",
    "register_each_run_on_experiment_board",
)

_HINT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SPEC_FIELDS = {
    "schema_version",
    "base_goal_text",
    "domain_hint",
    "hint_id",
    "domain_hint_independent_of_loopx",
}


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _prompt_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_hint_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("hint_id must be a string")
    normalized = value.strip()
    if not _HINT_ID_RE.fullmatch(normalized):
        raise ValueError("hint_id must be a compact lowercase token")
    return normalized


def build_benchmark_four_arm_contract(
    *,
    base_goal_text: str,
    domain_hint: str,
    hint_id: str = "domain_hint",
    domain_hint_independent_of_loopx: bool,
) -> dict[str, Any]:
    """Build the canonical Goal/LoopX by plain/domain-hint factorial contract.

    The returned prompt text is runner input.
    Compact receipts omit it by default.
    """

    base = _required_text(base_goal_text, field="base_goal_text")
    hint = _required_text(domain_hint, field="domain_hint")
    normalized_hint_id = _required_hint_id(hint_id)
    if domain_hint_independent_of_loopx is not True:
        raise ValueError("domain hint must be attested independent of LoopX")

    hinted = f"{base}\n\n{hint}"
    prompt_text = {
        "plain": base,
        "domain_hint": hinted,
    }
    prompt_hashes = {
        prompt_class: _prompt_sha256(text) for prompt_class, text in prompt_text.items()
    }
    hinted_goal_id = f"goal_{normalized_hint_id}"
    hinted_loopx_id = f"loopx_{normalized_hint_id}"
    arm_specs = (
        ("goal_plain", "baseline", False, False, None, "plain"),
        (
            "loopx_plain",
            "treatment",
            True,
            False,
            "goal_plain",
            "plain",
        ),
        (
            hinted_goal_id,
            "control",
            False,
            True,
            "goal_plain",
            "domain_hint",
        ),
        (
            hinted_loopx_id,
            "treatment",
            True,
            True,
            hinted_goal_id,
            "domain_hint",
        ),
    )
    arms: list[dict[str, Any]] = []
    for (
        arm_id,
        arm_role,
        loopx_enabled,
        domain_hint_enabled,
        comparison_anchor_arm_id,
        prompt_class,
    ) in arm_specs:
        arm: dict[str, Any] = {
            "arm_id": arm_id,
            "arm_role": arm_role,
            "loopx_enabled": loopx_enabled,
            "domain_hint_enabled": domain_hint_enabled,
            "prompt_class": prompt_class,
            "task_goal_text": prompt_text[prompt_class],
            "task_goal_sha256": prompt_hashes[prompt_class],
        }
        if comparison_anchor_arm_id is not None:
            arm["comparison_anchor_arm_id"] = comparison_anchor_arm_id
        arms.append(arm)

    prompt_parity: dict[str, bool] = {
        "plain_pair_equal": (
            arms[0]["task_goal_sha256"] == arms[1]["task_goal_sha256"]
        ),
        "domain_hint_pair_equal": (
            arms[2]["task_goal_sha256"] == arms[3]["task_goal_sha256"]
        ),
        "plain_and_domain_hint_distinct": (
            prompt_hashes["plain"] != prompt_hashes["domain_hint"]
        ),
    }
    if not all(prompt_parity.values()):
        raise ValueError("four-arm prompt parity failed")

    contract = {
        "schema_version": BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
        "qualified": True,
        "qualification_scope": BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE,
        "execution_qualified": False,
        "prompt_text_recorded": True,
        "hint_id": normalized_hint_id,
        "factors": {
            "loopx": [False, True],
            "domain_hint": [False, True],
        },
        "arms": arms,
        "prompt_parity": prompt_parity,
        "primary_comparisons": [
            {
                "effect": "loopx_without_domain_hint",
                "candidate_arm_id": "loopx_plain",
                "anchor_arm_id": "goal_plain",
            },
            {
                "effect": "domain_hint_without_loopx",
                "candidate_arm_id": hinted_goal_id,
                "anchor_arm_id": "goal_plain",
            },
            {
                "effect": "loopx_with_domain_hint",
                "candidate_arm_id": hinted_loopx_id,
                "anchor_arm_id": hinted_goal_id,
            },
        ],
        "interaction_contrast": {
            "candidate_effect": {
                "candidate_arm_id": hinted_loopx_id,
                "anchor_arm_id": hinted_goal_id,
            },
            "anchor_effect": {
                "candidate_arm_id": "loopx_plain",
                "anchor_arm_id": "goal_plain",
            },
        },
        "attestations": {
            "domain_hint_independent_of_loopx": True,
        },
        "runner_obligations": list(BENCHMARK_FOUR_ARM_RUNNER_OBLIGATIONS),
    }
    return contract


def build_benchmark_four_arm_contract_from_spec(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("four-arm spec must be an object")
    unknown = sorted(set(payload) - _SPEC_FIELDS)
    if unknown:
        raise ValueError(
            "four-arm spec contains unsupported fields: " + ", ".join(unknown)
        )
    if payload.get("schema_version") != BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION:
        raise ValueError("four-arm spec schema mismatch")
    independent = payload.get("domain_hint_independent_of_loopx")
    if not isinstance(independent, bool):
        raise TypeError("domain_hint_independent_of_loopx must be boolean")
    base_goal_text = _required_text(
        payload.get("base_goal_text"), field="base_goal_text"
    )
    domain_hint = _required_text(payload.get("domain_hint"), field="domain_hint")
    return build_benchmark_four_arm_contract(
        base_goal_text=base_goal_text,
        domain_hint=domain_hint,
        hint_id=(payload["hint_id"] if "hint_id" in payload else "domain_hint"),
        domain_hint_independent_of_loopx=independent,
    )


def compact_benchmark_four_arm_contract(
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove prompt text while retaining the exact runner qualification hashes."""

    if contract.get("schema_version") != BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION:
        raise ValueError("four-arm contract schema mismatch")
    compact = dict(contract)
    compact["arms"] = [
        {key: value for key, value in dict(arm).items() if key != "task_goal_text"}
        for arm in contract.get("arms", [])
    ]
    compact["prompt_text_recorded"] = False
    return compact
