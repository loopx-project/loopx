from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.context_providers.base import (
    ContextProviderItem,
    ContextProviderRetrieval,
    ContextProviderSync,
)
from loopx.capabilities.issue_fix.reward_memory import (
    run_issue_fix_reviewer_notification_automatic_reward_memory,
)
from loopx.capabilities.reward_memory.experiment import (
    canonical_reward_memory_actor_peer_id,
    load_reward_memory_experiment_config,
    preflight_reward_memory_experiment_config,
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
    validate_reward_memory_goal_agent_scope,
)
from loopx.capabilities.reward_memory.runtime_hooks import (
    run_reward_memory_automatic_recall_hook,
)
from loopx.cli import main
from loopx.cli_commands.status import attach_agent_lane_next_actions
from loopx.configure_goal import configure_goal
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.quota import build_quota_should_run
from loopx.presentation.renderers.status_markdown import render_status_markdown


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_FIXTURE = REPO_ROOT / "examples/fixtures/reward-memory-ingest-event.public.json"
SCOPED_PUBLIC_FIXTURE = (
    REPO_ROOT / "examples/fixtures/reward-memory-scoped-feedback-ingest.public.json"
)


class _RecallProvider:
    provider_id = "openviking"

    def __init__(
        self,
        *,
        content_by_scope: dict[str, str] | None = None,
        unavailable: bool = False,
    ) -> None:
        self.content_by_scope = content_by_scope or {}
        self.unavailable = unavailable
        self.retrieve_calls = 0

    def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
        self.retrieve_calls += 1
        if self.unavailable:
            raise RuntimeError("provider unavailable")
        scope_ref = str(kwargs["scope_ref"])
        content = self.content_by_scope.get(scope_ref)
        items = (
            (
                ContextProviderItem(
                    resource_ref=f"{scope_ref}/memory.json",
                    summary="Reviewed reward memory.",
                    content=content,
                    score=0.95,
                ),
            )
            if content
            else ()
        )
        return ContextProviderRetrieval(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            query_summary=str(kwargs["query_summary"]),
            observed_at=str(kwargs["observed_at"]),
            search_performed=True,
            read_performed=True,
            items=items,
            requested_limit=int(kwargs["max_results"]),
        )


