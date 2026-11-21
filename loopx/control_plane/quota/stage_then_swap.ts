import {createHash} from "node:crypto";

import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";

export const STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION = "loopx_stage_then_swap_policy_v0";
export const EPOCH_PREFETCH_RECEIPT_SCHEMA_VERSION = "loopx_epoch_prefetch_receipt_v0";
export const QUIET_TURN_METADATA_SCHEMA_VERSION = "loopx_quiet_turn_v0";
export const CONTEXT_POLICY_OPERATORS = ["KeepRaw", "FoldToReceipt", "EpochReset"] as const;

export const DEFAULT_PREFETCH_TIMEOUT_MS = 5000;
export const MAX_PREFETCH_TIMEOUT_MS = 60000;
export const DEFAULT_PREFETCH_MAX_OUTPUT_BYTES = 16384;
export const MIN_PREFETCH_MAX_OUTPUT_BYTES = 128;
export const MAX_PREFETCH_MAX_OUTPUT_BYTES = 65536;

export type ContextPolicyOperator = (typeof CONTEXT_POLICY_OPERATORS)[number];

export interface PrefetchSafetyDecision {
  allowed: boolean;
  reason: string | null;
  argv: string[];
  command_sha256: string | null;
}

export interface NormalizedStageThenSwapPolicy {
  schema_version: typeof STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION;
  enabled: boolean;
  initiator: string;
  prefetch_command: string | string[] | null;
  timeout_ms: number;
  max_output_bytes: number;
  min_net_savings_bytes: number | null;
  reject_on_truncation: boolean;
  replace_exploratory_context: boolean;
}

