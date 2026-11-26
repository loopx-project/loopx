/** Domain-neutral three-level host harness capability negotiation contract. */

import { isDeepStrictEqual } from "node:util";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  isStringLiteral,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION =
  "loopx_host_harness_capabilities_v0";

export const HOST_INTEGRATION_TIERS = [
  "boundary_cli",
  "orchestrated_app",
  "in_tree_codesigned",
] as const;
export type HostIntegrationTier = (typeof HOST_INTEGRATION_TIERS)[number];

export const HOST_HARNESS_CAPABILITY_FLAGS = [
  "session_fork",
  "epoch_reset",
  "fold_and_prefetch",
  "quiet_turn",
  "compact_hook",
  "verifier_hook",
  "native_context_budget",
  "managed_thread_resume",
] as const;
export type HostHarnessCapabilityFlag =
  (typeof HOST_HARNESS_CAPABILITY_FLAGS)[number];

const HOST_ID_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;
const MODULE_PATH_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/;
const HOOK_EVENT_PATTERN = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
// Shared TS/Python contract bound measured in Unicode code points (Array.from(str).length / len(str)).
const MAX_DESCRIPTION_CODE_POINTS = 512;
// Explicit cross-runtime edge-whitespace set matching Python _DESCRIPTION_WHITESPACE_CHARS:
// trims ASCII whitespace, U+0085 NEL, Unicode Zs/Zl/Zp separators, and U+FEFF BOM,
// while deliberately NOT stripping ASCII separators U+001C..U+001F.
const DESCRIPTION_EDGE_WHITESPACE_RE =
  /^[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/g;

const DESCRIPTOR_KEYS = new Set<string>([
  "schema_version",
  "host_id",
  "integration_tier",
  "adapter_module",
  "supported_capabilities",
  "hook_events",
  "requires_utf8_stdio",
  "native_goal_primitive",
  "native_scheduler_primitive",
  "description",
]);

const FLAG_ORDER = new Map<HostHarnessCapabilityFlag, number>(
  HOST_HARNESS_CAPABILITY_FLAGS.map((flag, index) => [flag, index]),
);

const HOOK_CAPABILITY_FLAGS: ReadonlySet<HostHarnessCapabilityFlag> = new Set([
  "compact_hook",
  "verifier_hook",
]);

export interface HostHarnessCapabilities {
  readonly schema_version: typeof HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION;
  readonly host_id: string;
  readonly integration_tier: HostIntegrationTier;
  readonly adapter_module: string;
  readonly supported_capabilities: readonly HostHarnessCapabilityFlag[];
  readonly hook_events: readonly string[];
  readonly requires_utf8_stdio: boolean;
  readonly native_goal_primitive: boolean;
  readonly native_scheduler_primitive: boolean;
  readonly description: string;
}

export interface HostHarnessCapabilitiesInput {
  readonly schema_version?: typeof HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION;
  readonly host_id: string;
  readonly integration_tier: HostIntegrationTier;
  readonly adapter_module: string;
  readonly supported_capabilities: readonly HostHarnessCapabilityFlag[];
  readonly hook_events?: readonly string[];
  readonly requires_utf8_stdio: boolean;
  readonly native_goal_primitive: boolean;
  readonly native_scheduler_primitive: boolean;
  readonly description: string;
}

export interface HostCapabilitiesRegistry {
  register(
    descriptor: unknown,
    options?: { readonly replace?: boolean },
  ): HostHarnessCapabilities;
  get(hostId: string): HostHarnessCapabilities | null;
  supports(hostId: string, capability: HostHarnessCapabilityFlag | string): boolean;
  list(options?: {
    readonly tier?: HostIntegrationTier | string;
    readonly capability?: HostHarnessCapabilityFlag | string;
  }): HostHarnessCapabilities[];
}

export function isHostIntegrationTier(
  value: unknown,
): value is HostIntegrationTier {
  return typeof value === "string" && isStringLiteral(value, HOST_INTEGRATION_TIERS);
}

export function isHostHarnessCapabilityFlag(
  value: unknown,
): value is HostHarnessCapabilityFlag {
  return (
    typeof value === "string"
    && isStringLiteral(value, HOST_HARNESS_CAPABILITY_FLAGS)
  );
}

export function validateHostIntegrationTier(
  value: unknown,
): HostIntegrationTier {
  return requireStringLiteral(
    value,
    HOST_INTEGRATION_TIERS,
    "integration_tier",
    `unsupported host integration tier: ${String(value)}`,
  );
}

export function validateHostHarnessCapabilityFlag(
  value: unknown,
): HostHarnessCapabilityFlag {
  return requireStringLiteral(
    value,
    HOST_HARNESS_CAPABILITY_FLAGS,
    "capability",
    `unsupported host harness capability flag: ${String(value)}`,
  );
}

function validateHostId(value: unknown): string {
  const hostId = requireNonEmptyString(value, "host_id");
  if (!HOST_ID_PATTERN.test(hostId)) {
    throw new EffectRuntimeRequestError(
      "host_id must be a lowercase slug matching ^[a-z][a-z0-9_-]{0,63}$",
    );
  }
  return hostId;
}

function validateAdapterModule(value: unknown): string {
  const adapterModule = requireNonEmptyString(value, "adapter_module");
  if (!MODULE_PATH_PATTERN.test(adapterModule)) {
    throw new EffectRuntimeRequestError(
      "adapter_module must be a dotted Python module path",
    );
  }
  return adapterModule;
}

function validateCapabilityFlags(
  value: unknown,
): readonly HostHarnessCapabilityFlag[] {
  const raw = requireStringArray(value, "supported_capabilities");
  const seen = new Set<HostHarnessCapabilityFlag>();
  for (const item of raw) {
    const flag = validateHostHarnessCapabilityFlag(item);
    if (seen.has(flag)) {
      throw new EffectRuntimeRequestError(
        `duplicate host harness capability flag: ${flag}`,
      );
    }
    seen.add(flag);
  }
  const ordered = [...seen].sort(
    (left, right) => (FLAG_ORDER.get(left) ?? 0) - (FLAG_ORDER.get(right) ?? 0),
  );
  return Object.freeze(ordered);
}

function validateHookEvents(value: unknown): readonly string[] {
  const raw = requireStringArray(value, "hook_events");
  const seen = new Set<string>();
  const events: string[] = [];
  for (const eventName of raw) {
    if (!HOOK_EVENT_PATTERN.test(eventName)) {
      throw new EffectRuntimeRequestError(
        `invalid hook_events entry: ${JSON.stringify(eventName)}`,
      );
    }
    if (seen.has(eventName)) {
      throw new EffectRuntimeRequestError(
        `duplicate hook_events entry: ${eventName}`,
      );
    }
    seen.add(eventName);
    events.push(eventName);
  }
  return Object.freeze(events);
}

export function validateHostHarnessCapabilities(
  value: unknown,
): HostHarnessCapabilities {
  const record = requireJsonObject(value, "host harness capabilities");
  const unknownKeys = Object.keys(record).filter((key) => !DESCRIPTOR_KEYS.has(key));
  if (unknownKeys.length > 0) {
    throw new EffectRuntimeRequestError(
      `unsupported host harness capabilities fields: ${unknownKeys.sort().join(", ")}`,
    );
  }
  const missingKeys = [...DESCRIPTOR_KEYS].filter((key) => !Object.hasOwn(record, key));
  if (missingKeys.length > 0) {
    throw new EffectRuntimeRequestError(
      `missing host harness capabilities fields: ${missingKeys.sort().join(", ")}`,
    );
  }

  const schemaVersion = requireNonEmptyString(
    record.schema_version,
    "schema_version",
  );
  if (schemaVersion !== HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `unsupported host harness capabilities schema_version: ${schemaVersion}`,
    );
  }

  const hostId = validateHostId(record.host_id);
  const integrationTier = validateHostIntegrationTier(record.integration_tier);
  const adapterModule = validateAdapterModule(record.adapter_module);
  const supportedCapabilities = validateCapabilityFlags(
    record.supported_capabilities,
  );
  const hookEvents = validateHookEvents(record.hook_events);
  const requiresUtf8Stdio = requireBoolean(
    record.requires_utf8_stdio,
    "requires_utf8_stdio",
  );
  const nativeGoalPrimitive = requireBoolean(
    record.native_goal_primitive,
    "native_goal_primitive",
  );
  const nativeSchedulerPrimitive = requireBoolean(
    record.native_scheduler_primitive,
    "native_scheduler_primitive",
  );
  if (typeof record.description !== "string") {
    throw new EffectRuntimeRequestError("description must be a non-empty string");
  }
  const description = record.description.replace(DESCRIPTION_EDGE_WHITESPACE_RE, "");
  if (!description) {
    throw new EffectRuntimeRequestError("description must be a non-empty string");
  }
  if (Array.from(description).length > MAX_DESCRIPTION_CODE_POINTS) {
    throw new EffectRuntimeRequestError(
      `description exceeds ${MAX_DESCRIPTION_CODE_POINTS} Unicode code points`,
    );
  }

  if (
    supportedCapabilities.some((flag) => HOOK_CAPABILITY_FLAGS.has(flag))
    && hookEvents.length === 0
  ) {
    throw new EffectRuntimeRequestError(
      "compact_hook and verifier_hook capabilities require non-empty hook_events",
    );
  }

  return Object.freeze({
    schema_version: HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,
    host_id: hostId,
    integration_tier: integrationTier,
    adapter_module: adapterModule,
    supported_capabilities: supportedCapabilities,
    hook_events: hookEvents,
    requires_utf8_stdio: requiresUtf8Stdio,
    native_goal_primitive: nativeGoalPrimitive,
    native_scheduler_primitive: nativeSchedulerPrimitive,
    description,
  });
}

