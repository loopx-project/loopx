#!/usr/bin/env python3
"""Smoke-check the goal vision/replan contract stays compact and reusable."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "docs" / "reference" / "protocols" / "goal-vision-replan-contract-v0.md"
PROTOCOL_INDEX = ROOT / "docs" / "reference" / "protocols" / "README.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
STATE_MACHINE = ROOT / "docs" / "product" / "core-control-plane" / "state-machine.md"
RULE_SEAM = ROOT / "docs" / "product" / "core-control-plane" / "rule-seam-map.md"
THREE_LAYER = ROOT / "docs" / "reference" / "protocols" / "multi-agent-three-layer-minimality-v0.md"
SELF_REPAIR_SKILL = ROOT / "skills" / "loopx-self-repair" / "SKILL.md"
REPAIR_PATTERNS = ROOT / "skills" / "loopx-self-repair" / "references" / "repair-patterns.md"


PRIVATE_MARKERS = [
    "byte" + "dance",
    "lark" + "office",
    "/" + "Users" + "/",
    "/" + "private" + "/",
    "/" + "tmp" + "/",
    "api" + "_key",
    "pass" + "word",
    "client" + "_secret",
    "app" + "_secret",
    "secret" + "_key",
]


def read(path: Path) -> str:
    assert path.exists(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def require(text: str, snippets: list[str], *, source: Path) -> None:
    compact = " ".join(text.split())
    missing = [
        snippet for snippet in snippets if snippet not in text and " ".join(snippet.split()) not in compact
    ]
    assert not missing, f"{source}: missing {missing}"


def assert_public_safe(text: str, label: str) -> None:
    lower = text.lower()
    leaked = [marker for marker in PRIVATE_MARKERS if marker.lower() in lower]
    assert not leaked, f"{label} leaks private markers: {leaked}"


def main() -> int:
    protocol = read(PROTOCOL)
    protocol_index = read(PROTOCOL_INDEX)
    docs_index = read(DOCS_INDEX)
    state_machine = read(STATE_MACHINE)
    rule_seam = read(RULE_SEAM)
    three_layer = read(THREE_LAYER)
    self_repair_skill = read(SELF_REPAIR_SKILL)
    repair_patterns = read(REPAIR_PATTERNS)

    assert_public_safe(
        "\n".join(
            [
                protocol,
                protocol_index,
                docs_index,
                state_machine,
                rule_seam,
                three_layer,
                self_repair_skill,
                repair_patterns,
            ]
        ),
        "goal vision/replan docs",
    )

    require(
        protocol,
        [
            "goal_vision_replan_contract_v0",
            "CLI Budget",
            "`vision_summary` | 420",
            "`advancement_policy` | 32",
            "`total_agent_vision` | 1800",
            "Reject over-budget writes with `vision_budget_exceeded`",
            "Do not silently truncate fields",
            "Long reasoning belongs in evidence artifacts or design docs",
            "The normal lightweight CLI write boundary is `loopx refresh-state`",
            "--vision-summary",
            "--vision-replan-trigger",
            "--vision-advancement-policy repeat_until_closed",
            "It defaults to `as_needed`",
            "monitor successors and a watch ACK",
            "`--agent-vision-json <packet.json>`",
            "The two forms are mutually exclusive",
            "Inline vision writes require `--agent-id`",
            "`goal_vision_patch` repair delta",
            "goal_path_delta_v0",
            "without adding more inline CLI flags or expanding the heartbeat prompt",
            "`continue`, `replan`, `wait`, `no_change`, `ask_human`, or",
            "`prior_assumption` and `observed_reality` are required",
            "at least one `retained`, `changed`, or `stopped`",
            "shares the 1,800-character `total_agent_vision` budget",
            "compact audit/read model, not a second planner or a new state machine",
            "Every vision packet and checkpoint is scoped by `agent_id`",
            "Vision Checkpoint",
            "`vision_checkpoint_v0`",
            "`missing_required` is not a chat reminder",
            "`--vision-unchanged-reason`",
            "State Machine",
            "ActiveVision --> VisionDriftDetected",
            "ReplanRequired --> ReplanDrafted",
            "VisionPatchProposed --> ActiveVision",
            "The replan decision must not be disturbed by monitor quiet skip",
            "Bad Case: ACK Hidden By Scheduler Accounting",
            "status must project the newest durable replan ACK across neutral",
            "An acknowledgement without an obligation-accepted typed semantic delta is",
            "`replan_noop`",
            "a new concrete blocker",
            "coverage-backed exploration exhaustion or no-follow-up",
            "durable replan ACKs survive neutral scheduler/accounting runs",
            "Vision Continuation Audit",
            "`vision_continuation_audit_v0`",
            "Quota/status expose this as",
            "selected todo is a bounded step",
            "Treat weak, indirect, stale, or protocol-only evidence as incomplete",
            "public web research findings",
            "registry-declared",
            "`topic_authority` and `project_materials`",
            "Registration guides discovery; it neither grants access nor proves acceptance",
            "`vision_gap_judge_v0` instruction packet",
            "the agent is told to compare the active vision",
            "projected required reads",
            "`replan_context` supplies",
            "bounded public web research",
            "`done=true` is only valid",
            "explicit completion with authoritative evidence",
            "a completed todo is only evidence input",
            "owns at least 15 open advancement Todos",
            "Write / Correction Mechanism",
            "Vision correction is a normal state-machine transition",
            "material `refresh-state` closeouts emit a per-agent `vision_checkpoint_v0`",
            "quota/status and `interaction_contract` expose a",
            "`vision_continuation_audit_v0` before todo closeout",
            "ordinary `refresh-state` calls can write bounded vision corrections",
            "missing per-agent checkpoints can become agent-scoped replan gaps",
            "`quota.py` consumes the resulting projection instead of storing vision logic",
        ],
        source=PROTOCOL,
    )
    require(
        state_machine,
        [
            "Agent Vision / Replan Machine",
            "Vision is per `agent_id`",
            "`vision_checkpoint_v0`",
            "required replan is evaluated before",
            "An acknowledgement without a vision, todo, acceptance, or no-follow-up",
            "`vision_checkpoint_missing`",
            "at least 15 open advancement todos",
            "bounded public research when local evidence is insufficient",
            "goal-vision-replan-contract-v0.md",
        ],
        source=STATE_MACHINE,
    )
    require(
        rule_seam,
        [
            "Agent vision and goal routing contract",
            "per-agent vision checkpoints",
            "Over-budget vision fails or compacts before status/quota",
            "material closeouts emit `vision_checkpoint_v0`",
            "rather than expanding `quota.py`",
            "Per-agent vision is a bounded goal-routing contract",
        ],
        source=RULE_SEAM,
    )
    require(
        three_layer,
        [
            "per-agent vision/replan state",
            "per-agent vision budgets",
            "vision/replan state transitions",
            "product-specific fork of per-agent vision/replan mechanics",
        ],
        source=THREE_LAYER,
    )
    require(
        self_repair_skill,
        [
            "Vision / Replan Writeback",
            "goal_vision_replan_contract_v0",
            "replan_trigger_summary",
            "using the same `--agent-id` as the current lane",
            "`--agent-vision-json`",
            "`--vision-unchanged-reason`",
            "`vision_checkpoint_v0`",
            "goal_frontier_projection.acceptance_gaps[]",
            "do not leave the correction only in chat or",
        ],
        source=SELF_REPAIR_SKILL,
    )
    require(
        repair_patterns,
        [
            "vision_replan_writeback_gap",
            "no `goal_frontier_projection.acceptance_gaps[]`",
            "--agent-id <agent-id>",
            "--vision-unchanged-reason",
            "vision_checkpoint_missing",
        ],
        source=REPAIR_PATTERNS,
    )
    require(
        protocol_index,
        ["goal_vision_replan_contract_v0", "goal-vision-replan-contract-v0.md"],
        source=PROTOCOL_INDEX,
    )
    require(
        docs_index,
        ["reference/README.md"],
        source=DOCS_INDEX,
    )

    print("goal-vision-replan-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