const DISALLOWED_SHELL_OPERATORS = new Set([";", "|", "&", "&&", "||", ">", ">>", "<", "<<", "<<<", "(", ")"]);
const READ_ONLY_PROBES = new Set(["cat", "head", "tail", "wc", "ls", "stat", "pwd", "rg", "grep"]);
const GIT_READ_ONLY_SUBCOMMANDS = new Set(["status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "branch"]);
const GIT_READ_ONLY_BRANCH_FLAGS = new Set(["--list", "--show-current", "-a", "--all", "-r", "--remotes", "-v", "--verbose", "-vv"]);
const GIT_FORBIDDEN_OUTPUT_FLAGS = new Set(["--output", "--ext-diff", "--open-files-in-pager", "-O", "--exec"]);
const FIND_MUTATING_PREDICATES = new Set(["-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls", "-fprint", "-fprint0", "-fprintf"]);
const LOOPX_READ_ONLY_SUBCOMMANDS = new Set(["status", "doctor", "version"]);
const LOOPX_QUOTA_READ_SUBCOMMANDS = new Set(["context-read", "receipt"]);
const DISALLOWED_LOOPX_FLAGS = new Set(["--execute"]);
const PASSIVE_PLUGIN_INITIATORS = new Set([
  "history_view_policy",
  "plugin",
  "passive_policy",
  "external_policy_plugin",
]);

function sha256Hex(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function optionalObject(value: unknown, label: string): JsonObject | null {
  if (value === undefined || value === null) return null;
  return requireJsonObject(value, label);
}

function optionalInt(value: unknown, label: string, min: number, max: number): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new EffectRuntimeRequestError(`${label} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function cleanTextList(value: unknown, limit = 32): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const items: string[] = [];
  for (const raw of value) {
    let text = "";
    if (typeof raw === "string") text = raw.trim();
    else if (raw && typeof raw === "object") text = JSON.stringify(raw);
    if (!text) continue;
    const bounded = text.length > 512 ? `${text.slice(0, 509)}...` : text;
    if (seen.has(bounded)) continue;
    seen.add(bounded);
    items.push(bounded);
    if (items.length >= limit) break;
  }
  return items;
}

export function normalizeStageThenSwapConfig(value: unknown): NormalizedStageThenSwapPolicy {
  if (value === undefined || value === null || value === false) {
    return {
      schema_version: STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION,
      enabled: false,
      initiator: "turn_driver",
      prefetch_command: null,
      timeout_ms: DEFAULT_PREFETCH_TIMEOUT_MS,
      max_output_bytes: DEFAULT_PREFETCH_MAX_OUTPUT_BYTES,
      min_net_savings_bytes: null,
      reject_on_truncation: false,
      replace_exploratory_context: false,
    };
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new EffectRuntimeRequestError("epoch_prefetch configuration must be an object or boolean");
  }
  const config = value as JsonObject;
  const enabled = config.enabled === undefined ? false : requireBoolean(config.enabled, "epoch_prefetch.enabled");
  const initiator = optionalNonEmptyString(config.initiator, "epoch_prefetch.initiator") ?? "turn_driver";
  const timeout_ms =
    optionalInt(config.timeout_ms, "epoch_prefetch.timeout_ms", 1, MAX_PREFETCH_TIMEOUT_MS)
    ?? DEFAULT_PREFETCH_TIMEOUT_MS;
  const max_output_bytes =
    optionalInt(
      config.max_output_bytes,
      "epoch_prefetch.max_output_bytes",
      MIN_PREFETCH_MAX_OUTPUT_BYTES,
      MAX_PREFETCH_MAX_OUTPUT_BYTES,
    ) ?? DEFAULT_PREFETCH_MAX_OUTPUT_BYTES;
  const min_net_savings_bytes = optionalInt(
    config.min_net_savings_bytes,
    "epoch_prefetch.min_net_savings_bytes",
    0,
    10_000_000,
  );
  const reject_on_truncation =
    config.reject_on_truncation === undefined
      ? false
      : requireBoolean(config.reject_on_truncation, "epoch_prefetch.reject_on_truncation");
  const replace_exploratory_context =
    config.replace_exploratory_context === undefined
      ? false
      : requireBoolean(config.replace_exploratory_context, "epoch_prefetch.replace_exploratory_context");

  let prefetch_command: string | string[] | null = null;
  if (config.prefetch_command !== undefined && config.prefetch_command !== null) {
    if (typeof config.prefetch_command === "string") {
      prefetch_command = config.prefetch_command;
    } else if (
      Array.isArray(config.prefetch_command)
      && config.prefetch_command.every((part) => typeof part === "string")
    ) {
      prefetch_command = [...config.prefetch_command];
    } else {
      throw new EffectRuntimeRequestError("epoch_prefetch.prefetch_command must be string or string[]");
    }
  }
  return {
    schema_version: STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION,
    enabled,
    initiator,
    prefetch_command,
    timeout_ms,
    max_output_bytes,
    min_net_savings_bytes,
    reject_on_truncation,
    replace_exploratory_context,
  };
}

function parsePosixWords(command: string): {ok: true; argv: string[]} | {ok: false; reason: string} {
  if (/[\r\n\0]/.test(command)) return {ok: false, reason: "policy_violation:multiline_or_nul"};
  const argv: string[] = [];
  let current = "";
  let inToken = false;
  let quote: "'" | '"' | null = null;
  for (let i = 0; i < command.length; i += 1) {
    const ch = command[i]!;
    if (quote === "'") {
      if (ch === "'") quote = null;
      else current += ch;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') {
        quote = null;
      } else if (ch === "\\") {
        if (i + 1 >= command.length) return {ok: false, reason: "policy_violation:dangling_escape"};
        current += command[++i]!;
      } else if (ch === "$" || ch === "`") {
        return {ok: false, reason: "policy_violation:shell_expansion"};
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      inToken = true;
    } else if (ch === "\\") {
      if (i + 1 >= command.length) return {ok: false, reason: "policy_violation:dangling_escape"};
      current += command[++i]!;
      inToken = true;
    } else if (/\s/.test(ch)) {
      if (inToken) {
        argv.push(current);
        current = "";
        inToken = false;
      }
    } else if (";|&><()$ `*?[]{}".includes(ch)) {
      return {ok: false, reason: "policy_violation:shell_metacharacter"};
    } else {
      current += ch;
      inToken = true;
    }
  }
  if (quote !== null) return {ok: false, reason: "policy_violation:unterminated_quote"};
  if (inToken) argv.push(current);
  if (!argv.length) return {ok: false, reason: "policy_violation:empty_command"};
  return {ok: true, argv};
}

function normalizePrefetchArgv(command: unknown): {ok: true; argv: string[]} | {ok: false; reason: string} {
  if (typeof command === "string") {
    return parsePosixWords(command.trim());
  }
  if (Array.isArray(command)) {
    if (!command.length || command.some((part) => typeof part !== "string" || !part)) {
      return {ok: false, reason: "policy_violation:empty_command"};
    }
    for (const part of command) {
      if (/[\r\n\0]/.test(part)) return {ok: false, reason: "policy_violation:multiline_or_nul"};
      if (DISALLOWED_SHELL_OPERATORS.has(part) || part.includes("$(") || part.includes("`")) {
        return {ok: false, reason: "policy_violation:shell_metacharacter"};
      }
    }
    return {ok: true, argv: [...command]};
  }
  return {ok: false, reason: "policy_violation:missing_command"};
}

function isLoopxReadOnlyArgs(args: readonly string[]): PrefetchSafetyDecision | null {
  let cursor = 0;
  while (cursor < args.length && args[cursor]!.startsWith("-")) {
    const flag = args[cursor]!;
    if (DISALLOWED_LOOPX_FLAGS.has(flag)) {
      return null;
    }
    if (["--registry", "--runtime-root", "--format"].includes(flag)) {
      cursor += 2;
      continue;
    }
    if (flag.startsWith("--registry=") || flag.startsWith("--runtime-root=") || flag.startsWith("--format=")) {
      cursor += 1;
      continue;
    }
    return null;
  }
  if (args.some((arg) => DISALLOWED_LOOPX_FLAGS.has(arg))) return null;
  const remaining = args.slice(cursor);
  if (!remaining.length) return null;
  if (remaining[0] === "quota") {
    const sub = remaining[1];
    if (!sub || !LOOPX_QUOTA_READ_SUBCOMMANDS.has(sub)) return null;
    return {allowed: true, reason: null, argv: [], command_sha256: null};
  }
  if (remaining[0] === "turn" && remaining[1] === "inspect") {
    return {allowed: true, reason: null, argv: [], command_sha256: null};
  }
  if (remaining[0] === "todo" && remaining[1] === "list") {
    return {allowed: true, reason: null, argv: [], command_sha256: null};
  }
  if (remaining[0] === "task-lease" && remaining[1] === "inspect") {
    return {allowed: true, reason: null, argv: [], command_sha256: null};
  }
  if (LOOPX_READ_ONLY_SUBCOMMANDS.has(remaining[0]!)) {
    return {allowed: true, reason: null, argv: [], command_sha256: null};
  }
  return null;
}

export function validatePrefetchCommandSafety(params: {
  prefetch_command: unknown;
  initiator?: unknown;
}): PrefetchSafetyDecision {
  const initiator = typeof params.initiator === "string" && params.initiator.trim()
    ? params.initiator.trim()
    : "turn_driver";
  if (PASSIVE_PLUGIN_INITIATORS.has(initiator)) {
    return {
      allowed: false,
      reason: "policy_violation:passive_policy_cannot_emit_command",
      argv: [],
      command_sha256: null,
    };
  }
  const parsed = normalizePrefetchArgv(params.prefetch_command);
  if (!parsed.ok) {
    return {allowed: false, reason: parsed.reason, argv: [], command_sha256: null};
  }
  const argv = parsed.argv;
  const cmdHash = sha256Hex(JSON.stringify(argv));
  const exe = argv[0]!.split(/[/\\]/).pop() ?? "";
  const args = argv.slice(1);

  if (exe === "loopx") {
    if (isLoopxReadOnlyArgs(args)?.allowed) {
      return {allowed: true, reason: null, argv, command_sha256: cmdHash};
    }
    return {
      allowed: false,
      reason: "policy_violation:loopx_subcommand_not_read_only",
      argv,
      command_sha256: cmdHash,
    };
  }
  if (/^python(?:\d+(?:\.\d+)?)?$/.test(exe)) {
    if (args[0] === "-m" && (args[1] === "loopx" || args[1] === "loopx.cli" || args[1] === "loopx.entrypoint")) {
      if (isLoopxReadOnlyArgs(args.slice(2))?.allowed) {
        return {allowed: true, reason: null, argv, command_sha256: cmdHash};
      }
      return {
        allowed: false,
        reason: "policy_violation:loopx_subcommand_not_read_only",
        argv,
        command_sha256: cmdHash,
      };
    }
    if (args[0] === "-m" && args[1] === "pytest" && args.includes("--collect-only")) {
      return {allowed: true, reason: null, argv, command_sha256: cmdHash};
    }
    return {
      allowed: false,
      reason: "policy_violation:executable_not_read_only_allowlisted",
      argv,
      command_sha256: cmdHash,
    };
  }
  if (exe === "git") {
    let idx = 0;
    while (idx < args.length && (args[idx] === "-C" || args[idx] === "--git-dir" || args[idx] === "--work-tree")) {
      idx += 2;
    }
    const sub = args[idx];
    if (!sub || !GIT_READ_ONLY_SUBCOMMANDS.has(sub)) {
      return {
        allowed: false,
        reason: "policy_violation:git_subcommand_not_read_only",
        argv,
        command_sha256: cmdHash,
      };
    }
    const gitSubArgs = args.slice(idx + 1);
    if (
      gitSubArgs.some(
        (flag) =>
          GIT_FORBIDDEN_OUTPUT_FLAGS.has(flag)
          || flag.startsWith("-O")
          || flag.startsWith("--output=")
          || flag.startsWith("--ext-diff=")
          || flag.startsWith("--open-files-in-pager=")
          || flag.startsWith("--exec="),
      )
    ) {
      return {
        allowed: false,
        reason: "policy_violation:git_output_or_external_diff_forbidden",
        argv,
        command_sha256: cmdHash,
      };
    }
    if (sub === "branch") {
      const hasListFlag = gitSubArgs.includes("--list");
      const branchAllowed = gitSubArgs.every((arg) =>
        arg.startsWith("-") ? GIT_READ_ONLY_BRANCH_FLAGS.has(arg) : hasListFlag
      );
      if (!branchAllowed) {
        return {
          allowed: false,
          reason: "policy_violation:git_branch_mutation_forbidden",
          argv,
          command_sha256: cmdHash,
        };
      }
    }
    return {allowed: true, reason: null, argv, command_sha256: cmdHash};
  }
  if (exe === "find") {
    if (args.some((arg) => FIND_MUTATING_PREDICATES.has(arg))) {
      return {
        allowed: false,
        reason: "policy_violation:find_mutation_forbidden",
        argv,
        command_sha256: cmdHash,
      };
    }
    return {allowed: true, reason: null, argv, command_sha256: cmdHash};
  }
  if (READ_ONLY_PROBES.has(exe)) {
    if ((exe === "rg" || exe === "grep") && args.some((a) => a === "--pre" || a.startsWith("--pre="))) {
      return {
        allowed: false,
        reason: "policy_violation:probe_preprocessor_forbidden",
        argv,
        command_sha256: cmdHash,
      };
    }
    return {allowed: true, reason: null, argv, command_sha256: cmdHash};
  }
  return {
    allowed: false,
    reason: "policy_violation:executable_not_read_only_allowlisted",
    argv,
    command_sha256: cmdHash,
  };
}

export function planStageThenSwapPrefetch(rawRequest: JsonObject): JsonObject {
  const config = normalizeStageThenSwapConfig(rawRequest.epoch_prefetch);
  if (!config.enabled) {
    return {
      schema_version: STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION,
      enabled: false,
      eligible: false,
      fallback_operator: "KeepRaw",
    };
  }
  const safety = validatePrefetchCommandSafety({
    prefetch_command: config.prefetch_command,
    initiator: config.initiator,
  });
  return {
    schema_version: STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION,
    enabled: true,
    eligible: safety.allowed,
    safety,
    timeout_ms: config.timeout_ms,
    max_output_bytes: config.max_output_bytes,
    min_net_savings_bytes: config.min_net_savings_bytes,
    reject_on_truncation: config.reject_on_truncation,
    replace_exploratory_context: config.replace_exploratory_context,
    initiator: config.initiator,
    fallback_operator: "KeepRaw",
  };
}

function normalizeSourceRef(raw: unknown): {path: string; sha256: string} | null {
  if (raw === undefined || raw === null) return null;
  const obj = requireJsonObject(raw, "source_ref");
  const path = requireNonEmptyString(obj.path, "source_ref.path");
  const sha256 = requireNonEmptyString(obj.sha256, "source_ref.sha256");
  if (!BARE_SHA256_PATTERN.test(sha256)) {
    throw new EffectRuntimeRequestError("source_ref.sha256 must be a bare sha256 digest");
  }
  return {path, sha256};
}

export function buildAntiWhitewashingReceiptHeader(params: {
  context_epoch: string;
  reclaimed_turns: number;
  dropped_failures: number;
  failure_facts: string[];
  open_obligations: string[];
  exit_code: number | null;
  truncated: boolean;
  source_ref: {path: string; sha256: string} | null;
}): {header: string; fields: JsonObject} {
  const sourceRefText = params.source_ref
    ? `${params.source_ref.path} (sha256:${params.source_ref.sha256})`
    : "none";
  const fields: JsonObject = {
    trust_level: "tool",
    context_epoch: params.context_epoch,
    reclaimed_turns: params.reclaimed_turns,
    dropped_failures: params.dropped_failures,
    exit_code: params.exit_code,
    truncated: params.truncated,
    source_ref: params.source_ref,
    failure_facts: params.failure_facts,
    open_obligations: params.open_obligations,
  };
  const header = [
    "[Stage-then-Swap EpochReset Receipt | trust=tool]",
    `context_epoch: ${params.context_epoch}`,
    `reclaimed_turns: ${params.reclaimed_turns}`,
    `dropped_failures: ${params.dropped_failures}`,
    `exit_code: ${params.exit_code === null ? "null" : String(params.exit_code)}`,
    `truncated: ${params.truncated ? "true" : "false"}`,
    `source_ref: ${sourceRefText}`,
    `failure_facts: ${JSON.stringify(params.failure_facts)}`,
    `open_obligations: ${JSON.stringify(params.open_obligations)}`,
    "--- prefetched_output ---",
  ].join("\n");
  return {header, fields};
}

function inspectStructuredPrefetchOutput(
  output: string,
  sourceRef: {path: string; sha256: string} | null = null,
): {
  failed: boolean;
  incomplete: boolean;
  sourceMismatch: boolean;
} {
  const trimmed = output.trim();
  if (!trimmed.startsWith("{")) {
    return {failed: false, incomplete: false, sourceMismatch: false};
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {failed: false, incomplete: false, sourceMismatch: false};
    }
    const obj = parsed as Record<string, unknown>;
    const failed = obj.ok === false;
    const schema = typeof obj.schema_version === "string" ? obj.schema_version : "";
    const isPageSchema =
      schema === "context_capture_page_v1"
      || schema === "context_capture_selection_page_v1"
      || "eof" in obj
      || "offset" in obj
      || "next_offset" in obj
      || "total_bytes" in obj;
    if (!isPageSchema) {
      return {failed, incomplete: false, sourceMismatch: false};
    }
    let incomplete = obj.eof === false || obj.truncated === true;
    if (
      !failed
      && (schema === "context_capture_page_v1" || schema === "context_capture_selection_page_v1")
    ) {
      const hasZeroOffset =
        typeof obj.offset === "number"
        && Number.isInteger(obj.offset)
        && obj.offset === 0;
      const hasClosedNextOffset =
        obj.next_offset === null || obj.next_offset === undefined;
      const hasCompleteEof = obj.eof === true && obj.truncated !== true;
      const hasContiguousLength =
        typeof obj.total_bytes !== "number"
        || (
          typeof obj.content === "string"
          && Buffer.byteLength(obj.content, "utf8") === obj.total_bytes
        );
      if (!hasZeroOffset || !hasClosedNextOffset || !hasCompleteEof || !hasContiguousLength) {
        incomplete = true;
      }
    } else {
      if (typeof obj.offset === "number" && (!Number.isInteger(obj.offset) || obj.offset !== 0)) {
        incomplete = true;
      }
      if (obj.next_offset !== null && obj.next_offset !== undefined) {
        incomplete = true;
      }
    }
    const reportedDigest =
      typeof obj.sha256 === "string"
        ? obj.sha256
        : typeof obj.capture_sha256 === "string"
          ? obj.capture_sha256
          : null;
    const sourceMismatch = Boolean(
      !failed
      && sourceRef?.sha256
      && (
        (reportedDigest !== null && reportedDigest !== sourceRef.sha256)
        || (
          (schema === "context_capture_page_v1" || schema === "context_capture_selection_page_v1")
          && reportedDigest === null
        )
      ),
    );
    return {failed, incomplete, sourceMismatch};
  } catch {
    if (
      trimmed.includes("context_capture_page_v1")
      || trimmed.includes("context_capture_selection_page_v1")
    ) {
      return {failed: false, incomplete: true, sourceMismatch: false};
    }
    return {failed: false, incomplete: false, sourceMismatch: false};
  }
}

