import {createHash} from "node:crypto";

import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {
  jsonObject,
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";

export const STAGE_THEN_SWAP_POLICY_SCHEMA_VERSION = "loopx_stage_then_swap_policy_v0";
export const EPOCH_PREFETCH_RECEIPT_SCHEMA_VERSION = "loopx_epoch_prefetch_receipt_v0";
export const QUIET_TURN_METADATA_SCHEMA_VERSION = "loopx_quiet_turn_v0";
export const SEMANTIC_UNIT_ANCHOR_SCHEMA_VERSION = "loopx_semantic_unit_anchor_v0";
export const SEMANTIC_UNIT_FOLD_SWAP_SCHEMA_VERSION = "loopx_semantic_unit_fold_swap_v0";
export const CONTEXT_POLICY_OPERATORS = ["KeepRaw", "FoldToReceipt", "EpochReset"] as const;

export const DEFAULT_PREFETCH_TIMEOUT_MS = 5000;
export const MAX_PREFETCH_TIMEOUT_MS = 60000;
export const DEFAULT_PREFETCH_MAX_OUTPUT_BYTES = 16384;
export const MIN_PREFETCH_MAX_OUTPUT_BYTES = 128;
export const MAX_PREFETCH_MAX_OUTPUT_BYTES = 65536;
export const DEFAULT_MIN_NET_SAVINGS_TOKENS = 64;
export const DEFAULT_COLD_PREFILL_AMORTIZATION_DIVISOR = 20;
export const DEFAULT_TOKENIZER_ENCODING = "o200k_base";
const SUPPORTED_TOKENIZER_ENCODINGS = new Set([
  "o200k_base",
  "cl100k_base",
  "o200k_harmony",
  "p50k_base",
  "r50k_base",
]);

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
  min_net_savings_tokens: number;
  cold_prefill_amortization_tokens: number | null;
  tokenizer_encoding: string;
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
      min_net_savings_tokens: DEFAULT_MIN_NET_SAVINGS_TOKENS,
      cold_prefill_amortization_tokens: null,
      tokenizer_encoding: DEFAULT_TOKENIZER_ENCODING,
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
  const min_net_savings_tokens =
    optionalInt(
      config.min_net_savings_tokens,
      "epoch_prefetch.min_net_savings_tokens",
      0,
      10_000_000,
    ) ?? DEFAULT_MIN_NET_SAVINGS_TOKENS;
  const cold_prefill_amortization_tokens = optionalInt(
    config.cold_prefill_amortization_tokens,
    "epoch_prefetch.cold_prefill_amortization_tokens",
    0,
    10_000_000,
  );
  const tokenizer_encoding =
    optionalNonEmptyString(config.tokenizer_encoding, "epoch_prefetch.tokenizer_encoding")
    ?? DEFAULT_TOKENIZER_ENCODING;
  if (!SUPPORTED_TOKENIZER_ENCODINGS.has(tokenizer_encoding)) {
    throw new EffectRuntimeRequestError("epoch_prefetch.tokenizer_encoding is unsupported");
  }
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
    min_net_savings_tokens,
    cold_prefill_amortization_tokens,
    tokenizer_encoding,
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
    min_net_savings_tokens: config.min_net_savings_tokens,
    cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
    tokenizer_encoding: config.tokenizer_encoding,
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

function isContextReadCommand(command: readonly string[]): boolean {
  for (let idx = 0; idx < command.length; idx++) {
    if (command[idx] === "quota" && command[idx + 1] === "context-read") return true;
  }
  return false;
}

function inspectStructuredPrefetchOutput(
  output: string,
  sourceRef: {path: string; sha256: string} | null = null,
  requiresVerifiedPage = false,
): {
  failed: boolean;
  incomplete: boolean;
  sourceMismatch: boolean;
} {
  const trimmed = output.trim();
  if (!trimmed.startsWith("{")) {
    return {failed: false, incomplete: requiresVerifiedPage, sourceMismatch: false};
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {failed: false, incomplete: requiresVerifiedPage, sourceMismatch: false};
    }
    const obj = parsed as Record<string, unknown>;
    const failed = obj.ok === false;
    const schema = typeof obj.schema_version === "string" ? obj.schema_version : "";
    const isTypedCapturePage =
      schema === "context_capture_page_v1" || schema === "context_capture_selection_page_v1";
    const isPageSchema =
      isTypedCapturePage
      || "eof" in obj
      || "offset" in obj
      || "next_offset" in obj
      || "total_bytes" in obj;
    if (requiresVerifiedPage && !failed && !isTypedCapturePage) {
      return {failed: false, incomplete: true, sourceMismatch: false};
    }
    if (!isPageSchema) {
      return {failed, incomplete: false, sourceMismatch: false};
    }
    let incomplete = obj.eof === false || obj.truncated === true;
    if (!failed && (isTypedCapturePage || requiresVerifiedPage)) {
      const hasZeroOffset =
        typeof obj.offset === "number"
        && Number.isInteger(obj.offset)
        && obj.offset === 0;
      const hasClosedNextOffset = obj.next_offset === null;
      const hasCompleteEof = obj.eof === true && obj.truncated !== true;
      const hasContiguousLength =
        typeof obj.total_bytes === "number"
        && Number.isInteger(obj.total_bytes)
        && typeof obj.content === "string"
        && Buffer.byteLength(obj.content, "utf8") === obj.total_bytes;
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
          (isTypedCapturePage || requiresVerifiedPage)
          && reportedDigest === null
        )
      ),
    );
    return {failed, incomplete, sourceMismatch};
  } catch {
    if (
      requiresVerifiedPage
      || trimmed.includes("context_capture_page_v1")
      || trimmed.includes("context_capture_selection_page_v1")
    ) {
      return {failed: false, incomplete: true, sourceMismatch: false};
    }
    return {failed: false, incomplete: false, sourceMismatch: false};
  }
}

