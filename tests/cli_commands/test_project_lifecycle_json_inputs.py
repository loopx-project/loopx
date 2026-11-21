from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.cli_commands import (
    project_lifecycle,
    project_lifecycle_inputs,
    project_lifecycle_refresh_state,
)
from loopx.cli_runtime import add_subcommand_format, build_cli_parser


def _parse_refresh_args(*extra: str):
    parser, subparsers = build_cli_parser()
    project_lifecycle_refresh_state.register_refresh_state_command(
        subparsers, add_subcommand_format
    )
    return parser.parse_args(
        [
            "refresh-state",
            "--goal-id",
            "goal-public-fixture",
            "--agent-id",
            "agent-public-fixture",
            "--format",
            "json",
            *extra,
        ]
    )


def _valid_vision_packet(note: str = "Keep the boundary concise.") -> dict[str, object]:
    return {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "acceptance_summary": note,
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0",
            "outcome": "continue",
            "prior_assumption": "Prior assumption holds.",
            "observed_reality": "Observed reality supports continuation.",
            "retained": ["Retained deliverable"],
            "evidence_refs": ["evidence:unit-1"],
        },
    }


def _valid_outcome_update() -> dict[str, object]:
    return {
        "operation_id": "op-inline-1",
        "expected_provider_revision": "rev-1",
        "refinement": {
            "source": {"ref": "message:inline-1", "summary": "Refine acceptance"},
            "requirements": [
                {
                    "id": "req-1",
                    "description": "Inline requirement " + "r" * 80,
                }
            ],
        },
    }


def _valid_explore_attachment() -> dict[str, object]:
    return {
        "schema_version": "explore_result_attachment_v0",
        "node_id": "node-inline-1",
        "question": "Does inline JSON parse without Errno 63?",
        "applicability": "Applies to CLI JSON flags.",
        "input_revision": "fixture-v1",
        "observation": "Inline JSON exceeds NAME_MAX cleanly.",
        "interpretation": "Use shared inline-or-file loader.",
        "status": "confirmed",
        "evidence_refs": ["evidence:unit-1"],
    }


def test_load_json_object_or_file_supports_inline_at_path_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    payload = {"ok": True, "padding": "x" * 400}
    inline = json.dumps(payload)
    assert len(inline) > 300
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            inline, flag="--agent-vision-json"
        )
        == payload
    )
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            f"  \n{inline}", flag="--agent-vision-json"
        )
        == payload
    )

    file_path = tmp_path / "payload.json"
    file_path.write_text(inline, encoding="utf-8")
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            file_path, flag="--agent-vision-json"
        )
        == payload
    )
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            str(file_path), flag="--agent-vision-json"
        )
        == payload
    )
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            f"@{file_path}", flag="--agent-vision-json"
        )
        == payload
    )
    assert (
        project_lifecycle_inputs.load_json_object_or_file(
            "@~/payload.json", flag="--agent-vision-json"
        )
        == payload
    )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_load_json_object_or_file_rejects_non_standard_constants(
    tmp_path: Path, constant: str,
) -> None:
    inline = f'{{"value": {constant}}}'
    with pytest.raises(ValueError, match="strict JSON"):
        project_lifecycle_inputs.load_json_object_or_file(
            inline, flag="--agent-vision-json"
        )

    file_path = tmp_path / "bad.json"
    file_path.write_text(inline, encoding="utf-8")
    with pytest.raises(ValueError, match="strict JSON"):
        project_lifecycle_inputs.load_json_object_or_file(
            f"@{file_path}", flag="--agent-vision-json"
        )