export function evaluateEpochPrefetchSwap(rawRequest: JsonObject): JsonObject {
  const config = normalizeStageThenSwapConfig(rawRequest.epoch_prefetch);
  const context_epoch = requireNonEmptyString(rawRequest.context_epoch, "context_epoch");
  const reclaimed_turns =
    optionalInt(rawRequest.reclaimed_turns, "reclaimed_turns", 0, 1_000_000) ?? 0;
  const failure_facts = cleanTextList(rawRequest.failure_facts, 32);
  const raw_dropped_failures =
    optionalInt(rawRequest.dropped_failures, "dropped_failures", 0, 1_000_000) ?? 0;
  const dropped_failures = Math.max(raw_dropped_failures, failure_facts.length);
  const open_obligations = cleanTextList(rawRequest.open_obligations, 64);
  const source_ref = normalizeSourceRef(rawRequest.source_ref);
  const baseline_bytes = optionalInt(rawRequest.baseline_bytes, "baseline_bytes", 0, 100_000_000);
  const latency_ms = optionalInt(rawRequest.latency_ms, "latency_ms", 0, 3_600_000) ?? 0;
  const timed_out = rawRequest.timed_out === undefined
    ? false
    : requireBoolean(rawRequest.timed_out, "timed_out");
  const truncated = rawRequest.truncated === undefined
    ? false
    : requireBoolean(rawRequest.truncated, "truncated");
  const exit_code =
    rawRequest.exit_code === undefined || rawRequest.exit_code === null
      ? null
      : optionalInt(rawRequest.exit_code, "exit_code", -128, 255);
  const prefetched_output = typeof rawRequest.prefetched_output === "string"
    ? rawRequest.prefetched_output
    : "";
  const raw_bytes_read =
    optionalInt(rawRequest.raw_bytes_read, "raw_bytes_read", 0, 100_000_000)
    ?? Buffer.byteLength(prefetched_output, "utf8");
  const retained_bytes = Buffer.byteLength(prefetched_output, "utf8");

  if (!config.enabled) {
    throw new EffectRuntimeRequestError("Stage-then-Swap epoch prefetch is disabled");
  }

  const safety = validatePrefetchCommandSafety({
    prefetch_command: config.prefetch_command,
    initiator: config.initiator,
  });
  const pageStatus = inspectStructuredPrefetchOutput(prefetched_output, source_ref);
  const effectiveTruncated = truncated || pageStatus.incomplete;

  const {header, fields} = buildAntiWhitewashingReceiptHeader({
    context_epoch,
    reclaimed_turns,
    dropped_failures,
    failure_facts,
    open_obligations,
    exit_code,
    truncated: effectiveTruncated,
    source_ref,
  });
  const hydrated_text = prefetched_output ? `${header}\n${prefetched_output}` : header;
  const hydrated_bytes = Buffer.byteLength(hydrated_text, "utf8");
  const net_savings_bytes = baseline_bytes === null ? null : baseline_bytes - hydrated_bytes;
  const minNetSavings = config.min_net_savings_bytes ?? (baseline_bytes === null ? 0 : 1);

  let fallback_reason: string | null = null;
  if (!safety.allowed) {
    fallback_reason = safety.reason;
  } else if (timed_out) {
    fallback_reason = "timeout";
  } else if (exit_code === null || exit_code !== 0 || pageStatus.failed) {
    fallback_reason = "command_failed";
  } else if (pageStatus.incomplete) {
    fallback_reason = "incomplete_output";
  } else if (pageStatus.sourceMismatch) {
    fallback_reason = "source_digest_mismatch";
  } else if (config.reject_on_truncation && effectiveTruncated) {
    fallback_reason = "output_truncated";
  } else if (retained_bytes > config.max_output_bytes) {
    fallback_reason = "output_overflow";
  } else if (baseline_bytes !== null && (net_savings_bytes ?? -1) < minNetSavings) {
    fallback_reason = "negative_net_savings";
  }

  const output_sha256 = sha256Hex(prefetched_output);
  if (fallback_reason !== null) {
    const errorNote = optionalNonEmptyString(rawRequest.error_observation, "error_observation")
      ?? `Prefetch aborted atomically to KeepRaw (${fallback_reason}).`;
    const baseReceipt: JsonObject = {
      schema_version: EPOCH_PREFETCH_RECEIPT_SCHEMA_VERSION,
      operator: "KeepRaw",
      status: "fallback_keep_raw",
      fallback_reason,
      trust_level: "tool",
      preserves_raw_state: true,
      context_epoch,
      command_sha256: safety.command_sha256,
      source_ref,
      reclaimed_turns: 0,
      dropped_failures: 0,
      exit_code,
      timed_out,
      truncated: effectiveTruncated,
      raw_bytes_read,
      retained_bytes: 0,
      baseline_bytes,
      hydrated_bytes: baseline_bytes ?? 0,
      net_savings_bytes: 0,
      bytes_saved: 0,
      round_trips_saved: 0,
      latency_ms,
      header,
      header_fields: fields,
      preserved_failure_facts: failure_facts,
      preserved_open_obligations: open_obligations,
      prefetched_output: "",
      hydrated_text: null,
      output_sha256,
      error_observation: errorNote.slice(0, 512),
    };
    return {
      ...baseReceipt,
      receipt_sha256: sha256Hex(JSON.stringify(baseReceipt)),
    };
  }

  const bytes_saved = net_savings_bytes !== null && net_savings_bytes > 0 ? net_savings_bytes : 0;
  const committedReceipt: JsonObject = {
    schema_version: EPOCH_PREFETCH_RECEIPT_SCHEMA_VERSION,
    operator: "EpochReset",
    status: "committed",
    fallback_reason: null,
    trust_level: "tool",
    preserves_raw_state: false,
    context_epoch,
    command_sha256: safety.command_sha256,
    source_ref,
    reclaimed_turns,
    dropped_failures,
    exit_code,
    timed_out: false,
    truncated: effectiveTruncated,
    raw_bytes_read,
    retained_bytes,
    baseline_bytes,
    hydrated_bytes,
    net_savings_bytes,
    bytes_saved,
    round_trips_saved: 1,
    latency_ms,
    header,
    header_fields: fields,
    preserved_failure_facts: failure_facts,
    preserved_open_obligations: open_obligations,
    prefetched_output,
    hydrated_text,
    output_sha256,
    error_observation: null,
  };
  return {
    ...committedReceipt,
    receipt_sha256: sha256Hex(JSON.stringify(committedReceipt)),
  };
}

