/** Explicit optional Rune MCP provider and the direct-search control. No model calls. */
import {execFileSync, spawnSync} from "node:child_process";
import {createRequire} from "node:module";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {object, type Citation, type Query} from "./evidence.ts";

export const RUNE_REVISION = "4042a088f1672d32110f38a5ec10c59717ea5412";
const TIMEOUT = 60_000;

export function directSearch(root: string, query: Query): Citation[] {
  const args = ["--no-config", "--json", "--line-number", "--fixed-strings", "--max-count", "1",
    "--hidden", "--no-ignore", "--glob", "!.rune/**"];
  for (const term of query.terms) args.push("-e", term);
  args.push("--", ".");
  const result = spawnSync("rg", args, {cwd: root, encoding: "utf8", timeout: TIMEOUT, maxBuffer: 8 * 1024 * 1024});
  if (result.error || result.status === null || result.status > 1) throw new Error("direct_search_unavailable");
  return result.stdout.split("\n").filter(Boolean).flatMap(line => {
    const event = object(JSON.parse(line));
    if (event.type !== "match") return [];
    const data = object(event.data), file = object(data.path).text, snippet = object(data.lines).text;
    return typeof file === "string" && typeof snippet === "string"
      ? [{file: file.replace(/^\.\//u, ""), line: Number(data.line_number), snippet: snippet.trim()}] : [];
  }).sort((left, right) => left.file.localeCompare(right.file));
}

export function prepareRune(destination: string): void {
  // Trusted fixed upstream and immutable revision, never a suite-supplied command.
  execFileSync("git", ["clone", "--quiet", "https://github.com/thecolourfoundation/rune.git", destination],
    {timeout: TIMEOUT, stdio: "pipe"});
  execFileSync("git", ["-C", destination, "checkout", "--quiet", "--detach", RUNE_REVISION],
    {timeout: TIMEOUT, stdio: "pipe"});
  execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["ci", "--ignore-scripts"],
    {cwd: destination, timeout: TIMEOUT, stdio: "pipe"});
}

interface Client {
  connect(transport: unknown): Promise<void>;
  listTools(): Promise<unknown>;
  callTool(request: unknown, schema?: unknown, options?: {timeout: number}): Promise<unknown>;
  close(): Promise<void>;
}

function toolData(value: unknown): Record<string, unknown> {
  const result = object(value);
  if (result.isError || !Array.isArray(result.content)) throw new Error("provider_tool_failed");
  const text = result.content.find(item => object(item).type === "text");
  if (!text || typeof object(text).text !== "string") throw new Error("provider_payload_invalid");
  return object(JSON.parse(object(text).text as string));
}

export class RuneSession {
  client: Client;
  startup_ms = 0;
  file_count: number | null = null;
  constructor(client: Client) { this.client = client; }

  static async start(source: string, root: string): Promise<RuneSession> {
    const revision = execFileSync("git", ["-C", source, "rev-parse", "HEAD"], {encoding: "utf8", timeout: TIMEOUT}).trim();
    if (revision !== RUNE_REVISION) throw new Error("provider_revision_mismatch");
    if (execFileSync("git", ["-C", source, "status", "--porcelain", "--untracked-files=no"],
      {encoding: "utf8", timeout: TIMEOUT}).trim()) throw new Error("provider_source_modified");
    const require = createRequire(join(source, "package.json"));
    const {Client} = await import(pathToFileURL(require.resolve("@modelcontextprotocol/sdk/client/index.js")).href);
    const {StdioClientTransport} = await import(pathToFileURL(require.resolve("@modelcontextprotocol/sdk/client/stdio.js")).href);
    const transport = new StdioClientTransport({
      command: process.execPath, args: [join(source, "bin/rune.js"), "serve", root],
      cwd: root, stderr: "pipe", maxBufferSize: 2 * 1024 * 1024,
    });
    transport.stderr?.resume(); // Do not retain provider logs or absolute paths.
    const client = new Client({name: "loopx-repository-context-pilot", version: "0.1.0"}, {capabilities: {}}) as Client;
    const session = new RuneSession(client), start = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        client.connect(transport),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("provider_start_timeout")), TIMEOUT); }),
      ]);
      const listed = object(await client.listTools());
      if (!Array.isArray(listed.tools) || !listed.tools.some(tool => object(tool).name === "rune_agent"))
        throw new Error("provider_tool_missing");
      const overview = toolData(await client.callTool({name: "rune_get_overview", arguments: {}}, undefined, {timeout: TIMEOUT}));
      const meta = object(overview.meta);
      session.file_count = typeof meta.fileCount === "number" ? meta.fileCount : null;
      session.startup_ms = performance.now() - start;
      return session;
    } catch (error) { await client.close(); throw error; }
    finally { if (timer) clearTimeout(timer); }
  }

  async search(query: Query): Promise<{candidates: unknown[]; observation: Record<string, unknown>}> {
    const report = toolData(await this.client.callTool({
      name: "rune_agent", arguments: {objective: query.objective, maxTokens: 4096, deep: false, force: false},
    }, undefined, {timeout: TIMEOUT}));
    const synthesis = object(report.synthesis);
    if (!Array.isArray(synthesis.insights)) throw new Error("provider_payload_invalid");
    const candidates = synthesis.insights.flatMap(raw => {
      const insight = object(raw);
      return Array.isArray(insight.evidence) ? insight.evidence : [];
    });
    const budget = object(report.budget), sections = object(budget.sections);
    const kept = object(sections.synthesisInsights), triage = object(report.triage);
    return {candidates, observation: {
      relevant_fact_count: report.relevantFactCount,
      returned_insight_count: synthesis.insights.length,
      dropped_insight_count: kept.dropped,
      reported_token_estimate: budget.usedTokens,
      max_token_estimate: budget.maxTokens,
      triage_mode: triage.mode,
    }};
  }

  close(): Promise<void> { return this.client.close(); }
}
