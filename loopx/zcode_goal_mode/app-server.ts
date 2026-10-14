/** Provider transport for ZCode's legacy NDJSON session/goal protocol. */
import { spawn, execFile, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { StringDecoder } from "node:string_decoder";

import { ZCodeGoalError, type NativeObservation, type ZCodeModelSelection, type ZCodeModelOption } from "./contract.ts";
export type { ZCodeModelSelection, ZCodeModelOption } from "./contract.ts";
export interface NativeGoalReadback {
  session_id: string;
  target_id: string | null;
  objective_sha256: string | null;
  selected_model: ZCodeModelSelection | null;
  available_models: ZCodeModelOption[];
  status: NativeObservation["status"];
  raw_status: string | null;
  session_status: string;
  running: boolean;
  revision: number;
}
export interface NativeGoalReceipt extends NativeGoalReadback { started_turn: boolean }
export interface ZCodeAppServerOptions {
  timeoutMs?: number;
  /** The local guardian owns a separate native process group and confirms its cleanup. */
  guardian?: boolean;
  onExit?: (exit: { code: number | null; expected: boolean }) => void;
  /** Notifications contain only method/session identity, never model output or private logs. */
  onEvent?: (event: { method: string; session_id?: string }) => void;
}
export class ZCodeProtocolError extends ZCodeGoalError {
  readonly code: string;
  readonly protocolCode?: number;
  constructor(code: string, protocolCode?: number) {
    super(`ZCode app-server ${code}`);
    this.name = "ZCodeProtocolError";
    this.code = code;
    this.protocolCode = protocolCode;
  }
}
type JsonObject = Record<string, unknown>;
function object(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ZCodeProtocolError("invalid_response");
  return value as JsonObject;
}
function identity(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new ZCodeProtocolError("invalid_identity");
  return value;
}
const MAX_FRAME_BYTES = 4 * 1024 * 1024;

export class ZCodeAppServer {
  readonly command: readonly string[];
  readonly cwd: string;
  readonly env: NodeJS.ProcessEnv;
  readonly options: ZCodeAppServerOptions;
  private process?: ChildProcessWithoutNullStreams;
  private closed = false;
  private termination?: Promise<void>;
  private failure?: ZCodeProtocolError;
  private buffer = "";
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  private targets = new Map<string, string | null>();
  private models = new Map<string, ZCodeModelOption[]>();
  constructor(command: readonly string[], cwd: string, env: NodeJS.ProcessEnv, options: ZCodeAppServerOptions = {}) {
    if (!command.length || command.some((part) => typeof part !== "string" || !part)) throw new ZCodeProtocolError("invalid_command");
    this.command = [...command]; this.cwd = resolve(cwd); this.env = { ...env }; this.options = options;
  }
  private fail(error: ZCodeProtocolError): void {
    this.failure ??= error;
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(error); }
    this.pending.clear();
    void this.terminate().catch(() => { /* close() retains and reports this cleanup rejection. */ });
  }
  private start(): void {
    if (this.process) return;
    if (this.closed || this.failure) throw this.failure ?? new ZCodeProtocolError("closed");
    const process = spawn(this.command[0], this.command.slice(1), { cwd: this.cwd, env: this.env, stdio: "pipe", windowsHide: true, detached: globalThis.process.platform !== "win32", shell: false });
    this.process = process;
    const decoder = new StringDecoder("utf8");
    process.stdout.on("data", (data: Buffer) => this.receive(decoder.write(data)));
    process.stdout.on("end", () => { this.receive(decoder.end()); if (this.buffer.trim()) this.fail(new ZCodeProtocolError("incomplete_frame")); });
    // Drain and discard diagnostics: upstream error text may contain credentials or prompts.
    process.stderr.on("data", () => {});
    process.on("error", () => this.fail(new ZCodeProtocolError("spawn_failed")));
    process.on("exit", (code) => {
      const expected = this.closed;
      if (!expected) this.fail(new ZCodeProtocolError("process_exited"));
      this.options.onExit?.({ code, expected });
    });
    process.stdin.on("error", () => { if (!this.closed) this.fail(new ZCodeProtocolError("input_closed")); });
  }
  private receive(chunk: string): void {
    if (this.failure || this.closed) return;
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer) > MAX_FRAME_BYTES && !this.buffer.includes("\n")) { this.fail(new ZCodeProtocolError("frame_limit")); return; }
    let newline: number;
    while ((newline = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, ""); this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;
      if (Buffer.byteLength(line) > MAX_FRAME_BYTES) { this.fail(new ZCodeProtocolError("frame_limit")); return; }
      try { this.receiveFrame(object(JSON.parse(line))); } catch { this.fail(new ZCodeProtocolError("invalid_frame")); return; }
    }
  }
  private receiveFrame(frame: JsonObject): void {
    if (frame.jsonrpc !== undefined) throw new ZCodeProtocolError("unexpected_framing");
    const hasId = typeof frame.id === "string" || typeof frame.id === "number";
    if (typeof frame.method === "string") {
      if (hasId) {
        const result = frame.method === "interaction/requestPermission"
          ? { id: frame.id, result: { decision: "deny", reason: "LoopX native host requires explicit permission approval." } }
          : frame.method === "session/requestRuntimePreferences" ? { id: frame.id, result: { memoryEnabled: false, nativeSearchEnhancementsEnabled: false, askUserQuestionAutoResolutionEnabled: false } }
          : { id: frame.id, error: { code: -32601, message: "Host interaction unavailable." } };
        this.process?.stdin.write(`${JSON.stringify(result)}\n`);
      } else {
        const params = frame.params && typeof frame.params === "object" ? frame.params as JsonObject : {};
        this.options.onEvent?.({ method: frame.method, ...(typeof params.sessionId === "string" ? { session_id: params.sessionId } : {}) });
      }
      return;
    }
    if (!hasId || (Object.hasOwn(frame, "result") === Object.hasOwn(frame, "error"))) throw new ZCodeProtocolError("invalid_response");
    const request = this.pending.get(String(frame.id));
    if (!request) throw new ZCodeProtocolError("unexpected_response");
    this.pending.delete(String(frame.id)); clearTimeout(request.timer);
    if (frame.error !== undefined) { const error = object(frame.error); request.reject(new ZCodeProtocolError("request_rejected", typeof error.code === "number" ? error.code : undefined)); }
    else request.resolve(frame.result);
  }
  private request(method: string, params: JsonObject = {}): Promise<unknown> {
    this.start();
    if (this.closed || this.failure) return Promise.reject(this.failure ?? new ZCodeProtocolError("closed"));
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new ZCodeProtocolError("request_timeout")), this.options.timeoutMs ?? 15_000);
      this.pending.set(id, { resolve, reject, timer });
      this.process!.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }
  async initialize(): Promise<{ protocol: "zcode-ndjson-session-goal"; capabilities: { independentPlanState: boolean } }> {
    const result = object(await this.request("runtime/capabilities"));
    if (typeof result.independentPlanState !== "boolean") throw new ZCodeProtocolError("unsupported_capabilities");
    return { protocol: "zcode-ndjson-session-goal", capabilities: { independentPlanState: result.independentPlanState } };
  }
  private selection(value: unknown): ZCodeModelSelection {
    const ref = object(value);
    const options = ref.options == null ? undefined : object(ref.options);
    return { providerId: identity(ref.providerId), modelId: identity(ref.modelId), ...(options?.reasoningLevel ? { options: { reasoningLevel: identity(options.reasoningLevel) } } : {}) };
  }
  private readSnapshot(value: unknown, expectedSession?: string, fullCatalogue = false): NativeGoalReadback {
    const snapshot = object(value), protocol = object(snapshot.protocol), session = object(snapshot.session), projection = object(snapshot.projection), runtime = object(snapshot.runtime);
    if (protocol.name !== "ZCode Protocol" || protocol.version !== 1) throw new ZCodeProtocolError("unsupported_protocol");
    const sessionId = identity(session.sessionId);
    if (expectedSession && sessionId !== expectedSession) throw new ZCodeProtocolError("session_identity_mismatch");
    if (!Number.isSafeInteger(runtime.stateRevision) || (runtime.stateRevision as number) < 0) throw new ZCodeProtocolError("invalid_revision");
    const model = object(object(snapshot.settings).model);
    if (!Array.isArray(model.available)) throw new ZCodeProtocolError("invalid_models");
    const incoming = model.available.map((value): ZCodeModelOption => {
      const candidate = object(value), reasoning = candidate.reasoning == null ? null : object(candidate.reasoning);
      if (reasoning && !Array.isArray(reasoning.levels)) throw new ZCodeProtocolError("invalid_models");
      return { selection: this.selection(candidate.ref), label: identity(candidate.label), ...(typeof candidate.providerLabel === "string" ? { provider_label: candidate.providerLabel } : {}), reasoning_levels: reasoning ? (reasoning.levels as unknown[]).map((level) => identity(object(level).value)) : [], default_reasoning_level: reasoning?.defaultLevel == null ? null : identity(reasoning.defaultLevel), disabled: typeof candidate.disabledReason === "string" };
    });
    if (fullCatalogue) this.models.set(sessionId, incoming);
    const availableModels = this.models.get(sessionId) ?? incoming;
    const selectedModel = model.current == null ? null : this.selection(model.current);
    const target = projection.target == null ? null : object(projection.target);
    const targetId = target ? identity(target.targetId) : null;
    if (target && target.sessionId !== sessionId) throw new ZCodeProtocolError("target_identity_mismatch");
    const rawStatus = target ? identity(target.status) : null;
    if (rawStatus !== null && !["active", "paused", "budget_limited", "complete"].includes(rawStatus)) throw new ZCodeProtocolError("unsupported_goal_status");
    const sessionStatus = identity(projection.status);
    if (!["idle", "running", "waiting", "paused", "completed", "error"].includes(sessionStatus)) throw new ZCodeProtocolError("unsupported_session_status");
    return { session_id: sessionId, target_id: targetId, selected_model: selectedModel, available_models: availableModels.map((model) => ({ ...model, selection: { ...model.selection }, reasoning_levels: [...model.reasoning_levels] })), objective_sha256: target ? createHash("sha256").update(identity(target.objective)).digest("hex") : null, status: rawStatus === "complete" ? "completed" : rawStatus as NativeGoalReadback["status"], raw_status: rawStatus, session_status: sessionStatus, running: sessionStatus === "running" || sessionStatus === "waiting", revision: runtime.stateRevision as number };
  }
  async create(model?: ZCodeModelSelection): Promise<NativeGoalReadback> {
    const readback = this.readSnapshot(await this.request("session/create", { workspace: { workspacePath: this.cwd, workspaceKey: this.cwd }, mode: "build", titleGenerationEnabled: false, ...(model ? { model, ...(model.options?.reasoningLevel ? { thoughtLevel: model.options.reasoningLevel } : {}) } : {}) }), undefined, true);
    if (readback.target_id || readback.running) throw new ZCodeProtocolError("unexpected_session_goal");
    this.targets.set(readback.session_id, null);
    // Even persistence=immediate leaves an unused native session in memory only.
    // Empty Goal pause persists its metadata without starting a Goal or model.
    const persisted = await this.pauseGoal(readback.session_id);
    if (persisted.target_id || persisted.running) throw new ZCodeProtocolError("empty_session_not_quiescent");
    return persisted;
  }
  async resumeSession(sessionId: string): Promise<NativeGoalReadback> {
    const readback = this.readSnapshot(await this.request("session/resume", { sessionId, workspace: { workspacePath: this.cwd, workspaceKey: this.cwd } }), sessionId, true);
    this.targets.set(sessionId, readback.target_id); return readback;
  }
  async readGoal(sessionId: string): Promise<NativeGoalReadback> {
    return this.readSnapshot(await this.request("session/read", { sessionId, messageLimit: 1 }), sessionId);
  }
  async selectModel(sessionId: string, selection: ZCodeModelSelection): Promise<NativeGoalReadback> {
    const before = await this.readGoal(sessionId);
    if (!this.targets.has(sessionId) || this.targets.get(sessionId) !== before.target_id) throw new ZCodeProtocolError("goal_identity_changed");
    if (before.running || before.status === "active") throw new ZCodeProtocolError("model_change_requires_pause");
    const candidate = before.available_models.find((model) => model.selection.providerId === selection.providerId && model.selection.modelId === selection.modelId);
    if (!candidate || candidate.disabled) throw new ZCodeProtocolError("model_unavailable");
    const reasoning = selection.options?.reasoningLevel;
    if ((candidate.reasoning_levels.length && !reasoning) || (reasoning && !candidate.reasoning_levels.includes(reasoning))) throw new ZCodeProtocolError("reasoning_unavailable");
    const after = this.readSnapshot(await this.request("session/setModel", { sessionId, model: selection, expectedRevision: before.revision, persistAsWorkspaceLastUsed: false }), sessionId);
    if (after.target_id !== before.target_id) throw new ZCodeProtocolError("goal_identity_changed");
    if (JSON.stringify(after.selected_model) !== JSON.stringify(this.selection(selection))) throw new ZCodeProtocolError("model_selection_not_applied");
    return after;
  }
  private async mutate(sessionId: string, action: string, objective?: string): Promise<NativeGoalReceipt> {
    const before = await this.readGoal(sessionId);
    if (!this.targets.has(sessionId) || this.targets.get(sessionId) !== before.target_id) throw new ZCodeProtocolError("goal_identity_changed");
    const result = object(await this.request("session/goal", { sessionId, action, expectedRevision: before.revision, ...(objective === undefined ? {} : { objective }) }));
    if ((action === "pause" || action === "clear") && result.startedTurn !== false) throw new ZCodeProtocolError("unexpected_execution");
    const after = this.readSnapshot(result.snapshot, sessionId);
    if (action !== "set" && action !== "clear" && after.target_id !== before.target_id) throw new ZCodeProtocolError("goal_identity_changed");
    if (action === "set" && (!after.target_id || after.status !== "active" || result.startedTurn !== true)) throw new ZCodeProtocolError("goal_not_started");
    if (action === "resume" && (!after.target_id || after.status !== "active" || result.startedTurn !== true)) throw new ZCodeProtocolError("goal_not_started");
    if (action === "clear" && after.target_id !== null) throw new ZCodeProtocolError("goal_not_cleared");
    if (action === "set" && after.objective_sha256 !== createHash("sha256").update(objective!.trim()).digest("hex")) throw new ZCodeProtocolError("objective_not_applied");
    this.targets.set(sessionId, after.target_id);
    return { ...after, started_turn: result.startedTurn === true };
  }
  async setGoal(sessionId: string, objective: string): Promise<NativeGoalReceipt> {
    if (!objective.trim()) throw new ZCodeProtocolError("empty_objective");
    return await this.mutate(sessionId, "set", objective);
  }
  async resumeGoal(sessionId: string): Promise<NativeGoalReceipt> { return await this.mutate(sessionId, "resume"); }
  async pauseGoal(sessionId: string): Promise<NativeGoalReadback> {
    const receipt = await this.mutate(sessionId, "pause");
    const deadline = Date.now() + (this.options.timeoutMs ?? 15_000);
    let current: NativeGoalReadback = receipt;
    if (!current.target_id && current.running) throw new ZCodeProtocolError("goal_missing");
    while (current.target_id && (current.status !== "paused" || current.running)) {
      if (Date.now() >= deadline) throw new ZCodeProtocolError("pause_readback_timeout");
      await new Promise((resolve) => setTimeout(resolve, 30));
      current = await this.readGoal(sessionId);
      if (current.target_id !== receipt.target_id) throw new ZCodeProtocolError("goal_identity_changed");
    }
    return current;
  }
  async stopGoal(sessionId: string): Promise<NativeGoalReadback> { return await this.pauseGoal(sessionId); }
  async clearGoal(sessionId: string): Promise<NativeGoalReadback> {
    await this.pauseGoal(sessionId);
    return await this.mutate(sessionId, "clear");
  }
  private terminate(): Promise<void> {
    if (this.termination) return this.termination;
    const child = this.process;
    if (!child) return Promise.resolve();
    this.termination = (async () => {
      if (this.options.guardian) {
        // Revocation must reach the guardian before its outer group is killed.
        if (child.exitCode === null && child.signalCode === null) {
          child.stdin.end();
          if (globalThis.process.platform !== "win32") child.kill("SIGTERM");
          try { await waitForOwnedExit(child, 3_000); }
          catch {
            try { await terminateOwnedProcess(child); }
            finally { throw new ZCodeProtocolError("process_cleanup_unconfirmed"); }
          }
        }
        if (child.exitCode !== 0) throw new ZCodeProtocolError("process_cleanup_unconfirmed");
        if (globalThis.process.platform === "win32") return; // Successful guardian taskkill confirmed its native tree.
      }
      await terminateOwnedProcess(child);
    })();
    return this.termination;
  }
  async close(): Promise<void> {
    this.closed = true;
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(new ZCodeProtocolError("closed")); }
    this.pending.clear();
    await this.terminate();
  }
}


