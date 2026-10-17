#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
GUIDE = (ROOT / "docs" / "reference" / "extensions.md").read_text(encoding="utf-8")

for anchor in (
    "## Capability And Extension Placement",
    "docs/reference/extensions.md#placement-decision-for-agents",
    "capability id, provider id",
    "Name public capabilities after caller outcomes",
):
    assert anchor in AGENTS, anchor

for anchor in (
    "## Placement Decision For Agents",
    "## Repository Layout",
    "loopx/capabilities/<capability-id>/",
    "loopx/extensions/",
    "packages/<package-id>/",
    "There is intentionally no",
    "What user outcome and caller-visible contract is being added or changed?",
    "Does the implementation need independent installation",
    "An extension-owned command and packet contract may remain a",
    "`value-connectors` is an existing compatibility CLI",
    "a standalone extension such as `loopx-finance-value-discovery`",
    "loopx extension run <extension-id>",
    "Direct provider binaries are implementation and debugging surfaces",
    "capability_id: <existing-or-new-contract>",
    "Use `capability_id: none` for a standalone extension.",
    "reason: <why the nearest existing owner is or is not sufficient>",
):
    assert anchor in GUIDE, anchor

print("capability-extension-placement-doc-smoke: ok")
