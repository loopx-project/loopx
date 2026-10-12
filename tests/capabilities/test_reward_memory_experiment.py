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


@pytest.mark.parametrize("failure", ["drift", "missing_receipt"])
def test_enablement_repair_preserves_scope_and_cli_reason(
    tmp_path: Path, capsys, failure: str
) -> None:
    registry_path, event_path, _ = _experiment(tmp_path)
    custom_registry = tmp_path / "custom registry" / "explicit.json"
    custom_registry.parent.mkdir()
    custom_registry.write_bytes(registry_path.read_bytes())
    registry_path = custom_registry
    registry = json.loads(registry_path.read_text())
    policy = registry["goals"][0]["control_plane"]["reward_memory"]
    policy["enabled_agents"] = ["pilot", "meta"]
    if failure == "drift":
        config = tmp_path / "project/.loopx/config/reward-memory/experiment.json"
        config.write_text(config.read_text() + "\n")
    else:
        policy["enablement_receipts"] = {
            "pilot": {"config_digest": policy["config_digest"]}
        }
    registry_path.write_text(json.dumps(registry))
    before = registry_path.read_bytes()
    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path, goal_id="reward-memory-goal", agent_id="pilot"
    )
    expected = "enablement_stale" if failure == "drift" else "enablement_unverified"
    assert status["status"] == expected
    assert config is None
    repair = status["repair"]
    assert repair["preview_command"] == (
        "loopx --registry '<invoked-registry>' configure-goal --goal-id reward-memory-goal "
        "--reward-memory-agent pilot --reward-memory-agent meta"
    )
    assert repair["apply_command"] == repair["preview_command"] + " --execute"
    assert repair["automatic_apply"] is False
    assert repair["registry_context"] == "reuse_invoked_registry"
    assert repair["commands_are_templates"] is True
    assert repair["required_bindings"] == {"<invoked-registry>": "invoked_registry_path"}
    import shlex

    for key in ("preview_command", "apply_command", "verify_command"):
        argv = shlex.split(repair[key])
        assert argv[1:3] == ["--registry", "<invoked-registry>"]
        argv[2] = str(registry_path)
        assert shlex.split(shlex.join(argv))[2] == str(registry_path)
    assert policy["config_path"] not in json.dumps(repair)
    assert "viking://" not in json.dumps(repair)
    code, payload = _run(
        capsys,
        registry_path,
        "agent-turn-recall",
        "--goal-id",
        "reward-memory-goal",
        "--agent-id",
        "pilot",
        "--turn-instance-id",
        "repair-probe",
        "--quota-decision-json",
        str(event_path),
        "--execute",
    )
    assert code == 0
    assert payload["status"] == expected
    assert payload["reason_code"] == status["reason_code"]
    assert payload["experiment"]["repair"] == repair
    assert payload["provider_call_count"] == 0
    assert payload["external_writes_performed"] is False
    assert registry_path.read_bytes() == before
    verify_argv = shlex.split(repair["verify_command"])
    verify_argv[2] = str(registry_path)
    assert main(["--format", "json", *verify_argv[1:]]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == expected
    assert registry_path.read_bytes() == before


def test_enablement_repair_not_offered_when_disabled(tmp_path: Path) -> None:
    registry_path, _, _ = _experiment(tmp_path)
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
    registry_path.write_text(json.dumps(registry))
    status, config = resolve_reward_memory_experiment(
        registry_path=registry_path, goal_id="reward-memory-goal", agent_id="pilot"
    )
    assert status["status"] == "disabled"
    assert "repair" not in status
    assert config is None


def test_enablement_repair_reaches_shared_status_projection() -> None:
    from loopx.control_plane.quota.goal_boundary import goal_boundary
    from loopx.cli_commands.status import _agent_reward_memory_projection
    from loopx.presentation.renderers.reward_memory_markdown import (
        append_agent_reward_memory_markdown,
    )

    repair = {
        "preview_command": "loopx configure-goal --goal-id goal --reward-memory-agent pilot"
    }
    status = {
        "goal_id": "goal",
        "agent_id": "pilot",
        "status": "enablement_stale",
        "available": False,
        "reason_code": "config_digest_missing_or_drifted",
        "repair": repair,
    }
    boundary = goal_boundary(
        {
            "id": "goal",
            "control_plane": {
                "reward_memory": {
                    "enabled": True,
                    "experimental": True,
                    "enabled_agents": ["pilot"],
                    "config_path": ".loopx/config/private.json",
                }
            },
        },
        agent_id="pilot",
        reward_memory_experiment_status=status,
    )
    assert boundary is not None
    capability = boundary["capabilities"]["reward_memory"]
    assert capability["repair"] == repair
    projection = _agent_reward_memory_projection(
        {"goal_boundary": boundary}, agent_id="pilot"
    )
    assert projection["repair"] == repair
    lines = []
    append_agent_reward_memory_markdown(lines, {"agent_reward_memory": projection}, {})
    assert any(repair["preview_command"] in line for line in lines)


def test_catalog_distinguishes_cached_receipt_from_live_config(tmp_path: Path) -> None:
    from loopx.capabilities.reward_memory.configuration import (
        reward_memory_goal_configuration_summary,
    )

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path,
        config_automatic_ingest=True,
        policy_automatic_ingest=True,
        config_automatic_recall=True,
        policy_automatic_recall=True,
    )
    goal["control_plane"]["reward_memory"]["automation"] = {
        "automatic_recall": True,
        "automatic_ingest": True,
    }
    config = Path(goal["repo"]) / goal["control_plane"]["reward_memory"]["config_path"]
    original = config.read_bytes()
    assert (
        reward_memory_goal_configuration_summary(goal)["effective_available"] is True
    )
    config.write_bytes(original + b"\n")
    drifted = reward_memory_goal_configuration_summary(goal)
    assert drifted["enabled"] is True
    assert drifted["binding_status"] == "drifted"
    assert drifted["recorded_verified_agents"] == ["pilot"]
    assert drifted["enablement_verified_agents"] == []
    assert drifted["effective_available"] is False
    assert drifted["automatic_recall"] is False
    assert drifted["automatic_ingest"] is False
    assert drifted["desired_automation"]["automatic_recall"] is True
    assert str(config) not in json.dumps(drifted)
    from loopx.configuration_catalog import build_goal_configuration_catalog

    catalog = build_goal_configuration_catalog(
        goal_id=goal["id"],
        settings={},
        feature_summary={"reward_memory": drifted},
        default_multi_subagent_max_children=4,
        explore_harness_profiles=[],
    )
    current = next(
        f["current"] for f in catalog["features"] if f["feature_id"] == "reward_memory"
    )
    assert current["binding_status"] == "drifted"
    assert current["effective_available"] is False
    assert current["desired_automation"]["automatic_recall"] is True
    assert current["automatic_recall"] is False
    config.write_bytes(original)
    restored = reward_memory_goal_configuration_summary(goal)
    assert restored["binding_status"] == "verified"
    assert restored["effective_available"] is True
    assert restored["automatic_recall"] is True
    config.unlink()
    assert (
        reward_memory_goal_configuration_summary(goal)["binding_status"]
        == "unavailable"
    )


