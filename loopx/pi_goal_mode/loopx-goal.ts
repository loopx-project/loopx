// <!-- loopx-managed-slash-command:v1 command=/loopx surface=pi-extension -->
//
// LoopX Pi goal extension — the Pi host adapter for LoopX.
//
// Pi is a terminal coding agent whose extensions register commands, tools, and
// event handlers. This extension gives a Pi session a LoopX surface:
//
// - `/loopx` inspects the project packet, or starts a guided LoopX goal when
//   arguments are provided.
// - `loopx_goal_activate` binds the current session to a LoopX goal after
//   `loopx start-goal` wrote todos and produced a heartbeat task_body.
// - `loopx_task_lease` exposes the existing task_lease_v0 CLI contract for
//   explicit acquire/renew/transfer/release/inspect calls; it never automates
//   lease lifecycle actions or creates a second lease store.
// - Once bound, every `agent_settled` continuation runs through
//   `loopx quota should-run --runtime-profile generic_cli`; LoopX decides
//   whether to continue (injecting the heartbeat task_body as a follow-up),
//   wait with backoff, or stop only at a validated terminal no-follow-up.
//
// The extension is self-contained: pi's extension loader aliases `typebox` and
// the `@earendil-works/*` packages, so no local node_modules are required.
// The quota/wait/store loop core lives in the sibling
// `pi-goal-loop-runtime.mjs` (installed alongside, not auto-discovered as an
// extension); it is directly executable by node:test, and session shutdown
// atomically disposes it so an in-flight quota probe can never continue the
// old session past a reload / session-replacement boundary.
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Text } from "@earendil-works/pi-tui";
import { execFile as execFileCallback } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { Type } from "typebox";
import {
  buildQuotaArgs,
  PI_ACTIVATION_SCHEMA_VERSION,
  PI_SESSION_AUTHORITY_SCHEMA_VERSION,
  TASK_LEASE_CAPABILITY,
  runPiTaskLease,
  createBindingStore,
  createEphemeralSessionIdentity,
  createGoalLoop,
  hasAbortedAssistantMessage,
  sessionKey,
} from "./pi-goal-loop-runtime.mjs";

const execFile = promisify(execFileCallback);
const LOOPX_CLI_TIMEOUT_MS = 30_000;
const LOOPX_INSPECT_COMMANDS = new Set(["", "status", "history", "list", "resume"]);

function packetString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function rejectedCliReturncode(error: unknown): number {
  const failure = error as { returncode?: unknown; code?: unknown };
  if (typeof failure.returncode === "number") return failure.returncode;
  if (typeof failure.code === "number") return failure.code;
  return 1;
}

async function runLoopxCli(args: string[], directory: string): Promise<string> {
  const { stdout } = await execFile(process.env.LOOPX_BIN || "loopx", args, {
    cwd: directory,
    timeout: LOOPX_CLI_TIMEOUT_MS,
    maxBuffer: 8 * 1024 * 1024,
  });
  return stdout;
}

type TaskLeaseCliResult = {
  stdout?: string;
  returncode?: number;
};

type PiSessionAuthority = {
  schemaVersion: string;
  token: string;
  goalId: string;
  agentId: string;
  registryPath: string;
  availableCapabilities: string[];
};

type StartGoalPacket = {
  ok?: boolean;
  goal_id?: unknown;
  project?: unknown;
  project_connection?: { registry?: unknown };
  host_loop_activation?: {
    activation_allowed?: unknown;
    agent_id?: unknown;
    available_capabilities?: unknown;
  };
  command_pack?: {
    project?: unknown;
    goal_id?: unknown;
    agent_id?: unknown;
    registry_path?: unknown;
    project_connection?: { registry?: unknown; connection_state?: unknown };
    recommended_next_step?: { kind?: unknown; summary?: unknown };
    host_loop_activation?: {
      activation_allowed?: unknown;
      agent_id?: unknown;
      available_capabilities?: unknown;
    };
  };
  message?: unknown;
};