def test_load_json_object_or_file_rejects_oversized_non_object_and_invalid_paths(
    tmp_path: Path,
) -> None:
    oversized_inline = json.dumps({"text": "x" * 1024})
    with pytest.raises(ValueError, match="exceeds 1 KiB"):
        project_lifecycle_inputs.load_json_object_or_file(
            oversized_inline, flag="--agent-vision-json", max_bytes=1024
        )

    oversized_file = tmp_path / "oversized.json"
    oversized_file.write_text(oversized_inline, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds 1 KiB"):
        project_lifecycle_inputs.load_json_object_or_file(
            oversized_file, flag="--goal-outcome-update-json", max_bytes=1024
        )

    with pytest.raises(ValueError, match="must be a JSON object"):
        project_lifecycle_inputs.load_json_object_or_file(
            "[1, 2, 3]", flag="--explore-result-json"
        )

    list_file = tmp_path / "array.json"
    list_file.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        project_lifecycle_inputs.load_json_object_or_file(
            list_file, flag="--explore-result-json"
        )

    with pytest.raises(ValueError, match="valid JSON"):
        project_lifecycle_inputs.load_json_object_or_file(
            "{malformed", flag="--agent-vision-json"
        )

    for bad_path in (
        "",
        "@",
        str(tmp_path),
        str(tmp_path / "missing.json"),
        "x" * 5000,
        "/tmp/\x00bad",
    ):
        with pytest.raises(ValueError, match="JSON object or valid file path"):
            project_lifecycle_inputs.load_json_object_or_file(
                bad_path, flag="--agent-vision-json"
            )


@pytest.mark.parametrize("mode", ["inline", "at_path", "path"])
def test_refresh_state_accepts_long_inline_or_file_json_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    vision = _valid_vision_packet("Verify long inline JSON input: " + "v" * 280)
    outcome_update = _valid_outcome_update()
    explore = _valid_explore_attachment()

    def encode_arg(name: str, payload: dict[str, object]) -> str:
        raw = json.dumps(payload)
        assert len(raw) > 260
        if mode == "inline":
            return raw
        file_path = tmp_path / f"{name}.json"
        file_path.write_text(raw, encoding="utf-8")
        return f"@{file_path}" if mode == "at_path" else str(file_path)

    recorded_refresh: dict[str, object] = {}
    recorded_refine: dict[str, object] = {}

    monkeypatch.setattr(
        "loopx.control_plane.goals.acceptance.refine_goal_outcome",
        lambda **kwargs: (
            recorded_refine.update(kwargs)
            or {"ok": True, "status": "applied", "goal_acceptance_contract": {}}
        ),
    )
    monkeypatch.setattr(
        "loopx.control_plane.goals.acceptance.public_goal_acceptance",
        lambda result, include_contract=False: {
            "ok": result["ok"],
            "status": result["status"],
        },
    )
    monkeypatch.setattr(
        "loopx.capabilities.explore.result_writeback.normalize_result_attachment",
        lambda attachment, **_kwargs: dict(attachment),
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: (
            recorded_refresh.update(kwargs)
            or {"ok": True, "appended": False, "dry_run": True, "classification": "validated"}
        ),
    )

    args = _parse_refresh_args(
        "--dry-run",
        "--agent-vision-json",
        encode_arg("vision", vision),
        "--goal-outcome-update-json",
        encode_arg("outcome", outcome_update),
        "--explore-result-json",
        encode_arg("explore", explore),
    )
    captured: dict[str, object] = {}
    rc = project_lifecycle.handle_project_lifecycle_command(
        args,
        registry_path=tmp_path / "registry.json",
        print_payload=lambda payload, _fmt, _renderer: captured.update(payload),
        output_format=lambda _args: "json",
        append_cli_rollout_event=lambda *_a, **_kw: {},
    )
    assert rc == 0, captured
    assert captured["ok"] is True
    assert recorded_refresh["agent_vision_packet"] == vision
    assert recorded_refresh["explore_result"] == explore
    assert recorded_refine["operation_id"] == outcome_update["operation_id"]


@pytest.mark.parametrize(
    ("flag", "value", "expected_fragment"),
    [
        ("--agent-vision-json", "{bad", "valid JSON"),
        ("--agent-vision-json", "[1, 2]", "JSON object"),
        ("--agent-vision-json", json.dumps({"a": "x" * 270000}), "exceeds 256 KiB"),
        ("--agent-vision-json", "x" * 5000, "JSON object or valid file path"),
        ("--goal-outcome-update-json", "{bad", "valid JSON"),
        ("--goal-outcome-update-json", "[1, 2]", "JSON object"),
        (
            "--goal-outcome-update-json",
            json.dumps({"a": "x" * 270000}),
            "exceeds 256 KiB",
        ),
        ("--goal-outcome-update-json", "x" * 5000, "JSON object or valid file path"),
        ("--explore-result-json", "{bad", "valid JSON"),
        ("--explore-result-json", "[1, 2]", "JSON object"),
        ("--explore-result-json", json.dumps({"a": "x" * 270000}), "exceeds 256 KiB"),
        ("--explore-result-json", "x" * 5000, "JSON object or valid file path"),
    ],
)
def test_refresh_state_returns_clean_error_payload_on_invalid_json_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    value: str,
    expected_fragment: str,
) -> None:
    refresh_calls: list[object] = []
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: refresh_calls.append(kwargs) or {},
    )
    args = _parse_refresh_args(flag, value)
    captured: dict[str, object] = {}
    rc = project_lifecycle.handle_project_lifecycle_command(
        args,
        registry_path=tmp_path / "registry.json",
        print_payload=lambda payload, _fmt, _renderer: captured.update(payload),
        output_format=lambda _args: "json",
        append_cli_rollout_event=lambda *_a, **_kw: {},
    )
    assert rc == 1
    assert captured["ok"] is False
    assert expected_fragment in str(captured["error"])
    assert refresh_calls == []


def test_split_explore_result_input_parses_inline_json_without_errno_63(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment = _valid_explore_attachment()
    attachment["observation"] = "x" * 400
    monkeypatch.setattr(
        "loopx.capabilities.explore.result_writeback.normalize_result_attachment",
        lambda item, **_kwargs: dict(item),
    )
    vision, normalized = project_lifecycle_inputs.split_explore_result_input(
        SimpleNamespace(), json.dumps(attachment)
    )
    assert normalized == attachment
    assert isinstance(vision, SimpleNamespace)
