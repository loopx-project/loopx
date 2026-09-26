/** Machine-local telemetry owner. Never opens Goal/provider state. */
import { readFile, chmod } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { arch, platform } from "node:os";
import type { JsonObject } from "../effect_program.ts";
import { withFileMutationLock, atomicWriteJson } from "../effect_runtime_io.ts";
import { AGGREGATE_SCHEMA, PING_SCHEMA, MAX_COUNT, MAX_ROWS, counterKey, object, validAggregate, validCounter, validId, validPing } from "./usage_statistics_contract.ts";
import type { Aggregate, Counter, Ping } from "./usage_statistics_contract.ts";

export const STATE_SCHEMA = "loopx_usage_ping_state_v1";
export const DEFAULT_ENDPOINT = "https://loopx-usage-collector.huangrt01.workers.dev/v1/ping";
export const NOTICE_VERSION = 1;
export type Env = Record<string, string | undefined>;
export type Context = { env: Env; version: string; python: string; channel: string; now?: Date };
type Notice = { version: number; endpoint: string; policy: string };
type State = {
  schema: typeof STATE_SCHEMA; consent: "default" | "enabled" | "disabled"; generation: string;
  install_id?: string; notice?: Notice; last_attempt_day?: string; last_sent_day?: string;
  day?: string; counters?: Counter[];
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
    || (raw.counters !== undefined && (!Array.isArray(raw.counters) || raw.counters.length > MAX_ROWS || !raw.counters.every(validCounter)))) throw new Error("usage_state_invalid");
  return raw as State;
}
async function save(path: string, state: State) {
  await atomicWriteJson(path, state as unknown as JsonObject);
  await chmod(path, 0o600);
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
    notice: notice(ctx), notice_required: !sameNotice(state, ctx), last_sent_day: state.last_sent_day ?? null,
    next_payload: state.consent === "disabled" ? null : ping(state, ctx),
    aggregate_preview: state.consent === "disabled" || !state.counters?.length ? null : { schema: AGGREGATE_SCHEMA, counters: state.counters },
    aggregate_day: state.day ?? null,
    disclosure: "LoopX basic usage statistics are on by default after this notice. Daily heartbeats send a random installation ID, version, OS, CPU architecture, Python version and install channel to the configured LoopX collector (Cloudflare). Fixed CLI feature/result/duration/error counts are sent separately without an ID. No prompts, code, paths, arguments, Goal data or raw errors. Disable both with loopx usage-ping disable or LOOPX_USAGE_PING=0; inspect with loopx usage-ping status. Consent-required distributions wait for explicit enable. Recipient: " + (endpoint(ctx.env) || "not configured") };
}
export async function configure(path: string, ctx: Context, action: "enable" | "disable" | "acknowledge", expectedNotice?: unknown) {
  await withFileMutationLock(path, async () => {
    // Explicit disable can repair malformed state without permitting a send.
    const state = action === "disable" ? { schema: STATE_SCHEMA, consent: "disabled", generation: randomUUID() } as State : await load(path);
    if (action === "disable") return save(path, state);
    if (action === "acknowledge" && JSON.stringify(expectedNotice) !== JSON.stringify(notice(ctx))) throw new Error("usage_notice_changed");
    if (action === "acknowledge" && state.consent === "disabled") return;
    if (action === "enable") state.consent = "enabled";
    if (state.notice && !sameNotice(state, ctx)) {
      state.counters = [];
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
export type Post = (url: string, payload: Ping | Aggregate) => Promise<number>;
const post: Post = async (url, payload) => (await fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "loopx-usage-ping" },
  body: JSON.stringify(payload), signal: AbortSignal.timeout(3000), redirect: "error",
})).status;

/** Called in a detached process with one allowlisted observation, never raw argv/output. */
export async function observe(path: string, ctx: Context, generation: string, counter: Counter | null, send: Post = post) {
  if (counter !== null && (!validCounter(counter) || counter.count !== 1)) return { sent: false, reason: "invalid_observation" };
  let heartbeat: Ping | null = null;
  let aggregate: Aggregate | null = null;
  const today = day(ctx);
  const allowed = await withFileMutationLock(path, async () => {
    const state = await load(path);
    const blocked = blockedBy(state, ctx);
    if (blocked || !generation || generation !== state.generation) return false;
    // Flush only a completed day's local aggregate. No event times or per-install key leave this boundary.
    if (state.day && state.day < today && state.counters?.length) {
      const age = Date.parse(today) - Date.parse(state.day);
      if (age <= 7 * 86400000) aggregate = { schema: AGGREGATE_SCHEMA, counters: state.counters };
      state.counters = [];
    }
    if (state.day && state.day > today) return false; // backward clock: do not resend or mislabel counts
    state.day = today;
    state.counters ??= [];
    if (counter) {
      const row = state.counters.find((entry) => counterKey(entry) === counterKey(counter));
      if (row) row.count = Math.min(MAX_COUNT, row.count + 1);
      else if (state.counters.length < MAX_ROWS) state.counters.push({ ...counter });
    }
    if (!state.last_attempt_day || state.last_attempt_day < today) {
      heartbeat = ping(state, ctx);
      state.last_attempt_day = today; // claim before I/O; failures are not retried
    } else aggregate = null;
    await save(path, state);
    return true;
  }, 0); // Never queue behind business or telemetry work.
  if (!allowed) return { sent: false, reason: "blocked" };
  let sent = false;
  for (const [url, payload] of [[endpoint(ctx.env), heartbeat], [endpoint(ctx.env).replace(/\/ping$/, "/aggregate"), aggregate]] as const) {
    if (!payload) continue;
    if (!(validPing(payload) || validAggregate(payload))) continue;
    try {
      let request: Promise<number> | undefined;
      // Start under the same short lock as disable, but never hold it while
      // awaiting network I/O. Once disable returns, no new channel can start.
      await withFileMutationLock(path, async () => {
        const current = await load(path);
        if (!blockedBy(current, ctx) && current.generation === generation) request = send(url, payload).catch(() => 0);
      }, 0);
      if (!request) break;
      const code = await request;
      if (url.endsWith("/ping") && code >= 200 && code < 300) {
        sent = true;
        await withFileMutationLock(path, async () => {
          const latest = await load(path);
          if (latest.generation === generation && !blockedBy(latest, ctx)) {
            latest.last_sent_day = today;
            await save(path, latest);
          }
        }, 0);
      }
    } catch { /* Lossy diagnostics must never affect work. No raw errors persisted. */ }
  }
  return { sent };
}
