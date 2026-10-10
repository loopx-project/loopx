from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from loopx.capabilities.material_lifecycle import (
    MaterialAuthoritySnapshot,
    MaterialAuthorityTransition,
    MaterialCandidateReadback,
    MaterialCandidateRevisionProvider,
    MaterialCandidateRevisionReconciliation,
    MaterialProjectScope,
    MaterialStagedCandidate,
    apply_material_candidate_revision,
    build_material_candidate_revision_proposal,
    rollback_material_candidate_revision,
)

STAMP = "2026-07-26T15:20:00Z"
SCOPE = MaterialProjectScope("project:notes", "profile:materials", "grant:write")
CONTENT = b"full article and exact-read source evidence"


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class FileSource:
    """Real immutable catalog/content files with an independently checked gate."""

    provider_id = "file-materials"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.authority = "revision:old"
        self.allowed = True
        self.revoke_after_stage = False
        self.race = False
        self.fault: str | None = None
        self.switches = 0
        old = self.backing(b"short preview")
        peer = self.backing(b"unrelated material")
        records = {
            ref: {
                "material_ref": ref,
                "lifecycle_state": "candidate",
                "source_ref": "source:article",
                "source_revision": "source:old",
                "exact_read_ref": "read:preview",
                "candidate_record_ref": "record:old",
                "content_backing_ref": backing,
                "content_digest": digest((root / backing).read_bytes()),
                "content_size_bytes": len((root / backing).read_bytes()),
            }
            for ref, backing in (("material:article", old), ("material:peer", peer))
        }
        self.save(
            self.authority,
            {"records": records, "rankings": ["material:peer", "material:article"]},
        )

    def backing(self, content: bytes) -> str:
        name = hashlib.sha256(content).hexdigest() + ".bin"
        (self.root / name).write_bytes(content)
        return name

    def save(self, revision: str, catalog: dict[str, Any]) -> None:
        (self.root / (revision.replace(":", "-") + ".json")).write_text(
            json.dumps(catalog)
        )

    def load(self, revision: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(
                (self.root / (revision.replace(":", "-") + ".json")).read_text()
            ),
        )

    def verify_project_scope(self, **kwargs: Any) -> bool:
        return (
            self.allowed
            and kwargs["project_scope"]
            == {
                "project_ref": SCOPE.project_ref,
                "source_profile_ref": SCOPE.source_profile_ref,
                "workspace_grant_ref": SCOPE.workspace_grant_ref,
            }
            and kwargs["owner_gate_ref"] == "gate:current"
            and kwargs["store_id"] == "store:materials"
        )

    def inspect_authority(
        self, *, store_id: str, observed_at: str
    ) -> MaterialAuthoritySnapshot:
        return MaterialAuthoritySnapshot(
            self.provider_id, store_id, self.authority, observed_at
        )

    def read_candidate(
        self,
        *,
        store_id: str,
        authority_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateReadback | None:
        record = self.load(authority_revision)["records"].get(material_ref)
        if record is None:
            return None
        content = (self.root / record["content_backing_ref"]).read_bytes()
        assert digest(content) == record["content_digest"]
        assert len(content) == record["content_size_bytes"]
        return MaterialCandidateReadback(
            self.provider_id,
            store_id,
            authority_revision,
            **record,
            readback_verified=True,
        )

    def stage_candidate_revision(self, **kwargs: Any) -> MaterialStagedCandidate:
        assert self.verify_project_scope(
            project_scope={
                "project_ref": SCOPE.project_ref,
                "source_profile_ref": SCOPE.source_profile_ref,
                "workspace_grant_ref": SCOPE.workspace_grant_ref,
            },
            **kwargs,
        )
        source = self.load(kwargs["source_revision"])
        target = copy.deepcopy(source)
        ref = kwargs["material_ref"]
        assert (
            target["records"][ref]["candidate_record_ref"]
            == kwargs["expected_record_ref"]
        )
        body = kwargs["content"]
        backing = self.backing(body)
        target["records"][ref].update(
            candidate_record_ref=kwargs["candidate_record_ref"],
            content_backing_ref=backing,
            source_ref=kwargs["source_ref"],
            source_revision=kwargs["source_material_revision"],
            exact_read_ref=kwargs["exact_read_ref"],
            content_digest=digest(body),
            content_size_bytes=len(body),
        )
        if self.fault == "peer":
            target["records"]["material:peer"]["source_revision"] = "source:corrupted"
        if self.fault == "rank":
            target["rankings"].reverse()
        if self.fault == "membership":
            target["records"].pop("material:peer")
        self.save("revision:new", target)
        if self.race:
            self.authority = "revision:competitor"
        if self.revoke_after_stage:
            self.allowed = False
        return MaterialStagedCandidate(
            self.provider_id,
            kwargs["store_id"],
            kwargs["source_revision"],
            "revision:new",
            ref,
            kwargs["candidate_record_ref"],
            backing,
            digest(body),
            len(body),
            True,
            True,
        )

    def reconcile_candidate_revision(
        self, **kwargs: Any
    ) -> MaterialCandidateRevisionReconciliation:
        before = self.load(kwargs["source_revision"])
        after = self.load(kwargs["target_revision"])
        ref = kwargs["material_ref"]
        return MaterialCandidateRevisionReconciliation(
            self.provider_id,
            kwargs["store_id"],
            kwargs["source_revision"],
            kwargs["target_revision"],
            ref,
            len(before["records"]),
            len(after["records"]),
            "validation:revision",
            stable_ids_verified=set(before["records"]) == set(after["records"]),
            other_records_unchanged={
                k: v for k, v in before["records"].items() if k != ref
            }
            == {k: v for k, v in after["records"].items() if k != ref},
            rankings_unchanged=before["rankings"] == after["rankings"],
            lifecycle_unchanged=before["records"][ref]["lifecycle_state"]
            == after["records"][ref]["lifecycle_state"],
            previous_content_retained=(
                self.root / before["records"][ref]["content_backing_ref"]
            ).is_file(),
        )

    def switch_authority(self, **kwargs: Any) -> MaterialAuthorityTransition:
        assert self.allowed
        assert self.authority == kwargs["expected_revision"]
        self.authority = kwargs["target_revision"]
        self.switches += 1
        return MaterialAuthorityTransition(
            self.provider_id,
            kwargs["store_id"],
            kwargs["expected_revision"],
            kwargs["target_revision"],
            "authority:materials",
            "rollback:old",
            True,
            True,
        )


def proposal(**updates: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "project_scope": SCOPE,
        "proposal_id": "proposal:revise",
        "store_id": "store:materials",
        "source_authority_revision": "revision:old",
        "material_ref": "material:article",
        "expected_record_ref": "record:old",
        "candidate_record_ref": "record:full-text",
        "source_ref": "source:article",
        "source_revision": "source:full",
        "exact_read_ref": "read:full",
        "content_digest": digest(CONTENT),
        "content_size_bytes": len(CONTENT),
        "observed_at": STAMP,
    }
    return build_material_candidate_revision_proposal(**{**args, **updates})


def apply(
    source: FileSource, packet: dict[str, Any] | None = None, content: bytes = CONTENT
) -> dict[str, Any]:
    return apply_material_candidate_revision(
        provider=cast(MaterialCandidateRevisionProvider, source),
        provider_id=source.provider_id,
        revision_proposal=packet or proposal(),
        content=content,
        owner_gate_ref="gate:current",
        receipt_id="receipt:revision",
        observed_at=STAMP,
    )


def test_revision_and_rollback_keep_identity_order_and_original_bytes(
    tmp_path: Path,
) -> None:
    source = FileSource(tmp_path)
    original = source.load("revision:old")
    receipt = apply(source)
    current = source.load(source.authority)
    assert set(current["records"]) == set(original["records"])
    assert current["rankings"] == original["rankings"]
    assert current["records"]["material:peer"] == original["records"]["material:peer"]
    assert (
        tmp_path / current["records"]["material:article"]["content_backing_ref"]
    ).read_bytes() == CONTENT
    assert source.load("revision:old") == original
    restored = rollback_material_candidate_revision(
        provider=cast(MaterialCandidateRevisionProvider, source),
        provider_id=source.provider_id,
        apply_receipt=receipt,
        owner_gate_ref="gate:current",
        receipt_id="receipt:restore",
        observed_at=STAMP,
    )
    assert restored["previous_content_restored"]
    assert source.authority == "revision:old"
    assert source.load("revision:new") == current
    assert source.load(source.authority) == original
    assert "full article" not in json.dumps(receipt)
    assert str(tmp_path) not in json.dumps(receipt)


@pytest.mark.parametrize("fault", ["peer", "rank", "membership"])
def test_invalid_revision_never_switches_authority(tmp_path: Path, fault: str) -> None:
    source = FileSource(tmp_path)
    source.fault = fault
    with pytest.raises(ValueError):
        apply(source)
    assert source.authority == "revision:old"
    assert source.switches == 0


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("source_ref", "https://example.invalid/private-source"),
        ("source_revision", "/private/source-revision"),
        ("exact_read_ref", "https://example.invalid/private-read"),
        ("content_digest", "https://example.invalid/private-digest"),
        ("candidate_record_ref", "/private/record"),
        ("content_backing_ref", "/private/content"),
        ("content_size_bytes", 0),
        ("content_size_bytes", -1),
        ("content_size_bytes", True),
        ("content_size_bytes", 1.25),
        ("content_size_bytes", "13"),
    ],
)
def test_invalid_previous_snapshot_is_rejected_before_any_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    unsafe_value: Any,
) -> None:
    source = FileSource(tmp_path)
    original = source.load("revision:old")
    before_files = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    read_candidate = source.read_candidate
    stage_candidate = source.stage_candidate_revision
    stages = 0

    def unsafe_previous(**kwargs: Any) -> MaterialCandidateReadback | None:
        value = read_candidate(**kwargs)
        if value is not None and kwargs["authority_revision"] == "revision:old":
            return replace(value, **{field: unsafe_value})
        return value

    def stage(**kwargs: Any) -> MaterialStagedCandidate:
        nonlocal stages
        stages += 1
        return stage_candidate(**kwargs)

    monkeypatch.setattr(source, "read_candidate", unsafe_previous)
    monkeypatch.setattr(source, "stage_candidate_revision", stage)
    with pytest.raises(ValueError):
        apply(source)
    assert stages == 0
    assert source.switches == 0
    assert source.authority == "revision:old"
    assert source.load(source.authority) == original
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before_files