def test_effective_automation_requires_live_config_flags_in_catalog(
    tmp_path: Path,
) -> None:
    from loopx.capabilities.reward_memory.configuration import (
        reward_memory_goal_configuration_summary,
    )
    from loopx.configuration_catalog import build_goal_configuration_catalog

    registry_path, _, _ = _experiment(tmp_path)
    goal = json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0]
    policy = goal["control_plane"]["reward_memory"]
    live_config = json.loads(
        (Path(goal["repo"]) / policy["config_path"]).read_text(encoding="utf-8")
    )
    assert live_config["automation"]["automatic_recall"] is False
    assert live_config["automation"]["automatic_ingest"] is False
    policy["automation"] = {"automatic_recall": True, "automatic_ingest": True}

    summary = reward_memory_goal_configuration_summary(goal)
    assert summary["effective_available"] is True
    assert summary["desired_automation"] == {
        "automatic_recall": True,
        "automatic_ingest": True,
    }
    assert summary["automatic_recall"] is False
    assert summary["automatic_ingest"] is False

    catalog = build_goal_configuration_catalog(
        goal_id=goal["id"],
        settings={},
        feature_summary={"reward_memory": summary},
        default_multi_subagent_max_children=4,
        explore_harness_profiles=[],
    )
    current = next(
        feature["current"]
        for feature in catalog["features"]
        if feature["feature_id"] == "reward_memory"
    )
    assert current["effective_available"] is True
    assert current["desired_automation"]["automatic_recall"] is True
    assert current["automatic_recall"] is False
    assert current["automatic_ingest"] is False

    policy["automation"] = {"automatic_recall": False, "automatic_ingest": False}
    feature_off = reward_memory_goal_configuration_summary(goal)
    assert feature_off["effective_available"] is True
    assert feature_off["desired_automation"] == {
        "automatic_recall": False,
        "automatic_ingest": False,
    }
    assert feature_off["automatic_recall"] is False
    assert feature_off["automatic_ingest"] is False


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("goal_id", "another-goal"),
        ("agent_id", "meta"),
        ("provider_id", "different-provider"),
        ("isolation_mode", "invalid"),
    ],
)
def test_catalog_availability_uses_runtime_receipt_checks(
    tmp_path: Path, field: str, invalid: str
) -> None:
    from loopx.capabilities.reward_memory.configuration import (
        reward_memory_goal_configuration_summary,
    )

    registry_path, _, _ = _experiment(tmp_path)
    registry = json.loads(registry_path.read_text())
    goal = registry["goals"][0]
    policy = goal["control_plane"]["reward_memory"]
    policy["automation"] = {"automatic_recall": True, "automatic_ingest": True}
    policy["enablement_receipts"]["pilot"][field] = invalid
    registry_path.write_text(json.dumps(registry))
    status, _ = resolve_reward_memory_experiment(
        registry_path=registry_path, goal_id=goal["id"], agent_id="pilot"
    )
    summary = reward_memory_goal_configuration_summary(goal)
    assert status["status"] == "enablement_unverified"
    assert summary["binding_status"] == "verified"
    assert summary["effective_available"] is False
    assert summary["enablement_verified_agents"] == []
    assert summary["automatic_recall"] is False


