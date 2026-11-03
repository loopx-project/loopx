/** DSH's existing Python SDK, hosted through OAR's public runtime-author SPI. */
import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { defineRuntime, UnsupportedOptionError } from "@botiverse/oar/browser";
import { createSessionKernel, inputImagesRefusal, sealSession } from "@botiverse/oar/kernel";
import type {
  InputOptions, RawEvent, RefusedSessionOptions, Runtime, RuntimeEventBody, SessionCapabilities,
  SessionOptions, TokenTotals, TurnOutcome,
} from "@botiverse/oar";

/** Provider-specific launch configuration; model/cwd/effort/env use SessionOptions. */
export interface DshRuntimeConfig {
  readonly python_executable?: string;
  readonly worker_path?: string;
  readonly provider?: string;
  readonly dsh_home?: string;
  readonly max_tokens?: number | null;
  readonly cordis?: string | null;
  readonly runtime_bin?: string | null;
  readonly request_timeout_seconds?: number | null;
}

type JsonObject = Record<string, unknown>;
const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
const text = (value: unknown): string | undefined => typeof value === "string" ? value : undefined;
const finite = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;

const refusedSessionOptions: RefusedSessionOptions = {
  systemPrompt: "DSH system prompts are configured by its Cordis composition",
  appendSystemPrompt: "DSH system prompts are configured by its Cordis composition",
  mcpServers: "Attach DSH MCP servers through its Cordis composition",
  disallowedTools: "DSH tool restrictions are configured by its permission mode and composition",
  launchArgs: "The pinned DSH SDK owns its runtime launch arguments",
  additionalDirectories: "The pinned DSH SDK exposes one workspace",
  outputSchema: "The pinned DSH SDK has no native output-schema option",
  name: "The pinned DSH SDK has no native session-name option",
  serviceTier: "The pinned DSH SDK has no native service-tier option",
  userConfiguration: "The pinned DSH SDK cannot exclude user configuration",
};

function checkOptions(options: SessionOptions): void {
  for (const [key, reason] of Object.entries(refusedSessionOptions)) {
    const value = options[key as keyof SessionOptions];
    if (key === "userConfiguration" ? value === "exclude"
      : Array.isArray(value) ? value.length > 0 : value !== undefined) {
      throw new UnsupportedOptionError(key as keyof SessionOptions, reason!);
    }
  }
}

function environment(overlay: SessionOptions["env"]): NodeJS.ProcessEnv {
  const env = { ...process.env };
  for (const [name, value] of Object.entries(overlay ?? {})) {
    if (value === null) delete env[name];
    else env[name] = value;
  }
  return env;
}

function credentialRedactor(env: NodeJS.ProcessEnv): <T extends RawEvent>(record: T) => T {
  // The public kernel accepts a redactor; OAR's built-in credential selector
  // is private. Mask known secret values before retention and observation.
  const secrets = Object.entries(env).filter(([name, value]) =>
    /token|key|secret|password|credential|authorization|cookie/i.test(name)
    && !/(?:file|path|dir)$/i.test(name) && typeof value === "string" && value.length >= 8)
    .map(([, value]) => value!).sort((left, right) => right.length - left.length);
  const redact = (value: unknown): unknown => {
    if (typeof value === "string") {
      for (const secret of secrets) value = (value as string).split(secret).join("[redacted]");
      return value;
    }
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, part]) => [key, redact(part)]));
    }
    return value;
  };
  return <T extends RawEvent>(record: T): T => redact(record) as T;
}

function sdkConfig(config: DshRuntimeConfig, options: SessionOptions): JsonObject {
  const result: JsonObject = { cwd: options.cwd };
  const values: JsonObject = {
    provider: config.provider, model: options.model, reasoning_effort: options.effort,
    dsh_home: config.dsh_home, max_tokens: config.max_tokens,
    dsh_bin: config.runtime_bin, request_timeout_seconds: config.request_timeout_seconds,
  };
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null) result[key] = value;
  }
  if (config.cordis) result.patches = [config.cordis];
  if (options.env) {
    // Removals were applied to the worker environment; the Python SDK accepts
    // only string overrides. Never replace the caller's DSH_PERMISSION_MODE.
    result.env = Object.fromEntries(Object.entries(options.env).filter((entry) => entry[1] !== null));
  }
  return result;
}

function nativeOutcome(result: JsonObject): TurnOutcome | null {
  if (typeof result.final_response !== "string" || !Array.isArray(result.events)
    || result.events.some((event) => event === null || typeof event !== "object" || Array.isArray(event))) {
    return null;
  }
  const terminal = result.events.map(object).findLast((event) => event.type === "turn/end");
  const reason = object(object(terminal?.data).reason);
  if (typeof reason.kind !== "string" || result.finish_reason !== reason.kind) {
    return null;
  }
  switch (reason.kind) {
    case "completed":
    case "stop":
      return { kind: "completed" };
    case "aborted":
      return { kind: "aborted" };
    case "max-tokens":
      return { kind: "failed", failure: "limit_reached", reason: "DSH reached its output-token limit" };
    default:
      return {
        kind: "failed", failure: "unknown",
        reason: text(object(reason.error).message) ?? `DSH turn ended: ${reason.kind}`,
      };
  }
}

