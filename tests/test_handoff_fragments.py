from __future__ import annotations

import pytest

from loopx.cli_commands.status import review_packet_handoff_only_payload
from loopx.control_plane.handoff.handoff_fragments import (
    FENCE_OPEN_MARKER,
    FENCE_RESUME_MARKER,
    LINE_CONTINUATION_MARKER,
    HandoffShardError,
    build_handoff_shard_manifest,
    extract_handoff_shards,
    parse_handoff_shard,
    reassemble_handoff_shards,
    restore_handoff_text,
    split_handoff_text,
)
from loopx.control_plane.handoff.handoff_fragments import render_handoff_transport


# ---------------------------------------------------------------------------
# Within-budget compatibility
# ---------------------------------------------------------------------------


def test_within_budget_text_returned_verbatim() -> None:
    text = "目标校验：本段只适用于 goal_id=`g`\n上下文规则：保持最小当前指令"
    shards = split_handoff_text(text)

    assert shards == [text]
    assert split_handoff_text(text) == [text]


def test_small_custom_budget_still_identity_when_fit() -> None:
    text = "short\ntext"
    assert split_handoff_text(text, max_lines=16, max_chars=400) == [text]


# ---------------------------------------------------------------------------
# Split and restore
# ---------------------------------------------------------------------------


def _line_overflow_text(lines: int = 60) -> str:
    return "\n".join(f"第 {index} 行：" + "甲乙丙丁" * 12 for index in range(lines))


def test_line_overflow_splits_into_verifiable_ordered_shards() -> None:
    text = _line_overflow_text()
    shards = split_handoff_text(text)

    assert len(shards) >= 2
    parsed = [parse_handoff_shard(shard) for shard in shards]
    assert [shard.index for shard in parsed] == list(range(len(shards)))
    assert all(shard.total == len(shards) for shard in parsed)
    assert len({shard.set_id for shard in parsed}) == 1
    assert len({shard.digest_hex for shard in parsed}) == 1

    prev_hash = None
    for shard in parsed:
        assert shard.prev_hash == prev_hash
        prev_hash = shard.chunk_hash

    assert all(len(shard.split("\n")) <= 16 for shard in shards)
    assert all(len(shard) <= 1800 for shard in shards)
    assert shards[0].split("\n", 1)[1].startswith("第 0 行：")

    assert reassemble_handoff_shards(shards) == text


def test_shard_zero_keeps_prefix_semantics() -> None:
    text = (
        "目标校验：本段只适用于 goal_id=`g`\n"
        "上下文规则：最小指令\n"
        + _line_overflow_text(28)
    )
    shards = split_handoff_text(text)
    assert len(shards) >= 2
    first_payload = shards[0].split("\n", 1)[1]
    assert first_payload.startswith("目标校验：本段只适用于 goal_id=`g`")
    assert reassemble_handoff_shards(shards) == text


def test_lossless_restore_preserves_legacy_dropped_sections() -> None:
    text = "\n".join(
        [
            "目标校验：本段只适用于 goal_id=`g`",
            "Agent 待办：推进当前 bounded segment",
            "Agent 待办候选 2：观察 sibling controller",
            "Agent 待办候选 3：保持 canary 可观测",
            "材料上下文：authority/material: topics=2, materials=4",
            "交付观测：post_handoff_run=impl, scale=implementation",
            "交付合同：下一轮回到 active state P0/P1 outcome",
            "转发条件：只有用户同意 safe local path 后才转发",
            "执行边界：只读或 dry-run",
            "停止条件：需要写入时停下等授权",
            "```bash",
            "loopx status",
            "```",
            *[f"补充说明行 {index}：" + "内容" * 30 for index in range(20)],
        ]
    )
    shards = split_handoff_text(text)
    assert len(shards) >= 2
    restored = reassemble_handoff_shards(shards)
    assert restored == text
    for prefix in (
        "Agent 待办候选 2：",
        "Agent 待办候选 3：",
        "材料上下文：",
        "交付观测：",
        "交付合同：",
    ):
        assert prefix in restored


# ---------------------------------------------------------------------------
# Missing / out-of-order / tamper errors
# ---------------------------------------------------------------------------


def test_missing_shard_raises_named_error() -> None:
    shards = split_handoff_text(_line_overflow_text())
    assert len(shards) >= 3
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards([shards[0], *shards[2:]])
    assert excinfo.value.code == "missing"
    assert "1" in str(excinfo.value)


def test_shuffled_delivery_raises_out_of_order() -> None:
    shards = split_handoff_text(_line_overflow_text())
    reordered = [shards[0], shards[2], shards[1], *shards[3:]]
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards(reordered)
    assert excinfo.value.code == "out_of_order"