function parseJsonObject(stdout: string): StartGoalPacket | null {
  try {
    const parsed: unknown = JSON.parse(stdout || "");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as StartGoalPacket)
      : null;
  } catch {
    return null;
  }
}

// A widget is a fixed-height area near the editor, so a long packet preview has
// to be summarized into a few clamped lines; the full text stays in the
// `loopx-packet` session entry.
const STATUS_WIDGET_LINE_MAX = 96;

function widgetLine(text: string): string {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > STATUS_WIDGET_LINE_MAX
    ? `${value.slice(0, STATUS_WIDGET_LINE_MAX - 1)}…`
    : value;
}

function buildLoopxStatusWidget(packet: StartGoalPacket | null): string[] | undefined {
  if (!packet) return undefined;
  const connection = packet.project_connection || {};
  const next = packet.recommended_next_step || {};
  const project = typeof packet.project === "string" ? packet.project : "";
  const goalId = typeof packet.goal_id === "string" ? packet.goal_id : "";
  const state = typeof connection.connection_state === "string" ? connection.connection_state : "";
  const nextText =
    typeof next.summary === "string" ? next.summary : typeof next.kind === "string" ? next.kind : "";
  return [
    widgetLine(`LoopX · ${goalId || "(no goal id)"}`),
    widgetLine(
      `state: ${state || "unknown"}${project ? ` · ${project.split("/").filter(Boolean).pop()}` : ""}`,
    ),
    widgetLine(`next: ${nextText || "-"}`),
  ];
}

function buildPiSessionAuthority(packet: StartGoalPacket): PiSessionAuthority {
  const commandPack = packet.command_pack || {};
  const activation = packet.host_loop_activation || commandPack.host_loop_activation || {};
  if (activation.activation_allowed !== true) {
    throw new Error("LoopX start-goal packet does not authorize Pi host activation");
  }
  const goalId = packetString(packet.goal_id) || packetString(commandPack.goal_id);
  const project = packetString(packet.project) || packetString(commandPack.project);
  const registryPath =
    packetString(packet.project_connection?.registry) ||
    packetString(commandPack.project_connection?.registry) ||
    packetString(commandPack.registry_path);
  const agentId =
    packetString(activation.agent_id) ||
    packetString(packet.agent_id) ||
    packetString(commandPack.agent_id);
  if (!goalId || !project || !registryPath || !agentId) {
    throw new Error("LoopX start-goal packet is missing verified Pi authority fields");
  }
  const packetCapabilities = Array.isArray(activation.available_capabilities)
    ? activation.available_capabilities.map(packetString).filter(Boolean)
    : [];
  const availableCapabilities = [...new Set(packetCapabilities)].sort((left, right) =>
    left.localeCompare(right),
  );
  return {
    schemaVersion: PI_SESSION_AUTHORITY_SCHEMA_VERSION,
    token: randomUUID(),
    goalId,
    agentId,
    registryPath,
    availableCapabilities,
  };
}

function authorityFieldsEqual(left: PiSessionAuthority, right: PiSessionAuthority): boolean {
  return left.goalId === right.goalId &&
    left.agentId === right.agentId &&
    left.registryPath === right.registryPath &&
    JSON.stringify(left.availableCapabilities) === JSON.stringify(right.availableCapabilities);
}

function packetNeedsHostSelection(packet: StartGoalPacket): boolean {
  const activation = packet?.host_loop_activation ?? packet?.command_pack?.host_loop_activation;
  return !activation || activation.activation_allowed !== true;
}

