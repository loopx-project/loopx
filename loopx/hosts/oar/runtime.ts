/** Shared OAR session ownership for LoopX chat and bounded execution callers. */
import { randomUUID } from "node:crypto";
import type {
  ContextUsage,
  ControlOutcome,
  Event,
  FailureClass,
  InputOptions,
  McpServer,
  RawEvent,
  RejectionCode,
  Runtime,
  Session,
  SessionOptions,
  TokenTotals,
  TurnOutcome,
} from "@botiverse/oar";
import { turnEndAfter } from "@botiverse/oar/observe";
import type { DshRuntimeConfig } from "./dsh.ts";

/** Existing host selections, kept local to the OAR transport boundary. */
export type OarRuntimeId = "codex" | "pi" | "dsh";
export type CodexSandbox = "read-only" | "workspace-write" | "danger-full-access";

export interface OarSessionConfig {
  readonly runtime: OarRuntimeId;
  readonly cwd: string;
  readonly env?: Readonly<Record<string, string | null>>;
  readonly model?: string;
  readonly effort?: string;
  readonly serviceTier?: string;
  readonly resume?: string;
  readonly outputSchema?: Readonly<Record<string, unknown>>;
  readonly mcpServers?: readonly McpServer[];
  readonly launchArgs?: readonly string[];
  /** Explicit Codex executable; Pi uses the version bundled with OAR. */
  readonly command?: string;
  /** Explicit authority from the caller; OAR's permissive default is never used. */
  readonly sandbox?: CodexSandbox;
  readonly dsh?: DshRuntimeConfig;
}

export interface OarErrorInfo {
  readonly name: string;
  readonly message: string;
  readonly code?: string;
  readonly sessionId?: string;
  readonly method?: string;
}

export interface OarTurnResult {
  readonly sessionId: string | null;
  readonly text: string;
  readonly outcome: TurnOutcome;
  readonly usage: TokenTotals | null;
  readonly contextUsage: ContextUsage | null;
  /** True only after owned disposal settles, or before session creation began. */
  readonly cleanupConfirmed: boolean;
  readonly rejection?: { readonly code: RejectionCode; readonly reason: string };
  readonly timedOut?: true;
  /** Typed startup failures let existing callers invalidate a missing native binding. */
  readonly error?: OarErrorInfo;
}

export type OarOutputRecord =
  | { readonly type: "ready"; readonly runtime: OarRuntimeId; readonly sessionId: string }
  | { readonly type: "event"; readonly event: Event }
  | { readonly type: "native"; readonly frame: RawEvent }
  | { readonly type: "result"; readonly result: OarTurnResult };

export interface OarRunOptions {
  readonly signal?: AbortSignal;
  /** Covers opening as well as the turn. The owning process also enforces a hard limit. */
  readonly timeoutMs?: number;
  readonly onRecord?: (record: OarOutputRecord) => void;
}

export interface OarRuntimeDependencies {
  /** A real OAR runtime can be injected without a vendor binary or account. */
  readonly loadRuntime?: (config: OarSessionConfig) => Runtime | Promise<Runtime>;
}

const CLEANUP_TIMEOUT_MS = 5_000;

class OarTimeoutError extends Error {
  override readonly name = "OarTimeoutError";
  constructor() { super("OAR execution exceeded its timeout"); }
}

function stopped(signal: AbortSignal): Error {
  return signal.reason instanceof OarTimeoutError
    ? signal.reason
    : Object.assign(new Error("OAR execution was cancelled"), { name: "AbortError" });
}

function deadline(options: Pick<OarRunOptions, "signal" | "timeoutMs">): {
  signal: AbortSignal;
  disarm: () => void;
} {
  if (options.timeoutMs !== undefined && (!Number.isFinite(options.timeoutMs)
    || options.timeoutMs <= 0 || options.timeoutMs > 2_147_483_647)) {
    throw new RangeError("timeoutMs must be positive and no greater than 2147483647");
  }
  const controller = new AbortController();
  const onAbort = (): void => { controller.abort(options.signal?.reason); };
  if (options.signal?.aborted) onAbort();
  else options.signal?.addEventListener("abort", onAbort, { once: true });
  const timer = options.timeoutMs === undefined ? undefined
    : setTimeout(() => { controller.abort(new OarTimeoutError()); }, options.timeoutMs);
  return {
    signal: controller.signal,
    disarm: () => {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onAbort);
    },
  };
}

/** Cancelling a wait never turns a rejected in-flight promise into an unhandled rejection. */
async function untilStopped<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  let onAbort: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    onAbort = () => { reject(stopped(signal)); };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  });
  try { return await Promise.race([work, cancellation]); }
  finally { if (onAbort) signal.removeEventListener("abort", onAbort); }
}

