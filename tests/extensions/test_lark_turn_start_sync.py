from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loopx.cli_commands import lark_inbox as lark_inbox_cli
from loopx.control_plane.capability_hooks import dispatch_turn_start_hooks
from loopx.control_plane.work_items.work_lane import (
    operator_inbox_material_review_due_work_lane_contract,
)
from loopx.extensions.lark import goal_topic_connections as goal_topic_connections_module
from loopx.extensions.lark import turn_start_sync as turn_start_sync_module
from loopx.extensions.lark.event_collector import load_lark_event_collector_config
from loopx.extensions.lark.event_inbox import project_lark_event_inbox_urgency
from loopx.extensions.lark.inbox_reactions import lark_inbox_reaction_receipts
from loopx.extensions.lark.routed_inbox import project_routed_lark_event_inbox_urgency
from loopx.extensions.lark.turn_start_sync import sync_lark_turn_start_inbox

FIRST_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
SECOND_NOW = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _project(
    tmp_path: Path,
    *,
    material_review: bool = True,
    received_reaction: bool = False,
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config_dir = project / ".loopx" / "config"
    config_dir.mkdir(parents=True)
    inbox = config_dir / "inbox.json"
    inbox.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/requirements",
                "capture_scope": "configured_chat_all",
                "material_review": {"enabled": material_review, "drain_limit": 20},
                "reply": (
                    {
                        "enabled": True,
                        "sender_profile": "fixture-bot",
                        "sender_identity": "bot",
                        "bot_display_name": "Fixture Bot",
                        "chat_id": "oc_fixture",
                    }
                    if received_reaction
                    else {"enabled": False}
                ),
            }
        ),
        encoding="utf-8",
    )
    collector = config_dir / "collector.json"
    collector.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_collector_config_v1",
                "enabled": True,
                "service_name": "loopx-turn-start-fixture",
                "supervisor": "systemd",
                "consume_timeout": "30m",
                "profile": "fixture-bot",
                "turn_start_sync": {
                    "enabled": True,
                    "initial_lookback_seconds": 900,
                    "overlap_seconds": 5,
                    "page_size": 50,
                },
                "routes": [
                    {
                        "route_key": "requirements",
                        "chat_id": "oc_fixture",
                        "event_inbox_config": ".loopx/config/inbox.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, collector


def _write_direct_inbox(
    project: Path,
    *,
    agent_id: str,
    app_ref: str,
    bot_display_name: str,
    topic_root_message_id: str,
    capture_scope: str = "addressed_only",
) -> tuple[Path, Path]:
    config, config_ref, payload = goal_topic_connections_module._agent_inbox_config(
        goal={"id": "goal-fixture", "repo": str(project)},
        agent_id=agent_id,
        app_ref=app_ref,
        chat_id="oc_fixture",
        bot_display_name=bot_display_name,
        capture_scope=capture_scope,
        topic_root_message_id=topic_root_message_id,
    )
    written_ref = goal_topic_connections_module._write_agent_inbox_config(
        config_path=config,
        config_ref=config_ref,
        payload=payload,
    )
    assert written_ref == config_ref
    return config, project / str(payload["inbox_dir"])


def _direct_addressed_inbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config, inbox = _write_direct_inbox(
        project,
        agent_id="agent-fixture",
        app_ref="fixture-bot",
        bot_display_name="Fixture Bot",
        topic_root_message_id="om_goal_topic_root",
    )
    return project, config, inbox


def _agent_collector(
    project: Path,
    *,
    agent_key: str,
    profile: str,
    chat_id: str,
) -> Path:
    config_dir = project / ".loopx" / "config" / agent_key
    config_dir.mkdir(parents=True)
    inbox = config_dir / "inbox.json"
    inbox.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": f".loopx/inbox/{agent_key}/requirements",
                "capture_scope": "configured_chat_all",
                "material_review": {"enabled": True, "drain_limit": 20},
                "reply": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    collector = config_dir / "collector.json"
    collector.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_collector_config_v1",
                "enabled": True,
                "service_name": f"loopx-turn-start-{agent_key}",
                "supervisor": "systemd",
                "consume_timeout": "30m",
                "profile": profile,
                "turn_start_sync": {
                    "enabled": True,
                    "initial_lookback_seconds": 900,
                    "overlap_seconds": 5,
                    "page_size": 50,
                },
                "routes": [
                    {
                        "route_key": "requirements",
                        "chat_id": chat_id,
                        "event_inbox_config": (f".loopx/config/{agent_key}/inbox.json"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return collector


class PageRunner:
    def __init__(self, pages: Sequence[dict[str, object]]) -> None:
        self.pages = list(pages)
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        page = self.pages.pop(0)
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=0,
            stdout=json.dumps({"ok": True, "identity": "bot", "data": page}),
            stderr="",
        )


class ReactionPageRunner(PageRunner):
    def __init__(
        self,
        pages: Sequence[dict[str, object]],
        *,
        fail_reaction: bool = False,
    ) -> None:
        super().__init__(pages)
        self.fail_reaction = fail_reaction
        self.profile_app_id = "cli_fixture_bot"

    def __call__(
        self, argv: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        call = list(argv)
        if "whoami" in call:
            self.calls.append(call)
            return subprocess.CompletedProcess(
                args=call,
                returncode=0,
                stdout=json.dumps({"appId": self.profile_app_id}),
                stderr="",
            )
        if "reactions" not in call:
            return super().__call__(argv, **kwargs)
        self.calls.append(call)
        if self.fail_reaction:
            return subprocess.CompletedProcess(
                args=call,
                returncode=1,
                stdout=json.dumps({"ok": False}),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=call,
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"reaction_id": "reaction_Get"}}),
            stderr="",
        )


def _page(*messages: dict[str, object]) -> dict[str, object]:
    return {
        "messages": list(messages),
        "has_more": False,
        "page_token": "",
    }


def test_turn_start_sync_captures_then_requires_same_turn_agent_read(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner(
        [
            _page(
                {
                    "message_id": "om_new_context",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Please adjust the current plan.",
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["private_content_returned"] is False
    assert result["provider_payload_returned"] is False
    captured = json.loads(
        (project / ".loopx/inbox/requirements/om_new_context.json").read_text()
    )
    assert captured["content"] == "Please adjust the current plan."
    urgency = project_routed_lark_event_inbox_urgency(
        project=project,
        config_path=config,
    )
    lane = operator_inbox_material_review_due_work_lane_contract(
        {
            "capabilities": {
                "lark_event_inbox": {
                    "urgency": urgency,
                    "drain_command": "loopx lark-inbox drain --goal-id fixture",
                }
            }
        },
        current_contract={"lane": "advancement_task"},
    )
    assert lane is not None
    assert lane["priority_preemption"] is True
    assert lane["semantic_triage_required"] is True
    assert "replan_goal" in lane["allowed_dispositions"]
    assert "before ordinary work" in str(lane["action"])


def test_turn_start_sync_accepts_one_click_addressed_only_inbox(
    tmp_path: Path,
) -> None:
    project, config, inbox = _direct_addressed_inbox(tmp_path)
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_addressed_goal_topic",
                    "root_id": "om_goal_topic_root",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Please prepare the requested report.",
                    "mentions": [{"name": "Fixture Bot"}],
                    "deleted": False,
                },
                {
                    "message_id": "om_unaddressed_goal_topic",
                    "root_id": "om_goal_topic_root",
                    "create_time": "2026-08-26T09:59:01Z",
                    "content": "This conversation belongs to another participant.",
                    "mentions": [],
                    "deleted": False,
                },
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["received_reaction_count"] == 1
    captured = json.loads((inbox / "om_addressed_goal_topic.json").read_text())
    assert captured["route_key"] == "default"
    assert captured["addressed_to_bot"] is True
    assert not (inbox / "om_unaddressed_goal_topic.json").exists()


def test_turn_start_sync_rejects_direct_inbox_without_goal_topic_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config, config_ref, payload = goal_topic_connections_module._agent_inbox_config(
        goal={"id": "goal-fixture", "repo": str(project)},
        agent_id="agent-fixture",
        app_ref="fixture-bot",
        chat_id="oc_fixture",
        bot_display_name="Fixture Bot",
        capture_scope="addressed_only",
    )
    goal_topic_connections_module._write_agent_inbox_config(
        config_path=config,
        config_ref=config_ref,
        payload=payload,
    )

    with pytest.raises(ValueError, match="requires a Goal Topic root"):
        sync_lark_turn_start_inbox(
            project=project,
            config_path=config,
            runner=ReactionPageRunner([]),
            now=FIRST_NOW,
        )


def test_turn_start_sync_rejects_same_chat_bot_mention_from_another_topic(
    tmp_path: Path,
) -> None:
    project, config, inbox = _direct_addressed_inbox(tmp_path)
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_other_topic_mention",
                    "root_id": "om_other_topic_root",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "@Fixture Bot this belongs elsewhere.",
                    "mentions": [{"name": "Fixture Bot"}],
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "empty"
    assert result["observation_count"] == 0
    assert result["agent_read_required"] is False
    assert not (inbox / "om_other_topic_mention.json").exists()


def test_turn_start_sync_configured_chat_all_explicitly_accepts_other_topic(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config, inbox = _write_direct_inbox(
        project,
        agent_id="agent-fixture",
        app_ref="fixture-bot",
        bot_display_name="Fixture Bot",
        topic_root_message_id="om_goal_topic_root",
        capture_scope="configured_chat_all",
    )
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_chat_wide_message",
                    "root_id": "om_other_topic_root",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "A chat-wide update without a mention.",
                    "mentions": [],
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["observation_count"] == 1
    assert (inbox / "om_chat_wide_message.json").is_file()


def test_turn_start_sync_can_capture_history_as_quiet_context_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config, inbox = _write_direct_inbox(
        project,
        agent_id="agent-fixture",
        app_ref="fixture-bot",
        bot_display_name="Fixture Bot",
        topic_root_message_id="om_goal_topic_root",
        capture_scope="configured_chat_all",
    )
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_old_addressed_message",
                    "root_id": "om_goal_topic_root",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "@Fixture Bot an old request must not replay.",
                    "mentions": [{"name": "Fixture Bot"}],
                    "sender": {"sender_type": "app", "id": "cli_fixture_worker"},
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
        historical_context_only=True,
        emit_received_reactions=False,
    )

    assert result["status"] == "observed"
    captured = json.loads((inbox / "om_old_addressed_message.json").read_text())
    assert captured["addressed_to_bot"] is False
    assert captured["historical_context_only"] is True
    assert captured["sender_type"] == "app"
    assert captured["sender_id"] == "cli_fixture_worker"
    assert captured["historical_was_addressed_to_bot"] is True
    assert captured["historical_addressing_source"] == "provider_mention"
    urgency = project_lark_event_inbox_urgency(
        project=project,
        config_path=config,
        now=FIRST_NOW,
    )
    assert urgency["reply_due"] is False
    assert urgency["attention_required_count"] == 0
    assert urgency["material_review_count"] == 1
    assert all("reactions" not in call for call in runner.calls)


def test_turn_start_sync_isolates_two_agents_and_apps_in_the_same_chat(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    alpha_config, alpha_inbox = _write_direct_inbox(
        project,
        agent_id="agent-alpha",
        app_ref="bot-alpha",
        bot_display_name="Bot Alpha",
        topic_root_message_id="om_topic_alpha",
    )
    beta_config, beta_inbox = _write_direct_inbox(
        project,
        agent_id="agent-beta",
        app_ref="bot-beta",
        bot_display_name="Bot Beta",
        topic_root_message_id="om_topic_beta",
    )
    messages = (
        {
            "message_id": "om_message_alpha",
            "root_id": "om_topic_alpha",
            "create_time": "2026-08-26T09:59:00Z",
            "content": "@Bot Alpha alpha only.",
            "mentions": [{"name": "Bot Alpha"}],
            "deleted": False,
        },
        {
            "message_id": "om_message_beta",
            "root_id": "om_topic_beta",
            "create_time": "2026-08-26T09:59:01Z",
            "content": "@Bot Beta beta only.",
            "mentions": [{"name": "Bot Beta"}],
            "deleted": False,
        },
    )

    alpha = sync_lark_turn_start_inbox(
        project=project,
        config_path=alpha_config,
        runner=ReactionPageRunner([_page(*messages)]),
        now=FIRST_NOW,
    )
    beta = sync_lark_turn_start_inbox(
        project=project,
        config_path=beta_config,
        runner=ReactionPageRunner([_page(*messages)]),
        now=FIRST_NOW,
    )

    assert alpha["observation_count"] == 1
    assert beta["observation_count"] == 1
    assert (alpha_inbox / "om_message_alpha.json").is_file()
    assert not (alpha_inbox / "om_message_beta.json").exists()
    assert (beta_inbox / "om_message_beta.json").is_file()
    assert not (beta_inbox / "om_message_alpha.json").exists()


def test_turn_start_sync_acknowledges_ordinary_pending_message_once_by_default(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    message = {
        "message_id": "om_ordinary_pending",
        "create_time": "2026-08-26T09:59:00Z",
        "content": "Please keep the current investigation moving.",
        "deleted": False,
    }
    first = ReactionPageRunner([_page(message)])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=first,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["received_reaction_count"] == 1
    assert result["received_reaction_failure_count"] == 0
    assert result["external_writes_performed"] is True
    inbox = project / ".loopx/inbox/requirements"
    assert (
        lark_inbox_reaction_receipts(
            inbox=inbox,
            message_id="om_ordinary_pending",
        )["received"]["emoji_type"]
        == "Get"
    )

    duplicate = ReactionPageRunner([_page(message)])
    duplicate_result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=duplicate,
        now=SECOND_NOW,
    )

    assert duplicate_result["status"] == "empty"
    assert duplicate_result["received_reaction_count"] == 0
    assert not any("reactions" in call for call in duplicate.calls)


def test_turn_start_sync_acknowledges_message_previously_captured_by_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    message = {
        "message_id": "om_collector_captured",
        "create_time": "2026-08-26T09:59:00Z",
        "content": "Collector stored this before the Agent turn began.",
        "deleted": False,
    }
    inbox = project / ".loopx/inbox/requirements"
    inbox.mkdir(parents=True)
    (inbox / "om_collector_captured.json").write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "om_collector_captured",
                **message,
            }
        ),
        encoding="utf-8",
    )
    runner = ReactionPageRunner([_page(message)])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["received_reaction_count"] == 1
    assert result["external_writes_performed"] is True
    assert any("reactions" in call for call in runner.calls)

    monkeypatch.setattr(
        lark_inbox_cli,
        "_resolve_lark_activation",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        lark_inbox_cli,
        "sync_lark_turn_start_inbox",
        lambda **_kwargs: result,
    )
    dispatch = dispatch_turn_start_hooks(
        [
            lark_inbox_cli.build_lark_turn_start_inbox_hook(
                project=project,
                config_path=config,
                runtime_root_arg=None,
                required_read_command="loopx lark-inbox drain --goal-id fixture",
            )
        ]
    )
    assert dispatch["failures"] == []
    assert dispatch["results"][0]["observation_count"] == 1
    assert dispatch["results"][0]["agent_read_required"] is True


def test_turn_start_sync_respects_explicit_received_reaction_disable(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    inbox_config = project / ".loopx/config/inbox.json"
    payload = json.loads(inbox_config.read_text(encoding="utf-8"))
    payload["reply"]["received_reaction_emoji"] = ""
    inbox_config.write_text(json.dumps(payload), encoding="utf-8")
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_reaction_disabled",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Read this without a provider acknowledgement.",
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["received_reaction_count"] == 0
    assert result["external_writes_performed"] is False
    assert not any("reactions" in call for call in runner.calls)


def test_turn_start_sync_collector_capture_still_requires_read_when_reaction_disabled(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    inbox_config = project / ".loopx/config/inbox.json"
    payload = json.loads(inbox_config.read_text(encoding="utf-8"))
    payload["reply"]["received_reaction_emoji"] = ""
    inbox_config.write_text(json.dumps(payload), encoding="utf-8")
    message = {
        "message_id": "om_collector_reaction_disabled",
        "create_time": "2026-08-26T09:59:00Z",
        "content": "Collector captured this before the turn-start read.",
        "deleted": False,
    }
    inbox = project / ".loopx/inbox/requirements"
    inbox.mkdir(parents=True)
    (inbox / "om_collector_reaction_disabled.json").write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "om_collector_reaction_disabled",
                **message,
            }
        ),
        encoding="utf-8",
    )
    runner = ReactionPageRunner([_page(message)])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["external_writes_performed"] is False
    assert not any("reactions" in call for call in runner.calls)


def test_turn_start_sync_reports_reaction_write_failure_without_losing_message(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_reaction_failure",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Please take a look.",
                    "deleted": False,
                }
            )
        ],
        fail_reaction=True,
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "partial"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["received_reaction_count"] == 0
    assert result["received_reaction_failure_count"] == 1
    assert (project / ".loopx/inbox/requirements/om_reaction_failure.json").is_file()


def test_turn_start_sync_retries_reaction_from_local_read_beyond_overlap_window(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    message = {
        "message_id": "om_reaction_retry",
        "create_time": "2026-08-26T09:59:00Z",
        "content": "Please retry the acknowledgement.",
        "deleted": False,
    }
    failed = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=ReactionPageRunner([_page(message)], fail_reaction=True),
        now=FIRST_NOW,
    )

    assert failed["status"] == "partial"
    # The real second provider window begins at 09:59:55, so the 09:59:00
    # message is no longer visible and cannot be recovered from overlap.
    retry_runner = ReactionPageRunner([_page()])
    retried = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=retry_runner,
        now=SECOND_NOW,
    )

    assert retried["status"] == "empty"
    assert retried["observation_count"] == 0
    assert retried["agent_read_required"] is False
    assert retried["received_reaction_count"] == 1
    assert retried["external_writes_performed"] is True
    assert any("reactions" in call for call in retry_runner.calls)
    history_call = next(call for call in retry_runner.calls if "reactions" not in call)
    assert history_call[history_call.index("--start") + 1] == "2026-08-26T09:59:55Z"