def test_payload_tampering_raises_integrity_error() -> None:
    shards = split_handoff_text(_line_overflow_text())
    tampered = shards[1].replace("甲乙", "丙丁", 1)
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards([shards[0], tampered, *shards[2:]])
    assert excinfo.value.code == "integrity"


def test_broken_hash_chain_raises_out_of_order() -> None:
    from loopx.control_plane.handoff import handoff_fragments as hf

    shards = split_handoff_text(_line_overflow_text())
    parsed = [parse_handoff_shard(shard) for shard in shards]
    rogue_payload = parsed[2].payload
    rogue = hf._envelope_line(
        set_id=parsed[2].set_id,
        index=2,
        total=parsed[2].total,
        chunk_hash=parsed[2].chunk_hash,
        prev_hash="0" * 16,
        digest_hex=parsed[2].digest_hex,
    ) + "\n" + rogue_payload
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards([shards[0], shards[1], rogue, *shards[3:]])
    assert excinfo.value.code == "out_of_order"


def test_full_content_digest_mismatch_raises() -> None:
    from loopx.control_plane.handoff import handoff_fragments as hf

    text = _line_overflow_text()
    shards = split_handoff_text(text)
    foreign_digest = hf._sha256_hex("something else")
    forged = []
    for shard_text in shards:
        parsed = parse_handoff_shard(shard_text)
        envelope = hf._envelope_line(
            set_id=foreign_digest[:16],
            index=parsed.index,
            total=parsed.total,
            chunk_hash=parsed.chunk_hash,
            prev_hash=parsed.prev_hash,
            digest_hex=foreign_digest,
        )
        forged.append(envelope + "\n" + parsed.payload)
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards(forged)
    assert excinfo.value.code == "digest"


def test_duplicate_shard_in_batch_raises() -> None:
    shards = split_handoff_text(_line_overflow_text())
    with pytest.raises(HandoffShardError) as excinfo:
        reassemble_handoff_shards([*shards, shards[-1]])
    assert excinfo.value.code == "duplicate"




# ---------------------------------------------------------------------------
# Idempotent regeneration and import
# ---------------------------------------------------------------------------


def test_regeneration_is_byte_stable() -> None:
    text = _line_overflow_text()
    first = split_handoff_text(text)
    second = split_handoff_text(text)
    assert first == second
    assert parse_handoff_shard(first[0]).set_id == parse_handoff_shard(second[0]).set_id






# ---------------------------------------------------------------------------
# Over-long lines and fenced blocks
# ---------------------------------------------------------------------------


def test_long_spaced_line_splits_at_safe_boundaries_and_restores() -> None:
    text = " ".join(f"token{index}" for index in range(500))
    shards = split_handoff_text(text, max_chars=400)
    assert len(shards) >= 2
    restored = reassemble_handoff_shards(shards)
    assert restored == text

    payload_lines = "\n".join(shard.split("\n", 1)[1] for shard in shards)
    assert LINE_CONTINUATION_MARKER in payload_lines
    continuation_chunks = [
        line for line in payload_lines.split("\n") if line.startswith(LINE_CONTINUATION_MARKER)
    ]
    assert continuation_chunks


def test_long_unbreakable_token_hard_cuts_and_restores() -> None:
    text = "a" * 5000
    shards = split_handoff_text(text)
    assert len(shards) >= 3
    assert reassemble_handoff_shards(shards) == text
    assert all(len(shard) <= 1800 for shard in shards)


def test_unicode_long_line_restores_code_points_exactly() -> None:
    text = "中文超长行：" + "甲乙丙丁" * 800
    shards = split_handoff_text(text)
    assert reassemble_handoff_shards(shards) == text


def test_oversized_fence_keeps_each_shard_balanced_and_restores() -> None:
    command = "loopx " + " ".join(f"--opt{index}=value{index}" for index in range(120))
    text = "目标校验：g\n停止条件：停下等授权\n```bash\n" + command + "\n```"
    shards = split_handoff_text(text, max_chars=400)
    assert len(shards) >= 2

    for shard in shards:
        assert shard.count("```") % 2 == 0, shard
        assert len(shard.split("\n")) <= 16
        assert len(shard) <= 400

    payloads = "\n".join(shard.split("\n", 1)[1] for shard in shards)
    assert FENCE_OPEN_MARKER in payloads
    assert FENCE_RESUME_MARKER in payloads

    restored = reassemble_handoff_shards(shards)
    assert restored == text
    assert restored.count("```bash") == 1
    assert FENCE_OPEN_MARKER not in restored
    assert FENCE_RESUME_MARKER not in restored
    assert command in restored