def _configure_prompt_ingest_binding(
    registry_path: Path,
    *,
    config_automatic_ingest: bool,
    policy_automatic_ingest: bool = True,
    config_automatic_recall: bool = False,
    policy_automatic_recall: bool = False,
    receipt_status: str = "verified",
    receipt_provider_id: str = "openviking",
    drift_config_after_binding: bool = False,
    omit_receipt: bool = False,
) -> tuple[dict[str, Any], Path]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    goal = registry["goals"][0]
    goal["state_file"] = "STATE.md"
    (Path(goal["repo"]) / "STATE.md").write_text("# Synthetic Goal\n", encoding="utf-8")
    policy = goal["control_plane"]["reward_memory"]
    config_path = Path(goal["repo"]) / policy["config_path"]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["automation"]["automatic_ingest"] = config_automatic_ingest
    config["automation"]["automatic_recall"] = config_automatic_recall
    config_path.write_text(json.dumps(config), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest()
    policy["config_digest"] = digest
    policy["automation"] = {
        "automatic_recall": policy_automatic_recall,
        "automatic_ingest": policy_automatic_ingest,
        "fail_open": True,
    }
    if omit_receipt:
        policy["enablement_receipts"].pop("pilot", None)
    else:
        receipt = policy["enablement_receipts"]["pilot"]
        receipt["config_digest"] = digest
        receipt["status"] = receipt_status
        receipt["provider_id"] = receipt_provider_id
    if drift_config_after_binding:
        config_path.write_bytes(config_path.read_bytes() + b"\n")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return goal, config_path


@pytest.mark.parametrize(
    ("agent_id", "config_ingest", "policy_ingest", "receipt_status", "receipt_provider", "drift", "omit_receipt", "expected"),
    [
        ("pilot", True, True, "verified", "openviking", False, False, True),
        ("meta", True, True, "verified", "openviking", False, False, False),
        ("pilot", True, True, "pending", "openviking", False, False, False),
        ("pilot", True, True, "verified", "other-provider", False, False, False),
        ("pilot", True, True, "verified", "openviking", True, False, False),
        ("pilot", False, True, "verified", "openviking", False, False, False),
        ("pilot", True, True, "verified", "openviking", False, True, False),
    ],
)
def test_heartbeat_prompts_require_effective_agent_ingest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent_id: str,
    config_ingest: bool,
    policy_ingest: bool,
    receipt_status: str,
    receipt_provider: str,
    drift: bool,
    omit_receipt: bool,
    expected: bool,
) -> None:
    from loopx.upgrade import goal_heartbeat_prompt

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path,
        config_automatic_ingest=config_ingest,
        policy_automatic_ingest=policy_ingest,
        receipt_status=receipt_status,
        receipt_provider_id=receipt_provider,
        drift_config_after_binding=drift,
        omit_receipt=omit_receipt,
    )

    upgraded = goal_heartbeat_prompt(
        goal, cli_bin="loopx", mode="thin", agent_id=agent_id,
    )
    code = main([
        "--registry", str(registry_path),
        "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "heartbeat-prompt",
        "--goal-id", goal["id"], "--agent-id", agent_id,
        "--codex-app", "--thin",
    ])
    packet = json.loads(capsys.readouterr().out)
    assert code == 0 and packet["ok"] is True
    actual = (
        "--reward-memory-reflection-json" in upgraded["task_body"],
        "--reward-memory-reflection-json" in packet["task_body"],
    )
    assert actual == (expected, expected)


