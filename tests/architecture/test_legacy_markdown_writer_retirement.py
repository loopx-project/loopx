"""Prevent the retired Markdown coordination writers from returning."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
import json
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "loopx"

RETIRED_MODULES = {
    "loopx.control_plane.coordination.runtime_shadow_writer_adapter",
    "loopx.control_plane.scheduler.legacy_monitor_poll",
    "loopx.control_plane.testing.authority_e2e_rows_stage2c",
    "loopx.control_plane.testing.authority_e2e_rows_stage2c2",
    "loopx.control_plane.todos.handoff_mode_source",
    "loopx.control_plane.todos.legacy_mutation",
    "loopx.control_plane.todos.line_update",
}
RETIRED_FILES = {
    Path(*module.split(".")).with_suffix(".py")
    for module in RETIRED_MODULES
} | {
    Path("loopx/control_plane/coordination/handoff_mode_legacy_plan.ts"),
}
RETIRED_PYTHON_SYMBOLS = {
    "_VerifiedTaskLeaseFence",
    "_add_goal_todo_legacy",
    "_archive_completed_todos_legacy",
    "_complete_goal_todo_legacy",
    "_supersede_goal_todo_legacy",
    "_update_goal_todo_legacy",
    "apply_legacy_monitor_poll",
    "apply_todo_update_to_lines",
    "begin_todo_runtime_shadow_capture",
    "enter_terminal_todo_lease_fence",
    "goal_handoff_mode_for_lease",
    "handoff_mode_source",
    "hold_handoff_lease_holder_gate",
    "hold_task_lease_mutation_fence",
    "legacy_todo_write_transaction",
    "link_generated_successor_todo_ids",
    "link_superseding_todo_id",
    "release_verified_task_lease_fence",
    "require_runtime_shadow_capture_prepared",
    "settle_lease_runtime_shadow_capture",
    "settle_todo_runtime_shadow_capture",
    "successor_add_kwargs",
    "upsert_todo_metadata",
    "write_captured_todo_state",
}
RETIRED_TYPESCRIPT_SYMBOLS = {
    "LEGACY_HANDOFF_PLAN_SCHEMA",
    "patchHandoffMode",
    "planLegacyMonitorBatch",
    "planLegacyHandoffMode",
}

TYPESCRIPT_RETIREMENT_SCAN = r"""
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const retiredModule = path.resolve(
  request.root,
  "loopx/control_plane/coordination/handoff_mode_legacy_plan",
);
const retiredSymbols = new Set(request.retired_symbols);
const offenders = [];

const normalizedModule = (sourcePath, specifier) => {
  if (!specifier.startsWith(".")) return path.resolve(request.root, specifier);
  return path.resolve(path.dirname(sourcePath), specifier);
};
const withoutScriptExtension = value =>
  value.replace(/\.(?:[cm]?[jt]s)$/, "").replace(/[/\\]index$/, "");
const noteModule = (sourcePath, node) => {
  if (!node || !ts.isStringLiteralLike(node)) return;
  const resolved = withoutScriptExtension(normalizedModule(sourcePath, node.text));
  if (resolved === retiredModule) {
    offenders.push({path: path.relative(request.root, sourcePath), kind: "import", name: node.text});
  }
};
const noteName = (sourcePath, node) => {
  if (node && ts.isIdentifier(node) && retiredSymbols.has(node.text)) {
    offenders.push({path: path.relative(request.root, sourcePath), kind: "symbol", name: node.text});
  }
};

for (const relative of request.sources) {
  const sourcePath = path.resolve(request.root, relative);
  const text = fs.readFileSync(sourcePath, "utf8");
  const tree = ts.createSourceFile(
    relative,
    text,
    ts.ScriptTarget.Latest,
    true,
    relative.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  if (tree.parseDiagnostics.length) {
    const diagnostic = tree.parseDiagnostics[0];
    const line = tree.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line + 1;
    throw new Error(`${relative}:${line}: invalid TypeScript source`);
  }
  const visit = node => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      noteModule(sourcePath, node.moduleSpecifier);
    } else if (ts.isImportEqualsDeclaration(node) &&
               ts.isExternalModuleReference(node.moduleReference)) {
      noteModule(sourcePath, node.moduleReference.expression);
    } else if (ts.isCallExpression(node) &&
               node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      noteModule(sourcePath, node.arguments[0]);
    }
    if (ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) ||
        ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) ||
        ts.isEnumDeclaration(node) || ts.isVariableDeclaration(node)) {
      noteName(sourcePath, node.name);
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
}
process.stdout.write(JSON.stringify(offenders));
"""


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("loopx", *parts))


def _python_offenders() -> list[tuple[str, str, str]]:
    offenders: list[tuple[str, str, str]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = str(path.relative_to(REPOSITORY_ROOT))
        module = _module_name(path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported_module = node.module or ""
                if node.level:
                    imported_module = resolve_name(
                        "." * node.level + imported_module,
                        package,
                    )
                imported = {imported_module}
                imported.update(
                    f"{imported_module}.{alias.name}" for alias in node.names
                )
            else:
                imported = set()
            for imported_module in imported & RETIRED_MODULES:
                offenders.append((relative, "import", imported_module))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in RETIRED_PYTHON_SYMBOLS:
                    offenders.append((relative, "symbol", node.name))
    return offenders


def _typescript_offenders() -> list[dict[str, str]]:
    sources = sorted(
        str(path.relative_to(REPOSITORY_ROOT))
        for suffix in ("*.ts", "*.tsx", "*.mts")
        for path in PACKAGE_ROOT.rglob(suffix)
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", TYPESCRIPT_RETIREMENT_SCAN],
        input=json.dumps(
            {
                "root": str(REPOSITORY_ROOT),
                "sources": sources,
                "retired_symbols": sorted(RETIRED_TYPESCRIPT_SYMBOLS),
            }
        ),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, list)
    return result


def test_legacy_markdown_writer_retirement_is_structural() -> None:
    existing = sorted(
        str(path)
        for relative in RETIRED_FILES
        if (path := REPOSITORY_ROOT / relative).exists()
    )
    assert existing == []
    assert _python_offenders() == []
    assert _typescript_offenders() == []