def test_oversized_multiline_fence_restores_exactly() -> None:
    command = " \\\n  ".join(f"--part{index}=value{index}" for index in range(200))
    text = "目标校验：g\n```bash\n" + command + "\n```"
    shards = split_handoff_text(text, max_chars=400)
    assert len(shards) >= 2
    for shard in shards:
        assert shard.count("```") % 2 == 0, shard
    restored = reassemble_handoff_shards(shards)
    assert restored == text
    assert restored.count("```bash") == 1


def test_single_huge_command_line_inside_fence_restores() -> None:
    command = "x" * 4000
    text = "目标校验：g\n```bash\n" + command + "\n```"
    shards = split_handoff_text(text, max_chars=400)
    assert len(shards) >= 2
    for shard in shards:
        assert shard.count("```") % 2 == 0, shard
    restored = reassemble_handoff_shards(shards)
    assert restored == text
    assert f"```bash\n{command}\n```" in restored


def test_fence_starting_near_line_limit_stays_balanced() -> None:
    text = "\n".join(
        [f"plain line {index} " + "x" * 20 for index in range(10)]
        + ["```bash", "loopx status --goal-id g", "```"]
    )
    shards = split_handoff_text(text, max_lines=8, max_chars=1800)
    assert len(shards) >= 2
    for shard in shards:
        assert len(shard.split("\n")) <= 8
        assert shard.count("```") % 2 == 0
    assert reassemble_handoff_shards(shards) == text


@pytest.mark.parametrize(
    ("max_lines", "max_chars"),
    [
        (8, 300),
        (8, 600),
        (8, 1800),
        (12, 300),
        (12, 1800),
        (16, 300),
        (16, 1800),
    ],
)
def test_shard_budget_matrix_round_trips(max_lines: int, max_chars: int) -> None:
    texts = [
        "\n".join(f"line{index} " + "y" * 5 for index in range(40)),
        "word " * 800,
        "z" * 6000,
        "目标：g\n```bash\n"
        + " ".join(f"--k{index}=v{index}" for index in range(200))
        + "\n```",
        "g\n```bash\n"
        + " \\\n  ".join(f"--p{index}=v{index}" for index in range(300))
        + "\n```",
        ("中文 " * 400) + "\n```bash\n" + "a" * 3000 + "\n```\n尾行：停",
    ]
    for text in texts:
        shards = split_handoff_text(
            text, max_lines=max_lines, max_chars=max_chars
        )
        for shard in shards:
            assert len(shard.split("\n")) <= max_lines
            assert len(shard) <= max_chars
            assert shard.count("```") % 2 == 0
        assert restore_handoff_text(shards) == text


# ---------------------------------------------------------------------------
# Legacy single-text compatibility at the receiver
# ---------------------------------------------------------------------------


def test_restore_accepts_unfragmented_plain_text() -> None:
    text = "目标校验：本段只适用于 goal_id=`g`\n停止条件：停下"
    assert restore_handoff_text(text) is text
    assert restore_handoff_text([text]) == text


def test_restore_rejects_envelope_free_multi_part() -> None:
    with pytest.raises(HandoffShardError) as excinfo:
        restore_handoff_text(["plain-a", "plain-b"])
    assert excinfo.value.code == "envelope"


def test_restore_restores_fragment_list_and_embedded_blob() -> None:
    text = _line_overflow_text()
    shards = split_handoff_text(text)
    assert restore_handoff_text(shards) == text
    blob = "\n".join(["【给项目 Agent】", *shards, "回报：done"])
    assert restore_handoff_text(blob) == text


# ---------------------------------------------------------------------------
# Extraction from relay framing
# ---------------------------------------------------------------------------


def test_extract_shards_from_full_packet_framing() -> None:
    text = _line_overflow_text()
    shards = split_handoff_text(text)
    framing = ["【给项目 Agent】", shards[0]]
    for index, shard in enumerate(shards[1:], start=2):
        framing.extend([f"【给项目 Agent · 交接分片 {index}/{len(shards)}】", shard])
    framing.append("回报：changed files / validation / next safe action")
    blob = "\n".join(framing)

    extracted = extract_handoff_shards(blob)
    assert len(extracted) == len(shards)
    assert reassemble_handoff_shards(extracted) == text


def test_extract_without_envelope_raises() -> None:
    with pytest.raises(HandoffShardError) as excinfo:
        extract_handoff_shards("普通文本，没有分片\n目标校验：g")
    assert excinfo.value.code == "envelope"