def test_turn_start_sync_bounds_failed_reactions_and_resumes_round_robin(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    messages = [
        {
            "message_id": f"om_backlog_{index}",
            "create_time": "2026-08-26T09:59:00Z",
            "content": f"Pending acknowledgement {index}",
            "deleted": False,
        }
        for index in range(5)
    ]
    first_runner = ReactionPageRunner([_page(*messages)], fail_reaction=True)

    first = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=first_runner,
        now=FIRST_NOW,
    )

    first_reactions = [
        call[call.index("--message-id") + 1]
        for call in first_runner.calls
        if "reactions" in call
    ]
    assert first_reactions == ["om_backlog_0", "om_backlog_1", "om_backlog_2"]
    assert first["received_reaction_failure_count"] == 3
    assert first["received_reaction_deferred_count"] == 2
    assert first["read_ack_attempt_count"] == 3

    second_runner = ReactionPageRunner([_page()], fail_reaction=True)
    second = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second_runner,
        now=SECOND_NOW,
    )

    second_reactions = [
        call[call.index("--message-id") + 1]
        for call in second_runner.calls
        if "reactions" in call
    ]
    assert second_reactions == ["om_backlog_3", "om_backlog_4", "om_backlog_0"]
    assert second["received_reaction_failure_count"] == 3
    assert second["received_reaction_deferred_count"] == 2
    assert second["observation_count"] == 0
    assert second["agent_read_required"] is False
    assert "om_backlog_" not in json.dumps(second)


