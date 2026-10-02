/** Native completion's fixed graph IO adapter. The model supplies no process,
 * snapshot, authority or approval fields; the provider supplies the actual Todo. */
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {isAbsolute} from "node:path";
import {fileURLToPath} from "node:url";
import {createInterface} from "node:readline";
import type {JsonObject} from "../effect_program.ts";
import type {TodoTerminalEvidenceGuard} from "../coordination/todo_terminal_lifecycle.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {normalizeResearchCompositionPolicy, qualifyResearchCompletion} from "./explore_research_execution.ts";

export class ResearchTerminalEvidenceHost {
  private child: ChildProcessWithoutNullStreams | null = null;
  private exited: Promise<void> | null = null;
  private readonly root: string;
  private readonly goal: string;
  private readonly source: JsonObject;
  constructor(root: string, goal: string, source: JsonObject) {this.root = root; this.goal = goal; this.source = source;}

  readonly qualify: TodoTerminalEvidenceGuard = async context => {
    if (context.todo.role === "user" || context.todo.status === "done"
      || context.todo.action_kind !== "joint_probe"
        && context.todo.capability_binding_ref !== "explore:research-composition-v0"
        && !(Array.isArray(context.todo.explore_result_node_refs) && context.todo.explore_result_node_refs.length)
        && !context.todo.replan_obligation_id) return {required: false, allowed: true, evidence: null};
    const source = requireJsonObject(this.source, "research registry source");
    const bytes = await readFile(String(source.path));
    if (createHash("sha256").update(bytes).digest("hex") !== source.sha256) {
      return {allowed: false, reason_code: "authority_source_changed", reason: "Goal configuration changed before completion; read the current source."};
    }
    const registry = requireJsonObject(JSON.parse(bytes.toString("utf8")), "research registry");
    const goals = Array.isArray(registry.goals) ? registry.goals : [];
    const matches = goals.filter((value): value is JsonObject => value !== null && typeof value === "object"
      && !Array.isArray(value)).filter(goal => goal.id === this.goal);
    if (matches.length > 1) throw new Error("ambiguous research Goal source");
    const spawnPolicy = matches[0]?.spawn_policy as JsonObject | undefined;
    const harness = spawnPolicy?.explore_harness as JsonObject | undefined;
    if (!harness || !normalizeResearchCompositionPolicy({harness}).enabled) return {required: false, allowed: true, evidence: null};
    const python = process.env.LOOPX_EFFECT_RUNTIME_PYTHON;
    if (!python || !isAbsolute(python) || python.includes("\0")) {
      return {allowed: false, reason_code: "research_evidence_host_required",
        reason: "Native research completion needs the source-selected Python adapter context; use the installed LoopX entrypoint."};
    }
    this.child = spawn(python, ["-m", "loopx.capabilities.explore.research_snapshot_host"], {
      stdio: ["pipe", "pipe", "pipe"], env: {...process.env, PYTHONPATH: fileURLToPath(new URL("../../../", import.meta.url))},
    });
    this.exited = new Promise(resolve => this.child!.once("close", () => resolve()));
    this.child.on("error", () => {}); this.child.stdin.on("error", () => {}); this.child.stderr.resume();
    const replies = createInterface({input: this.child.stdout})[Symbol.asyncIterator]();
    this.child.stdin.write(JSON.stringify({runtime_root: this.root, goal_id: this.goal,
      harness, agent_id: context.actor_agent_id ?? context.todo.claimed_by,
      todos: context.todos.map(todo => Object.fromEntries([
        "todo_id", "role", "status", "claimed_by", "replan_obligation_id", "task_class", "action_kind",
        "archive_state", "excluded_agents", "explore_result_node_refs", "target_key",
        "resume_when", "unblocks_todo_id",
      ].filter(field => Object.hasOwn(todo, field)).map(field => [field, todo[field]]))),
    }) + "\n");
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const line = await Promise.race([replies.next().then(row => row.done ? null : row.value),
        this.exited.then(() => null), new Promise<null>(resolve => {timer = setTimeout(() => resolve(null), 20000);})]);
      if (line === null || Buffer.byteLength(line, "utf8") > 2 * 1024 * 1024) {
        return {allowed: false, reason_code: "research_graph_snapshot_unavailable", reason: "Current locked research graph is unavailable; retry its source read before closeout."};
      }
      const snapshot = requireJsonObject(JSON.parse(line), "research graph snapshot");
      if (snapshot.schema_version !== "research_graph_snapshot_v0" || snapshot.error) {
        if (snapshot.error === "LockAcquireTimeoutError") return {allowed: false, reason_code: "research_graph_busy",
          reason: "An Explore writer owns the graph lock; retry this same completion identity after the writer finishes."};
        return {allowed: false, reason_code: "research_graph_snapshot_invalid", reason: "The current research graph is invalid; repair its canonical source before closeout."};
      }
      return qualifyResearchCompletion({frontier: snapshot.frontier, todo: context.todo,
        actor_agent_id: context.actor_agent_id, goal_id: context.goal_id});
    } finally {clearTimeout(timer);}
  };

  current(): boolean {
    // A child that exited no longer owns the kernel lock. Check this again at
    // the transaction's existing source-admission boundaries, including CAS.
    return this.child === null || this.child.exitCode === null && this.child.signalCode === null;
  }

  async close(): Promise<void> {
    if (this.child) {
      this.child.stdin.end("release\n");
      // The host exits after releasing its kernel lock. A bounded forced exit
      // also closes the descriptors if its process cannot finish normally.
      const timer = setTimeout(() => this.child?.kill(), 1000);
      try {await this.exited;} finally {clearTimeout(timer);}
    }
  }
}