/**
 * Only proven native fields enter the cross-runtime projection. Raw frames
 * preserve unknown/plugin events, descendant sessions, and failure details.
 * DSH input/cache token counts are disjoint; OAR input includes cache counts.
 */
function projectEvent(event: JsonObject, totals: { value: TokenTotals }): RuntimeEventBody[] {
  const data = object(event.data), message = object(data.message);
  const events: RuntimeEventBody[] = [];
  if (event.type === "turn/start") events.push({ kind: "turn_active" });
  if (event.type === "request/context" && typeof data.model === "string") {
    events.push({ kind: "model", model: data.model });
  }
  if (event.type === "assistant/message") {
    for (const value of Array.isArray(message.content) ? message.content : []) {
      const block = object(value);
      const messageId = text(message.id);
      if (block.type === "text" && typeof block.text === "string") {
        events.push({ kind: "text_delta", text: block.text, ...(messageId ? { messageId } : {}) });
      } else if (block.type === "reasoning" && typeof block.text === "string") {
        events.push({ kind: "reasoning", content: { kind: "text", text: block.text }, ...(messageId ? { messageId } : {}) });
      }
    }
    const usage = object(data.usage), input = finite(usage.inputTokens), output = finite(usage.outputTokens);
    if (input !== undefined && output !== undefined) {
      const previous = totals.value;
      const cacheRead = finite(usage.cacheReadTokens), cacheWrite = finite(usage.cacheWriteTokens);
      const reasoning = finite(usage.reasoningTokens);
      totals.value = { ...previous, input: previous.input + input + (cacheRead ?? 0) + (cacheWrite ?? 0), output: previous.output + output,
        ...(cacheRead === undefined ? {} : { cacheRead: (previous.cacheRead ?? 0) + cacheRead }),
        ...(cacheWrite === undefined ? {} : { cacheWrite: (previous.cacheWrite ?? 0) + cacheWrite }),
        ...(reasoning === undefined ? {} : { reasoning: (previous.reasoning ?? 0) + reasoning }),
      };
      events.push({ kind: "usage", usage: { tokens: totals.value } });
    }
  }
  if (event.type === "tool/call" && typeof data.callId === "string" && typeof data.name === "string") {
    events.push({ kind: "tool_call_started", callId: data.callId, tool: data.name,
      ...(typeof data.arguments === "string" ? { input: data.arguments } : {}) });
  }
  if (event.type === "tool/result" && typeof message.toolCallId === "string") {
    const content = (Array.isArray(message.content) ? message.content : []).map((part) => {
      const block = object(part);
      return block.type === "text" && typeof block.text === "string"
        ? { type: "text" as const, text: block.text }
        : { type: "other" as const, value: part };
    });
    events.push({ kind: "tool_call_ended", callId: message.toolCallId, content,
      ...(typeof message.isError === "boolean" ? { result: message.isError ? "failed" as const : "ok" as const } : {}) });
  }
  if (event.type === "compaction/start") events.push({ kind: "compaction_started" });
  if (event.type === "compaction/end") events.push({ kind: "compaction_ended",
    outcome: typeof data.error === "string" ? "failed" : "completed",
    ...(typeof data.error === "string" ? { reason: data.error } : {}) });
  return events;
}