@pytest.mark.parametrize("phase", ["before", "after_stage"])
def test_current_project_grant_is_checked_before_every_write(
    tmp_path: Path, phase: str
) -> None:
    source = FileSource(tmp_path)
    source.allowed = phase != "before"
    source.revoke_after_stage = phase == "after_stage"
    with pytest.raises(ValueError, match="authorization"):
        apply(source)
    assert source.authority == "revision:old"
    assert source.switches == 0


def test_stale_record_and_authority_conflicts_do_not_publish(tmp_path: Path) -> None:
    source = FileSource(tmp_path)
    with pytest.raises(ValueError, match="previous record CAS"):
        apply(source, proposal(expected_record_ref="record:stale"))
    source.race = True
    with pytest.raises(ValueError, match="authority CAS"):
        apply(source)
    assert source.authority == "revision:competitor"
    assert source.switches == 0


def test_archive_requires_explicit_lifecycle_action(tmp_path: Path) -> None:
    source = FileSource(tmp_path)
    catalog = source.load("revision:old")
    catalog["records"]["material:article"]["lifecycle_state"] = "archive"
    source.save("revision:old", catalog)
    with pytest.raises(ValueError, match="reactivate"):
        apply(source)
    assert source.switches == 0


def test_proposal_and_content_tampering_are_rejected_before_staging(
    tmp_path: Path,
) -> None:
    source = FileSource(tmp_path)
    packet = proposal()
    packet["material_ref"] = "material:peer"
    with pytest.raises(ValueError, match="digest"):
        apply(source, packet)
    with pytest.raises(ValueError, match="content does not match"):
        apply(source, content=b"other bytes")
    assert not (tmp_path / "revision-new.json").exists()