function waitForOwnedExit(child: ChildProcessWithoutNullStreams, timeout: number): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((done, reject) => {
    const exited = () => { clearTimeout(timer); done(); };
    const timer = setTimeout(() => {
      child.removeListener("exit", exited);
      reject(new ZCodeProtocolError("process_cleanup_unconfirmed"));
    }, timeout);
    child.once("exit", exited);
  });
}
const observeProcesses = promisify(execFile);
async function ownedGroupHasExited(pid: number, timeout: number): Promise<boolean> {
  try {
    const { stdout } = await observeProcesses("ps", ["-A", "-o", "pgid=", "-o", "stat="], {timeout, encoding: "utf8"});
    if (!stdout.trim()) throw new Error("Missing process observation");
    for (const line of stdout.split("\n")) {
      if (!line.trim()) continue;
      const fields = line.trim().split(/\s+/);
      if (fields.length !== 2 || !/^\d+$/.test(fields[0])) throw new Error("Invalid process observation");
      if (Number(fields[0]) === pid && !fields[1].startsWith("Z")) return false;
    }
    return true;
  } catch { throw new ZCodeProtocolError("process_cleanup_unconfirmed"); }
}
/** Terminate only a directly spawned child and its owned process tree. */
export async function terminateOwnedProcess(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (!child.pid) return;
  if (globalThis.process.platform === "win32") {
    // Once a Windows leader is gone, taskkill cannot prove descendant ownership.
    if (child.exitCode !== null || child.signalCode !== null) throw new ZCodeProtocolError("process_cleanup_unconfirmed");
    await new Promise<void>((done, reject) => {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {stdio: "ignore", windowsHide: true});
      const timer = setTimeout(() => { killer.kill(); reject(new ZCodeProtocolError("process_cleanup_unconfirmed")); }, 2_000);
      killer.once("error", () => { clearTimeout(timer); reject(new ZCodeProtocolError("process_cleanup_unconfirmed")); });
      killer.once("exit", code => { clearTimeout(timer); code === 0 ? done() : reject(new ZCodeProtocolError("process_cleanup_unconfirmed")); });
    });
    await waitForOwnedExit(child, 2_000);
    return;
  }
  const deadline = Date.now() + 2_000;
  // A reaped leader retains its group identity while executable descendants live.
  if (!await ownedGroupHasExited(child.pid, 2_000)) {
    try { globalThis.process.kill(-child.pid, "SIGKILL"); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw new ZCodeProtocolError("process_cleanup_unconfirmed");
    }
  }
  await waitForOwnedExit(child, Math.max(1, deadline - Date.now()));
  while (!await ownedGroupHasExited(child.pid, Math.max(1, deadline - Date.now()))) {
    if (Date.now() >= deadline) throw new ZCodeProtocolError("process_cleanup_unconfirmed");
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