export function createDshRuntime(config: DshRuntimeConfig = {}): Runtime {
  const python = config.python_executable ?? "python3";
  const worker = config.worker_path ?? fileURLToPath(new URL("./dsh_worker.py", import.meta.url));
  const runtime = defineRuntime({
    id: "dsh", brand: { name: "DeepSeek Harness", icon: null }, refusedSessionOptions,
    installation: async (options) => {
      try {
        const { stdout } = await promisify(execFile)(python, [worker, "--probe"], {
          env: environment(options?.env), encoding: "utf8", timeout: 10_000, maxBuffer: 64 * 1024,
        });
        return JSON.parse(stdout).available === true
          ? { kind: "available", via: "bundled" }
          : { kind: "not_found" };
      } catch {
        return { kind: "unsupported", reason: "DSH requires the configured Python interpreter and loopx[deepseek-harness]" };
      }
    },
    session: async (_installation, options) => {
      checkOptions(options);
      // Preserve the existing SDK's caller-selected native identity. Its
      // session/prompt API does not expose durable resume: an existing id may
      // fail. Never switch ids or retry fresh after that native failure.
      const kernel = createSessionKernel(options.resume, credentialRedactor(environment(options.env)));
      // OAR requires queue durability metadata but has no availability flag.
      // This bounded SDK Session refuses queue() and owns at most one prompt.
      const capabilities: SessionCapabilities = { queue: { durable: false }, attribution: "opaque", images: false };
      const totals = { value: { input: 0, output: 0 } as TokenTotals };
      type Running = { child: ChildProcessWithoutNullStreams; done: Promise<void>; finished: boolean; stopping?: Promise<void> };
      const children = new Set<Running>();
      let active: Running | null = null;
      let prompted = false;
      let disposing: Promise<void> | undefined;

      const stop = (run: Running): Promise<void> => {
        if (!children.has(run)) return Promise.resolve();
        run.stopping ??= (async () => {
          run.child.kill("SIGTERM");
          const force = setTimeout(() => run.child.kill("SIGKILL"), 4_500);
          force.unref();
          try { await run.done; } finally { clearTimeout(force); }
        })();
        return run.stopping;
      };

      function start(input: string): void {
        const child = spawn(python, [worker], {
          cwd: options.cwd, env: environment(options.env), stdio: ["pipe", "pipe", "pipe"],
          // The enclosing LoopX process runner owns the group, including DSH tools.
          detached: false,
        });
        let closed!: () => void;
        const run: Running = { child, done: new Promise((resolve) => { closed = resolve; }), finished: false };
        active = run;
        children.add(run);
        child.stderr.resume();
        child.stdin.on("error", () => { /* Process close reports failed transport. */ });
        child.once("error", () => { /* A failed spawn is followed by close. */ });
        const lines = createInterface({ input: child.stdout });
        lines.on("line", (line) => {
          let frame: JsonObject;
          try { frame = object(JSON.parse(line)); }
          catch {
            kernel.frame({ type: "dsh/stdout", native: line, events: [] });
            void stop(run);
            return;
          }
          if (frame.type === "notification") {
            const native = object(frame.notification), payload = object(native.payload), event = object(payload.event);
            const sessionId = text(payload.sessionId) ?? kernel.sessionId;
            kernel.frame({ type: text(event.type) ?? text(native.method) ?? "dsh/notification", native,
              events: sessionId === kernel.sessionId ? projectEvent(event, totals) : [] }, { sessionId });
          } else if (frame.type === "result") {
            const native = object(frame.result);
            const outcome = nativeOutcome(native);
            run.finished = outcome !== null;
            if (run.finished && active === run) active = null;
            // SDK RunResult owns the interval through whole-agent idle. An
            // intermediate turn/end must not end OAR before this result exists.
            kernel.frame({ type: "dsh/result", native,
              events: outcome === null ? [] : [{ kind: "turn_ended", outcome }] });
          } else {
            kernel.frame({ type: `dsh/${text(frame.type) ?? "unknown"}`, native: frame.error ?? frame, events: [] });
          }
        });
        child.once("close", (code) => {
          lines.close();
          children.delete(run);
          if (active === run) active = null;
          if (!run.finished) {
            kernel.respond("", { kind: "exited", code });
          }
          closed();
        });
        child.stdin.end(JSON.stringify({ prompt: input, session_id: kernel.sessionId, config: sdkConfig(config, options) }) + "\n");
      }

      const accept = (input: string, inputOptions: InputOptions | undefined) => {
        const refused = inputImagesRefusal(capabilities, inputOptions?.images);
        if (refused) return refused;
        if (active !== null) return { kind: "rejected" as const, code: "busy" as const, reason: "DSH is running a turn" };
        if (prompted) return { kind: "rejected" as const, code: "unsupported" as const,
          reason: "The pinned DSH SDK supports one native Turn per OAR Session; repeated prompts are unsupported" };
        prompted = true;
        start(input);
        return { kind: "accepted" as const };
      };
      return sealSession({
        id: kernel.sessionId, capabilities,
        prompt: (input, inputOptions) => kernel.control({ kind: "prompt", input, ...inputOptions }, () => accept(input, inputOptions)),
        queue: (input, inputOptions) => kernel.control({ kind: "queue", input, ...inputOptions }, () => ({
          kind: "rejected", code: "unsupported",
          reason: "The pinned DSH SDK supports one native Turn per OAR Session; queued input is unsupported",
        })),
        abort: () => kernel.control({ kind: "abort" }, () => {
          if (active === null) return { kind: "rejected", code: "no_active_turn", reason: "DSH has no active turn" };
          void stop(active);
          return { kind: "accepted" };
        }),
        rawEvents: (observer, cursor) => kernel.rawEvents(observer, cursor),
        records: () => kernel.records(), graph: () => kernel.graph(),
        dispose: () => {
          if (disposing) return disposing;
          const request = kernel.request("toRuntime", { kind: "dispose" });
          disposing = Promise.all([...children].map(stop)).then(() => { kernel.respond(request.id, { kind: "accepted" }); });
          return disposing;
        },
      });
    },
  });
  return runtime;
}