export function defineHostHarnessCapabilities(
  input: HostHarnessCapabilitiesInput,
): HostHarnessCapabilities {
  return validateHostHarnessCapabilities({
    schema_version: input.schema_version ?? HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,
    host_id: input.host_id,
    integration_tier: input.integration_tier,
    adapter_module: input.adapter_module,
    supported_capabilities: [...input.supported_capabilities],
    hook_events: [...(input.hook_events ?? [])],
    requires_utf8_stdio: input.requires_utf8_stdio,
    native_goal_primitive: input.native_goal_primitive,
    native_scheduler_primitive: input.native_scheduler_primitive,
    description: input.description,
  });
}

export function createHostCapabilitiesRegistry(
  initial: readonly HostHarnessCapabilities[] = [],
): HostCapabilitiesRegistry {
  const entries = new Map<string, HostHarnessCapabilities>();

  const register = (
    descriptor: unknown,
    options: { readonly replace?: boolean } = {},
  ): HostHarnessCapabilities => {
    const validated = validateHostHarnessCapabilities(descriptor);
    const existing = entries.get(validated.host_id);
    if (existing !== undefined && options.replace !== true) {
      if (isDeepStrictEqual(existing, validated)) {
        return existing;
      }
      throw new EffectRuntimeRequestError(
        `conflicting host capabilities registration for ${validated.host_id}`,
      );
    }
    entries.set(validated.host_id, validated);
    return validated;
  };

  for (const item of initial) {
    register(item);
  }

  const get = (hostId: string): HostHarnessCapabilities | null => {
    const normalized = validateHostId(hostId);
    return entries.get(normalized) ?? null;
  };

  const supports = (
    hostId: string,
    capability: HostHarnessCapabilityFlag | string,
  ): boolean => {
    const flag = validateHostHarnessCapabilityFlag(capability);
    const descriptor = get(hostId);
    return descriptor !== null && descriptor.supported_capabilities.includes(flag);
  };

  const list = (
    options: {
      readonly tier?: HostIntegrationTier | string;
      readonly capability?: HostHarnessCapabilityFlag | string;
    } = {},
  ): HostHarnessCapabilities[] => {
    const tierFilter =
      options.tier !== undefined ? validateHostIntegrationTier(options.tier) : null;
    const capabilityFilter =
      options.capability !== undefined
        ? validateHostHarnessCapabilityFlag(options.capability)
        : null;
    return [...entries.values()]
      .filter((entry) => tierFilter === null || entry.integration_tier === tierFilter)
      .filter(
        (entry) =>
          capabilityFilter === null
          || entry.supported_capabilities.includes(capabilityFilter),
      )
      // Deterministic ASCII code-unit ordering matching Python tuple(sorted(..., key=host_id));
      // avoids localeCompare collation where "-" and "_" reorder across locales.
      .sort((left, right) =>
        left.host_id < right.host_id ? -1 : left.host_id > right.host_id ? 1 : 0,
      );
  };

  return { register, get, supports, list };
}

