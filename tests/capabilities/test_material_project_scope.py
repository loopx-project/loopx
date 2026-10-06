"""Project material packets keep source ownership without a synthetic Goal."""
from loopx.capabilities.material_lifecycle import (
    MaterialProjectScope,
    build_material_intake_ranking_settlement,
    build_material_readable_projection,
    build_material_rerank_apply_receipt,
    build_material_rerank_proposal,
    build_material_store_inventory,
)

SCOPE = MaterialProjectScope('project:alpha', 'profile:materials', 'grant:workspace-write')
NOW = '2026-10-04T08:30:00+00:00'


def test_project_inventory_ranking_projection_and_settlement_share_owner():
    inventory = build_material_store_inventory(
        project_scope=SCOPE, store_id='store:materials', store_revision='revision:42',
        observed_at=NOW, source_snapshot_ref='snapshot:42', backup_ref='backup:42',
        source_digest='digest:42', lifecycle_counts={}, stable_ids_verified=True,
        backup_verified=True,
    )
    assert inventory['item_count'] == 0
    proposal = build_material_rerank_proposal(
        project_scope=SCOPE, proposal_id='proposal:rank', inventory_ref=inventory['inventory_ref'],
        decision_evidence_ref='decision:project-priorities', observed_at=NOW,
        target_window_size=30, max_moved_items=3, max_rank_displacement=3,
        no_change_reason='initial project policy is unchanged',
    )
    ranking = build_material_rerank_apply_receipt(
        project_scope=SCOPE, receipt_id='receipt:ranking', proposal_ref=proposal['proposal_ref'],
        observed_at=NOW, status='applied', before_revision='revision:43', after_revision='revision:44',
        owner_gate_ref='gate:material-input', validation_ref='validation:ranking',
        applied_material_refs=['material:one'], rollback_ref='rollback:ranking',
    )
    rendered, projection = build_material_readable_projection(
        project_scope=SCOPE, projection_id='projection:current', authority_revision='revision:44',
        observed_at=NOW, entries=[{
            'entry_ref': 'entry:one', 'rank': 1, 'title': 'One source', 'stage': 'candidate',
            'judgment': 'Current project relevance', 'action': 'Read the source',
            'materials': [{'material_ref': 'material:one', 'title': 'One source',
                           'summary': 'Synthetic source text', 'judgment': 'Project relevance',
                           'action': 'Read the source', 'audit_ref': 'audit:one',
                           'source_status': 'Exact read completed'}],
        }], expected_material_refs=['material:one'], canonical_item_count=1, top_window_size=1,
    )
    settlement = build_material_intake_ranking_settlement(
        project_scope=SCOPE, settlement_id='settlement:one', material_ref='material:one',
        observed_at=NOW, decision_evidence_ref='decision:project-priorities',
        value_classification='high_value', ranking_disposition='top_window',
        intake_before_revision='revision:42', intake_after_revision='revision:43',
        ranking_before_revision='revision:43', ranking_after_revision='revision:44',
        intake_receipt_ref='receipt:intake', ranking_receipt_ref=ranking['receipt_ref'],
        owner_gate_ref='gate:material-input', validation_ref='validation:settlement',
        top_window_size=30, target_rank=1, authority_readback_verified=True,
        ranking_source_contains_material_verified=True, ranked_membership_verified=True,
        projection_receipt_ref=projection['receipt_ref'], rollback_ref='rollback:ranking',
    )
    assert b'Synthetic source text' in rendered
    for packet in [inventory, proposal, ranking, projection, settlement]:
        assert 'goal_id' not in packet
        assert packet['project_scope'] == inventory['project_scope']
        assert packet['capability']['scope'] == 'project'
        assert packet['capability']['default_enabled'] is False
        assert packet['capability']['creates_authority'] is False
        assert packet['capability']['mutates_core_state'] is False