/** Use OAR's turn correlation, while releasing the watcher even if cleanup fails. */
async function waitForTurn(session: Session, afterSeq: number, inputId: string, signal: AbortSignal): Promise<TurnOutcome> {
  let unsubscribe: (() => void) | undefined;
  const ended = new Promise<TurnOutcome>((resolve) => {
    unsubscribe = session.rawEvents((record) => {
      if (record.seq <= afterSeq || record.sessionId !== session.id || record.agentPath.length !== 0) return;
      const terminal = record.kind === "response" && record.body.kind === "exited"
        || record.kind === "frame" && record.body.events.some((event) => event.kind === "turn_ended" || event.kind === "input_dropped");
      if (!terminal) return;
      const outcome = turnEndAfter(session.records(), afterSeq, session.id, inputId);
      if (outcome) resolve(outcome);
    }, { sessionId: session.id, afterSeq });
  });
  try { return await untilStopped(ended, signal); }
  finally { unsubscribe?.(); }
}

function observer(callback?: OarRunOptions["onRecord"]): (record: OarOutputRecord) => void {
  return (record) => {
    try { callback?.(record); } catch { /* Observation cannot change runtime ownership. */ }
  };
}

function redact(text: string, config: OarSessionConfig): string {
  let result = text;
  for (const [name, value] of Object.entries(config.env ?? {})) {
    if (typeof value === "string" && value.length > 0 && /token|key|secret|password|credential|authorization|cookie/i.test(name)) {
      result = result.split(value).join("[redacted]");
    }
  }
  return result;
}

function errorInfo(error: unknown, config: OarSessionConfig): OarErrorInfo {
  const value = error instanceof Error ? error : new Error(String(error));
  const detail = value as Error & { code?: unknown; sessionId?: unknown };
  const cause = value.cause as { method?: unknown } | undefined;
  return {
    name: value.name,
    message: redact(value.message, config),
    ...(typeof detail.code === "string" ? { code: detail.code } : {}),
    ...(typeof detail.sessionId === "string" ? { sessionId: detail.sessionId } : {}),
    ...(cause && typeof cause.method === "string" ? { method: cause.method } : {}),
  };
}

function failed(error: unknown, config: OarSessionConfig, sessionId: string | null = null): OarTurnResult {
  const info = errorInfo(error, config);
  const detail = error as { failure?: FailureClass; status?: number; credential?: "missing" | "rejected" } | undefined;
  const outcome: TurnOutcome = info.name === "AbortError"
    ? { kind: "aborted" }
    : {
      kind: "failed",
      failure: error instanceof OarTimeoutError ? "limit_reached"
        : info.name === "RuntimeFailureError" && detail?.failure ? detail.failure : "unknown",
      reason: info.message,
      ...(typeof detail?.status === "number" ? { status: detail.status } : {}),
      ...(detail?.failure === "auth" && detail.credential ? { credential: detail.credential } : {}),
    };
  return {
    sessionId, text: "", outcome, usage: null, contextUsage: null, cleanupConfirmed: false,
    ...(error instanceof OarTimeoutError ? { timedOut: true } : {}),
    error: info,
  };
}

function sessionOptions(config: OarSessionConfig): SessionOptions {
  const sandbox = config.sandbox ?? "read-only";
  if (!["read-only", "workspace-write", "danger-full-access"].includes(sandbox)) {
    throw new Error("Unsupported Codex sandbox mode");
  }
  if (typeof config.cwd !== "string" || config.cwd.trim().length === 0) {
    throw new Error("An OAR session requires a working directory");
  }
  if (config.command !== undefined && (config.runtime !== "codex" || config.command.trim().length === 0)) {
    throw new Error("command must name a Codex executable; Pi is bundled with OAR");
  }
  return {
    cwd: config.cwd,
    ...(config.env === undefined ? {} : { env: config.env }),
    ...(config.model === undefined ? {} : { model: config.model }),
    ...(config.effort === undefined ? {} : { effort: config.effort }),
    ...(config.serviceTier === undefined ? {} : { serviceTier: config.serviceTier }),
    ...(config.resume === undefined ? {} : { resume: config.resume }),
    ...(config.outputSchema === undefined ? {} : { outputSchema: config.outputSchema }),
    ...(config.mcpServers === undefined ? {} : { mcpServers: config.mcpServers }),
    ...(config.runtime === "codex"
      // OAR's sandbox default is danger-full-access. The caller's admitted sandbox
      // goes last so a lower-level launch argument cannot expand its authority.
      ? { launchArgs: [...(config.launchArgs ?? []), "-c", `sandbox_mode=${JSON.stringify(sandbox)}`] }
      : config.launchArgs === undefined ? {} : { launchArgs: config.launchArgs }),
  };
}