def test_rollback_refuses_later_writes_and_tampered_receipts(tmp_path: Path) -> None:
    source = FileSource(tmp_path)
    receipt = apply(source)
    source.authority = "revision:later"
    for packet in (receipt, {**receipt, "before_revision": "revision:unrelated"}):
        with pytest.raises(ValueError):
            rollback_material_candidate_revision(
                provider=cast(MaterialCandidateRevisionProvider, source),
                provider_id=source.provider_id,
                apply_receipt=packet,
                owner_gate_ref="gate:current",
                receipt_id="receipt:restore",
                observed_at=STAMP,
            )
    assert source.authority == "revision:later"
    assert source.switches == 1


def test_rollback_rechecks_grant_before_reading_history(tmp_path: Path) -> None:
    source = FileSource(tmp_path)
    receipt = apply(source)
    source.allowed = False
    with pytest.raises(ValueError, match="authorization"):
        rollback_material_candidate_revision(
            provider=cast(MaterialCandidateRevisionProvider, source),
            provider_id=source.provider_id,
            apply_receipt=receipt,
            owner_gate_ref="gate:current",
            receipt_id="receipt:restore",
            observed_at=STAMP,
        )
    assert source.authority == "revision:new"
    assert source.switches == 1


def test_rollback_checks_peers_even_with_recomputed_receipt_digest(
    tmp_path: Path,
) -> None:
    from loopx.capabilities.material_lifecycle._validation import packet_ref

    source = FileSource(tmp_path)
    receipt = apply(source)
    unrelated = source.load("revision:old")
    unrelated["records"]["material:peer"]["source_revision"] = "source:unrelated"
    source.save("revision:unrelated", unrelated)
    packet = {k: v for k, v in receipt.items() if k != "receipt_ref"}
    packet["before_revision"] = "revision:unrelated"
    packet["receipt_ref"] = packet_ref("material-candidate-revision-apply", packet)
    with pytest.raises(ValueError, match="other_records_unchanged"):
        rollback_material_candidate_revision(
            provider=cast(MaterialCandidateRevisionProvider, source),
            provider_id=source.provider_id,
            apply_receipt=packet,
            owner_gate_ref="gate:current",
            receipt_id="receipt:restore",
            observed_at=STAMP,
        )
    assert source.authority == "revision:new"
    assert source.switches == 1