function normalizeTokenizerProvenance(
  value: unknown,
  expectedEncoding?: string,
): {name: string; encoding: string; version?: string} | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const obj = value as JsonObject;
  const name = typeof obj.name === "string" ? obj.name.trim() : "";
  const encoding = typeof obj.encoding === "string" ? obj.encoding.trim() : "";
  if (!name || !encoding) return null;
  if (expectedEncoding && encoding !== expectedEncoding) return null;
  const version = typeof obj.version === "string" && obj.version.trim() ? obj.version.trim() : undefined;
  return {name, encoding, ...(version ? {version} : {})};
}

interface TokenSavingsGateResult {
  evaluated: boolean;
  passed: boolean;
  fallback_reason: string | null;
  baseline_tokens: number | null;
  delivered_tokens: number | null;
  raw_net_savings_tokens: number | null;
  cold_prefill_amortization_tokens: number | null;
  amortized_net_savings_tokens: number | null;
  min_net_savings_tokens: number;
  tokenizer: {name: string; encoding: string; version?: string} | null;
}

function evaluateNetTokenSavingsGate(params: {
  min_net_savings_tokens: number;
  cold_prefill_amortization_tokens: number | null;
  tokenizer_encoding: string;
  baseline_tokens: number | null;
  delivered_tokens: number | null;
  rebased_prompt_tokens: number | null;
  tokenizer: unknown;
  require_token_gate: boolean;
}): TokenSavingsGateResult {
  const tokenizer = normalizeTokenizerProvenance(params.tokenizer, params.tokenizer_encoding);
  const {baseline_tokens, delivered_tokens, rebased_prompt_tokens} = params;
  if (
    baseline_tokens === null
    || delivered_tokens === null
    || (params.require_token_gate && tokenizer === null)
  ) {
    if (!params.require_token_gate && baseline_tokens === null && delivered_tokens === null) {
      return {
        evaluated: false,
        passed: true,
        fallback_reason: null,
        baseline_tokens: null,
        delivered_tokens: null,
        raw_net_savings_tokens: null,
        cold_prefill_amortization_tokens: null,
        amortized_net_savings_tokens: null,
        min_net_savings_tokens: params.min_net_savings_tokens,
        tokenizer,
      };
    }
    return {
      evaluated: true,
      passed: false,
      fallback_reason: "token_estimation_unavailable",
      baseline_tokens,
      delivered_tokens,
      raw_net_savings_tokens: null,
      cold_prefill_amortization_tokens: null,
      amortized_net_savings_tokens: null,
      min_net_savings_tokens: params.min_net_savings_tokens,
      tokenizer,
    };
  }
  const rawNet = baseline_tokens - delivered_tokens;
  const rebasedTokens = rebased_prompt_tokens ?? delivered_tokens;
  const amortization =
    params.cold_prefill_amortization_tokens
    ?? Math.ceil(rebasedTokens / DEFAULT_COLD_PREFILL_AMORTIZATION_DIVISOR);
  const amortizedNet = rawNet - amortization;
  const passed = rawNet > 0 && amortizedNet >= params.min_net_savings_tokens;
  return {
    evaluated: true,
    passed,
    fallback_reason: passed ? null : "negative_net_savings",
    baseline_tokens,
    delivered_tokens,
    raw_net_savings_tokens: rawNet,
    cold_prefill_amortization_tokens: amortization,
    amortized_net_savings_tokens: amortizedNet,
    min_net_savings_tokens: params.min_net_savings_tokens,
    tokenizer,
  };
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
  const baseline_tokens = optionalInt(rawRequest.baseline_tokens, "baseline_tokens", 0, 100_000_000);
  const hydrated_tokens = optionalInt(rawRequest.hydrated_tokens, "hydrated_tokens", 0, 100_000_000);
  const require_token_gate = rawRequest.require_token_gate === undefined
    ? false
    : requireBoolean(rawRequest.require_token_gate, "require_token_gate");
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
  const requiresVerifiedPage = source_ref !== null || isContextReadCommand(safety.argv);
  const pageStatus = inspectStructuredPrefetchOutput(prefetched_output, source_ref, requiresVerifiedPage);
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
  const tokenGate = evaluateNetTokenSavingsGate({
    min_net_savings_tokens: config.min_net_savings_tokens,
    cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
    tokenizer_encoding: config.tokenizer_encoding,
    baseline_tokens,
    delivered_tokens: hydrated_tokens,
    rebased_prompt_tokens: hydrated_tokens,
    tokenizer: rawRequest.tokenizer,
    require_token_gate,
  });

  let fallback_reason: string | null = null;
  if (!safety.allowed) {
    fallback_reason = safety.reason;
  } else if (timed_out) {
    fallback_reason = "timeout";
  } else if (exit_code === null || exit_code !== 0 || pageStatus.failed) {
    fallback_reason = "command_failed";
  } else if (config.reject_on_truncation && truncated) {
    fallback_reason = "output_truncated";
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
  } else if (tokenGate.evaluated && !tokenGate.passed) {
    fallback_reason = tokenGate.fallback_reason;
  }

  const tokenReceiptFields: JsonObject = tokenGate.evaluated
    ? {
        baseline_tokens: tokenGate.baseline_tokens,
        hydrated_tokens: tokenGate.delivered_tokens,
        net_savings_tokens: tokenGate.raw_net_savings_tokens,
        cold_prefill_amortization_tokens: tokenGate.cold_prefill_amortization_tokens,
        amortized_net_savings_tokens: tokenGate.amortized_net_savings_tokens,
        min_net_savings_tokens: tokenGate.min_net_savings_tokens,
        tokenizer: tokenGate.tokenizer,
      }
    : {};

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
      ...tokenReceiptFields,
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
    ...tokenReceiptFields,
  };
  return {
    ...committedReceipt,
    receipt_sha256: sha256Hex(JSON.stringify(committedReceipt)),
  };
}

