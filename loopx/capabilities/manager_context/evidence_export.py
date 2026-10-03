"""CLI wire projection reusing the manager's existing Core providers."""

from pathlib import Path


def export_page(registry_path, runtime_root_arg, args):
    from ...paths import resolve_runtime_root
    from ...chat_manager_context import manager_turn_context
    from ...history import load_registry
    from .inspection import ManagerInspection, TOOL_NAME

    registry = load_registry(Path(registry_path))
    root = resolve_runtime_root(registry, runtime_root_arg, registry_path=registry_path)
    ids = args.portfolio_goal_ids
    if getattr(args, "context_todo_id", None) is not None and args.manager_view != "todos":
        raise ValueError("--todo-id is only valid for --manager-view todos")
    if not 1 <= args.limit <= 12 or not 1 <= args.days <= 90 or args.offset < 0:
        raise ValueError("invalid evidence bounds")
    if args.manager_view not in {"portfolio", "agents"} and (not ids or len(ids) != 1):
        raise ValueError("one exact Goal required for details")
    if args.manager_view == "agents":
        from .discovery import agent_page
        if not isinstance(args.query, str) or len(args.query) > 200:
            raise ValueError("invalid discovery query")
        return {**agent_page(Path(registry_path), goal_ids=ids, query=args.query,
                            include_stopped=args.include_stopped, offset=args.offset, limit=args.limit),
                "schema_version": "manager_evidence_page_v1"}
    if args.manager_view == "portfolio":
        # Local CLI authority chooses the scope before collection; all exported
        # fields use the same audience-safe projection as the manager.
        session = {"channel_id": "manager" if ids is None else "manager.export"}
        context = manager_turn_context(
            registry_path, session, root, authorized_goal_ids=ids, include_details=False
        )
    else:
        available = {g.get("id") for g in registry.get("goals", [])}
        context = {"goals": [{"goal_id": g} for g in ids if g in available]}
    inspector = ManagerInspection(
        context=context,
        registry_path=registry_path,
        runtime_root=root,
        owner_scope=False,
        scope_valid=lambda: True,
        record=lambda _: None,
    )
    query = {"view": args.manager_view, "offset": args.offset, "limit": args.limit}
    if args.manager_view == "portfolio":
        query["include_stopped"] = args.include_stopped
    else:
        query["goal_id"] = ids[0]
    if args.manager_view == "deliveries":
        query["days"] = args.days
    if getattr(args, "context_todo_id", None) is not None:
        query["todo_id"] = args.context_todo_id
    result = inspector.read(TOOL_NAME, query)
    for row in result.get("rows", []):
        row.setdefault("goal_id", query.get("goal_id"))
    result["schema_version"] = "manager_evidence_page_v1"
    return result
