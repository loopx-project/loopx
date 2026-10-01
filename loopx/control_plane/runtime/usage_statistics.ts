/** Machine-local telemetry owner. Never opens Goal/provider state. */
import { readFile, chmod, rm } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { arch, platform } from "node:os";
import type { JsonObject } from "../effect_program.ts";
import { withFileMutationLock, atomicWriteJson } from "../effect_runtime_io.ts";
import { AGGREGATE_SCHEMA, PING_SCHEMA, MAX_COUNT, MAX_ROWS, counterKey, object, validAggregate, validCounter, validId, validPing } from "./usage_statistics_contract.ts";
import type { Aggregate, Counter, Ping } from "./usage_statistics_contract.ts";
import { DIAGNOSTIC_SCHEMA, diagnosticKey, validDiagnostic, validDiagnostics } from "./usage_statistics_diagnostics.ts";
import type { Diagnostic, DiagnosticAggregate } from "./usage_statistics_diagnostics.ts";

import { recordGoalUsage, goalPreview } from "./usage_statistics_goals.ts";
import { validGoalAggregate } from "./usage_statistics_goal_contract.ts";
import type { GoalAggregate, GoalObservation } from "./usage_statistics_goal_contract.ts";

import { cycleObservations } from "./usage_statistics_cycles.ts";
import type { CycleObservation } from "./usage_statistics_cycles.ts";