def _experiment(
    tmp_path: Path,
    fixture_path: Path = PUBLIC_FIXTURE,
) -> tuple[Path, Path, Path]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    project = tmp_path / "project"
    config_path = project / ".loopx/config/reward-memory/experiment.json"
    config_path.parent.mkdir(parents=True)
    corpus_id = fixture["corpus"]["corpus_id"]
    surface_id = fixture["standing_policy"]["scope"]["surface_ids"][0]
    provider_binding = fixture["provider_binding"]
    project_provider_binding = {
        key: value
        for key, value in provider_binding.items()
        if key not in {"corpus_id", "scope_ref"}
    }
    project_provider_binding["corpus_scopes"] = [
        {"corpus_id": corpus_id, "scope_ref": provider_binding["scope_ref"]}
    ]
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "reward_memory_experiment_config_v1",
                "project_provider_binding": project_provider_binding,
                "corpora": [
                    {
                        "corpus": fixture["corpus"],
                        "standing_policy": fixture["standing_policy"],
                    }
                ],
                "surfaces": [
                    {
                        "surface_id": surface_id,
                        "adapter": fixture["adapter"],
                        "corpus_ids": [corpus_id],
                        "ingest_corpus_id": corpus_id,
                        "recall_profile": {
                            "profile_id": "fixture_function_boundary_v1",
                            "mode": "function_boundary",
                            "max_queries": 1,
                            "limit": 5,
                        },
                    }
                ],
                "automation": {
                    "automatic_recall": False,
                    "automatic_ingest": False,
                    "fail_open": True,
                },
            }
        ),
        encoding="utf-8",
    )
    config_digest = f"sha256:{hashlib.sha256(config_path.read_bytes()).hexdigest()}"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": "reward-memory-goal",
                        "repo": str(project),
                        "coordination": {
                            "registered_agents": ["pilot", "meta"],
                        },
                        "control_plane": {
                            "reward_memory": {
                                "enabled": True,
                                "experimental": True,
                                "config_path": (
                                    ".loopx/config/reward-memory/experiment.json"
                                ),
                                "enabled_agents": ["pilot"],
                                "config_digest": config_digest,
                                "enablement_receipts": {
                                    "pilot": {
                                        "schema_version": (
                                            "reward_memory_enablement_receipt_v0"
                                        ),
                                        "status": "verified",
                                        "goal_id": "reward-memory-goal",
                                        "agent_id": "pilot",
                                        "config_digest": config_digest,
                                        "provider_id": "openviking",
                                        "isolation_mode": "explicit_shared",
                                        "actor_binding_verified": False,
                                        "writability_verified": True,
                                        "exact_readback_verified": True,
                                        "probe_count": 1,
                                        "write_count": 1,
                                        "external_writes_performed": True,
                                        "observed_at": "2026-01-01T00:00:00Z",
                                    }
                                },
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "adapter": fixture["adapter"],
                "event": fixture["event"],
                "observed_at": fixture["observed_at"],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, event_path, fixture_path


def _run(capsys, registry_path: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = main(
        [
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ]
    )
    output = capsys.readouterr().out
    return result, json.loads(output)


def _v1_config() -> dict[str, object]:
    fixture = json.loads(SCOPED_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    entries: list[dict[str, object]] = []
    scopes: list[dict[str, str]] = []
    for corpus_id, surface_id in (
        ("reviewer_policy_primary", "reviewer_artifact.summary"),
        ("reviewer_policy_overlay", "reviewer_artifact.summary"),
        ("patch_policy_separate", "issue_fix.patch_planning"),
    ):
        corpus = copy.deepcopy(fixture["corpus"])
        policy = copy.deepcopy(fixture["standing_policy"])
        scope_ref = f"viking://resources/reward-memory/{corpus_id}"
        corpus["corpus_id"] = corpus_id
        corpus["scope"]["surface_ids"] = [surface_id]
        corpus["provider_scope_ref_digest"] = hashlib.sha256(
            scope_ref.encode("utf-8")
        ).hexdigest()[:16]
        policy["policy_id"] = f"policy:example:{corpus_id}"
        policy["scope"]["surface_ids"] = [surface_id]
        entries.append({"corpus": corpus, "standing_policy": policy})
        scopes.append({"corpus_id": corpus_id, "scope_ref": scope_ref})
    return {
        "schema_version": "reward_memory_experiment_config_v1",
        "project_provider_binding": {
            "provider_id": "openviking",
            "namespace": "reward_memory",
            "timeout_seconds": 30,
            "minimum_provider_version": "0.4.9",
            "corpus_scopes": scopes,
        },
        "corpora": entries,
        "surfaces": [
            {
                "surface_id": "reviewer_artifact.summary",
                "adapter": "scoped_feedback",
                "corpus_ids": [
                    "reviewer_policy_primary",
                    "reviewer_policy_overlay",
                ],
                "ingest_corpus_id": "reviewer_policy_primary",
                "recall_profile": {
                    "profile_id": "reviewer_summary_v1",
                    "mode": "function_boundary",
                    "max_queries": 1,
                    "limit": 4,
                },
            },
            {
                "surface_id": "issue_fix.patch_planning",
                "adapter": "issue_fix_maintainer_feedback",
                "corpus_ids": ["patch_policy_separate"],
                "ingest_corpus_id": "patch_policy_separate",
                "recall_profile": {
                    "profile_id": "patch_planning_v1",
                    "mode": "bounded_agentic_search",
                    "max_queries": 2,
                    "limit": 5,
                },
            },
        ],
        "automation": {
            "automatic_recall": True,
            "automatic_ingest": True,
            "fail_open": True,
        },
    }


def _write_v1_config(registry_path: Path, config: dict[str, object]) -> None:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    project = Path(registry["goals"][0]["repo"])
    config_path = project / ".loopx/config/reward-memory/experiment.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    digest = f"sha256:{hashlib.sha256(config_path.read_bytes()).hexdigest()}"
    binding = registry["goals"][0]["control_plane"]["reward_memory"]
    binding["config_digest"] = digest
    binding["enablement_receipts"]["pilot"]["config_digest"] = digest
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


def _private_v1_config(*, goal_id: str, agent_id: str) -> dict[str, object]:
    config = _v1_config()
    actor = canonical_reward_memory_actor_peer_id(
        goal_id=goal_id,
        agent_id=agent_id,
    )
    config["project_provider_binding"]["actor_peer_id"] = actor
    for index, (entry, scope) in enumerate(
        zip(
            config["corpora"],
            config["project_provider_binding"]["corpus_scopes"],
            strict=True,
        )
    ):
        corpus = entry["corpus"]
        policy = entry["standing_policy"]
        corpus["privacy"]["visibility"] = "private"
        corpus["scope"]["peer_ref"] = f"agent:{agent_id}"
        policy["scope"]["peer_ref"] = f"agent:{agent_id}"
        scope_ref = (
            f"viking://user/default/peers/{actor}/memories/reward-memory/"
            f"goals/{goal_id}/corpus-{index}"
        )
        scope["scope_ref"] = scope_ref
        corpus["provider_scope_ref_digest"] = hashlib.sha256(
            scope_ref.encode("utf-8")
        ).hexdigest()[:16]
    return config


class _EnablementProvider:
    provider_id = "openviking"

    def __init__(self) -> None:
        self.preview_calls = 0
        self.write_calls = 0

    def sync(self, **kwargs: Any) -> ContextProviderSync:
        _source, target = kwargs["resources"][0]
        if kwargs["execute"] is not True:
            self.preview_calls += 1
            return ContextProviderSync(
                provider=self.provider_id,
                namespace=str(kwargs["namespace"]),
                status="preflight_ready",
                observed_at=str(kwargs["observed_at"]),
                requested_count=1,
                completed_count=0,
                reason_code="execute_required_for_verified_write",
                visibility="private",
                target_scope_kind="peer_memories",
                write_strategy="content_write",
                actor_binding_verified=True,
                provider_preflight_performed=True,
                target_access_preflight_verified=True,
            )
        self.write_calls += 1
        return ContextProviderSync(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            observed_at=str(kwargs["observed_at"]),
            requested_count=1,
            completed_count=1,
            write_count=1,
            result_refs=(target,),
            visibility="private",
            target_scope_kind="peer_memories",
            write_strategy="content_write",
            actor_binding_verified=True,
            provider_preflight_performed=True,
            target_access_preflight_verified=True,
            writability_verified=True,
        )


def test_canonical_actor_namespaces_same_local_agent_by_goal() -> None:
    first = canonical_reward_memory_actor_peer_id(
        goal_id="finance-research-goal",
        agent_id="explorer",
    )
    repeated = canonical_reward_memory_actor_peer_id(
        goal_id="finance-research-goal",
        agent_id="explorer",
    )
    second = canonical_reward_memory_actor_peer_id(
        goal_id="another-goal",
        agent_id="explorer",
    )
    sibling = canonical_reward_memory_actor_peer_id(
        goal_id="finance-research-goal",
        agent_id="reviewer",
    )

    assert first == repeated
    assert len({first, second, sibling}) == 3
    assert ":" not in first and "+" not in first and "/" not in first


def test_v1_omitted_automation_defaults_new_enablement_to_automatic(
    tmp_path: Path,
) -> None:
    raw = _v1_config()
    raw.pop("automation")
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_reward_memory_experiment_config(
        project=tmp_path,
        config_path="experiment.json",
    )

    assert config["automation"] == {
        "automatic_recall": True,
        "automatic_ingest": True,
        "fail_open": True,
    }
    assert config["automation_intent"] == {
        "automatic_recall": "default_enabled_new_config",
        "automatic_ingest": "default_enabled_new_config",
        "fail_open": "default",
    }


def test_v1_explicit_automation_disable_is_preserved(tmp_path: Path) -> None:
    raw = _v1_config()
    raw["automation"] = {
        "automatic_recall": False,
        "automatic_ingest": False,
        "fail_open": True,
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_reward_memory_experiment_config(
        project=tmp_path,
        config_path="experiment.json",
    )

    assert config["automation"]["automatic_recall"] is False
    assert config["automation"]["automatic_ingest"] is False
    assert config["automation_intent"] == {
        "automatic_recall": "explicit",
        "automatic_ingest": "explicit",
        "fail_open": "explicit",
    }


def test_private_goal_agent_scope_rejects_session_partition(tmp_path: Path) -> None:
    raw = _private_v1_config(goal_id="goal", agent_id="pilot")
    for entry in raw["corpora"]:
        entry["corpus"]["scope"]["session_ref"] = "session:temporary"
        entry["standing_policy"]["scope"]["session_ref"] = "session:temporary"
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_reward_memory_experiment_config(
        project=tmp_path,
        config_path="experiment.json",
    )

    with pytest.raises(ValueError, match="cannot be session-scoped"):
        validate_reward_memory_goal_agent_scope(
            config,
            goal_id="goal",
            agent_id="pilot",
        )


def test_private_scope_binds_exact_goal_scoped_agent(tmp_path: Path) -> None:
    goal_id = "reward-memory-goal"
    agent_id = "pilot"
    project = tmp_path / "project"
    path = project / ".loopx/config/reward-memory/private.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_private_v1_config(goal_id=goal_id, agent_id=agent_id)),
        encoding="utf-8",
    )
    config = load_reward_memory_experiment_config(
        project=project,
        config_path=".loopx/config/reward-memory/private.json",
    )

    scope = validate_reward_memory_goal_agent_scope(
        config,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    assert scope["isolation_mode"] == "goal_scoped_agent_private"
    assert scope["actor_peer_id"] == canonical_reward_memory_actor_peer_id(
        goal_id=goal_id,
        agent_id=agent_id,
    )
    with pytest.raises(ValueError):
        validate_reward_memory_goal_agent_scope(
            config,
            goal_id="another-goal",
            agent_id=agent_id,
        )
    with pytest.raises(ValueError):
        validate_reward_memory_goal_agent_scope(
            config,
            goal_id=goal_id,
            agent_id="meta",
        )


def test_one_private_config_cannot_enable_multiple_goal_agents(
    tmp_path: Path,
) -> None:
    goal_id = "reward-memory-goal"
    project = tmp_path / "project"
    path = project / ".loopx/config/reward-memory/private.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_private_v1_config(goal_id=goal_id, agent_id="pilot")),
        encoding="utf-8",
    )
    config = load_reward_memory_experiment_config(
        project=project,
        config_path=".loopx/config/reward-memory/private.json",
    )

    with pytest.raises(ValueError, match="exactly one Goal-scoped Agent"):
        preflight_reward_memory_experiment_config(
            config,
            goal_id=goal_id,
            agent_ids=["pilot", "meta"],
            observed_at="2026-01-01T00:00:00Z",
            execute=False,
            provider=_EnablementProvider(),
        )


def test_configure_goal_requires_write_preflight_and_persists_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id = "reward-memory-goal"
    agent_id = "pilot"
    project = tmp_path / "project"
    config_path = project / ".loopx/config/reward-memory/private.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(_private_v1_config(goal_id=goal_id, agent_id=agent_id)),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": goal_id,
                        "repo": str(project),
                        "coordination": {
                            "registered_agents": [agent_id, "meta"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = _EnablementProvider()
    monkeypatch.setattr(
        "loopx.capabilities.reward_memory.experiment.build_context_provider",
        lambda _config: provider,
    )

    preview = configure_goal(
        registry_path=registry_path,
        goal_id=goal_id,
        reward_memory_config=".loopx/config/reward-memory/private.json",
        reward_memory_agents=[agent_id],
        execute=False,
    )
    assert preview["ok"] is True
    assert preview["written"] is False
    assert preview["reward_memory_enablement_preflight"]["status"] == (
        "ready_for_apply"
    )
    assert provider.preview_calls == 3
    assert provider.write_calls == 0

    applied = configure_goal(
        registry_path=registry_path,
        goal_id=goal_id,
        reward_memory_config=".loopx/config/reward-memory/private.json",
        reward_memory_agents=[agent_id],
        execute=True,
    )
    assert applied["written"] is True
    assert provider.write_calls == 3
    policy = json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0][
        "control_plane"
    ]["reward_memory"]
    assert policy["config_digest"].startswith("sha256:")
    assert policy["automation"] == {
        "automatic_recall": True,
        "automatic_ingest": True,
        "fail_open": True,
    }
    receipt = policy["enablement_receipts"][agent_id]
    assert receipt["status"] == "verified"
    assert receipt["writability_verified"] is True
    assert receipt["exact_readback_verified"] is True
    assert receipt["actor_binding_verified"] is True

    status, resolved = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    assert status["status"] == "available"
    assert status["isolation_mode"] == "goal_scoped_agent_private"
    assert resolved is not None


def test_status_is_agent_scoped_and_public_safe(tmp_path: Path) -> None:
    registry_path, _, _ = _experiment(tmp_path)

    allowed, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    denied, denied_config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="meta",
    )

    assert allowed["status"] == "available"
    assert allowed["automatic_ingest"] is False
    assert allowed["automatic_recall"] is False
    assert allowed["config_schema_version"] == ("reward_memory_experiment_config_v1")
    assert allowed["config_runtime_route"] == {
        "schema_version": "reward_memory_config_runtime_route_v0",
        "registry_source": "invoked_registry",
        "registry_role": "project-local",
        "runtime_scope": "project_runtime",
        "goal_source": "registry.goals",
        "config_source": "goal_repo_relative_config_pointer",
        "readback_status": "verified",
        "exact_readback_verified": True,
    }
    assert "config_path" not in allowed
    assert config is not None
    assert not {
        "adapter",
        "corpus",
        "standing_policy",
        "provider_binding",
    }.intersection(config)
    assert denied["status"] == "agent_not_enabled"
    assert denied_config is None


def test_split_runtime_quota_and_status_use_v1_config_readback(
    tmp_path: Path,
) -> None:
    source_registry, _, _ = _experiment(tmp_path)
    _write_v1_config(source_registry, _v1_config())
    shared_registry = tmp_path / "shared-runtime/registry.global.json"
    shared_registry.parent.mkdir()
    registry = json.loads(source_registry.read_text(encoding="utf-8"))
    registry.update(
        {
            "registry_role": "global-local",
            "common_runtime_root": str(shared_registry.parent),
        }
    )
    goal = registry["goals"][0]
    goal["source_registry"] = str(source_registry)
    goal["control_plane"]["reward_memory"].update(
        {
            "automatic_ingest": False,
            "automatic_recall": False,
        }
    )
    shared_registry.write_text(json.dumps(registry), encoding="utf-8")

    todo = quota_todo_item(
        todo_id="todo_reward_memory_projection",
        text="[P1] Project the configured Reward Memory automation policy.",
        claimed_by="pilot",
    )
    status_payload = quota_status_payload(
        goal_id="reward-memory-goal",
        status="active",
        recommended_action=todo["text"],
        agent_todo_items=[todo],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["pilot", "meta"],
        },
    )
    status_payload["registry"] = str(shared_registry)

    guard = build_quota_should_run(
        status_payload,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    projected = guard["goal_boundary"]["capabilities"]["reward_memory"]
    assert projected["automatic_ingest"] is True
    assert projected["automatic_recall"] is True
    assert projected["automation_projection_source"] == (
        "reward_memory_experiment_status_v1"
    )
    assert projected["config_runtime_route"]["runtime_scope"] == "shared_runtime"
    assert projected["config_runtime_route"]["exact_readback_verified"] is True
    host_coverage = {
        item["host_id"]: item for item in projected["host_coverage"]
    }
    assert host_coverage["codex_cli_turn"] == {
        "host_id": "codex_cli_turn",
        "automatic_recall": "connected",
        "automatic_ingest": "connected_post_settlement",
    }
    assert host_coverage["codex_app_quota"]["automatic_ingest"] == (
        "connected_refresh_spend_post_settlement"
    )
    assert host_coverage["lark"]["automatic_ingest"] == "uncovered"

    attach_agent_lane_next_actions(status_payload, agent_id="pilot")
    status_projection = status_payload["attention_queue"]["items"][0][
        "agent_reward_memory"
    ]
    assert status_projection["automatic_ingest"] is True
    assert status_projection["automatic_recall"] is True
    assert (
        status_projection["config_runtime_route"] == projected["config_runtime_route"]
    )
    assert status_payload["agent_reward_memory_projection"] == {
        "schema_version": "agent_reward_memory_projection_summary_v1",
        "agent_id": "pilot",
        "attached_count": 1,
        "source": "quota.goal_boundary.capabilities.reward_memory",
    }
    markdown = render_status_markdown(status_payload)
    assert (
        "agent_reward_memory: agent=pilot status=available "
        "automatic_ingest=True automatic_recall=True "
        "isolation=explicit_shared enablement=verified "
        "writability=True runtime_scope=shared_runtime exact_readback=True"
    ) in markdown
    assert "lark:recall=status_projection_only,ingest=uncovered" in markdown


def test_registry_cannot_enable_experiment_without_explicit_marker(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["control_plane"]["reward_memory"].pop("experimental")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )

    assert status["status"] == "disabled"
    assert status["experimental"] is False
    assert config is None


def test_v0_config_is_rejected_fail_open(tmp_path: Path) -> None:
    registry_path, _, fixture_path = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    project = Path(registry["goals"][0]["repo"])
    config_path = project / ".loopx/config/reward-memory/experiment.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "reward_memory_experiment_config_v0",
                "adapter": fixture["adapter"],
                "corpus": fixture["corpus"],
                "standing_policy": fixture["standing_policy"],
                "provider_binding": fixture["provider_binding"],
            }
        ),
        encoding="utf-8",
    )

    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )

    assert status["status"] == "config_invalid"
    assert status["automatic_ingest"] is False
    assert status["automatic_recall"] is False
    assert status["fail_open"] is True
    assert config is None


