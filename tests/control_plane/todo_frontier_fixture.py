"""Exercise the same complete-source summary producer consumed by quota/replan."""
from loopx.control_plane.todos.todo_summary import compact_todo_group


def summary_frontier_index(items):
    summary = compact_todo_group(items, role="agent", source_section="Agent Todo",
                                 text_limit=None, item_limit=1)
    return summary["advancement_frontier_revision_index"]
