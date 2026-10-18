from loopx.control_plane.presentation.goal_metadata import goal_parent_fields


def test_organization_is_optional_and_never_publishes_a_path_or_object():
    for value in (None, "", "/private/fixture/parent", "../parent", {"id": "parent"}):
        assert goal_parent_fields({"parent_goal_id": value}) == {}
    assert goal_parent_fields({"claimed_by": "parent", "goal_id": "parent.child"}) == {}
    assert goal_parent_fields({"parent_goal_id": "parent"}) == {"parent_goal_id": "parent"}
