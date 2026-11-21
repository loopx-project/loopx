#!/usr/bin/env python3
"""Smoke-test explicit, idempotent validated-outcome repository-memory writes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.context_providers.base import (  # noqa: E402
    ContextProviderItem,
    ContextProviderRetrieval,
    ContextProviderSync,
)
from loopx.capabilities.issue_fix.feasibility import (  # noqa: E402
    build_issue_fix_feasibility_packet,
)
from loopx.capabilities.issue_fix.outcome_projection import (  # noqa: E402
    build_issue_fix_outcome_projection,
)
from loopx.capabilities.issue_fix.repository_memory_provider import (  # noqa: E402
    retrieve_issue_fix_repository_memory,
    write_issue_fix_validated_outcome_memory,
)


def _resolve_recovery_ref(repo_root: Path) -> str | None:
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    ).stdout.strip()
    if symbolic:
        return symbolic
    candidates = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--points-at",
            "HEAD",
            "--format=%(refname)",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    ).stdout.splitlines()
    valid = [
        ref.strip()
        for ref in candidates
        if ref.strip() and 4 <= len(ref.strip()) <= 80
    ]
    for preferred in ("refs/remotes/origin/main", "refs/heads/main"):
        if preferred in valid:
            return preferred
    for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
        for ref in valid:
            if ref.startswith(prefix):
                return ref
    return None


REVISION = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=ROOT,
    text=True,
    stdout=subprocess.PIPE,
    check=True,
).stdout.strip()
RECOVERY_REF = _resolve_recovery_ref(ROOT)
OBSERVED_AT = "2026-07-11T08:40:00+08:00"


class WritebackContractProvider:
    provider_id = "contract_provider"

    def __init__(self) -> None:
        self.contents: dict[str, str] = {}

    def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
        target, content = next(iter(self.contents.items()))
        return ContextProviderRetrieval(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            query_summary=str(kwargs["query_summary"]),
            observed_at=str(kwargs["observed_at"]),
            search_performed=True,
            read_performed=True,
            requested_limit=int(kwargs["max_results"]),
            items=(
                ContextProviderItem(
                    resource_ref=target,
                    summary="Configured identity is independent of optional usage.",
                    content=content,
                    score=0.94,
                ),
            ),
        )

    def sync(self, **kwargs: Any) -> ContextProviderSync:
        assert kwargs["execute"] is True, kwargs
        source, target = list(kwargs["resources"])[0]
        content = Path(source).read_text(encoding="utf-8")
        existing = self.contents.get(target)
        if existing is not None:
            assert existing == content, (existing, content)
        else:
            self.contents[target] = content
        return ContextProviderSync(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            observed_at=str(kwargs["observed_at"]),
            requested_count=1,
            completed_count=1,
            write_count=0 if existing is not None else 1,
            result_refs=(target,),
        )


def repository_context(revision: str = REVISION) -> dict[str, object]:
    return {
        "schema_version": "issue_fix_repository_context_input_v0",
        "repository_revision": revision,
        "sources": [
            {
                "source_id": "current-source",
                "source_kind": "source_code",
                "reference": "src/worker.py",
                "trust": "authoritative",
                "freshness": "current",
                "supports": ["change_scope", "reproduction"],
                "summary": "Current checkout bounds the affected worker path.",
            },
            {
                "source_id": "focused-test",
                "source_kind": "test_surface",
                "reference": "tests/test_worker.py",
                "trust": "verified",
                "freshness": "current",
                "supports": ["validation"],
                "summary": "Focused worker regression surface.",
            },
        ],
    }


def merged_lifecycle() -> dict[str, object]:
    return {
        "ok": True,
        "schema_version": "issue_fix_pr_lifecycle_monitor_v0",
        "observation": {
            "repo": "loopx-project/loopx",
            "pr_ref": "pull_8",
            "number": 8,
            "permalink": "https://github.com/loopx-project/loopx/pull/8",
            "state": "MERGED",
            "is_draft": False,
            "checks": {
                "aggregate": "PASSING",
                "failing_count": 0,
                "pending_count": 0,
                "passing_count": 1,
            },
            "review_decision": "APPROVED",
            "merge_state_status": "CLEAN",
            "merged_at": OBSERVED_AT,
            "closed_at": OBSERVED_AT,
        },
        "transition": {
            "decision": "no_followup",
            "reason": "PR is merged; close the monitor with no follow-up.",
        },
    }


def outcome_packet(
    *,
    revision: str = REVISION,
    commit_ref: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    feasibility = build_issue_fix_feasibility_packet(
        url="https://github.com/loopx-project/loopx/issues/7",
        reproduction_status="confirmed",
        reproduction_label="focused worker reproduction",
        scope_class="bounded",
        validation_label="focused worker regression",
        repository_context_input=repository_context(revision),
    )
    delivery = {
        "schema_version": "issue_fix_delivery_evidence_input_v0",
        "outcome_status": "completed",
        "validation_status": "passed",
        "validation_label": "passed focused public contract test",
        "changed_files": ["src/worker.py", "tests/test_worker.py"],
        "commit_ref": commit_ref or revision,
        "repository_commit_evidence": {
            "schema_version": "issue_fix_repository_commit_evidence_v0",
            "status": "verified",
            "repo": "loopx-project/loopx",
            "repository_fingerprint": "sha256:" + "a" * 64,
            "repository_revision": revision,
            "declared_commit_ref": commit_ref or revision,
            "commit_oid": commit_ref or revision,
            "recovery_ref": "refs/heads/main",
            "recovery_ref_oid": revision,
            "commit_is_ancestor": True,
            "verified_at": OBSERVED_AT,
            "repo_path_captured": False,
            "remote_urls_captured": False,
            "raw_git_output_captured": False,
        },
        "outputs": [
            {
                "kind": "pull_request",
                "url": "https://github.com/loopx-project/loopx/pull/8",
            }
        ],
        "risks": ["broader integration validation was not run"],
        "recorded_at": OBSERVED_AT,
        "reusable_knowledge": {
            "schema_version": "issue_fix_repository_learning_card_input_v0",
            "confidence": "high",
            "affected_modules": [
                "loopx/capabilities/issue_fix",
                "examples",
            ],
            "invalidation_conditions": [
                "Status identity no longer combines configuration with optional usage.",
                "The cited verification references are removed or substantially rewritten.",
            ],
            "revalidation_contract": (
                "At the current checkout, inspect the cited implementation and rerun "
                "the focused empty-usage and usage-failure cases before influence."
            ),
            "current_checkout_verification_required": True,
            "symptom_signature": (
                "A configured status component disappears when runtime usage is empty."
            ),
            "reproduction_contract": (
                "Build status with a configured model and empty usage, then assert "
                "that the configured identity remains visible."
            ),
            "root_cause": (
                "The read model made configured identity conditional on optional "
                "runtime usage observations."
            ),
            "violated_invariant": (
                "Configured identity remains visible before usage; usage may enrich "
                "the status but must not gate it."
            ),
            "repair_pattern": (
                "Use configuration as the fallback identity while preferring runtime "
                "usage when it is present."
            ),
            "validation_contract": (
                "Cover empty usage and usage-read failure while retaining runtime "
                "usage precedence when observations exist."
            ),
            "applicability": (
                "Status and read-model paths that combine stable configuration with "
                "optional runtime observations."
            ),
            "non_applicability": (
                "Do not synthesize an identity when neither configuration nor a "
                "runtime observation exists."
            ),
            "verification_references": [
                "pyproject.toml",
                "LICENSE",
            ],
        },
    }
    outcome = build_issue_fix_outcome_projection(
        goal_id="public-issue-fix-goal",
        feasibility_packet=feasibility,
        pr_lifecycle_packet=merged_lifecycle(),
        delivery_evidence_input=delivery,
        agent_id="public-issue-fix-agent",
        generated_at=OBSERVED_AT,
    )
    return feasibility, delivery, outcome


def provider_config(
    provider: str = "contract_provider",
    *,
    revision: str = REVISION,
) -> dict[str, Any]:
    return {
        "schema_version": "issue_fix_repository_memory_provider_config_v0",
        "enabled": True,
        "provider": provider,
        "namespace": "public-repository",
        "visibility": "public",
        "scope_ref": f"viking://resources/public-repo/{revision}",
        "repository_revision": revision,
        "sync_timeout_seconds": 5,
        "writeback_enabled": True,
        "writeback_scope_ref": f"viking://resources/public-repo/{revision}",
        "workspace_scope": "owner-repo",
        "peer_scope": "issue-fix-agent",
    }


def assert_public_boundary(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    for forbidden in ("must never be written", "/Users/", "/private/tmp/"):
        assert forbidden not in text, (forbidden, text)


def main() -> int:
    feasibility, delivery, outcome = outcome_packet()
    provider = WritebackContractProvider()
    first = write_issue_fix_validated_outcome_memory(
        config=provider_config(),
        outcome_packet=outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )
    retry = write_issue_fix_validated_outcome_memory(
        config=provider_config(),
        outcome_packet=outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )
    assert first["status"] == "completed" and first["write_count"] == 1, first
    assert retry["status"] == "completed" and retry["write_count"] == 0, retry
    assert first["idempotency_key"] == retry["idempotency_key"]
    assert first["supersession_key_recorded"] is True
    assert first["checkout_verification"]["commit_is_ancestor"] is True, first
    assert first["checkout_verification"]["repo_path_recorded"] is False, first
    stored_fact = next(iter(provider.contents.values()))
    for expected in (
        "issue_fix_repository_learning_card_memory_v0",
        '"fact_type": "repository_learning_card"',
        '"confidence": "high"',
        '"current_checkout_verification_required": true',
        '"verification_reference_digests": {',
        '"invalidation_conditions": [',
        '"affected_modules": [',
        '"symptom_signature": "A configured status component disappears',
        '"validation_status": "passed"',
        '"freshness": "revision_pinned"',
        '"supersession_key": "sha256:',
    ):
        assert expected in stored_fact, expected
    assert first["knowledge_eligible"] is True, first
    assert "/repository-learning-cards/" in next(iter(provider.contents)), (
        provider.contents
    )
    assert_public_boundary(first)
    assert_public_boundary(retry)

    retrieved = retrieve_issue_fix_repository_memory(
        config=provider_config(),
        repo_path=ROOT,
        repository_revision=REVISION,
        query="configured status identity missing before usage",
        query_summary="Configured identity and optional usage telemetry.",
        supports=["change_scope", "validation"],
        observed_at=OBSERVED_AT,
        provider=provider,
    )
    memory = retrieved["memory_input"]
    assert memory["results"][0]["verification_status"] == "confirmed", memory
    checkout = retrieved["provider_projection"]["checkout_verification"]
    assert checkout["learning_card_count"] == 1, checkout
    assert checkout["learning_card_confirmed_count"] == 1, checkout
    candidate = retrieved["provider_projection"]["learning_cards"][0]
    assert candidate["confidence"] == "high", candidate
    assert candidate["reference_digest_match"] is True, candidate
    assert candidate["current_checkout_verification_required"] is True, candidate
    assert candidate["revalidation_contract"], candidate
    assert_public_boundary(retrieved)

    stale_provider = WritebackContractProvider()
    target, stored = next(iter(provider.contents.items()))
    marker = "```json"
    start = stored.index(marker) + len(marker)
    end = stored.index("```", start)
    stale_card = json.loads(stored[start:end].strip())
    stale_card["verification_reference_digests"]["pyproject.toml"] = (
        "sha256:" + "0" * 64
    )
    stale_provider.contents[target] = (
        stored[:start]
        + "\n"
        + json.dumps(stale_card, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
        + stored[end:]
    )
    stale = retrieve_issue_fix_repository_memory(
        config=provider_config(),
        repo_path=ROOT,
        repository_revision=REVISION,
        query="configured status identity missing before usage",
        query_summary="Configured identity and optional usage telemetry.",
        supports=["change_scope", "validation"],
        observed_at=OBSERVED_AT,
        provider=stale_provider,
    )
    assert stale["memory_input"]["results"][0]["verification_status"] == "unverified"
    assert (
        stale["provider_projection"]["learning_cards"][0]["reference_digest_match"]
        is False
    )

    audit_delivery = dict(delivery)
    audit_delivery.pop("reusable_knowledge")
    audit_outcome = build_issue_fix_outcome_projection(
        goal_id="public-issue-fix-goal",
        feasibility_packet=feasibility,
        delivery_evidence_input=audit_delivery,
        agent_id="public-issue-fix-agent",
        generated_at=OBSERVED_AT,
    )
    audit_provider = WritebackContractProvider()
    audit = write_issue_fix_validated_outcome_memory(
        config=provider_config(),
        outcome_packet=audit_outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=audit_provider,
    )
    assert audit["knowledge_eligible"] is False, audit
    assert audit["fact_type"] == "validated_issue_fix_outcome", audit
    assert "/validated-outcomes/" in next(iter(audit_provider.contents))

    legacy_delivery = json.loads(json.dumps(delivery))
    legacy_knowledge = legacy_delivery["reusable_knowledge"]
    legacy_knowledge["schema_version"] = "issue_fix_reusable_knowledge_input_v0"
    for key in (
        "confidence",
        "affected_modules",
        "invalidation_conditions",
        "revalidation_contract",
        "current_checkout_verification_required",
    ):
        legacy_knowledge.pop(key)
    legacy_outcome = build_issue_fix_outcome_projection(
        goal_id="public-issue-fix-goal",
        feasibility_packet=feasibility,
        delivery_evidence_input=legacy_delivery,
        agent_id="public-issue-fix-agent",
        generated_at=OBSERVED_AT,
    )
    legacy_provider = WritebackContractProvider()
    legacy = write_issue_fix_validated_outcome_memory(
        config=provider_config(),
        outcome_packet=legacy_outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=legacy_provider,
    )
    assert legacy["fact_type"] == "reusable_issue_fix_knowledge", legacy
    assert "/reusable-knowledge/" in next(iter(legacy_provider.contents))

    disabled = write_issue_fix_validated_outcome_memory(
        config={**provider_config(), "writeback_enabled": False},
        outcome_packet=outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )
    assert (
        disabled["status"] == "disabled" and not disabled["external_writes_performed"]
    )
    wrong_project_provider = WritebackContractProvider()
    wrong_project = write_issue_fix_validated_outcome_memory(
        config={
            **provider_config(),
            "repository_identity": "git:github.com/example/other",
        },
        outcome_packet=outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=wrong_project_provider,
    )
    assert wrong_project["status"] == "blocked", wrong_project
    assert wrong_project["reason_code"] == "repository_identity_mismatch"
    assert wrong_project["external_writes_performed"] is False
    assert wrong_project_provider.contents == {}
    assert_public_boundary(wrong_project)
    try:
        write_issue_fix_validated_outcome_memory(
            config=provider_config(),
            outcome_packet={**outcome, "raw_transcript": "must never be written"},
            repository_revision=REVISION,
            repo_path=ROOT,
            observed_at=OBSERVED_AT,
            execute=True,
            provider=provider,
        )
    except ValueError as exc:
        assert "unsafe field: raw_transcript" in str(exc), exc
    else:
        raise AssertionError("raw transcript writeback must be rejected")
    failed_outcome = json.loads(json.dumps(outcome))
    failed_outcome["issue_fix_outcomes"][0]["validation"]["status"] = "failed"
    try:
        write_issue_fix_validated_outcome_memory(
            config=provider_config(),
            outcome_packet=failed_outcome,
            repository_revision=REVISION,
            repo_path=ROOT,
            observed_at=OBSERVED_AT,
            execute=True,
            provider=provider,
        )
    except ValueError as exc:
        assert "requires passed validation" in str(exc), exc
    else:
        raise AssertionError("failed validation must block memory writeback")

    incomplete_delivery = dict(delivery)
    incomplete_delivery["reusable_knowledge"] = {
        **dict(delivery["reusable_knowledge"]),
        "root_cause": "",
    }
    try:
        build_issue_fix_outcome_projection(
            goal_id="public-issue-fix-goal",
            feasibility_packet=feasibility,
            delivery_evidence_input=incomplete_delivery,
            agent_id="public-issue-fix-agent",
            generated_at=OBSERVED_AT,
        )
    except ValueError as exc:
        assert "root_cause" in str(exc), exc
    else:
        raise AssertionError("incomplete reusable knowledge must be rejected")

    non_terminal = build_issue_fix_outcome_projection(
        goal_id="public-issue-fix-goal",
        feasibility_packet=feasibility,
        delivery_evidence_input=delivery,
        agent_id="public-issue-fix-agent",
        generated_at=OBSERVED_AT,
    )
    try:
        write_issue_fix_validated_outcome_memory(
            config=provider_config(),
            outcome_packet=non_terminal,
            repository_revision=REVISION,
            repo_path=ROOT,
            observed_at=OBSERVED_AT,
            execute=True,
            provider=WritebackContractProvider(),
        )
    except ValueError as exc:
        assert "terminal outcome" in str(exc), exc
    else:
        raise AssertionError("non-terminal learning cards must be rejected")

    missing_reference_delivery = json.loads(json.dumps(delivery))
    missing_reference_delivery["reusable_knowledge"]["verification_references"] = [
        "src/not-in-this-revision.py"
    ]
    missing_reference_outcome = build_issue_fix_outcome_projection(
        goal_id="public-issue-fix-goal",
        feasibility_packet=feasibility,
        delivery_evidence_input=missing_reference_delivery,
        agent_id="public-issue-fix-agent",
        generated_at=OBSERVED_AT,
    )
    missing_reference = write_issue_fix_validated_outcome_memory(
        config=provider_config(),
        outcome_packet=missing_reference_outcome,
        repository_revision=REVISION,
        repo_path=ROOT,
        observed_at=OBSERVED_AT,
        execute=True,
        provider=WritebackContractProvider(),
    )
    assert missing_reference["status"] == "blocked", missing_reference
    assert missing_reference["reason_code"] == (
        "knowledge_verification_reference_not_in_revision"
    ), missing_reference
    assert missing_reference["missing_reference_count"] == 1, missing_reference

    with tempfile.TemporaryDirectory(prefix="loopx-writeback-divergent-") as tmpdir:
        checkout = Path(tmpdir)
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        subprocess.run(
            ["git", "config", "user.email", "public@example.com"],
            cwd=checkout,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Public Fixture"],
            cwd=checkout,
            check=True,
        )
        (checkout / "main.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "main.txt"], cwd=checkout, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=checkout, check=True)
        main_branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=checkout,
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", "--orphan", "delivery"],
            cwd=checkout,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "rm", "-rf", "."],
            cwd=checkout,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        (checkout / "delivery.txt").write_text("delivered\n", encoding="utf-8")
        subprocess.run(["git", "add", "delivery.txt"], cwd=checkout, check=True)
        subprocess.run(["git", "commit", "-qm", "delivery"], cwd=checkout, check=True)
        divergent_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", main_branch],
            cwd=checkout,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (checkout / "main.txt").write_text("base\ncurrent\n", encoding="utf-8")
        subprocess.run(["git", "add", "main.txt"], cwd=checkout, check=True)
        subprocess.run(["git", "commit", "-qm", "current"], cwd=checkout, check=True)
        current_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        _, _, divergent_outcome = outcome_packet(
            revision=current_revision,
            commit_ref=divergent_commit,
        )
        blocked_provider = WritebackContractProvider()
        blocked = write_issue_fix_validated_outcome_memory(
            config=provider_config(revision=current_revision),
            outcome_packet=divergent_outcome,
            repository_revision=current_revision,
            repo_path=checkout,
            observed_at=OBSERVED_AT,
            execute=True,
            provider=blocked_provider,
        )
        assert blocked["ok"] is False and blocked["status"] == "blocked", blocked
        assert blocked["reason_code"] == "delivery_commit_not_in_repository_revision", (
            blocked
        )
        assert blocked["checkout_verification"]["commit_is_ancestor"] is False, blocked
        assert blocked["external_writes_performed"] is False, blocked
        assert blocked_provider.contents == {}, blocked_provider.contents
        assert_public_boundary(blocked)

    with tempfile.TemporaryDirectory(prefix="loopx-writeback-smoke-") as tmpdir:
        tmp = Path(tmpdir)
        fake_ov = tmp / "ov-contract"
        fake_ov.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "state_path = Path(__file__).with_suffix('.state')\n"
            "try: state = json.loads(state_path.read_text())\n"
            "except (FileNotFoundError, json.JSONDecodeError): state = {}\n"
            "if args == ['--version']: print('openviking 0.4.9.dev11')\n"
            "elif args and args[0] == 'status': print(json.dumps({'status':'healthy'}))\n"
            "elif args and args[0] == 'read':\n"
            "    target = args[1]; content = state.get(target)\n"
            "    print(json.dumps({'uri': target, 'content': content})) if content is not None else sys.exit(1)\n"
            "elif args and args[0] in {'tree','ls'}:\n"
            "    prefix = args[1].rstrip('/') if len(args) > 1 else ''\n"
            "    print(json.dumps({'resources': [{'uri': key} for key in state if key == prefix or key.startswith(prefix + '/')] }))\n"
            "elif args and args[0] == 'mkdir': print(json.dumps({'result':'ok'}))\n"
            "elif args and args[0] == 'add-resource':\n"
            "    target = args[args.index('--to') + 1]; state[target] = Path(args[1]).read_text(); state_path.write_text(json.dumps(state)); print(json.dumps({'result':'ok'}))\n"
            "else: sys.exit(2)\n",
            encoding="utf-8",
        )
        fake_ov.chmod(0o755)
        feasibility_path = tmp / "feasibility.json"
        lifecycle_path = tmp / "lifecycle.json"
        delivery_path = tmp / "delivery.json"
        config_path = tmp / "provider.json"
        feasibility_path.write_text(json.dumps(feasibility), encoding="utf-8")
        lifecycle_path.write_text(json.dumps(merged_lifecycle()), encoding="utf-8")
        delivery_path.write_text(json.dumps(delivery), encoding="utf-8")
        config_path.write_text(
            json.dumps(
                {
                    **provider_config("openviking"),
                    "provider_binary": str(fake_ov),
                }
            ),
            encoding="utf-8",
        )
        repo_path = ROOT
        recovery_ref = RECOVERY_REF
        if recovery_ref is None:
            # Detached HEAD with no matching ref: bind an isolated local ref without mutating ROOT.
            repo_path = tmp / "detached-recovery-repo"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(repo_path)],
                check=True,
            )
            remotes = subprocess.run(
                ["git", "remote"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.splitlines()
            for name in [item.strip() for item in remotes if item.strip()]:
                url = subprocess.run(
                    ["git", "remote", "get-url", name],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    check=True,
                ).stdout.strip()
                action = "set-url" if name == "origin" else "add"
                subprocess.run(["git", "remote", action, name, url], cwd=repo_path, check=True)
            recovery_ref = "refs/heads/smoke-recovery"
            subprocess.run(["git", "update-ref", recovery_ref, REVISION], cwd=repo_path, check=True)
        command = [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "issue-fix",
            "outcome",
            "--goal-id",
            "public-issue-fix-goal",
            "--repo",
            "loopx-project/loopx",
            "--issue-ref",
            str(feasibility["observation"]["issue_ref"]),
            "--feasibility-json",
            str(feasibility_path),
            "--pr-lifecycle-json",
            str(lifecycle_path),
            "--delivery-evidence-json",
            str(delivery_path),
            "--repository-memory-provider-json",
            str(config_path),
            "--write-repository-memory",
            "--repo-path",
            str(repo_path),
            "--repository-ref",
            recovery_ref,
            "--generated-at",
            OBSERVED_AT,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        packet = json.loads(result.stdout)
        assert packet["repository_memory_writeback"]["status"] == "completed"
        assert packet["repository_memory_writeback"]["write_count"] == 1
        assert packet["external_writes_performed"] is True
        assert packet["source_contract"]["repository_memory_writeback"] == (
            "issue_fix_validated_outcome_memory_writeback_v0"
        )
        assert packet["source_contract"]["writes_external_provider"] is True
        assert_public_boundary(packet)

    print("issue-fix-validated-memory-writeback-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