export const STATE_SCHEMA = "loopx_usage_ping_state_v1";
export const DEFAULT_ENDPOINT = "https://loopx-usage-collector.huangrt01.workers.dev/v1/ping";
export const NOTICE_VERSION = 5;
const AGGREGATE_INTERVAL_MS = 15 * 60 * 1000;
export type Env = Record<string, string | undefined>;
export type Context = { env: Env; version: string; python: string; channel: string; now?: Date };
type Notice = { version: number; endpoint: string; policy: string };
type Delivery = { day: string; channel: "heartbeat" | "cli" | "goal"; rows: number; status: "accepted" | "rejected" | "unavailable" };
const MAX_DELIVERIES = 20;
type State = {
  schema: typeof STATE_SCHEMA; consent: "default" | "enabled" | "disabled"; generation: string;
  install_id?: string; notice?: Notice; last_attempt_day?: string; last_sent_day?: string;
  day?: string; counters?: Counter[]; aggregate_last_attempt_ms?: number;
  deliveries?: Delivery[];
  diagnostics?: Diagnostic[]; diagnostic_dropped?: number;
};
export function endpoint(env: Env): string {
  try {
    const url = new URL(env.LOOPX_USAGE_PING_ENDPOINT ?? DEFAULT_ENDPOINT);
    if (url.username || url.password || url.search || url.hash || !url.pathname.endsWith("/v1/ping")) return "";
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) return "";
    return url.href;
  } catch { return ""; }
}
function notice(ctx: Context): Notice {
  return { version: NOTICE_VERSION, endpoint: endpoint(ctx.env), policy: ctx.env.LOOPX_USAGE_POLICY ?? "opt_out" };
}
function sameNotice(state: State, ctx: Context): boolean {
  return JSON.stringify(state.notice) === JSON.stringify(notice(ctx));
}
function automaticNoticeRequired(state: State, ctx: Context): boolean {
  // New installations and expanded disclosures may use notice-before-default-on.
  // A changed recipient or policy still needs the owner's explicit choice.
  return blockedBy(state, ctx) === "notice_required"
    && (!state.notice || (state.notice.version !== NOTICE_VERSION
      && state.notice.endpoint === endpoint(ctx.env) && state.notice.policy === notice(ctx).policy));
}
export function blockedBy(state: State, ctx: Context): string | null {
  const env = ctx.env;
  if (["0", "false", "no", "off"].includes((env.LOOPX_USAGE_PING ?? "").trim().toLowerCase())) return "LOOPX_USAGE_PING";
  if (!["", "0"].includes((env.DO_NOT_TRACK ?? "").trim())) return "DO_NOT_TRACK";
  if (!["", "0", "false"].includes((env.CI ?? "").trim().toLowerCase())) return "CI";
  if (state.consent === "disabled") return "disabled";
  const policy = notice(ctx).policy;
  if (!["opt_out", "consent_required"].includes(policy)) return "invalid_policy";
  if (policy === "consent_required" && state.consent !== "enabled") return "consent_required";
  if (!endpoint(env)) return "invalid_endpoint";
  if (!sameNotice(state, ctx)) return "notice_required";
  return null;
}
async function load(path: string): Promise<State> {
  let raw: unknown;
  try { raw = JSON.parse(await readFile(path, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { schema: STATE_SCHEMA, consent: "default", generation: "" };
    throw new Error("usage_state_invalid");
  }
  if (!object(raw)) throw new Error("usage_state_invalid");
  if (raw.schema === "loopx_usage_ping_state_v0") {
    // Migrate only explicit choices and the original random id. New scope needs a new notice.
    if (!["enabled", "disabled"].includes(String(raw.consent))) throw new Error("usage_state_invalid");
    return { schema: STATE_SCHEMA, consent: raw.consent as State["consent"], generation: "",
      ...(validId(raw.install_id) && raw.consent === "enabled" ? { install_id: raw.install_id } : {}),
      ...(typeof raw.last_attempt_day === "string" ? { last_attempt_day: raw.last_attempt_day } : {}) };
  }
  if (raw.schema !== STATE_SCHEMA || !["default", "enabled", "disabled"].includes(String(raw.consent))
    || !validId(raw.generation) || (raw.install_id !== undefined && !validId(raw.install_id))
    || (raw.aggregate_last_attempt_ms !== undefined && (typeof raw.aggregate_last_attempt_ms !== "number"
      || !Number.isSafeInteger(raw.aggregate_last_attempt_ms) || raw.aggregate_last_attempt_ms < 0))
    || (raw.counters !== undefined && (!Array.isArray(raw.counters) || raw.counters.length > MAX_ROWS || !raw.counters.every(validCounter)))
    || (raw.diagnostic_dropped !== undefined && (!Number.isSafeInteger(raw.diagnostic_dropped) || Number(raw.diagnostic_dropped) < 0 || Number(raw.diagnostic_dropped) > MAX_COUNT))
    || (raw.diagnostics !== undefined && (!Array.isArray(raw.diagnostics) || raw.diagnostics.length > 32 || !raw.diagnostics.every(validDiagnostic)))) throw new Error("usage_state_invalid");
  return raw as State;
}
async function save(path: string, state: State) {
  await atomicWriteJson(path, state as unknown as JsonObject);
  await chmod(path, 0o600);
}
function validDelivery(value: unknown): value is Delivery {
  return object(value) && Object.keys(value).sort().join() === "channel,day,rows,status"
    && typeof value.day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value.day)
    && ["heartbeat", "cli", "goal"].includes(String(value.channel))
    && ["accepted", "rejected", "unavailable"].includes(String(value.status))
    && Number.isSafeInteger(value.rows) && Number(value.rows) >= 1 && Number(value.rows) <= MAX_ROWS;
}
function day(ctx: Context): string { return (ctx.now ?? new Date()).toISOString().slice(0, 10); }
function ping(state: State, ctx: Context): Ping | null {
  const value = { schema: PING_SCHEMA, install_id: state.install_id, version: ctx.version,
    os: ({ win32: "windows", darwin: "darwin", linux: "linux" } as Record<string, string>)[platform()] ?? "other",
    arch: ({ x64: "x64", arm64: "arm64", ia32: "x86" } as Record<string, string>)[arch()] ?? "other",
    python: ctx.python, channel: ctx.channel };
  return validPing(value) ? value : null;
}
export async function inspect(path: string, ctx: Context) {
  const state = await load(path);
  const blocked = blockedBy(state, ctx);
  return { schema: "loopx_usage_ping_status_v1", consent: state.consent, sending: blocked === null,
    blocked_by: blocked, endpoint: endpoint(ctx.env) || null, policy: notice(ctx).policy,
    notice: notice(ctx), notice_required: !sameNotice(state, ctx),
    automatic_notice_required: automaticNoticeRequired(state, ctx), last_sent_day: state.last_sent_day ?? null,
    next_payload: state.consent === "disabled" ? null : ping(state, ctx),
    aggregate_preview: state.consent === "disabled" || !state.counters?.length ? null : { schema: AGGREGATE_SCHEMA, counters: state.counters },
    diagnostic_preview: state.consent === "disabled" || !state.diagnostics?.length ? null : { schema: DIAGNOSTIC_SCHEMA, counters: state.diagnostics },
    diagnostic_dropped: state.diagnostic_dropped ?? 0,
    goal_preview: state.consent === "disabled" ? null : await goalPreview(path + ".goals", state.generation).catch(() => null),
    identity_scope: "persistent_machine_state_directory_not_person_or_session",
    delivery_history: state.consent === "disabled" ? [] : (state.deliveries ?? []).filter(validDelivery).slice(-MAX_DELIVERIES),
    aggregate_day: state.day ?? null,
    disclosure: "LoopX basic usage statistics are on by default after this notice. Daily heartbeats send a random installation ID, version, OS, CPU architecture, Python version and install channel to the configured LoopX collector (Cloudflare). Fixed CLI feature/sub-operation/result/duration/error counts, release version, UTC activity day, voluntary deployment context and receipt-backed lifecycle signals are sent separately without an ID. Deployment context defaults to unknown and is never inferred. The first measured CLI result is sent immediately; later activity sends buffered counts at most once every 15 minutes. More frequent requests can make network timing correlation easier: network services may observe IP addresses and request times even though CLI summaries have no installation ID. Goal span/duration buckets and fixed Host labels are aggregated without Goal or installation IDs. Common quota-to-spend cycles cover every Host using the quota CLI; bound Codex tasks add local timing-event reads; managed Turns and regular owner Goal chat add direct Host-call timing. These overlapping measurements are separate, partial and not completion or billing evidence. Raw session content is never uploaded. No prompts, code, paths, argument values, Goal contents or raw errors. Local status keeps at most 20 content-free delivery summaries, cleared on disable. Disable all with loopx usage-ping disable or LOOPX_USAGE_PING=0; inspect with loopx usage-ping status. Consent-required distributions wait for explicit enable. Recipient: " + (endpoint(ctx.env) || "not configured") };
}
export async function configure(path: string, ctx: Context, action: "enable" | "disable" | "acknowledge", expectedNotice?: unknown) {
  await withFileMutationLock(path, async () => {
    // Explicit disable can repair malformed state without permitting a send.
    const state = action === "disable" ? { schema: STATE_SCHEMA, consent: "disabled", generation: randomUUID() } as State : await load(path);
    if (action === "disable") {
      await save(path, state);
      await rm(path + ".goals", { force: true });
      await rm(path + ".cycles", { force: true });
      return;
    }
    if (action === "acknowledge" && JSON.stringify(expectedNotice) !== JSON.stringify(notice(ctx))) throw new Error("usage_notice_changed");
    if (action === "acknowledge" && !automaticNoticeRequired(state, ctx)) return;
    if (action === "enable") state.consent = "enabled";
    if (state.notice && !sameNotice(state, ctx)) {
      state.counters = [];
      state.diagnostics = [];
      await rm(path + ".goals", { force: true });
      await rm(path + ".cycles", { force: true });
      state.generation = randomUUID();
      if (state.notice.endpoint !== endpoint(ctx.env)) state.install_id = randomUUID();
    }
    state.install_id ??= randomUUID();
    state.generation ||= randomUUID();
    state.notice = notice(ctx);
    await save(path, state);
  }, 1000);
  return inspect(path, ctx);
}
export type Post = (url: string, payload: Ping | Aggregate | GoalAggregate | DiagnosticAggregate) => Promise<number>;
const post: Post = async (url, payload) => (await fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "loopx-usage-ping" },
  body: JSON.stringify(payload), signal: AbortSignal.timeout(3000), redirect: "error",
})).status;