@pytest.mark.parametrize(
    ("agent_id", "expected_error"),
    [
        (None, "identity-aware peer heartbeat prompt required"),
        ("a" * 300, "public-safe token"),
        ("ghost", "not registered"),
    ],
)
def test_upgrade_reward_memory_prompt_keeps_identity_errors(
    tmp_path: Path, agent_id: str | None, expected_error: str
) -> None:
    from loopx.upgrade import goal_heartbeat_prompt

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path, config_automatic_ingest=True,
    )

    with pytest.raises(ValueError, match=expected_error):
        goal_heartbeat_prompt(goal, cli_bin="loopx", mode="thin", agent_id=agent_id)


def test_reward_memory_prompt_fails_closed_for_legacy_registration_alias(
    tmp_path: Path,
) -> None:
    from loopx.upgrade import goal_heartbeat_prompt

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path, config_automatic_ingest=True,
    )
    goal["registered_agents"] = goal["coordination"].pop("registered_agents")

    payload = goal_heartbeat_prompt(
        goal, cli_bin="loopx", mode="thin", agent_id="pilot",
    )
    assert "--reward-memory-reflection-json" not in payload["task_body"]


def test_heartbeat_prompt_admission_never_runs_provider_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.capabilities.reward_memory import experiment
    from loopx.upgrade import goal_heartbeat_prompt

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path, config_automatic_ingest=True,
    )
    monkeypatch.setattr(
        experiment,
        "preflight_reward_memory_experiment_config",
        lambda *_args, **_kwargs: pytest.fail("prompt admission must not contact provider"),
    )

    payload = goal_heartbeat_prompt(
        goal, cli_bin="loopx", mode="thin", agent_id="pilot",
    )
    assert "--reward-memory-reflection-json" in payload["task_body"]


def test_disabled_reward_memory_prompt_keeps_builder_bytes_and_skips_binding_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loopx.capabilities.reward_memory import configuration
    from loopx.upgrade import goal_heartbeat_prompt

    registry_path, _, _ = _experiment(tmp_path)
    goal, _ = _configure_prompt_ingest_binding(
        registry_path,
        config_automatic_ingest=True,
        policy_automatic_ingest=False,
    )
    monkeypatch.setattr(
        configuration,
        "resolve_goal_reward_memory_experiment",
        lambda **_kwargs: pytest.fail("disabled policy must not read the binding"),
    )

    payload = goal_heartbeat_prompt(
        goal, cli_bin="loopx", mode="thin", agent_id="pilot",
    )
    assert "--reward-memory-reflection-json" not in payload["task_body"]
    assert payload["interface_budget"]["reward_memory_headroom_chars"] == 0

    goal_without_reward_memory = json.loads(json.dumps(goal))
    goal_without_reward_memory["control_plane"].pop("reward_memory")
    without_policy = goal_heartbeat_prompt(
        goal_without_reward_memory,
        cli_bin="loopx",
        mode="thin",
        agent_id="pilot",
    )
    assert payload["task_body"] == without_policy["task_body"]
    assert payload["interface_budget"] == without_policy["interface_budget"]

    cli_argv = [
        "--registry", str(registry_path),
        "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "heartbeat-prompt",
        "--goal-id", goal["id"], "--agent-id", "pilot", "--codex-app", "--thin",
    ]
    assert main(cli_argv) == 0
    with_reward_memory = json.loads(capsys.readouterr().out)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["control_plane"].pop("reward_memory")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    assert main(cli_argv) == 0
    without_reward_memory = json.loads(capsys.readouterr().out)
    assert with_reward_memory["task_body"] == without_reward_memory["task_body"]
    assert with_reward_memory["interface_budget"] == without_reward_memory["interface_budget"]


@pytest.mark.parametrize(
    ("agent_id", "expected_error"),
    [
        (None, "identity-aware peer heartbeat prompt required"),
        ("a" * 300, "public-safe registered agent id"),
        ("ghost", "not registered"),
    ],
)
def test_cli_reward_memory_prompt_keeps_identity_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent_id: str | None,
    expected_error: str,
) -> None:
    registry_path, _, _ = _experiment(tmp_path)
    _configure_prompt_ingest_binding(
        registry_path, config_automatic_ingest=True,
    )
    argv = [
        "--registry", str(registry_path),
        "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "heartbeat-prompt",
        "--goal-id", "reward-memory-goal", "--codex-app", "--thin",
    ]
    if agent_id is not None:
        argv.extend(("--agent-id", agent_id))
    code = main(argv)
    output = capsys.readouterr().out
    assert code != 0
    assert expected_error in output