// execFile rejects on typed non-zero CLI outcomes. Node preserves the JSON
// stdout on that error, so task_lease_v0 remains the authoritative result.
async function runLoopxTaskLeaseCli(
  args: string[],
  directory: string,
): Promise<TaskLeaseCliResult> {
  try {
    const { stdout } = await execFile(process.env.LOOPX_BIN || "loopx", args, {
      cwd: directory,
      timeout: LOOPX_CLI_TIMEOUT_MS,
      maxBuffer: 8 * 1024 * 1024,
    });
    return { stdout, returncode: 0 };
  } catch (error) {
    const failure = error as { stdout?: unknown; returncode?: unknown; code?: unknown };
    return {
      stdout: typeof failure.stdout === "string" ? failure.stdout : "",
      returncode: rejectedCliReturncode(failure),
    };
  }
}

async function probeLoopxQuota(binding: Record<string, unknown>): Promise<Record<string, unknown>> {
  const args = buildQuotaArgs(binding);
  const stdout = await runLoopxCli(args, String(binding.directory || ""));
  const decision: unknown = JSON.parse(stdout || "{}");
  if (!decision || typeof decision !== "object" || Array.isArray(decision)) {
    throw new Error("loopx quota should-run returned a non-object payload");
  }
  return decision as Record<string, unknown>;
}