def test_turn_start_sync_captures_new_reply_in_an_existing_topic(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    root_id = "om_existing_topic_root"
    first = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=ReactionPageRunner(
            [
                _page(
                    {
                        "message_id": root_id,
                        "root_id": root_id,
                        "create_time": "2026-08-26T09:59:00Z",
                        "content": "Existing topic root.",
                        "deleted": False,
                    }
                )
            ]
        ),
        now=FIRST_NOW,
    )
    assert first["observation_count"] == 1

    new_reply_id = "om_existing_topic_new_reply"
    second_runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": new_reply_id,
                    "root_id": root_id,
                    "parent_id": root_id,
                    "create_time": "2026-08-26T10:04:00Z",
                    "content": "A new reply on the old topic.",
                    "deleted": False,
                }
            )
        ]
    )
    second = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second_runner,
        now=SECOND_NOW,
    )

    assert second["status"] == "observed"
    assert second["observation_count"] == 1
    assert second["received_reaction_count"] == 1
    assert (project / f".loopx/inbox/requirements/{new_reply_id}.json").is_file()
    assert any(
        "reactions" in call and new_reply_id in call for call in second_runner.calls
    )


def test_turn_start_sync_shares_budget_and_rotates_routes_across_dispatches(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    second_inbox_config = project / ".loopx/config/inbox-second.json"
    inbox_payload = json.loads(
        (project / ".loopx/config/inbox.json").read_text(encoding="utf-8")
    )
    inbox_payload["inbox_dir"] = ".loopx/inbox/second"
    inbox_payload["reply"]["chat_id"] = "oc_fixture_second"
    second_inbox_config.write_text(json.dumps(inbox_payload), encoding="utf-8")
    collector_payload = json.loads(config.read_text(encoding="utf-8"))
    collector_payload["routes"].append(
        {
            "route_key": "second",
            "chat_id": "oc_fixture_second",
            "event_inbox_config": ".loopx/config/inbox-second.json",
        }
    )
    config.write_text(json.dumps(collector_payload), encoding="utf-8")
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_first_route_0",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "First route message zero.",
                    "deleted": False,
                },
                {
                    "message_id": "om_first_route_1",
                    "create_time": "2026-08-26T09:59:01Z",
                    "content": "First route message one.",
                    "deleted": False,
                },
                {
                    "message_id": "om_first_route_2",
                    "create_time": "2026-08-26T09:59:02Z",
                    "content": "First route message two.",
                    "deleted": False,
                },
            ),
            _page(
                {
                    "message_id": "om_second_route_0",
                    "create_time": "2026-08-26T09:59:03Z",
                    "content": "Second route message zero.",
                    "deleted": False,
                },
            ),
        ],
        fail_reaction=True,
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    reaction_calls = [call for call in runner.calls if "reactions" in call]
    assert len(reaction_calls) == turn_start_sync_module.TURN_START_REACTION_ATTEMPT_LIMIT
    assert result["read_ack_attempt_count"] == 3
    assert result["received_reaction_deferred_count"] == 1

    second_runner = ReactionPageRunner([_page(), _page()], fail_reaction=True)
    second = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second_runner,
        now=SECOND_NOW,
    )

    second_reactions = [
        call[call.index("--message-id") + 1]
        for call in second_runner.calls
        if "reactions" in call
    ]
    assert second_reactions[0] == "om_second_route_0"
    assert len(second_reactions) == turn_start_sync_module.TURN_START_REACTION_ATTEMPT_LIMIT
    assert second["read_ack_attempt_count"] == 3
    assert second["received_reaction_deferred_count"] == 1