def test_configured_ingest_accepts_only_compact_event_and_stays_dry_run(
    tmp_path: Path, capsys
) -> None:
    registry_path, event_path, _ = _experiment(tmp_path)

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert result == 0
    assert receipt["status"] != "planned"
    assert receipt["external_writes_performed"] is False
    assert receipt["experiment"]["available"] is True
    assert "provider_binding" not in receipt["experiment"]


def test_execute_cannot_bypass_experiment_route(tmp_path: Path, capsys) -> None:
    registry_path, _, full_fixture = _experiment(tmp_path)

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--input",
        str(full_fixture),
        "--execute",
    )

    assert result == 2
    assert receipt["status"] == "invalid_request"
    assert "requires an enabled experiment route" in receipt["error"]


def test_legacy_full_packet_remains_available_for_no_write_evaluation(
    tmp_path: Path, capsys
) -> None:
    registry_path, _, full_fixture = _experiment(tmp_path)

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--input",
        str(full_fixture),
    )

    assert result == 0
    assert receipt["status"] != "planned"
    assert receipt["external_writes_performed"] is False


def test_scoped_feedback_uses_the_shared_ingest_core(tmp_path: Path, capsys) -> None:
    registry_path, event_path, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)

    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert status["status"] == "available"
    assert status["adapter"] == "scoped_feedback"
    assert config is not None
    assert result == 0
    assert receipt["status"] != "planned"
    assert receipt["guard"]["passed"] is True
    assert receipt["adapter_schema_version"] == (
        "scoped_feedback_reward_memory_candidate_adapter_v0"
    )
    assert receipt["next_reward_memory_call"] == "explicit_function_boundary_recall"
    assert "issue_ref" not in receipt
    assert receipt["external_writes_performed"] is False