export function evaluateEpochPrefetchPromptGate(rawRequest: JsonObject): JsonObject {
  const config =
    rawRequest.epoch_prefetch === undefined || rawRequest.epoch_prefetch === null
      ? normalizeStageThenSwapConfig(false)
      : normalizeStageThenSwapConfig(rawRequest.epoch_prefetch);
  const hasInline = requireBoolean(rawRequest.has_inline_exploratory, "has_inline_exploratory");
  const replaceInline = requireBoolean(rawRequest.replace_exploratory_context, "replace_exploratory_context");
  const hostInputMeasurable =
    rawRequest.host_input_measurable === undefined
      ? true
      : requireBoolean(rawRequest.host_input_measurable, "host_input_measurable");
  const beforeBytes = optionalInt(rawRequest.rendered_prompt_baseline_bytes, "rendered_prompt_baseline_bytes", 0, 100_000_000);
  const afterBytes = optionalInt(rawRequest.rendered_prompt_delivered_bytes, "rendered_prompt_delivered_bytes", 0, 100_000_000);
  const beforeTokens = optionalInt(rawRequest.rendered_prompt_baseline_tokens, "rendered_prompt_baseline_tokens", 0, 100_000_000);
  const afterTokens = optionalInt(rawRequest.rendered_prompt_delivered_tokens, "rendered_prompt_delivered_tokens", 0, 100_000_000);
  const externalBaselineBytes = optionalInt(rawRequest.external_baseline_bytes, "external_baseline_bytes", 0, 100_000_000);
  const externalBaselineTokens = optionalInt(rawRequest.external_baseline_tokens, "external_baseline_tokens", 0, 100_000_000);
  const potentialSaved = optionalInt(rawRequest.potential_bytes_saved, "potential_bytes_saved", 0, 100_000_000) ?? 0;

  const promptDeltaBytes = beforeBytes !== null && afterBytes !== null ? afterBytes - beforeBytes : null;
  const promptDeltaTokens = beforeTokens !== null && afterTokens !== null ? afterTokens - beforeTokens : null;

  const abortKeepRaw = (params: {
    fallback_reason: string;
    net_savings_bytes: number;
    tokenGate: TokenSavingsGateResult;
    error_observation: string;
  }): JsonObject => ({
    operator: "KeepRaw",
    status: "fallback_keep_raw",
    fallback_reason: params.fallback_reason,
    preserves_raw_state: true,
    bytes_saved: 0,
    net_savings_bytes: params.net_savings_bytes,
    net_savings_tokens: params.tokenGate.raw_net_savings_tokens,
    cold_prefill_amortization_tokens: params.tokenGate.cold_prefill_amortization_tokens,
    amortized_net_savings_tokens: params.tokenGate.amortized_net_savings_tokens,
    min_net_savings_tokens: params.tokenGate.min_net_savings_tokens,
    round_trips_saved: 0,
    rendered_prompt_baseline_bytes: beforeBytes,
    rendered_prompt_delivered_bytes: afterBytes,
    rendered_prompt_delta_bytes: promptDeltaBytes,
    rendered_prompt_baseline_tokens: beforeTokens,
    rendered_prompt_delivered_tokens: afterTokens,
    rendered_prompt_delta_tokens: promptDeltaTokens,
    tokenizer: params.tokenGate.tokenizer,
    error_observation: params.error_observation,
  });

  if (!hostInputMeasurable) {
    const unmeasurableGate = evaluateNetTokenSavingsGate({
      min_net_savings_tokens: config.min_net_savings_tokens,
      cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
      tokenizer_encoding: config.tokenizer_encoding,
      baseline_tokens: hasInline ? beforeTokens : externalBaselineTokens,
      delivered_tokens: null,
      rebased_prompt_tokens: afterTokens,
      tokenizer: rawRequest.tokenizer,
      require_token_gate: true,
    });
    return abortKeepRaw({
      fallback_reason: "host_input_unmeasurable",
      net_savings_bytes: 0,
      tokenGate: unmeasurableGate,
      error_observation:
        "Prefetch aborted atomically to KeepRaw (host_input_unmeasurable: actual host input cannot be measured before launch).",
    });
  }

  if (hasInline && !replaceInline) {
    const emptyGate = evaluateNetTokenSavingsGate({
      min_net_savings_tokens: config.min_net_savings_tokens,
      cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
      tokenizer_encoding: config.tokenizer_encoding,
      baseline_tokens: beforeTokens,
      delivered_tokens: afterTokens,
      rebased_prompt_tokens: afterTokens,
      tokenizer: rawRequest.tokenizer,
      require_token_gate: true,
    });
    return abortKeepRaw({
      fallback_reason: "negative_net_savings",
      net_savings_bytes: promptDeltaBytes !== null ? -Math.max(0, promptDeltaBytes) : 0,
      tokenGate: emptyGate,
      error_observation: "Prefetch aborted atomically to KeepRaw (append mode does not replace exploratory_context).",
    });
  }

  if (
    beforeBytes === null
    || afterBytes === null
    || beforeTokens === null
    || afterTokens === null
    || (!hasInline && externalBaselineTokens === null)
  ) {
    const missingGate = evaluateNetTokenSavingsGate({
      min_net_savings_tokens: config.min_net_savings_tokens,
      cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
      tokenizer_encoding: config.tokenizer_encoding,
      baseline_tokens: hasInline ? beforeTokens : externalBaselineTokens,
      delivered_tokens: hasInline ? afterTokens : null,
      rebased_prompt_tokens: afterTokens,
      tokenizer: rawRequest.tokenizer,
      require_token_gate: true,
    });
    return abortKeepRaw({
      fallback_reason: "token_estimation_unavailable",
      net_savings_bytes: 0,
      tokenGate: missingGate,
      error_observation:
        "Prefetch aborted atomically to KeepRaw (verifiable prompt/source token estimation unavailable).",
    });
  }

  const netSavingsBytes = hasInline
    ? beforeBytes - afterBytes
    : externalBaselineBytes !== null
      ? externalBaselineBytes - Math.max(0, afterBytes - beforeBytes)
      : 0;
  const minBytesRequired = config.min_net_savings_bytes ?? (hasInline || externalBaselineBytes !== null ? 1 : 0);
  const baselineTokensForGate = hasInline ? beforeTokens : externalBaselineTokens!;
  const deliveredTokensForGate = hasInline ? afterTokens : Math.max(0, afterTokens - beforeTokens);
  const tokenGate = evaluateNetTokenSavingsGate({
    min_net_savings_tokens: config.min_net_savings_tokens,
    cold_prefill_amortization_tokens: config.cold_prefill_amortization_tokens,
    tokenizer_encoding: config.tokenizer_encoding,
    baseline_tokens: baselineTokensForGate,
    delivered_tokens: deliveredTokensForGate,
    rebased_prompt_tokens: afterTokens,
    tokenizer: rawRequest.tokenizer,
    require_token_gate: true,
  });

  if (!tokenGate.passed) {
    const reason = tokenGate.fallback_reason ?? "negative_net_savings";
    return abortKeepRaw({
      fallback_reason: reason,
      net_savings_bytes: netSavingsBytes,
      tokenGate,
      error_observation:
        reason === "token_estimation_unavailable"
          ? "Prefetch aborted atomically to KeepRaw (verifiable tokenizer provenance missing)."
          : `Prefetch aborted atomically to KeepRaw (amortized net token savings ${tokenGate.amortized_net_savings_tokens} < ${tokenGate.min_net_savings_tokens}; raw token delta ${tokenGate.raw_net_savings_tokens}, cold prefill amortization ${tokenGate.cold_prefill_amortization_tokens}).`,
    });
  }

  if ((hasInline || externalBaselineBytes !== null) && netSavingsBytes < minBytesRequired) {
    return abortKeepRaw({
      fallback_reason: "negative_net_savings",
      net_savings_bytes: netSavingsBytes,
      tokenGate,
      error_observation: `Prefetch aborted atomically to KeepRaw (rendered prompt net byte savings ${netSavingsBytes}B < ${minBytesRequired}B).`,
    });
  }

  const bytesSaved = potentialSaved > 0 ? Math.min(potentialSaved, Math.max(0, netSavingsBytes)) : Math.max(0, netSavingsBytes);
  return {
    operator: "EpochReset",
    status: "committed",
    fallback_reason: null,
    preserves_raw_state: false,
    bytes_saved: bytesSaved,
    net_savings_bytes: netSavingsBytes,
    net_savings_tokens: tokenGate.raw_net_savings_tokens,
    cold_prefill_amortization_tokens: tokenGate.cold_prefill_amortization_tokens,
    amortized_net_savings_tokens: tokenGate.amortized_net_savings_tokens,
    min_net_savings_tokens: tokenGate.min_net_savings_tokens,
    round_trips_saved: 1,
    rendered_prompt_baseline_bytes: beforeBytes,
    rendered_prompt_delivered_bytes: afterBytes,
    rendered_prompt_delta_bytes: promptDeltaBytes,
    rendered_prompt_baseline_tokens: beforeTokens,
    rendered_prompt_delivered_tokens: afterTokens,
    rendered_prompt_delta_tokens: promptDeltaTokens,
    tokenizer: tokenGate.tokenizer,
    error_observation: null,
  };
}

