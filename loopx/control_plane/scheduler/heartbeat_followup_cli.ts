import { inflateSync } from "node:zlib";

import {
  effectRuntimeErrorPayload,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import {
  evaluateSchedulerHeartbeatFollowup,
  renderSchedulerHeartbeatFollowupMarkdown,
  SCHEDULER_HEARTBEAT_FOLLOWUP_ERROR_SCHEMA,
  SCHEDULER_HEARTBEAT_FOLLOWUP_HINT_SCHEMA,
  SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
  type SchedulerHeartbeatFollowupResult,
} from "./heartbeat_followup.ts";

const FACTS_FLAG = "--scheduler-host-facts-chunk";
const MAX_ENCODED_FACTS_CHARS = 4_096;
const MAX_INFLATED_FACTS_BYTES = 16_384;

interface ParsedArgs {
  format: "json" | "markdown";
  runtime_root: string;
  goal_id: string;
  agent_id: string;
  turn_instance_id: string;
  command: "scheduler-ack-current" | "scheduler-fail-current";
  chunks: string[];
  values: Record<string, string>;
  booleans: Set<string>;
}

function errorEnvelope(error: unknown) {
  return {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_ERROR_SCHEMA,
    status: "error",
    error: effectRuntimeErrorPayload(error),
  };
}

function optionValue(argv: string[], index: number, option: string): [string, number] {
  const token = argv[index];
  const prefix = `${option}=`;
  if (token.startsWith(prefix)) return [token.slice(prefix.length), index];
  const value = argv[index + 1];
  if (value === undefined || value.startsWith("--")) {
    throw new EffectRuntimeRequestError(`${option} requires a value`, "invalid_scheduler_followup_argv");
  }
  return [value, index + 1];
}

function parseArgs(argv: string[]): ParsedArgs {
  const values: Record<string, string> = {};
  const booleans = new Set<string>();
  const chunks: string[] = [];
  const positionals: string[] = [];
  const valueOptions = new Set([
    "--format",
    "--registry",
    "--runtime-root",
    "--goal-id",
    "--agent-id",
    "--surface",
    "--state-key",
    "--applied-rrule",
    "--failed-rrule",
    "--failure-kind",
    "--app-automation-current-rrule",
    "--codex-app-current-rrule",
    "--turn-instance-id",
    "--reset-token",
    "--identity-signature",
    "--reason-summary",
    "--available-capability",
    FACTS_FLAG,
  ]);
  const booleanOptions = new Set([
    "-A",
    "--codex-app",
    "--trae_app",
    "--execute",
    "--dry-run",
    "--host-match-observed",
    "--use-current-hint",
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    const option = token.startsWith("--") && token.includes("=")
      ? token.slice(0, token.indexOf("="))
      : token;
    if (valueOptions.has(option)) {
      const [value, next] = optionValue(argv, index, option);
      index = next;
      if (!value) {
        throw new EffectRuntimeRequestError(`${option} must not be empty`, "invalid_scheduler_followup_argv");
      }
      if (option === FACTS_FLAG) chunks.push(value);
      else if (option !== "--available-capability" && option !== "--registry") {
        values[option] = value;
      }
      continue;
    }
    if (booleanOptions.has(token)) {
      booleans.add(token);
      continue;
    }
    if (token.startsWith("-")) {
      throw new EffectRuntimeRequestError(
        `unsupported scheduler follow-up option: ${token}`,
        "invalid_scheduler_followup_argv",
      );
    }
    positionals.push(token);
  }
  if (
    positionals.length !== 2 ||
    positionals[0] !== "quota" ||
    !["scheduler-ack-current", "scheduler-fail-current"].includes(positionals[1])
  ) {
    throw new EffectRuntimeRequestError(
      "native scheduler follow-up requires an exact quota ACK/failure command",
      "invalid_scheduler_followup_command",
    );
  }
  const format = values["--format"] ?? "markdown";
  if (format !== "json" && format !== "markdown") {
    throw new EffectRuntimeRequestError("--format must be json or markdown", "invalid_scheduler_followup_argv");
  }
  if (booleans.has("--execute") && booleans.has("--dry-run")) {
    throw new EffectRuntimeRequestError(
      "scheduler follow-up accepts only one of --execute or --dry-run",
      "invalid_scheduler_followup_argv",
    );
  }
  return {
    format,
    runtime_root: values["--runtime-root"] ?? "",
    goal_id: values["--goal-id"] ?? "",
    agent_id: values["--agent-id"] ?? "",
    turn_instance_id: values["--turn-instance-id"] ?? "",
    command: positionals[1] as ParsedArgs["command"],
    chunks,
    values,
    booleans,
  };
}

function decodeSchedulerHostFactsChunks(chunks: string[]): Record<string, unknown> {
  const encoded = chunks.join("");
  if (
    chunks.length === 0 ||
    encoded.length > MAX_ENCODED_FACTS_CHARS ||
    !/^[A-Za-z0-9_+\/-]+$/.test(encoded)
  ) {
    throw new EffectRuntimeRequestError(
      "scheduler host facts are missing or exceed the encoded boundary",
      "invalid_scheduler_host_facts",
    );
  }
  try {
    const inflated = inflateSync(Buffer.from(encoded, "base64url"), {
      maxOutputLength: MAX_INFLATED_FACTS_BYTES,
    });
    return requireJsonObject(
      JSON.parse(inflated.toString("utf8")),
      "scheduler host facts",
    );
  } catch (error) {
    if (error instanceof EffectRuntimeRequestError) throw error;
    throw new EffectRuntimeRequestError(
      "scheduler host facts must be valid compressed JSON",
      "invalid_scheduler_host_facts",
    );
  }
}

function matchingText(
  facts: Record<string, unknown>,
  key: string,
  observed: string,
  label: string,
): void {
  if (String(facts[key] ?? "") !== observed) {
    throw new EffectRuntimeRequestError(
      `${label} does not match the bound scheduler host facts`,
      "scheduler_host_facts_identity_mismatch",
    );
  }
}

function requestFromArgs(argv: string[]): { request: Record<string, unknown>; format: "json" | "markdown" } {
  const parsed = parseArgs(argv);
  if (!parsed.runtime_root || !parsed.goal_id || !parsed.agent_id || !parsed.turn_instance_id) {
    throw new EffectRuntimeRequestError(
      "native scheduler follow-up requires runtime, goal, agent, and Turn bindings",
      "scheduler_followup_binding_missing",
    );
  }
  const hint = decodeSchedulerHostFactsChunks(parsed.chunks);
  if (hint.schema_version !== SCHEDULER_HEARTBEAT_FOLLOWUP_HINT_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Scheduler host follow-up hint schema mismatch",
      "scheduler_host_facts_schema_mismatch",
    );
  }
  const facts = requireJsonObject(hint.host_facts, "host_facts");
  matchingText(facts, "goal_id", parsed.goal_id, "--goal-id");
  matchingText(facts, "agent_id", parsed.agent_id, "--agent-id");
  const expectedOperation = parsed.command === "scheduler-ack-current"
    ? "ack"
    : "host_failure";
  const observedOperation = facts.operation === "failure" ? "host_failure" : facts.operation;
  if (observedOperation !== expectedOperation) {
    throw new EffectRuntimeRequestError(
      "scheduler command does not match the bound host-facts operation",
      "scheduler_host_facts_operation_mismatch",
    );
  }
  const values = parsed.values;
  const neutralObservedRrule = values["--app-automation-current-rrule"] ?? "";
  const legacyCodexObservedRrule = values["--codex-app-current-rrule"] ?? "";
  if (neutralObservedRrule && legacyCodexObservedRrule && neutralObservedRrule !== legacyCodexObservedRrule) {
    throw new EffectRuntimeRequestError(
      "App automation and Codex compatibility RRULE observations disagree",
      "scheduler_host_facts_identity_mismatch",
    );
  }
  if (parsed.booleans.has("--trae_app")) {
    matchingText(facts, "surface", "trae_app", "--trae_app");
    if (legacyCodexObservedRrule) {
      throw new EffectRuntimeRequestError(
        "Trae App cannot use the Codex RRULE compatibility alias",
        "scheduler_host_facts_identity_mismatch",
      );
    }
  }
  if (parsed.booleans.has("-A") || parsed.booleans.has("--codex-app")) {
    matchingText(facts, "surface", "codex_app", "--codex-app");
  }
  if (values["--surface"]) matchingText(facts, "surface", values["--surface"], "--surface");
  if (values["--state-key"]) matchingText(facts, "state_key", values["--state-key"], "--state-key");
  if (values["--reset-token"]) matchingText(facts, "reset_token", values["--reset-token"], "--reset-token");
  if (values["--identity-signature"]) {
    matchingText(facts, "identity_signature", values["--identity-signature"], "--identity-signature");
  }
  if (expectedOperation === "ack" && values["--applied-rrule"]) {
    matchingText(facts, "applied_rrule", values["--applied-rrule"], "--applied-rrule");
  }
  if (expectedOperation === "host_failure" && values["--failed-rrule"]) {
    matchingText(facts, "expected_rrule", values["--failed-rrule"], "--failed-rrule");
  }
  if (neutralObservedRrule || legacyCodexObservedRrule) {
    matchingText(
      facts,
      "observed_host_rrule",
      neutralObservedRrule || legacyCodexObservedRrule,
      neutralObservedRrule
        ? "--app-automation-current-rrule"
        : "--codex-app-current-rrule",
    );
  }
  if (values["--failure-kind"]) matchingText(facts, "failure_kind", values["--failure-kind"], "--failure-kind");
  return {
    format: parsed.format,
    request: {
      schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
      runtime_root: parsed.runtime_root,
      turn_instance_id: parsed.turn_instance_id,
      require_heartbeat_receipt: true,
      before: hint.before ?? {},
      use_current_hint: hint.use_current_hint ?? expectedOperation === "ack",
      reason_summary: values["--reason-summary"] ?? null,
      host_facts: {
        ...facts,
        operation: expectedOperation,
        runtime_root: parsed.runtime_root,
        execute: parsed.booleans.has("--execute"),
      },
    },
  };
}

async function readRequest(): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  const input = Buffer.concat(chunks).toString("utf8");
  if (!input.trim()) {
    throw new EffectRuntimeRequestError(
      "scheduler heartbeat follow-up input must not be empty",
      "empty_request",
    );
  }
  try {
    return JSON.parse(input);
  } catch {
    throw new EffectRuntimeRequestError(
      "scheduler heartbeat follow-up input must be valid JSON",
      "invalid_json",
    );
  }
}

async function main(): Promise<number> {
  try {
    const [major, minor, patch] = process.versions.node.split(".").map(Number);
    if (major < 22 || (major === 22 && (minor < 22 || (minor === 22 && patch < 3)))) {
      throw new EffectRuntimeRequestError(
        "native scheduler follow-up requires Node.js 22.22.3 or newer",
        "node_unsupported",
      );
    }
    const fromArgs = process.argv.length > 2;
    const parsed = fromArgs
      ? requestFromArgs(process.argv.slice(2))
      : { request: await readRequest(), format: "json" as const };
    const result = await evaluateSchedulerHeartbeatFollowup(parsed.request);
    const output = parsed.format === "json"
      ? JSON.stringify(result, null, 2)
      : renderSchedulerHeartbeatFollowupMarkdown(result as SchedulerHeartbeatFollowupResult);
    process.stdout.write(`${output}\n`);
    return result.ok === false ? 1 : 0;
  } catch (error) {
    process.stdout.write(`${JSON.stringify(errorEnvelope(error), null, 2)}\n`);
    return 1;
  }
}

const exitCode = await main();
process.exitCode = exitCode;