def test_turn_start_sync_excludes_verified_profile_self_message(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, received_reaction=True)
    runner = ReactionPageRunner(
        [
            _page(
                {
                    "message_id": "om_self_delivery",
                    "create_time": "2026-08-26T09:58:00Z",
                    "content": "Bot delivery status",
                    "sender": {
                        "sender_type": "app",
                        "id": "cli_fixture_bot",
                    },
                    "deleted": False,
                },
                {
                    "message_id": "om_human_follow_up",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Please keep investigating",
                    "sender": {
                        "sender_type": "user",
                        "id": "ou_fixture_user",
                    },
                    "deleted": False,
                },
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["self_message_skipped_count"] == 1
    assert result["received_reaction_count"] == 1
    inbox = project / ".loopx/inbox/requirements"
    assert not (inbox / "om_self_delivery.json").exists()
    assert (inbox / "om_human_follow_up.json").is_file()
    assert set(
        lark_inbox_reaction_receipts(
            inbox=inbox,
            message_id="om_human_follow_up",
        )
    ) == {"received"}


def test_completed_sync_opens_a_new_overlapping_tail_window(tmp_path: Path) -> None:
    project, config = _project(tmp_path)
    first = PageRunner([_page()])
    first_result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=first,
        now=FIRST_NOW,
    )
    assert first_result["status"] == "empty"

    second = PageRunner([_page()])
    second_result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second,
        now=SECOND_NOW,
    )

    assert second_result["status"] == "empty"
    argv = second.calls[0]
    assert argv[argv.index("--start") + 1] == "2026-08-26T09:59:55Z"
    assert argv[argv.index("--end") + 1] == "2026-08-26T10:05:00Z"


def test_same_route_key_isolates_cursor_and_lock_for_two_agent_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    first_config = _agent_collector(
        project,
        agent_key="agent-alpha",
        profile="fixture-alpha-bot",
        chat_id="oc_fixture_alpha",
    )
    second_config = _agent_collector(
        project,
        agent_key="agent-beta",
        profile="fixture-beta-bot",
        chat_id="oc_fixture_beta",
    )
    lock_targets: list[Path] = []
    real_lock = turn_start_sync_module.exclusive_file_lock

    def capture_lock_target(path: Path, **kwargs: object) -> object:
        lock_targets.append(path)
        return real_lock(path, **kwargs)

    monkeypatch.setattr(
        turn_start_sync_module,
        "exclusive_file_lock",
        capture_lock_target,
    )

    for config in (first_config, second_config):
        result = sync_lark_turn_start_inbox(
            project=project,
            config_path=config,
            runner=PageRunner([_page()]),
            now=FIRST_NOW,
        )
        assert result["status"] == "empty"

    assert len(lock_targets) == 4
    assert len(set(lock_targets)) == 4
    cursor_paths = sorted(
        (project / ".loopx/inbox/.turn-start/requirements").glob("*.json")
    )
    assert len(cursor_paths) == 2
    assert set(cursor_paths).issubset(lock_targets)
    dispatch_lock_targets = [
        path for path in lock_targets if path.parent.name == ".dispatch"
    ]
    assert len(dispatch_lock_targets) == 2

    for config in (first_config, second_config):
        runner = PageRunner([_page()])
        result = sync_lark_turn_start_inbox(
            project=project,
            config_path=config,
            runner=runner,
            now=SECOND_NOW,
        )
        assert result["status"] == "empty"
        argv = runner.calls[0]
        assert argv[argv.index("--start") + 1] == "2026-08-26T09:59:55Z"


def test_overlapping_duplicate_does_not_require_mutable_content_readback(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    first = PageRunner(
        [
            _page(
                {
                    "message_id": "om_overlap",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Original provider rendering.",
                    "deleted": False,
                }
            )
        ]
    )
    assert (
        sync_lark_turn_start_inbox(
            project=project,
            config_path=config,
            runner=first,
            now=FIRST_NOW,
        )["status"]
        == "observed"
    )
    second = PageRunner(
        [
            _page(
                {
                    "message_id": "om_overlap",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Edited provider rendering.",
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second,
        now=SECOND_NOW,
    )

    assert result["status"] == "empty"
    assert result["observation_count"] == 0
    assert result["agent_read_required"] is False
    captured = json.loads(
        (project / ".loopx/inbox/requirements/om_overlap.json").read_text()
    )
    assert captured["content"] == "Original provider rendering."


def test_provider_schema_error_is_distinct_from_a_real_empty_inbox(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner([{"items": [], "has_more": False, "page_token": ""}])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "unavailable"
    assert result["error_code"] == "provider_contract_error"
    assert result["agent_read_required"] is False
    assert not list((project / ".loopx/inbox/.turn-start").rglob("*.json"))


def test_corrupt_dispatch_cursor_fails_closed_before_provider_read(
    tmp_path: Path,
) -> None:
    project, config_path = _project(tmp_path)
    config = load_lark_event_collector_config(
        project=project,
        config_path=config_path,
    )
    cursor_path = turn_start_sync_module._dispatch_cursor_path(
        project,
        source_fingerprint=(
            turn_start_sync_module._dispatch_source_fingerprint(config)
        ),
    )
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text("{}\n", encoding="utf-8")
    runner = PageRunner([_page()])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config_path,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "unavailable"
    assert result["error_code"] == "dispatch_cursor_unreadable"
    assert result["external_reads_performed"] is False
    assert runner.calls == []


def test_inbox_write_still_requires_agent_read_when_cursor_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner(
        [
            _page(
                {
                    "message_id": "om_cursor_failure",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Steer the current turn.",
                    "deleted": False,
                }
            )
        ]
    )

    def fail_cursor_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture cursor write failure")

    monkeypatch.setattr(turn_start_sync_module, "_write_cursor", fail_cursor_write)
    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "partial"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["local_private_state_mutated"] is True
    assert result["error_code"] == "route_sync_partial"
    assert (project / ".loopx/inbox/requirements/om_cursor_failure.json").is_file()


def test_turn_start_sync_requires_an_agent_material_triage_lane(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, material_review=False)

    with pytest.raises(ValueError, match="Agent triage lane"):
        load_lark_event_collector_config(project=project, config_path=config)