function optionalBooleanField(obj: JsonObject | null, key: string): boolean | null {
  if (!obj || !Object.hasOwn(obj, key)) return null;
  return typeof obj[key] === "boolean" ? obj[key] : null;
}

function resolveSingleContractQuietEligibility(contract: JsonObject): {
  must_attempt: boolean;
  explicitly_forbidden: boolean;
  explicitly_allowed: boolean;
} {
  const decision = jsonObject(contract.decision);
  const interaction = jsonObject(contract.interaction_contract) ?? jsonObject(decision?.interaction_contract);
  const agentChannel = jsonObject(interaction?.agent_channel);
  const action = jsonObject(contract.action) ?? jsonObject(decision?.action);

  const mustAttempt =
    optionalBooleanField(contract, "must_attempt") === true
    || optionalBooleanField(decision, "must_attempt") === true
    || agentChannel?.must_attempt === true
    || action?.must_attempt === true;

  const flags = [
    optionalBooleanField(agentChannel, "quiet_noop_allowed"),
    optionalBooleanField(action, "quiet_noop_allowed"),
    optionalBooleanField(decision, "quiet_noop_allowed"),
    optionalBooleanField(contract, "quiet_noop_allowed"),
  ];
  return {
    must_attempt: mustAttempt,
    explicitly_forbidden: flags.some((flag) => flag === false),
    explicitly_allowed: flags.some((flag) => flag === true),
  };
}

export function resolvePlanQuietNoopEligibility(plan: JsonObject): {
  must_attempt: boolean;
  quiet_noop_allowed: boolean;
  source: "work_contract" | "turn_envelope" | "none";
} {
  const workContract = jsonObject(plan.work_contract);
  const turnEnvelope = jsonObject(plan.turn_envelope);
  if (!workContract && !turnEnvelope) {
    return {must_attempt: false, quiet_noop_allowed: false, source: "none"};
  }
  const source = workContract ? "work_contract" : "turn_envelope";
  const workCheck = workContract ? resolveSingleContractQuietEligibility(workContract) : null;
  const envCheck = turnEnvelope ? resolveSingleContractQuietEligibility(turnEnvelope) : null;
  const mustAttempt = (workCheck?.must_attempt ?? false) || (envCheck?.must_attempt ?? false);
  const explicitlyForbidden =
    (workCheck?.explicitly_forbidden ?? false) || (envCheck?.explicitly_forbidden ?? false);
  const explicitlyAllowed =
    (workCheck?.explicitly_allowed ?? false) || (envCheck?.explicitly_allowed ?? false);
  const quietNoopAllowed = !mustAttempt && !explicitlyForbidden && explicitlyAllowed;
  return {must_attempt: mustAttempt, quiet_noop_allowed: quietNoopAllowed, source};
}