export default function (pi: ExtensionAPI) {
  // Durable, TUI-only digest of the last inspected LoopX packet. Custom entries
  // never enter LLM context; the agent reads full state through the CLI itself.
  pi.registerEntryRenderer("loopx-packet", (entry, _options, theme) => {
    const data = (entry.data ?? {}) as { text?: string };
    const preview = String(data.text || "").split("\n").slice(0, 3).join("\n");
    return new Text(
      `${theme.fg("accent", "[loopx]")} packet ready:\n${preview || "(empty)"}`,
      0,
      0,
    );
  });

  const keyFor = (ctx: ExtensionContext) => {
    const file = ctx.sessionManager.getSessionFile();
    // Use the full path digest so two files whose first 161 bytes collide
    // never produce the same durable key.
    return file ? sessionKey(file) : ephemeral.key;
  };
  // One stable store instance per key, so the runtime's per-key commit queue
  // and compare-and-swap are shared across every event for the same session.
  const stores = new Map<string, ReturnType<typeof createBindingStore>>();
  // Host-issued authority is created only by the /loopx startup command. A
  // model-callable tool can present the token, but cannot mint or alter it.
  const sessionAuthorities = new Map<string, PiSessionAuthority>();

  // One loop per extension instance. Session services (store, idle probe,
  // notify) are bound per key on every event, and `session_shutdown` disposes
  // the whole instance so no old session can keep continuing.
  const loop = createGoalLoop({
    quotaProbe: probeLoopxQuota,
    sendMessage: (prompt: string) => {
      pi.sendUserMessage(prompt, { deliverAs: "followUp", triggerTurn: true });
    },
    setTimer: (callback: () => void, delayMs: number) => {
      const timer = setTimeout(callback, delayMs);
      if (typeof (timer as NodeJS.Timeout & { unref?: () => void })?.unref === "function") {
        (timer as NodeJS.Timeout & { unref: () => void }).unref();
      }
      return timer;
    },
    clearTimer: (timer: NodeJS.Timeout) => clearTimeout(timer),
  });
  // One unique, non-persisted identity per extension instance for ephemeral
  // sessions; declared here so keyFor stays stable within the run.
  const ephemeral = createEphemeralSessionIdentity();

  const bindContext = (ctx: ExtensionContext) => {
    const key = keyFor(ctx);
    const file = ctx.sessionManager.getSessionFile();
    let store = ephemeral.store;
    if (file) {
      const cached = stores.get(key);
      if (cached) {
        store = cached;
      } else {
        store = createBindingStore(ctx.cwd);
        stores.set(key, store);
      }
    }
    loop.bind(key, {
      store,
      isIdle: () => ctx.isIdle(),
      notify: (message: string, kind: string) => ctx.ui.notify(message, kind),
      authority: sessionAuthorities.get(key),
    });
    return { key, store };
  };

  const establishSessionAuthority = (key: string, packet: StartGoalPacket) => {
    const candidate = buildPiSessionAuthority(packet);
    const current = sessionAuthorities.get(key);
    if (current) {
      if (!authorityFieldsEqual(current, candidate)) {
        throw new Error("LoopX host authority cannot change within one Pi session");
      }
      return current;
    }
    loop.bindAuthority(key, candidate);
    sessionAuthorities.set(key, candidate);
    return candidate;
  };

  const inspectLoopx = async (key: string, ctx: ExtensionContext) => {
    try {
      const stdout = await runLoopxCli(
        ["--format", "json", "bootstrap-command-pack", "--project", "."],
        ctx.cwd,
      );
      const packet = parseJsonObject(stdout);
      if (packet) {
        try {
          establishSessionAuthority(key, packet);
        } catch {
          // A status-only packet may intentionally stop before host activation.
        }
      }
      const display = typeof packet?.message === "string" ? packet.message : stdout;
      // The packet preview is a long multi-section document. A widget is a
      // fixed-height area near the editor, so dumping the preview into it gets
      // chopped mid-sentence and covers the working indicator. Show a compact
      // status summary and keep the full text in the session entry instead.
      const status = buildLoopxStatusWidget(packet);
      if (status) ctx.ui.setWidget("loopx", status, { placement: "belowEditor" });
      ctx.ui.notify("LoopX packet ready (status widget below the editor).", "info");
      pi.appendEntry("loopx-packet", { text: display });
    } catch (error) {
      ctx.ui.notify(
        `LoopX inspect failed: ${(error as Error)?.message || String(error)}`,
        "error",
      );
    }
  };

  const startLoopx = async (
    trimmed: string,
    key: string,
    store: ReturnType<typeof createBindingStore>,
    ctx: ExtensionContext,
  ) => {
    // A status widget from an earlier /loopx preview would otherwise keep
    // floating while the guided start packet renders its own summary.
    ctx.ui.setWidget("loopx", undefined);
    try {
      const stdout = await runLoopxCli(
        [
          "--format",
          "json",
          "start-goal",
          "--guided",
          "--project",
          ".",
          "--goal-text",
          trimmed,
          "--host-surface",
          "pi",
          "--available-capability",
          TASK_LEASE_CAPABILITY,
        ],
        ctx.cwd,
      );
      const packet = parseJsonObject(stdout);
      if (!packet) throw new Error("LoopX start-goal returned a non-JSON packet");
      let authority: PiSessionAuthority | null = null;
      if (!packetNeedsHostSelection(packet)) {
        authority = establishSessionAuthority(key, packet);
        loop.bind(key, {
          store,
          isIdle: () => ctx.isIdle(),
          notify: (message: string, kind: string) => ctx.ui.notify(message, kind),
          authority,
        });
      }
      const deliveredPacket = {
        ...packet,
        ...(authority
          ? {
              pi_session_authority: {
                schema_version: authority.schemaVersion,
                token: authority.token,
                goal_id: authority.goalId,
                agent_id: authority.agentId,
                registry_path: authority.registryPath,
                available_capabilities: authority.availableCapabilities,
              },
            }
          : {}),
      };
      pi.sendUserMessage(JSON.stringify(deliveredPacket), {
        deliverAs: "followUp",
        triggerTurn: true,
      });
      ctx.ui.notify(
        "LoopX start-goal packet delivered to the agent; it will follow the ordered transaction.",
        "info",
      );
    } catch (error) {
      ctx.ui.notify(
        `LoopX start-goal failed: ${(error as Error)?.message || String(error)}`,
        "error",
      );
    }
  };

  // /loopx — inspect the project packet, or start a guided LoopX goal.
  pi.registerCommand("loopx", {
    description:
      "Inspect LoopX state, or start a concrete LoopX goal when arguments are provided.",
    handler: async (args, ctx) => {
      const trimmed = String(args || "").trim();
      const { key, store } = bindContext(ctx);
      const binding = await store.read(key).catch(() => null);
      if (LOOPX_INSPECT_COMMANDS.has(trimmed)) {
        if (trimmed === "resume" && binding) {
          await loop.resume(key);
          return;
        }
        await inspectLoopx(key, ctx);
        return;
      }
      await startLoopx(trimmed, key, store, ctx);
    },
  });

  // loopx_goal_activate — bind this session to a LoopX goal and start the
  // quota-gated auto-continuation loop. Called by the agent after start-goal.
  pi.registerTool({
    name: "loopx_goal_activate",
    label: "Activate LoopX Goal",
    description:
      "Activate a LoopX-backed Pi goal after LoopX start-goal has written todos and produced a heartbeat task_body. The extension then auto-continues through LoopX quota should-run; it never self-declares closure.",
    parameters: Type.Object({
      activationToken: Type.String({
        minLength: 1,
        description: "Host-issued token from the current Pi startup/session packet.",
      }),
      goalId: Type.Optional(
        Type.String({ description: "Optional compatibility echo of the host packet goal_id." }),
      ),
      objective: Type.String({
        description: "Heartbeat task_body from the start-goal packet.",
      }),
      agentId: Type.Optional(
        Type.String({ description: "Registered agent id when present." }),
      ),
      registryPath: Type.Optional(
        Type.String({ description: "Explicit LoopX registry path when present." }),
      ),
      availableCapabilities: Type.Optional(
        Type.Array(Type.String(), { description: "Declared host capabilities when present." }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const { key } = bindContext(ctx);
      try {
        const binding = await loop.activate(key, {
          directory: ctx.cwd,
          activationToken: String(params.activationToken),
          ...(params.goalId !== undefined ? { goalId: String(params.goalId) } : {}),
          ...(params.agentId !== undefined ? { agentId: String(params.agentId) } : {}),
          ...(params.registryPath !== undefined
            ? { registryPath: String(params.registryPath) }
            : {}),
          ...(params.availableCapabilities !== undefined
            ? { availableCapabilities: params.availableCapabilities.map(String) }
            : {}),
          taskBody: String(params.objective),
          autoResume: true,
          terminal: false,
          schedulerToken: "",
          unchangedPolls: 0,
          lastInjectedPrompt: "",
        });
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                version: 1,
                schema_version: PI_ACTIVATION_SCHEMA_VERSION,
                operation: "loopx_activate",
                ok: true,
                message: "LoopX-backed Pi goal activated.",
                goalId: binding.goalId,
              }),
            },
          ],
          details: {},
        };
      } catch (error) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                version: 1,
                schema_version: PI_ACTIVATION_SCHEMA_VERSION,
                operation: "loopx_activate",
                ok: false,
                error: String((error as Error)?.message || "Pi session authority rejected"),
                error_code: String((error as { code?: string })?.code || "authority_mismatch"),
              }),
            },
          ],
          details: {},
        };
      }
    },
  });

  // loopx_task_lease — an explicit, agent-callable facade over the existing
  // task_lease_v0 CLI. Goal and owner authority come from the active Pi
  // binding, and only an advertised capability may mutate a lease. No
  // lifecycle action is automated by the host.
  pi.registerTool({
    name: "loopx_task_lease",
    label: "LoopX Task Lease",
    description:
      "Inspect or explicitly mutate a task_lease_v0 lease for the active Pi goal. " +
      "Mutations require a registered bound agentId and an explicit task_lease_v0 " +
      "capability advertisement; goal and owner authority always come from the active session binding.",
    parameters: Type.Object({
      action: StringEnum(["acquire", "renew", "transfer", "release", "inspect"] as const, {
        description: "One of acquire, renew, transfer, release, or inspect.",
      }),
      todoId: Type.String({ minLength: 1, description: "Structured LoopX todo id." }),
      idempotencyKey: Type.Optional(
        Type.String({
          minLength: 1,
          description: "Execution-instance key for acquire/renew/transfer/release.",
        }),
      ),
      expectedVersion: Type.Optional(
        Type.Integer({
          minimum: 0,
          description: "CAS lease version required by renew/transfer/release.",
        }),
      ),
      ttlSeconds: Type.Optional(
        Type.Integer({
          minimum: 1,
          maximum: 86400,
          description: "Optional lease TTL in seconds for acquire/renew/transfer.",
        }),
      ),
      writeScopes: Type.Optional(
        Type.Array(Type.String({ minLength: 1 }), {
          description: "Relative write scopes for acquire; repeatable at the CLI boundary.",
        }),
      ),
      newOwner: Type.Optional(
        Type.String({ minLength: 1, description: "Target registered agent id for transfer." }),
      ),
      newIdempotencyKey: Type.Optional(
        Type.String({ minLength: 1, description: "Target execution-instance key for transfer." }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const { key, store } = bindContext(ctx);
      const binding = await store.read(key).catch(() => null);
      const result = await runPiTaskLease(
        binding,
        {
          action: String(params.action),
          todoId: String(params.todoId),
          ...(params.idempotencyKey !== undefined
            ? { idempotencyKey: String(params.idempotencyKey) }
            : {}),
          ...(params.expectedVersion !== undefined
            ? { expectedVersion: Number(params.expectedVersion) }
            : {}),
          ...(params.ttlSeconds !== undefined
            ? { ttlSeconds: Number(params.ttlSeconds) }
            : {}),
          ...(params.writeScopes !== undefined
            ? { writeScopes: params.writeScopes.map(String) }
            : {}),
          ...(params.newOwner !== undefined ? { newOwner: String(params.newOwner) } : {}),
          ...(params.newIdempotencyKey !== undefined
            ? { newIdempotencyKey: String(params.newIdempotencyKey) }
            : {}),
        },
        runLoopxTaskLeaseCli,
        sessionAuthorities.get(key),
      );
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        details: {},
      };
    },
  });

  // When the agent settles and a goal is bound, let LoopX decide the next move.
  pi.on("agent_settled", async (_event, ctx) => {
    const { key } = bindContext(ctx);
    await loop.settle(key);
  });

  // Escape aborts the active Pi run with an assistant stopReason of
  // "aborted". Persist the pause during agent_end, before agent_settled can
  // ask LoopX for another continuation. The owner must explicitly run
  // `/loopx resume` (or activate a goal again) to re-arm the loop.
  pi.on("agent_end", async (event, ctx) => {
    if (!hasAbortedAssistantMessage(event.messages)) return;
    const { key } = bindContext(ctx);
    await loop.interrupt(key);
  });

  // Pi runs its own context work (compaction, overflow recovery, retry) after a
  // turn and then starts another run with its own prompt. That prompt is not the
  // owner typing, so the next before_agent_start must not be read as user input;
  // otherwise a compaction disarms the loop silently and only /loopx resume
  // brings it back.
  let hostGeneratedPromptPending = false;
  pi.on("session_compact", async () => {
    hostGeneratedPromptPending = true;
  });

  // User-driven prompts pause the auto loop; only prompts the loop injected for
  // the same goal keep auto-resume armed. Host-generated prompts are passed
  // through so the runtime can tell them apart.
  pi.on("before_agent_start", async (event, ctx) => {
    const { key } = bindContext(ctx);
    const hostGenerated = hostGeneratedPromptPending;
    hostGeneratedPromptPending = false;
    await loop.userPrompt(key, String(event.prompt || ""), { hostGenerated });
  });

  // Session shutdown / session replacement: atomically dispose the whole
  // extension instance. Every timer is cancelled and an in-flight quota probe
  // returns to the runtime's disposed guard, so the old session cannot send a
  // follow-up or reschedule past this boundary.
  pi.on("session_shutdown", async (_event, ctx) => {
    ctx.ui.setWidget("loopx", undefined);
    loop.dispose();
  });
}
