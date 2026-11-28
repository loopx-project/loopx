/** Keep production JS/TS network entry points inside declared transport owners. */
import {execFileSync} from "node:child_process";
import {existsSync, readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import ts from "typescript";

const owners = new Map([
  ["packages/dsh-loopx-plugin/src/client/dashboard.tsx", "fetch"],
  ["loopx/control_plane/coordination/remote_authority_http.ts", "https"],
  ["loopx/control_plane/runtime/usage_statistics.ts", "fetch"],
  ["loopx/control_plane/runtime/workbench.ts", "http"],
  ["loopx/zcode_goal_mode/cli.ts", "http"],
]);
const httpModules = new Set(["http", "https", "http2", "axios", "undici", "node-fetch", "got"]);
const globalObjects = new Set(["globalThis", "global", "window", "self"]);

function httpModule(node) {
  if (!node || !ts.isStringLiteralLike(node)) return undefined;
  const name = node.text.replace(/^node:/, "").split("/")[0];
  return httpModules.has(name) ? name : undefined;
}

function transport(node, hasLocalBinding) {
  if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
    if (node.isTypeOnly || node.importClause?.isTypeOnly) return undefined;
    const names = ts.isExportDeclaration(node) ? node.exportClause : node.importClause?.namedBindings;
    if (names?.elements?.length && names.elements.every(item => item.isTypeOnly) && !node.importClause?.name) return undefined;
    return httpModule(node.moduleSpecifier);
  }
  if (ts.isExternalModuleReference(node)) return httpModule(node.expression);
  if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
      (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
    return httpModule(node.arguments[0]);
  }
  if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) &&
      ts.isIdentifier(node.expression) && globalObjects.has(node.expression.text) && !hasLocalBinding(node.expression)) {
    const member = ts.isPropertyAccessExpression(node) ? node.name : node.argumentExpression;
    if ((ts.isIdentifier(member) || ts.isStringLiteralLike(member)) && member.text === "fetch") return "fetch";
  }
  if (ts.isIdentifier(node) && node.text === "fetch") {
    const parent = node.parent;
    // Object keys and client.fetch are not references to the global transport.
    if (parent.name === node && !ts.isShorthandPropertyAssignment(parent)) return undefined;
    if (!hasLocalBinding(node)) return "fetch";
  }
  return undefined;
}

export function lintExternalTransports(file, source) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  // Resolve a potential global against declarations in this file, including
  // imports and function parameters. Local clients may name a dependency fetch.
  let checker;
  function hasLocalBinding(node) {
    if (!checker) {
      const options = {allowJs: true, noLib: true, noResolve: true, target: ts.ScriptTarget.Latest};
      const host = ts.createCompilerHost(options);
      host.getSourceFile = name => name === file ? tree : undefined;
      checker = ts.createProgram([file], options, host).getTypeChecker();
    }
    const symbol = ts.isShorthandPropertyAssignment(node.parent)
      ? checker.getShorthandAssignmentValueSymbol(node.parent) : checker.getSymbolAtLocation(node);
    return Boolean(symbol?.declarations?.length);
  }
  const violations = [];
  function visit(node) {
    const api = transport(node, hasLocalBinding);
    if (api && owners.get(file) !== api) {
      const {line} = tree.getLineAndCharacterOfPosition(node.getStart(tree));
      violations.push(`${file}:${line + 1}: external ${api} requires a declared transport owner`);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return violations;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const files = execFileSync("git", ["ls-files", "-co", "--exclude-standard", "-z", "--", "loopx", "packages"], {encoding: "utf8"}).split("\0");
  for (const file of new Set(files)) {
    if (!/^(?:loopx\/|packages\/[^/]+\/src\/)/.test(file) || !/\.[cm]?[jt]sx?$/.test(file) || !existsSync(file)) continue;
    for (const failure of lintExternalTransports(file, readFileSync(file, "utf8"))) {
      console.error(failure);
      process.exitCode = 1;
    }
  }
}