const QUIET_VOLATILE_STATE_KEYS = new Set([
  "projection_envelope",
  "contract_binding",
  "heartbeat_receipt",
  "turn_instance_id",
  "source_decision_hash",
  "detail_ref",
  "cold_path_detail",
  "recheck_after_seconds",
  "frontier_recheck_after_seconds",
  "seconds_until_due",
  "observed_at",
  "served_at",
  "last_read_at",
  "age_seconds",
  "staleness_seconds",
]);

function stripVolatileQuietFields(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stripVolatileQuietFields);
  }
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      if (QUIET_VOLATILE_STATE_KEYS.has(key)) continue;
      sorted[key] = stripVolatileQuietFields(obj[key]);
    }
    return sorted;
  }
  return value;
}

export function computeQuietTurnStateDigest(rawRequest: JsonObject): JsonObject {
  const goalId = requireNonEmptyString(rawRequest.goal_id, "goal_id");
  const agentId = typeof rawRequest.agent_id === "string" ? rawRequest.agent_id : "";
  const workContract = jsonObject(rawRequest.work_contract);
  const turnEnvelope = jsonObject(rawRequest.turn_envelope);
  const route = jsonObject(rawRequest.route);
  const contractForDigest = workContract ?? turnEnvelope ?? {};
  const eligibility = resolvePlanQuietNoopEligibility({
    ...(workContract !== null ? {work_contract: workContract} : {}),
    ...(turnEnvelope !== null ? {turn_envelope: turnEnvelope} : {}),
  });
  const canonicalPayload = stripVolatileQuietFields({
    goal_id: goalId,
    agent_id: agentId,
    route,
    work_contract: contractForDigest,
  });
  const state_digest = sha256Hex(JSON.stringify(canonicalPayload));
  return {
    state_digest,
    must_attempt: eligibility.must_attempt,
    quiet_noop_allowed: eligibility.quiet_noop_allowed,
    source: eligibility.source,
  };
}

