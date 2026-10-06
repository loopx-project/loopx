from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from loopx.capabilities.material_lifecycle import (
    MaterialAuthoritySnapshot,
    MaterialAuthorityTransition,
    MaterialCandidateAppendReconciliation,
    MaterialCandidateReadback,
    MaterialStagedCandidate,
    apply_material_candidate_intake,
    build_material_candidate_intake_proposal,
    rollback_material_candidate_intake,
)

OBSERVED_AT = "2026-07-26T15:20:00+00:00"
CONTENT = b"source-backed exact-read candidate"
CONTENT_DIGEST = (
    "sha256:4ca463c233aa746326b7725f447c54f264d45380db12f44f42f2a0f9e1c96ec0"
)


def proposal() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        build_material_candidate_intake_proposal(
            goal_id="goal:materials",
            proposal_id="proposal:candidate-43",
            store_id="store:materials",
            source_authority_revision="revision:42",
            material_ref="material:A43",
            source_ref="source:paper-43",
            source_revision="source-revision:7",
            exact_read_ref="exact-read:paper-43",
            content_digest=CONTENT_DIGEST,
            content_size_bytes=len(CONTENT),
            observed_at=OBSERVED_AT,
        ),
    )


class FakeCandidateProvider:
    provider_id = "private-material-store"

    def __init__(self) -> None:
        self.authority_revision = "revision:42"
        self.revisions: dict[str, dict[str, MaterialCandidateReadback]] = {
            "revision:42": {}
        }
        self.calls: list[str] = []
        self.reconciliation_override: MaterialCandidateAppendReconciliation | None = (
            None
        )

    def inspect_authority(
        self,
        *,
        store_id: str,
        observed_at: str,
    ) -> MaterialAuthoritySnapshot:
        self.calls.append("inspect")
        return MaterialAuthoritySnapshot(
            provider_id=self.provider_id,
            store_id=store_id,
            authority_revision=self.authority_revision,
            observed_at=observed_at,
        )

    def read_candidate(
        self,
        *,
        store_id: str,
        authority_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateReadback | None:
        self.calls.append(f"read:{authority_revision}")
        return self.revisions.get(authority_revision, {}).get(material_ref)

    def stage_candidate(
        self,
        *,
        store_id: str,
        proposal_ref: str,
        source_revision: str,
        material_ref: str,
        source_ref: str,
        source_material_revision: str,
        exact_read_ref: str,
        content: bytes,
        owner_gate_ref: str,
        observed_at: str,
    ) -> MaterialStagedCandidate:
        self.calls.append("stage")
        target_revision = "revision:43"
        target = dict(self.revisions[source_revision])
        target[material_ref] = MaterialCandidateReadback(
            provider_id=self.provider_id,
            store_id=store_id,
            authority_revision=target_revision,
            material_ref=material_ref,
            lifecycle_state="candidate",
            source_ref=source_ref,
            source_revision=source_material_revision,
            exact_read_ref=exact_read_ref,
            candidate_record_ref="candidate-record:A43",
            content_backing_ref="content-backing:A43",
            content_digest=CONTENT_DIGEST,
            content_size_bytes=len(content),
            readback_verified=True,
        )
        self.revisions[target_revision] = target
        return MaterialStagedCandidate(
            provider_id=self.provider_id,
            store_id=store_id,
            source_revision=source_revision,
            target_revision=target_revision,
            material_ref=material_ref,
            candidate_record_ref="candidate-record:A43",
            content_backing_ref="content-backing:A43",
            content_digest=CONTENT_DIGEST,
            content_size_bytes=len(content),
            atomic_write_verified=True,
            readback_verified=True,
        )

    def reconcile_candidate_append(
        self,
        *,
        store_id: str,
        source_revision: str,
        target_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateAppendReconciliation:
        self.calls.append("reconcile")
        if self.reconciliation_override is not None:
            return self.reconciliation_override
        return MaterialCandidateAppendReconciliation(
            provider_id=self.provider_id,
            store_id=store_id,
            source_revision=source_revision,
            target_revision=target_revision,
            material_ref=material_ref,
            before_item_count=len(self.revisions[source_revision]),
            after_item_count=len(self.revisions[target_revision]),
            validation_ref="validation:append-42-43",
            stable_ids_verified=True,
            existing_records_unchanged=True,
            duplicate_material_count=0,
        )

    def switch_authority(
        self,
        *,
        store_id: str,
        expected_revision: str,
        target_revision: str,
        owner_gate_ref: str,
        observed_at: str,
    ) -> MaterialAuthorityTransition:
        self.calls.append("switch")
        if self.authority_revision != expected_revision:
            raise ValueError("provider CAS failed")
        before_revision = self.authority_revision
        self.authority_revision = target_revision
        return MaterialAuthorityTransition(
            provider_id=self.provider_id,
            store_id=store_id,
            before_revision=before_revision,
            after_revision=target_revision,
            authority_ref=f"authority:{target_revision}",
            rollback_ref=f"rollback:{before_revision}",
            atomic_write_verified=True,
            readback_verified=True,
        )


def apply(provider: FakeCandidateProvider) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        apply_material_candidate_intake(
            provider=provider,
            provider_id=provider.provider_id,
            intake_proposal=proposal(),
            content=CONTENT,
            owner_gate_ref="owner-gate:candidate-intake",
            receipt_id="receipt:candidate-intake",
            observed_at=OBSERVED_AT,
        ),
    )


def test_candidate_intake_applies_one_content_backed_append() -> None:
    provider = FakeCandidateProvider()

    receipt = apply(provider)

    assert provider.calls == [
        "inspect",
        "read:revision:42",
        "stage",
        "read:revision:43",
        "inspect",
        "reconcile",
        "switch",
        "inspect",
        "read:revision:43",
    ]
    assert provider.authority_revision == "revision:43"
    assert receipt["status"] == "applied"
    assert receipt["before_item_count"] == 0
    assert receipt["after_item_count"] == 1
    assert receipt["content_backing_verified"] is True
    assert receipt["existing_records_unchanged"] is True
    assert receipt["source_revision_cas_verified"] is True
    assert receipt["raw_content_captured"] is False
    assert "content" not in receipt


def test_candidate_intake_rejects_drift_duplicate_and_content_mismatch() -> None:
    provider = FakeCandidateProvider()
    provider.authority_revision = "revision:99"
    with pytest.raises(ValueError, match="CAS failed before staging"):
        apply(provider)
    assert provider.calls == ["inspect"]

    provider = FakeCandidateProvider()
    provider.revisions["revision:42"]["material:A43"] = MaterialCandidateReadback(
        provider_id=provider.provider_id,
        store_id="store:materials",
        authority_revision="revision:42",
        material_ref="material:A43",
        lifecycle_state="candidate",
        source_ref="source:existing",
        source_revision="source-revision:1",
        exact_read_ref="exact-read:existing",
        candidate_record_ref="candidate-record:existing",
        content_backing_ref="content-backing:existing",
        content_digest=CONTENT_DIGEST,
        content_size_bytes=len(CONTENT),
        readback_verified=True,
    )
    with pytest.raises(ValueError, match="already exists"):
        apply(provider)
    assert "stage" not in provider.calls

    provider = FakeCandidateProvider()
    with pytest.raises(ValueError, match="content digest"):
        apply_material_candidate_intake(
            provider=provider,
            provider_id=provider.provider_id,
            intake_proposal=proposal(),
            content=b"source-backed exact-read candidatE",
            owner_gate_ref="owner-gate:candidate-intake",
            receipt_id="receipt:candidate-intake",
            observed_at=OBSERVED_AT,
        )
    assert provider.calls == []


def test_candidate_intake_rechecks_cas_and_lossless_reconciliation() -> None:
    class DriftProvider(FakeCandidateProvider):
        def stage_candidate(self, **kwargs: object) -> MaterialStagedCandidate:
            staged = super().stage_candidate(**kwargs)  # type: ignore[arg-type]
            self.authority_revision = "revision:concurrent"
            return staged

    drift_provider = DriftProvider()
    with pytest.raises(ValueError, match="CAS failed after staging"):
        apply(drift_provider)
    assert "switch" not in drift_provider.calls

    count_provider = FakeCandidateProvider()
    count_provider.reconciliation_override = MaterialCandidateAppendReconciliation(
        provider_id=count_provider.provider_id,
        store_id="store:materials",
        source_revision="revision:42",
        target_revision="revision:43",
        material_ref="material:A43",
        before_item_count=0,
        after_item_count=2,
        validation_ref="validation:unsafe-append",
        stable_ids_verified=True,
        existing_records_unchanged=True,
        duplicate_material_count=0,
    )
    with pytest.raises(ValueError, match="append exactly one"):
        apply(count_provider)
    assert "switch" not in count_provider.calls

    rewrite_provider = FakeCandidateProvider()
    rewrite_provider.reconciliation_override = MaterialCandidateAppendReconciliation(
        provider_id=rewrite_provider.provider_id,
        store_id="store:materials",
        source_revision="revision:42",
        target_revision="revision:43",
        material_ref="material:A43",
        before_item_count=0,
        after_item_count=1,
        validation_ref="validation:unsafe-rewrite",
        stable_ids_verified=True,
        existing_records_unchanged=False,
        duplicate_material_count=0,
    )
    with pytest.raises(ValueError, match="existing_records_unchanged"):
        apply(rewrite_provider)
    assert "switch" not in rewrite_provider.calls


def test_candidate_intake_rollback_restores_previous_authority() -> None:
    provider = FakeCandidateProvider()
    applied = apply(provider)
    provider.calls.clear()

    receipt = rollback_material_candidate_intake(
        provider=provider,
        provider_id=provider.provider_id,
        apply_receipt=applied,
        owner_gate_ref="owner-gate:candidate-rollback",
        receipt_id="receipt:candidate-rollback",
        observed_at=OBSERVED_AT,
    )

    assert provider.calls == [
        "inspect",
        "switch",
        "inspect",
        "read:revision:42",
    ]
    assert provider.authority_revision == "revision:42"
    assert receipt["status"] == "rolled_back"
    assert receipt["candidate_removed_from_authority"] is True
    assert receipt["staged_revision_retained"] is True
    assert "material:A43" in provider.revisions["revision:43"]


def test_candidate_intake_rollback_rejects_authority_drift() -> None:
    provider = FakeCandidateProvider()
    applied = apply(provider)
    provider.authority_revision = "revision:44"
    provider.calls.clear()

    with pytest.raises(ValueError, match="rollback authority revision CAS failed"):
        rollback_material_candidate_intake(
            provider=provider,
            provider_id=provider.provider_id,
            apply_receipt=applied,
            owner_gate_ref="owner-gate:candidate-rollback",
            receipt_id="receipt:candidate-rollback",
            observed_at=OBSERVED_AT,
        )

    assert provider.calls == ["inspect"]


def test_candidate_intake_rejects_unsafe_proposal_and_readback() -> None:
    provider = FakeCandidateProvider()
    unsafe = proposal()
    unsafe["raw_content"] = "private body"
    with pytest.raises(ValueError, match="unsafe fields"):
        apply_material_candidate_intake(
            provider=provider,
            provider_id=provider.provider_id,
            intake_proposal=unsafe,
            content=CONTENT,
            owner_gate_ref="owner-gate:candidate-intake",
            receipt_id="receipt:candidate-intake",
            observed_at=OBSERVED_AT,
        )

    class UnsafeReadbackProvider(FakeCandidateProvider):
        def stage_candidate(self, **kwargs: object) -> MaterialStagedCandidate:
            staged = super().stage_candidate(**kwargs)  # type: ignore[arg-type]
            record = self.revisions["revision:43"]["material:A43"]
            self.revisions["revision:43"]["material:A43"] = replace(
                record,
                content_backing_ref="/Users/example/private-material",
            )
            return staged

    with pytest.raises(ValueError, match="local path"):
        apply(UnsafeReadbackProvider())


class AuthorizedProjectProvider(FakeCandidateProvider):
    """Source-owner fixture resolves refs; packet metadata is not permission."""

    def __init__(self):
        super().__init__()
        self.write_allowed = True
        self.revoke_after_stage = False

    def verify_project_scope(self, *, project_scope, store_id, owner_gate_ref, observed_at):
        self.calls.append('verify-project')
        return (
            self.write_allowed
            and project_scope == {
                'project_ref': 'project:alpha',
                'source_profile_ref': 'profile:materials',
                'workspace_grant_ref': 'grant:workspace-write',
            }
            and store_id == 'store:materials'
            and owner_gate_ref == 'gate:material-input'
        )

    def stage_candidate(self, **kwargs):
        result = super().stage_candidate(**kwargs)
        if self.revoke_after_stage:
            self.write_allowed = False
        return result


def project_proposal(**scope_changes):
    from loopx.capabilities.material_lifecycle import MaterialProjectScope
    scope = {
        'project_ref': 'project:alpha',
        'source_profile_ref': 'profile:materials',
        'workspace_grant_ref': 'grant:workspace-write',
        **scope_changes,
    }
    return build_material_candidate_intake_proposal(
        project_scope=MaterialProjectScope(**scope),
        proposal_id='proposal:project-input', store_id='store:materials',
        source_authority_revision='revision:42', material_ref='material:A43',
        source_ref='source:paper-43', source_revision='source-revision:7',
        exact_read_ref='exact-read:paper-43', content_digest=CONTENT_DIGEST,
        content_size_bytes=len(CONTENT), observed_at=OBSERVED_AT,
    )


def apply_project(provider, intake_proposal=None, gate='gate:material-input'):
    return apply_material_candidate_intake(
        provider=provider, provider_id=provider.provider_id,
        intake_proposal=intake_proposal or project_proposal(), content=CONTENT,
        owner_gate_ref=gate, receipt_id='receipt:project-intake', observed_at=OBSERVED_AT,
    )


def test_project_intake_and_rollback_keep_scope_without_a_goal():
    provider = AuthorizedProjectProvider()
    candidate = project_proposal()
    receipt = apply_project(provider, candidate)
    assert 'goal_id' not in candidate and 'goal_id' not in receipt
    assert receipt['project_scope'] == candidate['project_scope']
    assert receipt['capability']['scope'] == 'project'
    assert provider.calls.count('verify-project') == 2
    restored = rollback_material_candidate_intake(
        provider=provider, provider_id=provider.provider_id, apply_receipt=receipt,
        owner_gate_ref='gate:material-input', receipt_id='receipt:project-rollback',
        observed_at=OBSERVED_AT,
    )
    assert restored['project_scope'] == candidate['project_scope']
    assert 'goal_id' not in restored
    assert restored['capability']['scope'] == 'project'
    assert provider.authority_revision == 'revision:42'


def test_project_intake_requires_a_source_verifier_before_provider_access():
    provider = FakeCandidateProvider()
    with pytest.raises(ValueError, match='source authorization verifier'):
        apply_project(provider)
    assert provider.calls == []


@pytest.mark.parametrize('field,value', [
    ('project_ref', 'project:other'),
    ('source_profile_ref', 'profile:other'),
    ('workspace_grant_ref', 'grant:read-only'),
])
def test_project_scope_selectors_cannot_borrow_other_authority(field, value):
    provider = AuthorizedProjectProvider()
    with pytest.raises(ValueError, match='authorization was not verified'):
        apply_project(provider, project_proposal(**{field: value}))
    assert provider.calls == ['verify-project']
    assert provider.authority_revision == 'revision:42'


def test_project_gate_must_match_the_exact_source_operation():
    provider = AuthorizedProjectProvider()
    with pytest.raises(ValueError, match='authorization was not verified'):
        apply_project(provider, gate='gate:unrelated')
    assert provider.calls == ['verify-project']


def test_project_revocation_after_staging_blocks_authority_cutover():
    provider = AuthorizedProjectProvider()
    provider.revoke_after_stage = True
    with pytest.raises(ValueError, match='authorization was not verified'):
        apply_project(provider)
    assert provider.authority_revision == 'revision:42'
    assert provider.calls.count('verify-project') == 2
    assert 'switch' not in provider.calls


def test_revoked_project_scope_blocks_rollback_before_provider_access():
    provider = AuthorizedProjectProvider()
    receipt = apply_project(provider)
    provider.write_allowed = False
    provider.calls.clear()
    with pytest.raises(ValueError, match='authorization was not verified'):
        rollback_material_candidate_intake(
            provider=provider, provider_id=provider.provider_id, apply_receipt=receipt,
            owner_gate_ref='gate:material-input', receipt_id='receipt:project-rollback',
            observed_at=OBSERVED_AT,
        )
    assert provider.calls == ['verify-project']
    assert provider.authority_revision == 'revision:43'


@pytest.mark.parametrize('goal_id,scope', [
    (None, None),
    ('goal:manager', {'project_ref': 'project:alpha', 'source_profile_ref': 'profile:materials',
                      'workspace_grant_ref': 'grant:workspace-write'}),
    (None, {'project_ref': 'project:alpha'}),
    (None, {'project_ref': 'project:alpha', 'source_profile_ref': 'https://example.test/private',
            'workspace_grant_ref': 'grant:workspace-write'}),
])
def test_material_owner_requires_one_complete_public_safe_scope(goal_id, scope):
    from loopx.capabilities.material_lifecycle.ownership import material_owner_fields
    with pytest.raises((ValueError, TypeError)):
        material_owner_fields(goal_id=goal_id, project_scope=scope)


def test_project_metadata_cannot_reuse_a_goal_capability_declaration():
    provider = AuthorizedProjectProvider()
    candidate = project_proposal()
    candidate['capability']['scope'] = 'goal'
    with pytest.raises(ValueError, match='project capability scope'):
        apply_project(provider, candidate)
    assert provider.calls == []



def test_project_rollback_rejects_a_mismatched_capability_before_provider_access():
    provider = AuthorizedProjectProvider()
    receipt = apply_project(provider)
    receipt['capability']['scope'] = 'goal'
    provider.calls.clear()
    with pytest.raises(ValueError, match='project capability scope'):
        rollback_material_candidate_intake(
            provider=provider, provider_id=provider.provider_id, apply_receipt=receipt,
            owner_gate_ref='gate:material-input', receipt_id='receipt:project-rollback',
            observed_at=OBSERVED_AT,
        )
    assert provider.calls == []
    assert provider.authority_revision == 'revision:43'


def test_project_verifier_must_return_true_rather_than_a_truthy_value():
    class TruthyProvider(AuthorizedProjectProvider):
        def verify_project_scope(self, **kwargs):
            super().verify_project_scope(**kwargs)
            return 1

    provider = TruthyProvider()
    with pytest.raises(ValueError, match='authorization was not verified'):
        apply_project(provider)
    assert provider.calls == ['verify-project']