export function evaluateQuietTurn(rawRequest: JsonObject): JsonObject {
  const enabled = rawRequest.enabled === undefined ? true : requireBoolean(rawRequest.enabled, "quiet_turn.enabled");
  const quietNoopAllowed =
    rawRequest.quiet_noop_allowed === undefined
      ? true
      : requireBoolean(rawRequest.quiet_noop_allowed, "quiet_noop_allowed");
  const currentStateDigest = optionalNonEmptyString(rawRequest.current_state_digest, "current_state_digest");
  const previousStateDigest = optionalNonEmptyString(rawRequest.previous_state_digest, "previous_state_digest");
  let stateChanged = rawRequest.state_changed === undefined
    ? null
    : requireBoolean(rawRequest.state_changed, "state_changed");
  if (stateChanged === null) {
    if (currentStateDigest !== null && previousStateDigest !== null) {
      stateChanged = currentStateDigest !== previousStateDigest;
    } else {
      stateChanged = true;
    }
  }
  const skipped = enabled && quietNoopAllowed && !stateChanged;
  return {
    schema_version: QUIET_TURN_METADATA_SCHEMA_VERSION,
    enabled,
    quiet_noop_allowed: quietNoopAllowed,
    state_changed: stateChanged,
    skipped,
    zero_tax: skipped,
    reason: !enabled
      ? "disabled"
      : !quietNoopAllowed
        ? "quiet_noop_disallowed"
        : skipped
          ? "unchanged_state"
          : "state_changed",
    context_delivery_skipped: skipped,
    prefetch_skipped: skipped,
    state_digest: currentStateDigest ?? previousStateDigest ?? null,
    operator: skipped ? "KeepRaw" : null,
  };
}