export function evaluateQuietTurn(rawRequest: JsonObject): JsonObject {
  const enabled = rawRequest.enabled === undefined ? true : requireBoolean(rawRequest.enabled, "quiet_turn.enabled");
  const requestedQuietNoop =
    rawRequest.quiet_noop_allowed === undefined
      ? true
      : requireBoolean(rawRequest.quiet_noop_allowed, "quiet_noop_allowed");
  const requestedMustAttempt =
    rawRequest.must_attempt === undefined
      ? false
      : requireBoolean(rawRequest.must_attempt, "must_attempt");
  let contractMustAttempt = requestedMustAttempt;
  let contractAllowsQuiet = true;
  if (rawRequest.work_contract !== undefined || rawRequest.turn_envelope !== undefined) {
    const eligibility = resolvePlanQuietNoopEligibility({
      ...(rawRequest.work_contract !== undefined ? {work_contract: rawRequest.work_contract} : {}),
      ...(rawRequest.turn_envelope !== undefined ? {turn_envelope: rawRequest.turn_envelope} : {}),
    });
    contractMustAttempt = contractMustAttempt || eligibility.must_attempt;
    contractAllowsQuiet = eligibility.quiet_noop_allowed;
  }
  const quietNoopAllowed = requestedQuietNoop && !contractMustAttempt && contractAllowsQuiet;
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

function requireBareSha256(value: unknown, label: string): string {
  const text = requireNonEmptyString(value, label);
  if (!BARE_SHA256_PATTERN.test(text)) {
    throw new EffectRuntimeRequestError(`${label} must be a bare sha256 digest`);
  }
  return text;
}

function requireNonNegativeInt(value: unknown, label: string, max = 1_000_000_000): number {
  const parsed = optionalInt(value, label, 0, max);
  if (parsed === null) {
    throw new EffectRuntimeRequestError(`${label} is required`);
  }
  return parsed;
}

export function recordSemanticUnitAnchor(rawRequest: JsonObject): JsonObject {
  const todo_id = requireNonEmptyString(rawRequest.todo_id, "todo_id");
  const turn_instance_id = requireNonEmptyString(rawRequest.turn_instance_id, "turn_instance_id");
  const anchor_step_index = requireNonNegativeInt(rawRequest.anchor_step_index, "anchor_step_index", 1_000_000);
  const prefix_digest = requireBareSha256(rawRequest.prefix_digest, "prefix_digest");
  const context_epoch = requireNonEmptyString(rawRequest.context_epoch, "context_epoch");
  const anchor_surface_seq = optionalInt(rawRequest.anchor_surface_seq, "anchor_surface_seq", 0, 1_000_000_000);
  const goal_id = optionalNonEmptyString(rawRequest.goal_id, "goal_id");
  const agent_id = optionalNonEmptyString(rawRequest.agent_id, "agent_id");
  const anchorCore: JsonObject = {
    schema_version: SEMANTIC_UNIT_ANCHOR_SCHEMA_VERSION,
    todo_id,
    turn_instance_id,
    anchor_step_index,
    anchor_surface_seq,
    prefix_digest,
    context_epoch,
    ...(goal_id !== null ? {goal_id} : {}),
    ...(agent_id !== null ? {agent_id} : {}),
  };
  return {
    ...anchorCore,
    anchor_sha256: sha256Hex(JSON.stringify(anchorCore)),
  };
}

function normalizeObsNextCommand(rawObs: unknown): {
  ok: boolean;
  reason: string | null;
  command: string[];
  command_sha256: string | null;
  exit_code: number | null;
  truncated: boolean;
  output: string;
  latency_ms: number;
} | null {
  if (rawObs === undefined || rawObs === null) return null;
  const obj = jsonObject(rawObs);
  if (!obj) {
    return {
      ok: false,
      reason: "invalid_obs_next_cmd",
      command: [],
      command_sha256: null,
      exit_code: null,
      truncated: false,
      output: "",
      latency_ms: 0,
    };
  }
  const initiator = optionalNonEmptyString(obj.initiator, "obs_next_cmd.initiator") ?? "turn_driver";
  const rawCommand = obj.prefetch_command ?? obj.command;
  const safety = validatePrefetchCommandSafety({prefetch_command: rawCommand, initiator});
  const timedOut = obj.timed_out === true;
  const truncated = obj.truncated === true;
  const rejectOnTruncation = obj.reject_on_truncation === undefined ? true : obj.reject_on_truncation === true;
  const maxBytes = optionalInt(
    obj.max_output_bytes,
    "obs_next_cmd.max_output_bytes",
    MIN_PREFETCH_MAX_OUTPUT_BYTES,
    MAX_PREFETCH_MAX_OUTPUT_BYTES,
  ) ?? DEFAULT_PREFETCH_MAX_OUTPUT_BYTES;
  const exitCode = obj.exit_code === undefined || obj.exit_code === null
    ? null
    : optionalInt(obj.exit_code, "obs_next_cmd.exit_code", -128, 255);
  const output = typeof obj.output === "string" ? obj.output : "";
  const latencyMs = optionalInt(obj.latency_ms, "obs_next_cmd.latency_ms", 0, 3_600_000) ?? 0;
  let reason: string | null = null;
  if (!safety.allowed) reason = safety.reason;
  else if (timedOut) reason = "obs_command_timeout";
  else if (exitCode === null || exitCode !== 0) reason = "command_error";
  else if (rejectOnTruncation && truncated) reason = "output_truncated";
  else if (Buffer.byteLength(output, "utf8") > maxBytes) reason = "output_overflow";
  return {
    ok: reason === null,
    reason,
    command: safety.argv,
    command_sha256: safety.command_sha256,
    exit_code: exitCode,
    truncated,
    output: reason === null ? output : "",
    latency_ms: latencyMs,
  };
}

function buildSemanticUnitReceiptText(params: {
  mu: JsonObject;
  sourceRefText: string;
  obsOutput: string | null;
}): string {
  const mu = params.mu;
  const range = mu.reclaim_range as number[];
  const lines = [
    "[Semantic-Unit Fold Receipt mu | trust=tool]",
    `todo_id: ${String(mu.todo_id)}`,
    `completion_receipt_id: ${String(mu.completion_receipt_id)}`,
    `completion_sha256: ${String(mu.sha256)}`,
    `turn_instance_id: ${String(mu.turn_instance_id)}`,
    `context_epoch: ${String(mu.context_epoch)}`,
    `prefix_digest: ${String(mu.prefix_digest)}`,
    `reclaim_range: [${range[0]}..${range[1]}]`,
    `exit_code: ${String(mu.exit_code)}`,
    `source_ref: ${params.sourceRefText}`,
    `failure_facts: ${JSON.stringify(mu.failure_facts)}`,
    `open_obligations: ${JSON.stringify(mu.open_obligations)}`,
  ];
  if (params.obsOutput !== null && params.obsOutput.length > 0) {
    lines.push("--- obs_next_cmd ---", params.obsOutput);
  }
  return lines.join("\n");
}

export function buildSemanticUnitFoldSwap(rawRequest: JsonObject): JsonObject {
  const enabled = rawRequest.enabled === undefined ? false : requireBoolean(rawRequest.enabled, "enabled");
  const todo_id = requireNonEmptyString(rawRequest.todo_id, "todo_id");
  const turn_instance_id = requireNonEmptyString(rawRequest.turn_instance_id, "turn_instance_id");
  const context_epoch = requireNonEmptyString(rawRequest.context_epoch, "context_epoch");
  const rawReceipt = jsonObject(rawRequest.completion_receipt);
  const receiptFailureFacts = cleanTextList(rawReceipt?.failure_facts ?? rawRequest.failure_facts, 32);
  const receiptOpenObligations = cleanTextList(rawReceipt?.open_obligations ?? rawRequest.open_obligations, 64);

  const minSavingsTokens =
    optionalInt(rawRequest.min_net_savings_tokens, "min_net_savings_tokens", 1, 1_000_000)
    ?? DEFAULT_MIN_NET_SAVINGS_TOKENS;
  const coldAmortizationTokens = optionalInt(
    rawRequest.cold_prefill_amortization_tokens,
    "cold_prefill_amortization_tokens",
    0,
    1_000_000,
  );
  const tokenizerEncoding =
    optionalNonEmptyString(rawRequest.tokenizer_encoding, "tokenizer_encoding") ?? DEFAULT_TOKENIZER_ENCODING;
  if (!SUPPORTED_TOKENIZER_ENCODINGS.has(tokenizerEncoding)) {
    throw new EffectRuntimeRequestError(`unsupported tokenizer_encoding ${tokenizerEncoding}`);
  }
  const baselineTokens = optionalInt(rawRequest.baseline_tokens, "baseline_tokens", 0, 100_000_000);
  const replacementTokens = optionalInt(rawRequest.replacement_tokens, "replacement_tokens", 0, 100_000_000);

  if (!enabled) {
    const disabledPayload: JsonObject = {
      schema_version: SEMANTIC_UNIT_FOLD_SWAP_SCHEMA_VERSION,
      enabled: false,
      operator: "KeepRaw",
      status: "disabled",
      fallback_reason: "disabled",
      trust_level: "tool",
      preserves_raw_state: true,
      preserves_prefix_kv: true,
      todo_id,
      turn_instance_id,
      context_epoch,
      prefix_digest: typeof rawRequest.prefix_digest === "string" ? rawRequest.prefix_digest : null,
      reclaim_range: null,
      reclaim_bounds: null,
      receipt_mu: null,
      obs_next_cmd: null,
      replacement_text: null,
      preserved_failure_facts: receiptFailureFacts,
      preserved_open_obligations: receiptOpenObligations,
      round_trips_saved: 0,
      bytes_saved: 0,
      net_savings_tokens: null,
      cold_prefill_amortization_tokens: null,
      amortized_net_savings_tokens: null,
      min_net_savings_tokens: minSavingsTokens,
      error_observation: null,
    };
    return {
      ...disabledPayload,
      receipt_sha256: sha256Hex(JSON.stringify(disabledPayload)),
    };
  }

  let anchor: JsonObject | null = null;
  let anchorValidError: string | null = null;
  const rawAnchor = jsonObject(rawRequest.anchor);
  if (!rawAnchor) {
    anchorValidError = "missing_anchor";
  } else {
    try {
      const normalizedAnchor = recordSemanticUnitAnchor(rawAnchor);
      if (
        typeof rawAnchor.anchor_sha256 === "string"
        && rawAnchor.anchor_sha256 !== normalizedAnchor.anchor_sha256
      ) {
        anchorValidError = "anchor_digest_mismatch";
      } else {
        anchor = normalizedAnchor;
      }
    } catch {
      anchorValidError = "invalid_anchor";
    }
  }

  const nowStepIndex = optionalInt(rawRequest.now_step_index, "now_step_index", 0, 1_000_000);
  const nowSurfaceSeq = optionalInt(rawRequest.now_surface_seq, "now_surface_seq", 0, 1_000_000_000);
  const surfacePrefixIntact =
    rawRequest.surface_prefix_intact === undefined
      ? true
      : requireBoolean(rawRequest.surface_prefix_intact, "surface_prefix_intact");
  const toolPairingValid =
    rawRequest.tool_pairing_valid === undefined
      ? true
      : requireBoolean(rawRequest.tool_pairing_valid, "tool_pairing_valid");
  const quiescentBoundary =
    rawRequest.quiescent_boundary === undefined
      ? true
      : requireBoolean(rawRequest.quiescent_boundary, "quiescent_boundary");

  let prefixDigest: string | null = null;
  let prefixDigestInvalid = false;
  if (typeof rawRequest.prefix_digest === "string" && BARE_SHA256_PATTERN.test(rawRequest.prefix_digest)) {
    prefixDigest = rawRequest.prefix_digest;
  } else {
    prefixDigestInvalid = true;
  }

  let sourceRef: {path: string; sha256: string} | null = null;
  let sourceRefInvalid = false;
  if (rawReceipt && rawReceipt.source_ref !== undefined && rawReceipt.source_ref !== null) {
    try {
      sourceRef = normalizeSourceRef(rawReceipt.source_ref);
    } catch {
      sourceRefInvalid = true;
    }
  } else {
    sourceRefInvalid = rawReceipt !== null;
  }

  const receiptTodoId =
    rawReceipt && typeof rawReceipt.todo_id === "string" && rawReceipt.todo_id.trim()
      ? rawReceipt.todo_id.trim()
      : null;
  const receiptId =
    rawReceipt && typeof rawReceipt.completion_receipt_id === "string" && rawReceipt.completion_receipt_id.trim()
      ? rawReceipt.completion_receipt_id.trim()
      : null;
  const receiptExitCode =
    rawReceipt && rawReceipt.exit_code !== undefined && rawReceipt.exit_code !== null
      && typeof rawReceipt.exit_code === "number" && Number.isSafeInteger(rawReceipt.exit_code)
      && rawReceipt.exit_code >= -128 && rawReceipt.exit_code <= 255
      ? rawReceipt.exit_code
      : null;
  const receiptSha =
    rawReceipt && typeof rawReceipt.sha256 === "string" && BARE_SHA256_PATTERN.test(rawReceipt.sha256)
      ? rawReceipt.sha256
      : null;

  const obsCheck = normalizeObsNextCommand(rawRequest.obs_next_cmd);

  let fallbackReason: string | null = null;
  if (!quiescentBoundary) fallbackReason = "non_quiescent_boundary";
  else if (anchorValidError !== null || anchor === null) fallbackReason = anchorValidError ?? "missing_anchor";
  else if (prefixDigestInvalid || prefixDigest === null) fallbackReason = "invalid_prefix_digest";
  else if (anchor.todo_id !== todo_id) fallbackReason = "todo_id_mismatch";
  else if (anchor.turn_instance_id !== turn_instance_id) fallbackReason = "turn_instance_mismatch";
  else if (anchor.context_epoch !== context_epoch) fallbackReason = "context_epoch_mismatch";
  else if (anchor.prefix_digest !== prefixDigest) fallbackReason = "prefix_digest_mismatch";
  else if (!surfacePrefixIntact) fallbackReason = "prefix_kv_not_preserved";
  else if (!toolPairingValid) fallbackReason = "unpaired_tool_calls";
  else if (nowStepIndex === null || nowStepIndex < (anchor.anchor_step_index as number)) {
    fallbackReason = "invalid_reclaim_range";
  } else if (
    anchor.anchor_surface_seq !== null
    && anchor.anchor_surface_seq !== undefined
    && (nowSurfaceSeq === null || nowSurfaceSeq < (anchor.anchor_surface_seq as number))
  ) {
    fallbackReason = "invalid_reclaim_range";
  } else if (rawReceipt === null) fallbackReason = "missing_completion_receipt";
  else if (receiptTodoId === null || receiptId === null) fallbackReason = "invalid_completion_receipt";
  else if (receiptTodoId !== todo_id) fallbackReason = "receipt_todo_mismatch";
  else if (receiptSha === null) fallbackReason = "invalid_completion_sha256";
  else if (sourceRefInvalid || sourceRef === null) fallbackReason = "missing_source_ref";
  else if (receiptExitCode === null || receiptExitCode !== 0) fallbackReason = "command_error";
  else if (obsCheck !== null && !obsCheck.ok) fallbackReason = obsCheck.reason;

  const anchorStep = anchor ? (anchor.anchor_step_index as number) : 0;
  const anchorSurfaceSeq = anchor ? ((anchor.anchor_surface_seq as number | null) ?? null) : null;
  const reclaimRange = fallbackReason === null && nowStepIndex !== null ? [anchorStep, nowStepIndex] : null;
  const reclaimBounds: JsonObject | null = reclaimRange
    ? {
        start_step: anchorStep,
        end_step: nowStepIndex,
        start_surface_seq: anchorSurfaceSeq,
        end_surface_seq: nowSurfaceSeq,
      }
    : null;

  const receiptMu: JsonObject | null =
    fallbackReason === null && reclaimRange !== null && sourceRef !== null
      ? {
          trust_level: "tool",
          todo_id,
          completion_receipt_id: receiptId,
          sha256: receiptSha,
          turn_instance_id,
          context_epoch,
          prefix_digest: prefixDigest,
          reclaim_range: reclaimRange,
          exit_code: receiptExitCode,
          failure_facts: receiptFailureFacts,
          open_obligations: receiptOpenObligations,
          source_ref: sourceRef,
        }
      : null;

  const obsOutputText = obsCheck && obsCheck.ok && obsCheck.output ? obsCheck.output : null;
  const replacementText =
    receiptMu !== null && sourceRef !== null
      ? buildSemanticUnitReceiptText({
          mu: receiptMu,
          sourceRefText: `${sourceRef.path} (sha256:${sourceRef.sha256})`,
          obsOutput: obsOutputText,
        })
      : null;

  const tokenGate = evaluateNetTokenSavingsGate({
    min_net_savings_tokens: minSavingsTokens,
    cold_prefill_amortization_tokens: coldAmortizationTokens,
    tokenizer_encoding: tokenizerEncoding,
    baseline_tokens: baselineTokens,
    delivered_tokens: replacementTokens,
    rebased_prompt_tokens: replacementTokens,
    tokenizer: rawRequest.tokenizer,
    require_token_gate: fallbackReason === null,
  });

  if (fallbackReason === null && !tokenGate.passed) {
    fallbackReason = tokenGate.fallback_reason ?? "negative_net_savings";
  }

  const tokenReceiptFields: JsonObject = {
    baseline_tokens: tokenGate.baseline_tokens,
    replacement_tokens: tokenGate.delivered_tokens,
    net_savings_tokens: tokenGate.raw_net_savings_tokens,
    cold_prefill_amortization_tokens: tokenGate.cold_prefill_amortization_tokens,
    amortized_net_savings_tokens: tokenGate.amortized_net_savings_tokens,
    min_net_savings_tokens: tokenGate.min_net_savings_tokens,
    tokenizer: tokenGate.tokenizer,
  };

  if (fallbackReason !== null) {
    const rollbackPayload: JsonObject = {
      schema_version: SEMANTIC_UNIT_FOLD_SWAP_SCHEMA_VERSION,
      enabled: true,
      operator: "KeepRaw",
      status: "fallback_keep_raw",
      fallback_reason: fallbackReason,
      trust_level: "tool",
      preserves_raw_state: true,
      preserves_prefix_kv: true,
      todo_id,
      turn_instance_id,
      context_epoch,
      prefix_digest: prefixDigest,
      reclaim_range: null,
      reclaim_bounds: null,
      receipt_mu: null,
      obs_next_cmd: obsCheck
        ? {
            allowed: obsCheck.reason === null,
            command_sha256: obsCheck.command_sha256,
            exit_code: obsCheck.exit_code,
            truncated: obsCheck.truncated,
            latency_ms: obsCheck.latency_ms,
          }
        : null,
      replacement_text: null,
      preserved_failure_facts: receiptFailureFacts,
      preserved_open_obligations: receiptOpenObligations,
      round_trips_saved: 0,
      bytes_saved: 0,
      ...tokenReceiptFields,
      error_observation: `Semantic-unit fold rolled back atomically to KeepRaw (${fallbackReason}).`,
    };
    return {
      ...rollbackPayload,
      receipt_sha256: sha256Hex(JSON.stringify(rollbackPayload)),
    };
  }

  const baselineBytes = optionalInt(rawRequest.baseline_bytes, "baseline_bytes", 0, 100_000_000);
  const replacementBytes = Buffer.byteLength(replacementText ?? "", "utf8");
  const bytesSaved = baselineBytes !== null && baselineBytes > replacementBytes
    ? baselineBytes - replacementBytes
    : 0;
  const committedPayload: JsonObject = {
    schema_version: SEMANTIC_UNIT_FOLD_SWAP_SCHEMA_VERSION,
    enabled: true,
    operator: "FoldToReceipt",
    status: "committed",
    fallback_reason: null,
    trust_level: "tool",
    preserves_raw_state: false,
    preserves_prefix_kv: true,
    todo_id,
    turn_instance_id,
    context_epoch,
    prefix_digest: prefixDigest,
    reclaim_range: reclaimRange,
    reclaim_bounds: reclaimBounds,
    receipt_mu: receiptMu,
    obs_next_cmd: obsCheck
      ? {
          allowed: true,
          command_sha256: obsCheck.command_sha256,
          exit_code: obsCheck.exit_code,
          truncated: obsCheck.truncated,
          latency_ms: obsCheck.latency_ms,
          output: obsOutputText,
        }
      : null,
    replacement_text: replacementText,
    preserved_failure_facts: receiptFailureFacts,
    preserved_open_obligations: receiptOpenObligations,
    round_trips_saved: obsCheck ? 1 : 0,
    bytes_saved: bytesSaved,
    ...tokenReceiptFields,
    error_observation: null,
  };
  return {
    ...committedPayload,
    receipt_sha256: sha256Hex(JSON.stringify(committedPayload)),
  };
}