export const AGY_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "agy",
    integration_tier: "orchestrated_app",
    adapter_module: "loopx.agy_goal_mode",
    supported_capabilities: [],
    hook_events: ["Stop"],
    requires_utf8_stdio: true,
    native_goal_primitive: true,
    native_scheduler_primitive: true,
    description:
      "Antigravity CLI and App orchestrated adapter with native /goal, schedule wake tools, Stop EOF recovery hook, and ANTIGRAVITY_CONVERSATION_ID binding.",
  });

export const CLAUDE_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "claude",
    integration_tier: "boundary_cli",
    adapter_module: "loopx.claude_goal_mode",
    supported_capabilities: ["verifier_hook"],
    hook_events: ["PreToolUse"],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: true,
    description:
      "Claude Code boundary CLI adapter driven by native /loop, MCP control-plane tools, and opt-in PreToolUse should_run/write_scope policy verification.",
  });

export const CODEX_APP_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "codex-app",
    integration_tier: "orchestrated_app",
    adapter_module: "loopx.codex_app_thread_activity",
    supported_capabilities: [],
    hook_events: [],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: true,
    description:
      "Codex App orchestrated host surface using native automation.toml RRULE heartbeat scheduling and local thread activity inspection; Goal admission stays outer-driven via skills and CLI.",
  });