/** Per-runtime imports keep Pi and DSH dependencies out of a Codex-only process. */
export async function loadOarRuntime(config: OarSessionConfig): Promise<Runtime> {
  switch (config.runtime) {
    case "codex": return (await import("@botiverse/oar/runtimes/codex")).codexRuntime;
    case "pi": return (await import("@botiverse/oar/runtimes/pi")).createPiRuntime({ execution: "subprocess" });
    case "dsh": return (await import("./dsh.ts")).createDshRuntime(config.dsh ?? {});
    default: throw new Error(`Unsupported OAR runtime: ${String(config.runtime)}`);
  }
}

async function disposeWithinLimit(session: Session): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      session.dispose(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => { reject(new Error("OAR session cleanup did not settle; runtime exit is unconfirmed")); }, CLEANUP_TIMEOUT_MS);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

/** OAR cannot cancel open. A late session remains owned and is disposed without a prompt. */
async function acquireSession(opening: Promise<Session>, signal: AbortSignal): Promise<Session> {
  return new Promise<Session>((resolve, reject) => {
    let settled = false;
    const onAbort = (): void => {
      if (settled) return;
      settled = true;
      reject(stopped(signal));
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
    void opening.then((session) => {
      signal.removeEventListener("abort", onAbort);
      if (settled) {
        // The enclosing managed process remains the hard cleanup boundary if a
        // native open never settles or disposal cannot confirm process exit.
        void disposeWithinLimit(session).catch(() => {});
        return;
      }
      settled = true;
      resolve(session);
    }, (error: unknown) => {
      signal.removeEventListener("abort", onAbort);
      if (!settled) { settled = true; reject(error); }
    });
  });
}

export class ManagedOarSession {
  readonly session: Session;
  private readonly config: OarSessionConfig;
  private readonly unsubscribers: (() => void)[];
  private disposal: Promise<void> | undefined;

  constructor(session: Session, config: OarSessionConfig, emit: (record: OarOutputRecord) => void) {
    this.session = session;
    this.config = config;
    const cursor = { sessionId: session.id, afterSeq: -1 };
    // Raw replay precedes normalized replay. Within each stream, OAR supplies
    // its own order and attribution; the host never reconstructs native facts.
    this.unsubscribers = [
      session.rawEvents((frame) => { emit({ type: "native", frame }); }, cursor),
      session.events((event) => { emit({ type: "event", event }); }, { cursor }),
    ];
    emit({ type: "ready", runtime: config.runtime, sessionId: session.id });
  }

  steer(input: string, options?: InputOptions): Promise<ControlOutcome> {
    if (!this.session.steer) return Promise.reject(new Error("This runtime does not support steering"));
    return this.session.steer(input, options);
  }

  queue(input: string, options?: InputOptions): Promise<ControlOutcome> { return this.session.queue(input, options); }
  abort(): Promise<ControlOutcome> { return this.session.abort(); }

  dispose(): Promise<void> {
    this.disposal ??= disposeWithinLimit(this.session).finally(() => {
      for (const unsubscribe of this.unsubscribers) unsubscribe();
    });
    return this.disposal;
  }

  /** Returns one turn result; the session stays open for the caller's next turn. */
  async runTurn(input: string, options: Pick<OarRunOptions, "signal" | "timeoutMs"> = {}): Promise<OarTurnResult> {
    const limit = deadline(options);
    const inputId = randomUUID();
    const messages = new Map<string, string>();
    let text = "";
    let waitingForInput = false;
    let textEnded = false;
    const output = (): string => {
      let answer = text;
      for (const message of messages.values()) if (message.trim().length > 0) answer = message;
      return answer;
    };
    const collected = (result: OarTurnResult): OarTurnResult => ({
      ...result,
      text: output(),
      usage: this.session.usage().value.total,
      contextUsage: this.session.contextUsage().value,
    });
    const unsubscribe = this.session.events((event) => {
      if (event.sessionId !== this.session.id || event.agentPath.length !== 0) return;
      if (event.kind === "input_queued" && event.inputId === inputId) waitingForInput = true;
      if (event.kind === "turn_active" && event.inputId === inputId) waitingForInput = false;
      if (event.kind === "turn_ended" && !waitingForInput) textEnded = true;
      if (event.kind !== "text_delta" || waitingForInput || textEnded) return;
      text += event.text;
      if (event.messageId !== undefined) messages.set(event.messageId, (messages.get(event.messageId) ?? "") + event.text);
    });
    const cancel = (): void => {
      // Abort acknowledgement can stall independently of disposal. Request both;
      // only OAR owns/signals native processes, and dispose is idempotent here.
      void this.session.abort().catch(() => {});
      void this.dispose().catch(() => {});
    };
    try {
      if (limit.signal.aborted) return failed(stopped(limit.signal), this.config, this.session.id);
      limit.signal.addEventListener("abort", cancel, { once: true });
      const admitted = await untilStopped(this.session.prompt(input, { inputId }), limit.signal);
      if (admitted.kind === "rejected") {
        return {
          sessionId: this.session.id, text: "", usage: null, contextUsage: null, cleanupConfirmed: false,
          outcome: { kind: "failed", failure: "invalid_request", reason: redact(admitted.reason, this.config) },
          rejection: { code: admitted.code, reason: redact(admitted.reason, this.config) },
        };
      }
      const outcome = await waitForTurn(this.session, admitted.seq, inputId, limit.signal);
      if (limit.signal.aborted) return collected(failed(stopped(limit.signal), this.config, this.session.id));
      return collected({ sessionId: this.session.id, text: "", outcome, usage: null, contextUsage: null, cleanupConfirmed: false });
    } catch (error) {
      return collected(failed(limit.signal.aborted ? stopped(limit.signal) : error, this.config, this.session.id));
    } finally {
      limit.signal.removeEventListener("abort", cancel);
      limit.disarm();
      unsubscribe();
    }
  }
}

export async function openOarSession(
  config: OarSessionConfig,
  options: OarRunOptions = {},
  dependencies: OarRuntimeDependencies = {},
): Promise<ManagedOarSession> {
  return openOwnedSession(config, options, dependencies, { cleanupConfirmed: true });
}

async function openOwnedSession(
  config: OarSessionConfig,
  options: OarRunOptions,
  dependencies: OarRuntimeDependencies,
  ownership: { cleanupConfirmed: boolean },
): Promise<ManagedOarSession> {
  const limit = deadline(options);
  try {
    if (limit.signal.aborted) throw stopped(limit.signal);
    const nativeOptions = sessionOptions(config);
    const runtime = await untilStopped(Promise.resolve((dependencies.loadRuntime ?? loadOarRuntime)(config)), limit.signal);
    const env = config.command === undefined ? config.env : { ...config.env, OAR_CODEX_BIN: config.command };
    ownership.cleanupConfirmed = false;
    const probing = Promise.resolve().then(() => runtime.installation?.({ env })).then(
      (installation) => { ownership.cleanupConfirmed = true; return installation; },
      (error: unknown) => { ownership.cleanupConfirmed = true; throw error; },
    );
    const installation = await untilStopped(probing, limit.signal);
    if (limit.signal.aborted) throw stopped(limit.signal);
    if (installation?.kind !== "available") {
      throw new Error(installation?.kind === "unsupported" ? installation.reason : `OAR runtime ${config.runtime} is not installed`);
    }
    ownership.cleanupConfirmed = false;
    const session = await acquireSession(runtime.session(installation, nativeOptions), limit.signal);
    const managed = new ManagedOarSession(session, config, observer(options.onRecord));
    if (limit.signal.aborted) {
      await managed.dispose();
      ownership.cleanupConfirmed = true;
      throw stopped(limit.signal);
    }
    return managed;
  } finally { limit.disarm(); }
}

/** One owned native session, one prompt, a terminal result only after cleanup. */
export async function runOarTurn(
  config: OarSessionConfig,
  input: string,
  options: OarRunOptions = {},
  dependencies: OarRuntimeDependencies = {},
): Promise<OarTurnResult> {
  const emit = observer(options.onRecord);
  let managed: ManagedOarSession | undefined;
  let result: OarTurnResult;
  let limit: ReturnType<typeof deadline> | undefined;
  const ownership = { cleanupConfirmed: true };
  try {
    limit = deadline(options);
    managed = await openOwnedSession(config, { signal: limit.signal, onRecord: emit }, dependencies, ownership);
    result = await managed.runTurn(input, { signal: limit.signal });
  } catch (error) {
    result = { ...failed(limit?.signal.aborted ? stopped(limit.signal) : error, config, managed?.session.id ?? null),
      cleanupConfirmed: ownership.cleanupConfirmed };
  } finally { limit?.disarm(); }
  if (managed) {
    try { await managed.dispose(); result = { ...result, cleanupConfirmed: true }; }
    catch (error) {
      result = { ...result, ...failed(error, config, managed.session.id), text: result.text,
        usage: result.usage, contextUsage: result.contextUsage };
    }
  }
  emit({ type: "result", result });
  return result;
}
