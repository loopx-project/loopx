"""Bounded document IO; the presentation owner chooses and verifies the binding."""
from dataclasses import asdict
import json

_DOCUMENT = """
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.is_absolute():
    root = pathlib.Path(sys.argv[2])
    if not root.is_absolute(): raise ValueError('relative document needs its original repository')
    path = root / path
with path.open('rb') as f: data = f.read(128 * 1024 + 1)
if len(data) > 128 * 1024: raise ValueError('document too large')
print(json.dumps({'text': data.decode('utf-8')}))
"""


def read_goal_document(reader, goal_id):
    from ..control_plane.effect_runtime import effect_runtime_result

    if goal_id is None:
        raise ValueError("Choose an original Goal before reading its document")
    reader.registered_status(goal_id, fresh=True)
    source = reader.source

    before = source.read_registry_goal(goal_id)
    plan = effect_runtime_result("presentation.goal_sources.document_plan", {
        "goal_id": goal_id, "registration": before,
        "snapshot": asdict(source.goal_document_snapshot) if source.goal_document_snapshot else None,
    })
    source.check_access(goal_id)
    document = json.loads(source._execute([
        source.python, "-c", _DOCUMENT, plan["path"], plan["repo"],
    ]))
    after = source.read_registry_goal(goal_id)
    reader.registered_status(goal_id, fresh=True)
    projected = effect_runtime_result("presentation.goal_sources.document", {
        "plan": plan, "registration": after, "document": document,
    })
    return {"ok": True, "goal_id": goal_id, "source": source.describe(), **projected}