def test_configured_route_rejects_adapter_override(tmp_path: Path, capsys) -> None:
    registry_path, event_path, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    source = json.loads(event_path.read_text(encoding="utf-8"))
    source["adapter"] = "issue_fix_maintainer_feedback"
    event_path.write_text(json.dumps(source), encoding="utf-8")

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert result == 2
    assert receipt["status"] == "invalid_request"
    assert "does not match the configured route" in receipt["error"]


def test_scoped_feedback_rejects_unmodelled_event_fields(
    tmp_path: Path, capsys
) -> None:
    registry_path, event_path, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    source = json.loads(event_path.read_text(encoding="utf-8"))
    source["event"]["raw_comment"] = "not accepted"
    event_path.write_text(json.dumps(source), encoding="utf-8")

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert result == 2
    assert receipt["status"] == "invalid_request"
    assert "raw_comment" in receipt["error"]


def test_v1_uses_one_project_provider_and_explicit_surface_corpus_sets(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(registry_path, _v1_config())

    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None
    route = resolve_reward_memory_surface_config(config, "reviewer_artifact.summary")

    assert status["automatic_ingest"] is True
    assert status["automatic_recall"] is True
    assert status["provider_id"] == "openviking"
    assert status["corpus_count"] == 3
    assert route["selection"] == {
        "mode": "explicit_surface_corpus_ids",
        "global_corpus_scan": False,
        "corpus_ids": ["reviewer_policy_primary", "reviewer_policy_overlay"],
    }
    assert [item["corpus"]["corpus_id"] for item in route["recall_corpora"]] == [
        "reviewer_policy_primary",
        "reviewer_policy_overlay",
    ]
    assert route["corpus"]["corpus_id"] == "reviewer_policy_primary"
    assert route["recall_profile"]["profile_id"] == "reviewer_summary_v1"
    assert "scope_ref" not in json.dumps(status)


def test_v1_configured_ingest_selects_the_event_surface(tmp_path: Path, capsys) -> None:
    registry_path, event_path, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(registry_path, _v1_config())

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert result == 0
    assert receipt["status"] != "planned"
    assert receipt["experiment"]["automatic_ingest"] is True
    assert receipt["experiment"]["automatic_recall"] is True
    assert receipt["next_recall"]["automatic_recall"] is True
    assert receipt["experiment"]["corpus_count"] == 3
    assert "scope_ref" not in json.dumps(receipt["experiment"])


def test_v1_configured_ingest_preserves_explicit_automatic_recall_disable(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path, event_path, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    config = _v1_config()
    config["automation"]["automatic_recall"] = False
    _write_v1_config(registry_path, config)

    result, receipt = _run(
        capsys,
        registry_path,
        "reward-memory",
        "ingest-event",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--input",
        str(event_path),
    )

    assert result == 0
    assert receipt["experiment"]["automatic_recall"] is False
    assert receipt["next_recall"]["automatic_recall"] is False


@pytest.mark.parametrize(
    "dimension",
    ["class", "authority", "privacy", "freshness", "lifecycle"],
)
def test_v1_rejects_incompatible_surface_corpus_before_provider(
    tmp_path: Path,
    dimension: str,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    config = _v1_config()
    second = config["corpora"][1]
    corpus = second["corpus"]
    policy = second["standing_policy"]
    if dimension == "class":
        corpus["class_id"] = "soft_preference"
        policy["allowed_target_classes"] = ["soft_preference"]
    elif dimension == "authority":
        corpus["read_authority"] = "authority_scoped"
    elif dimension == "privacy":
        corpus["privacy"]["visibility"] = "workspace"
    elif dimension == "freshness":
        corpus["freshness"] = {"mode": "time_bound", "max_age_seconds": 300}
    else:
        corpus["lifecycle"] = {
            "state": "retired",
            "supersedes": [],
            "retirement_reason": "fixture",
        }
    _write_v1_config(registry_path, config)

    status, normalized = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )

    assert status["status"] == "config_invalid"
    assert status["automatic_ingest"] is False
    assert status["automatic_recall"] is False
    assert status["fail_open"] is True
    assert normalized is None


def test_v1_rejects_unknown_surface_and_adapter_override(tmp_path: Path) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(registry_path, _v1_config())
    _, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None

    with pytest.raises(ValueError, match="surface is not configured"):
        resolve_reward_memory_surface_config(config, "unknown.surface")
    with pytest.raises(ValueError, match="does not match the configured route"):
        resolve_reward_memory_surface_config(
            config,
            "reviewer_artifact.summary",
            adapter="issue_fix_maintainer_feedback",
        )


def test_v1_rejects_surface_not_authorized_by_selected_policy(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    config = _v1_config()
    second = config["corpora"][1]
    second["corpus"]["scope"]["surface_ids"].append("issue_fix.patch_planning")
    second["standing_policy"]["scope"]["surface_ids"] = ["issue_fix.patch_planning"]
    _write_v1_config(registry_path, config)

    status, normalized = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )

    assert status["status"] == "config_invalid"
    assert status["automatic_ingest"] is False
    assert status["automatic_recall"] is False
    assert normalized is None


def test_v1_rejects_non_fail_open_automation(tmp_path: Path) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    config = _v1_config()
    config["automation"]["fail_open"] = False
    _write_v1_config(registry_path, config)

    status, normalized = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )

    assert status["status"] == "config_invalid"
    assert status["automatic_ingest"] is False
    assert status["automatic_recall"] is False
    assert normalized is None


def _automatic_recall_context(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    route = resolve_reward_memory_surface_config(
        config,
        "reviewer_artifact.summary",
    )
    checkpoints = {
        item["corpus"]["corpus_id"]: {
            "verified": True,
            "corpus_id": item["corpus"]["corpus_id"],
            "workspace_ref": item["corpus"]["scope"]["workspace_ref"],
            "project_ref": item["corpus"]["scope"]["project_ref"],
            "surface_id": "reviewer_artifact.summary",
            "read_authority": item["corpus"]["read_authority"],
            "source_ref": item["standing_policy"]["authority_source_ref"],
        }
        for item in route["recall_corpora"]
    }
    return route, checkpoints


def _run_automatic_recall(
    config: dict[str, Any],
    provider: _RecallProvider,
    *,
    queries: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    route, checkpoints = _automatic_recall_context(config)
    scope = route["corpus"]["scope"]
    return run_reward_memory_automatic_recall_hook(
        config,
        surface_id="reviewer_artifact.summary",
        base_output={"summary": "base"},
        workspace_ref=scope["workspace_ref"],
        project_ref=scope["project_ref"],
        revision_ref="revision:abc123",
        queries=queries
        or [
            {
                "query": "Which reviewed summary policy applies?",
                "query_summary": "reviewed summary policy",
            }
        ],
        observed_at="2026-07-17T03:00:00+08:00",
        freshness_context={
            "source_truth_current": True,
            "source_revision": "revision:abc123",
        },
        conflict_state="clear",
        read_authority_checkpoints=checkpoints,
        application_id="test:automatic-recall",
        apply_memory=lambda base, items: {
            "outcome": "applied",
            "output": {"summary": "memory applied"},
            "memory_refs": [item.memory_ref for item in items],
            "reasoning_summary": "Applied exact reviewed policy.",
            "current_artifact_verified": True,
        },
        provider=provider,
    )


def test_automatic_recall_is_zero_call_when_flag_is_off(tmp_path: Path) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None
    provider = _RecallProvider()

    result = _run_automatic_recall(config, provider)

    assert result["status"] == "disabled"
    assert result["output"] == {"summary": "base"}
    assert result["telemetry"]["provider_call_count"] == 0
    assert provider.retrieve_calls == 0


def test_automatic_recall_uses_ordered_corpora_and_applies_once(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(registry_path, _v1_config())
    _, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None
    route, _ = _automatic_recall_context(config)
    overlay = route["recall_corpora"][1]
    corpus = overlay["corpus"]
    scope_ref = overlay["provider_binding"]["scope_ref"]
    active_record = {
        "schema_version": "reward_memory_active_record_v0",
        "corpus_id": corpus["corpus_id"],
        "candidate_ref": "candidate:reviewer-summary",
        "target_class": corpus["class_id"],
        "content_summary": "Reviewer-facing summaries use concise Chinese.",
        "scope": {
            **corpus["scope"],
            "revision_ref": "revision:abc123",
        },
        "lifecycle": {"state": "active"},
    }
    provider = _RecallProvider(content_by_scope={scope_ref: json.dumps(active_record)})

    result = _run_automatic_recall(config, provider)

    assert result["status"] == "applied"
    assert result["output"] == {"summary": "memory applied"}
    assert result["application"]["receipt"]["result_readback_verified"] is True
    assert result["telemetry"]["attempted_corpus_count"] == 2
    assert result["telemetry"]["provider_call_count"] == 2
    assert result["telemetry"]["result_readback_verified"] is True
    assert provider.retrieve_calls == 2


def test_automatic_recall_caps_queries_and_provider_failure_fails_open(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(registry_path, _v1_config())
    _, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None
    provider = _RecallProvider()
    two_queries = [
        {"query": "one", "query_summary": "one"},
        {"query": "two", "query_summary": "two"},
    ]

    rejected = _run_automatic_recall(config, provider, queries=two_queries)
    unavailable_provider = _RecallProvider(unavailable=True)
    unavailable = _run_automatic_recall(config, unavailable_provider)

    assert rejected["status"] == "guard_rejected"
    assert rejected["telemetry"]["provider_call_count"] == 0
    assert provider.retrieve_calls == 0
    assert unavailable["status"] == "provider_unavailable"
    assert unavailable["output"] == {"summary": "base"}
    assert unavailable["provider_failure_is_user_gate"] is False
    assert unavailable_provider.retrieve_calls == 1


def _with_reviewer_notification_surface(
    config: dict[str, object],
) -> dict[str, object]:
    result = copy.deepcopy(config)
    entries = result["corpora"]
    binding = result["project_provider_binding"]
    surfaces = result["surfaces"]
    assert isinstance(entries, list)
    assert isinstance(binding, dict)
    assert isinstance(surfaces, list)
    entry = copy.deepcopy(entries[0])
    corpus = entry["corpus"]
    policy = entry["standing_policy"]
    assert isinstance(corpus, dict)
    assert isinstance(policy, dict)
    corpus_id = "reviewer_notification_delivery_policy"
    surface_id = "reviewer_notification.before_send"
    scope_ref = f"viking://resources/reward-memory/{corpus_id}"
    corpus["corpus_id"] = corpus_id
    corpus["scope"]["surface_ids"] = [surface_id]
    corpus["provider_scope_ref_digest"] = hashlib.sha256(
        scope_ref.encode("utf-8")
    ).hexdigest()[:16]
    policy["policy_id"] = "policy:example:reviewer-notification-delivery"
    policy["scope"]["surface_ids"] = [surface_id]
    entries.append(entry)
    binding["corpus_scopes"].append({"corpus_id": corpus_id, "scope_ref": scope_ref})
    surfaces.append(
        {
            "surface_id": surface_id,
            "adapter": "issue_fix_maintainer_feedback",
            "corpus_ids": [corpus_id],
            "ingest_corpus_id": corpus_id,
            "recall_profile": {
                "profile_id": "reviewer_notification_before_send_v1",
                "mode": "function_boundary",
                "max_queries": 1,
                "limit": 2,
            },
        }
    )
    return result


def test_issue_fix_before_send_recall_applies_structured_policy_and_fails_open(
    tmp_path: Path,
) -> None:
    registry_path, _, _ = _experiment(tmp_path, SCOPED_PUBLIC_FIXTURE)
    _write_v1_config(
        registry_path,
        _with_reviewer_notification_surface(_v1_config()),
    )
    _, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id="reward-memory-goal",
        agent_id="pilot",
    )
    assert config is not None
    route = resolve_reward_memory_surface_config(
        config,
        "reviewer_notification.before_send",
    )
    corpus = route["corpus"]
    scope_ref = route["provider_binding"]["scope_ref"]
    active_record = {
        "schema_version": "reward_memory_active_record_v0",
        "corpus_id": corpus["corpus_id"],
        "candidate_ref": "candidate:reviewer-delivery-policy",
        "target_class": "hard_policy",
        "content_summary": json.dumps(
            {
                "schema_version": (
                    "issue_fix_reviewer_notification_delivery_policy_v0"
                ),
                "delivery_policy": {
                    "timezone": "Asia/Shanghai",
                    "allowed_local_time": {"start": "09:00", "end": "21:00"},
                    "outside_window": "queue_without_send",
                },
            },
            separators=(",", ":"),
        ),
        "scope": {
            **corpus["scope"],
            "revision_ref": "revision:abc123",
        },
        "lifecycle": {"state": "active"},
    }
    provider = _RecallProvider(content_by_scope={scope_ref: json.dumps(active_record)})

    applied = run_issue_fix_reviewer_notification_automatic_reward_memory(
        repo="owner/repo",
        pr_number=42,
        pr_url="https://github.com/owner/repo/pull/42",
        delivery_policy=None,
        experiment_config=config,
        revision_ref="revision:abc123",
        observed_at="2026-07-17T03:00:00+08:00",
        freshness_context={
            "source_truth_current": True,
            "source_revision": "revision:abc123",
        },
        conflict_state="clear",
        application_id="test:reviewer-notification:before-send",
        provider=provider,
    )
    unavailable = run_issue_fix_reviewer_notification_automatic_reward_memory(
        repo="owner/repo",
        pr_number=42,
        pr_url="https://github.com/owner/repo/pull/42",
        delivery_policy=None,
        experiment_config=config,
        revision_ref="revision:abc123",
        observed_at="2026-07-17T03:00:00+08:00",
        freshness_context={
            "source_truth_current": True,
            "source_revision": "revision:abc123",
        },
        conflict_state="clear",
        application_id="test:reviewer-notification:provider-unavailable",
        provider=_RecallProvider(unavailable=True),
    )

    assert applied["before_send_gate"]["passed"] is True
    assert applied["before_send_gate"]["delivery_policy"] == {
        "timezone": "Asia/Shanghai",
        "allowed_local_time": {"start": "09:00", "end": "21:00"},
        "outside_window": "queue_without_send",
    }
    assert applied["application"]["receipt"]["result_readback_verified"] is True
    assert applied["telemetry"]["provider_call_count"] == 1
    assert unavailable["before_send_gate"]["status"] == "fail_open"
    assert unavailable["decision"]["delivery_policy"] is None
    assert unavailable["provider_failure_is_user_gate"] is False