# ---------------------------------------------------------------------------
# Review Packet integration
# ---------------------------------------------------------------------------


def _giant_command_payload(goal_id: str) -> dict:
    huge_command = "loopx " + " ".join(
        f"--flag-{index}=value-{index}" for index in range(400)
    )
    return {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "operator_gate_approved",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "run the approved handoff now",
                    "agent_command": huge_command,
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "run the approved handoff now",
                        "stop_condition": "stop if the command needs write control",
                        "agent_todos": {"next": "Run the approved dry-run."},
                    },
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {"id": goal_id, "status": "operator_gate_approved", "latest_runs": []}
            ]
        },
    }


def test_review_packet_fragments_oversized_handoff_losslessly() -> None:
    from loopx.review_packet import build_review_packet

    goal_id = "giant-command-handoff"
    payload = build_review_packet(_giant_command_payload(goal_id), goal_id=goal_id)
    assert payload["ok"] is True

    shard0 = payload["project_agent_handoff_fragments"][0]
    fragments = payload["project_agent_handoff_fragments"]
    manifest = payload["handoff_fragment_manifest"]
    all_shards = fragments

    assert len(fragments) >= 1
    assert manifest["schema_version"] == "project_agent_handoff_shard_v1"
    assert manifest["total"] == len(all_shards)
    assert len(manifest["set_id"]) == 16
    assert manifest["original_char_count"] >= len(shard0)

    parsed = [parse_handoff_shard(shard) for shard in all_shards]
    assert all(shard.set_id == manifest["set_id"] for shard in parsed)
    for shard_text, shard in zip(all_shards, parsed):
        assert len(shard_text.split("\n")) <= 16
        assert len(shard_text) <= 1800
        entry = manifest["shards"][shard.index]
        assert entry["line_count"] == len(shard_text.split("\n"))
        assert entry["char_count"] == len(shard_text)

    first_payload = shard0.split("\n", 1)[1]
    assert first_payload.startswith("目标校验：本段只适用于 goal_id=`giant-command-handoff`")
    restored = reassemble_handoff_shards(all_shards)
    assert restored.startswith("目标校验：本段只适用于 goal_id=`giant-command-handoff`")
    assert restored.count("```bash") == 1
    assert "--flag-0=value-0" in restored
    assert "--flag-399=value-399" in restored

    extracted = extract_handoff_shards(payload["packet"])
    assert reassemble_handoff_shards(extracted) == restored
    assert "交接分片 1/" in payload["packet"]

    handoff_only = review_packet_handoff_only_payload(payload)
    assert handoff_only["project_agent_handoff_fragments"] == fragments
    assert handoff_only["handoff_fragment_manifest"] == manifest
    assert handoff_only["handoff_text"] == restored == payload["project_agent_handoff"]

    markdown = render_handoff_transport(
        handoff_only["handoff_text"], handoff_only["project_agent_handoff_fragments"]
    )
    assert reassemble_handoff_shards(extract_handoff_shards(markdown)) == restored

    assert build_handoff_shard_manifest(restored, all_shards)["set_id"] == manifest["set_id"]


def test_review_packet_within_budget_shape_is_unchanged() -> None:
    from loopx.review_packet import build_review_packet

    payload = build_review_packet(
        {
            "attention_queue": {"items": []},
            "run_history": {
                "goals": [{"id": "plain-goal", "status": "active", "latest_runs": []}]
            },
        },
        goal_id="plain-goal",
    )
    handoff = payload["project_agent_handoff"]
    assert handoff.startswith("目标校验：本段只适用于 goal_id=`plain-goal`")
    assert "project_agent_handoff_fragments" not in payload
    assert "handoff_fragment_manifest" not in payload
    assert "<!--loopx-handoff" not in payload["packet"]

    handoff_only = review_packet_handoff_only_payload(payload)
    assert "project_agent_handoff_fragments" not in handoff_only
    rendered = render_handoff_transport(handoff_only["handoff_text"], [])
    assert rendered == handoff_only["handoff_text"] == handoff


def test_handoff_budget_reports_complete_text_overflow() -> None:
    from loopx.review_packet import build_review_packet

    goal_id = "giant-command-handoff-budget"
    payload = build_review_packet(_giant_command_payload(goal_id), goal_id=goal_id)
    budget = payload["handoff_interface_budget"]
    assert budget["mode"] == "project_agent_handoff"
    assert budget["within_budget"] is False
    assert budget["within_line_budget"] is True
    assert budget["within_char_budget"] is False
    assert budget["line_count"] == len(payload["project_agent_handoff"].splitlines())
    assert budget["char_count"] == len(payload["project_agent_handoff"])
