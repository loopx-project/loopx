"""Keep research date membership compatible with the native history reader."""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from loopx.control_plane.runtime.time import parse_timestamp


NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js runs the research reader")


def test_native_timestamp_grammar_and_research_date_policy() -> None:
    dates = ["2026-03-03", "20260303", "2026-W10-2", "2026W102"]
    clocks = ["01", "0115", "01:15", "011530", "01:15:30", "01:15:30.123456", "01:15:30,123456", "01:15.123456"]
    zones = ["Z", "z", "+02", "+0200", "+02:00", "-0200", "+02:00:30", "+02:00:30.5"]
    sources = [date + separator + clock + zone for date, separator, clock, zone in itertools.product(dates, ["T", "t", " "], clocks, zones)]
    sources += [" 2026-03-03T01:15+0200 ", "2026-03-02T23:59:59.999999Z", "2026-03-03T00:00:00Z", "2026-03-03T00:00:00+00:00:00.000001"]
    cases = []
    for source in sources:
        parsed = parse_timestamp(source)
        assert parsed is not None, source
        cases.append({"source": source, "day": parsed.date().isoformat()})
    # Intentional policy differences: native Python assumes UTC for naive dates
    # and normalizes overflowing offset components; a date-filtered view does neither.
    for source in ["2026-03-02", "2026-03-02T12", "2026-03-02T12+0260", "2026-03-02T12+02:00:60"]:
        assert parse_timestamp(source) is not None
        cases.append({"source": source, "day": None})
    for source in ["20260230T0115+0200", "1900-02-29T12Z", "2026-03-02T24+02", "2026-03-02T1260+02", "2026-03-02T120060+02", "2026-03-02T12+24", "0001-01-01T00+01", "9999-12-31T23-01"]:
        assert parse_timestamp(source) is None, source
        cases.append({"source": source, "day": None})
    program = r'''
import assert from "node:assert/strict";
import fs from "node:fs";
import {searchResearchRecords} from "./apps/presentation/dashboard/src/features/personal-workspace/research-record-search.ts";
for (const {source, day} of JSON.parse(fs.readFileSync(0, "utf8"))) {
  const row = Object.freeze({recorded_at: source, status: "", title: "", summary: "", evidence_refs: []});
  const search = range => searchResearchRecords([row], "", "", undefined, range);
  assert.deepEqual(search({}), [row], source);
  assert.equal(search({})[0], row, "Original identity and timestamp must survive");
  if (day === null) {
    assert.deepEqual(search({from: "0001-01-01", to: "9999-12-31"}), [], source);
  } else {
    assert.deepEqual(search({from: day, to: day}), [row], source);
    const adjacent = new Date(`${day}T00:00:00Z`);
    for (const delta of [-1, 1]) {
      const outside = new Date(adjacent.valueOf() + delta * 86400000).toISOString().slice(0, 10);
      assert.deepEqual(search({from: outside, to: outside}), [], source);
    }
  }
}
'''
    completed = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-e", program],
        input=json.dumps(cases), text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