export const CODEX_APP_SERVER_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "codex-app-server",
    integration_tier: "orchestrated_app",
    adapter_module: "loopx.control_plane.turn_driver.codex_operation_host",
    supported_capabilities: ["session_fork", "managed_thread_resume"],
    hook_events: [],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: false,
    description:
      "Long-lived Codex app-server JSON-RPC stdio runner orchestrated by LoopX Turn/Steward controllers with managed thread resume and native thread/fork support; outer scheduler and Goals remain external.",
  });

export const CODEX_CLI_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "codex-cli",
    integration_tier: "boundary_cli",
    adapter_module: "loopx.control_plane.turn_driver.codex_cli",
    supported_capabilities: [
      "epoch_reset",
      "quiet_turn",
      "managed_thread_resume",
    ],
    hook_events: [],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: false,
    description:
      "Outer-driven Codex CLI (codex exec) boundary Turn runner with UTF-8 stdio, managed thread resume, Quiet-Turn pre-admission skips, and EpochReset prefetch transitions (no intra-epoch fold_and_prefetch without hooks).",
  });

export const CODEX_CLI_HOOKS_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "codex-cli-hooks",
    integration_tier: "boundary_cli",
    adapter_module: "loopx.control_plane.turn_driver.codex_context",
    supported_capabilities: [
      "epoch_reset",
      "fold_and_prefetch",
      "quiet_turn",
      "compact_hook",
    ],
    hook_events: [
      "SessionStart",
      "PreCompact",
      "PostCompact",
      "PostToolUse",
      "Interrupt",
    ],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: false,
    description:
      "Codex CLI native lifecycle hook runner wired through loopx quota context-hook, handling SessionStart, PreCompact, PostCompact, PostToolUse, and Interrupt with semantic-unit folding, Quiet-Turn, and EpochReset.",
  });

export const DSH_HOST_CAPABILITIES: HostHarnessCapabilities =
  defineHostHarnessCapabilities({
    host_id: "dsh",
    integration_tier: "in_tree_codesigned",
    adapter_module: "loopx.dsh_goal_mode",
    supported_capabilities: [],
    hook_events: [],
    requires_utf8_stdio: true,
    native_goal_primitive: false,
    native_scheduler_primitive: false,
    description:
      "DeepSeek Harness in-tree Turn adapter with typed failure mapping and per-request max_tokens output limit only (no native input/context budget management).",
  });

export const CANONICAL_HOST_HARNESS_CAPABILITIES: readonly HostHarnessCapabilities[] =
  Object.freeze([
    AGY_HOST_CAPABILITIES,
    CLAUDE_HOST_CAPABILITIES,
    CODEX_APP_HOST_CAPABILITIES,
    CODEX_APP_SERVER_HOST_CAPABILITIES,
    CODEX_CLI_HOST_CAPABILITIES,
    CODEX_CLI_HOOKS_HOST_CAPABILITIES,
    DSH_HOST_CAPABILITIES,
  ]);

export const DEFAULT_HOST_CAPABILITIES_REGISTRY: HostCapabilitiesRegistry =
  createHostCapabilitiesRegistry(CANONICAL_HOST_HARNESS_CAPABILITIES);

export function registerHostCapabilities(
  descriptor: unknown,
  options: {
    readonly registry?: HostCapabilitiesRegistry;
    readonly replace?: boolean;
  } = {},
): HostHarnessCapabilities {
  const registry = options.registry ?? DEFAULT_HOST_CAPABILITIES_REGISTRY;
  return registry.register(descriptor, { replace: options.replace });
}

export function getHostCapabilities(
  hostId: string,
  options: { readonly registry?: HostCapabilitiesRegistry } = {},
): HostHarnessCapabilities | null {
  const registry = options.registry ?? DEFAULT_HOST_CAPABILITIES_REGISTRY;
  return registry.get(hostId);
}

export function hostSupportsCapability(
  hostId: string,
  capability: HostHarnessCapabilityFlag | string,
  options: { readonly registry?: HostCapabilitiesRegistry } = {},
): boolean {
  const registry = options.registry ?? DEFAULT_HOST_CAPABILITIES_REGISTRY;
  return registry.supports(hostId, capability);
}

export function listHostCapabilities(
  options: {
    readonly tier?: HostIntegrationTier | string;
    readonly capability?: HostHarnessCapabilityFlag | string;
    readonly registry?: HostCapabilitiesRegistry;
  } = {},
): HostHarnessCapabilities[] {
  const registry = options.registry ?? DEFAULT_HOST_CAPABILITIES_REGISTRY;
  return registry.list({
    tier: options.tier,
    capability: options.capability,
  });
}
