"""Shared synthetic research outputs for tests and dependency-free browser fixtures."""
from hashlib import sha256
import json
from pathlib import Path

from scenario import assignments, encoded, evidence


def fixture(root: Path) -> dict:
    dependencies = {}
    for member in assignments(root):
        worker, revision = member["worker"], member["revision"]
        raw, normalized, stale, source = {
            "initial": (90, 40, False, "filing-initial"),
            "corrected": (75, 25, True, "filing-correction"),
        }[revision]
        work = root / worker / revision
        work.mkdir(parents=True, exist_ok=True)
        input_bytes = encoded(evidence(revision))
        (work / "input.json").write_bytes(input_bytes)
        output = {"revision": revision, "input_sha256": sha256(input_bytes).hexdigest(),
                  "raw_fcf": raw, "normalized_fcf": normalized, "period_comparable": False,
                  "growth_supported": False, "independent_source_families": 1, "repost_stale": stale,
                  "source_refs": [source, "prior-filing", "repost"], "reason": "Different periods; one source family."}
        if member.get("upstream"):
            previous = json.loads((root / member["upstream"] / "output.json").read_text())
            output["adopted_dependencies"] = {member["upstream"]: sha256(encoded(previous)).hexdigest()}
        (work / "output.json").write_bytes(encoded(output))
        (root / "accepted").mkdir(exist_ok=True)
        (root / "accepted" / (worker + "-" + revision + ".json")).write_bytes(encoded({"turn_status": "committed", "evidence": output}))
        dependencies[worker + "/" + revision] = sha256(encoded(output)).hexdigest()
    report = {"initial_normalized_fcf": 40, "corrected_normalized_fcf": 25, "revision_delta": -15,
              "growth_supported": False, "independent_source_families": 1,
              "repost_stale_after_correction": True, "dependencies": dependencies,
              "reason": "Correction reduces normalized cash; growth is unsupported."}
    (root / "lead").mkdir(exist_ok=True)
    (root / "lead" / "report.json").write_bytes(encoded(report))
    return report