/** Called in a detached process with one allowlisted observation, never raw argv/output. */
export async function observe(path: string, ctx: Context, generation: string, counter: Counter | null, send: Post = post, goal?: GoalObservation, cycle?: CycleObservation, diagnostic?: Diagnostic) {
  if (counter !== null && (!validCounter(counter) || counter.count !== 1)) return { sent: false, reason: "invalid_observation" };
  if (diagnostic && (!validDiagnostic(diagnostic) || diagnostic.count !== 1)) return { sent: false, reason: "invalid_observation" };
  let heartbeat: Ping | null = null;
  let heartbeatRequest: Promise<number> | undefined;
  let aggregate: Aggregate | null = null;
  let aggregateRequest: Promise<number> | undefined;
  let diagnostics: DiagnosticAggregate | null = null;
  let diagnosticRequest: Promise<number> | undefined;
  let goals: GoalAggregate | null = null;
  const today = day(ctx);
  const now = (ctx.now ?? new Date()).getTime();
  const allowed = await withFileMutationLock(path, async () => {
    const state = await load(path);
    const blocked = blockedBy(state, ctx);
    if (blocked || !generation || generation !== state.generation) return false;
    if (state.day && state.day > today) return false;
    if (state.aggregate_last_attempt_ms !== undefined && now < state.aggregate_last_attempt_ms) return false;
    try {
      const intervals = cycle ? await cycleObservations(path + ".cycles", generation, now, cycle) : [];
      goals = await recordGoalUsage(path + ".goals", generation, now, [...(goal ? [goal] : []), ...intervals]);
    }
    catch { /* A damaged optional measurement cannot block other diagnostics. */ }
    // Keep the oldest buffered UTC day for expiry, including across midnight.
    // Legacy daily buffers remain readable; no event times or join keys leave.
    if (state.day && Date.parse(today) - Date.parse(state.day) > 7 * 86400000) state.counters = [];
    state.counters ??= [];
    if (!state.counters.length) state.day = today;
    if (counter) {
      const row = state.counters.find((entry) => counterKey(entry) === counterKey(counter));
      if (row) row.count = Math.min(MAX_COUNT, row.count + 1);
      else if (state.counters.length < MAX_ROWS) state.counters.push({ ...counter });
    }
    state.diagnostics = (state.diagnostics ?? []).filter(row => Date.parse(today) - Date.parse(row.activity_day) <= 7 * 86400000);
    if (diagnostic) {
      const row = state.diagnostics.find(entry => diagnosticKey(entry) === diagnosticKey(diagnostic));
      if (row) row.count = Math.min(MAX_COUNT, row.count + 1);
      else if (state.diagnostics.length < 32) state.diagnostics.push({ ...diagnostic });
      else state.diagnostic_dropped = Math.min(MAX_COUNT, (state.diagnostic_dropped ?? 0) + 1);
    }
    if (!state.last_attempt_day || state.last_attempt_day < today) {
      heartbeat = ping(state, ctx);
      state.last_attempt_day = today; // claim before I/O; failures are not retried
    }
    // First result is eligible immediately. Later activity flushes deltas at
    // most once per interval, independently of heartbeat success or UTC rollover.
    if ((state.counters.length || state.diagnostics.length) && (state.aggregate_last_attempt_ms === undefined
      || now - state.aggregate_last_attempt_ms >= AGGREGATE_INTERVAL_MS)) {
      if (state.counters.length) aggregate = { schema: AGGREGATE_SCHEMA, counters: state.counters };
      if (state.diagnostics.length) diagnostics = { schema: DIAGNOSTIC_SCHEMA, counters: state.diagnostics };
      state.counters = [];
      state.diagnostics = [];
      state.aggregate_last_attempt_ms = now; // consume before I/O; never retry a lossy batch
    }
    await save(path, state);
    // Persist both claims, then initiate their requests before releasing this
    // lock. A competing observer must not consume a second acquisition between
    // the claim and the request. Network waiting stays outside the lock.
    if (heartbeat) {
      try { heartbeatRequest = send(endpoint(ctx.env), heartbeat).catch(() => 0); }
      catch { heartbeatRequest = Promise.resolve(0); }
    }
    if (aggregate && validAggregate(aggregate)) {
      try { aggregateRequest = send(endpoint(ctx.env).replace(/\/ping$/, "/aggregate"), aggregate).catch(() => 0); }
      catch { aggregateRequest = Promise.resolve(0); }
    }
    if (diagnostics && validDiagnostics(diagnostics)) {
      try { diagnosticRequest = send(endpoint(ctx.env).replace(/\/ping$/, "/aggregate"), diagnostics).catch(() => 0); }
      catch { diagnosticRequest = Promise.resolve(0); }
    }
    return true;
  }, 0); // Never queue behind business or telemetry work.
  if (!allowed) return { sent: false, reason: "blocked" };
  let sent = false;
  const outgoing: Array<readonly [string, Parameters<Post>[1] | null]> = [
    [endpoint(ctx.env), heartbeat], [endpoint(ctx.env).replace(/\/ping$/, "/aggregate"), aggregate],
    [endpoint(ctx.env).replace(/\/ping$/, "/aggregate"), diagnostics], [endpoint(ctx.env).replace(/\/ping$/, "/goals"), goals],
  ];
  for (const [url, payload] of outgoing) {
    if (!payload) continue;
    if (!(validPing(payload) || validAggregate(payload) || validGoalAggregate(payload) || validDiagnostics(payload))) continue;
    try {
      let request = url.endsWith("/ping") ? heartbeatRequest : payload.schema === DIAGNOSTIC_SCHEMA ? diagnosticRequest : url.endsWith("/aggregate") ? aggregateRequest : undefined;
      // Start under the same short lock as disable, but never hold it while
      // awaiting network I/O. Once disable returns, no new channel can start.
      if (url.endsWith("/goals")) {
        await withFileMutationLock(path, async () => {
          const current = await load(path);
          if (!blockedBy(current, ctx) && current.generation === generation) request = send(url, payload).catch(() => 0);
        }, 0);
      }
      if (!request) break;
      const code = await request;
      const accepted = code >= 200 && code < 300;
      if (url.endsWith("/ping") && accepted) sent = true;
      await withFileMutationLock(path, async () => {
        const latest = await load(path);
        if (latest.generation !== generation || blockedBy(latest, ctx)) return;
        if (url.endsWith("/ping") && accepted) latest.last_sent_day = today;
        const delivery: Delivery = { day: today,
          channel: url.endsWith("/ping") ? "heartbeat" : url.endsWith("/aggregate") ? "cli" : "goal",
          rows: "counters" in payload ? payload.counters.length : 1,
          status: accepted ? "accepted" : code ? "rejected" : "unavailable" };
        latest.deliveries = [...(latest.deliveries ?? []).filter(validDelivery), delivery].slice(-MAX_DELIVERIES);
        await save(path, latest);
      }, 0);
    } catch { /* Lossy diagnostics must never affect work. No raw errors persisted. */ }
  }
  return { sent };
}
