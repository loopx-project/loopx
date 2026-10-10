/** Local broker for one managed ZCode app-server. It owns no LoopX work scheduler. */
import {spawn, execFile} from "node:child_process";
import {randomBytes} from "node:crypto";
import {createServer, request as httpRequest} from "node:http";
import {mkdir, readFile, unlink} from "node:fs/promises";
import type {JsonObject} from "../control_plane/effect_program.ts";
import {BARE_SHA256_PATTERN} from "../control_plane/content_digest.ts";
import {dirname, isAbsolute, join} from "node:path";
import {fileURLToPath} from "node:url";
import {ZCODE_GOAL_ACTIONS, sameBinding, ZCodeGoalError, safeFailure, type NativeRequest, type ZCodeGoalReadback} from "./contract.ts";
import {NativeGoalController, readState, atomicState, type BindingState} from "./runtime.ts";
import {ZCodeAppServer} from "./app-server.ts";
import {acquireFileMutationLock, releaseFileMutationLock, durableWriteJson} from "../control_plane/effect_runtime_io.ts";

const MAX_INPUT = 64 * 1024;
const MAX_RESPONSE = 1024 * 1024;
const MONITOR_MS = 2000;
const ADMISSION_TIMEOUT_MS = 10000;
type Endpoint = {port: number; token: string; pid: number};
async function input(stream: NodeJS.ReadableStream, limit = MAX_INPUT): Promise<string> {
  let text = "";
  for await (const piece of stream) {
    text += piece.toString();
    if (Buffer.byteLength(text) > limit) throw new ZCodeGoalError("ZCode operation exceeds its input limit");
  }
  return text;
}
function parseRequest(raw: unknown): NativeRequest {
  const r = raw as NativeRequest;
  if (!r || !ZCODE_GOAL_ACTIONS.includes(r.action) || !r.goal_ref
    || r.goal_ref.goal_id !== r.goal_id || !r.agent_id
    || ![r.project, r.registry, r.state_path].every(p => typeof p === "string" && isAbsolute(p))
    || !Array.isArray(r.loopx_command) || !r.loopx_command.length
    || !Array.isArray(r.validation_command) || !r.validation_command.length
    || (r.cli_command !== undefined && (!Array.isArray(r.cli_command) || !r.cli_command.length))
    || ![...r.loopx_command, ...r.validation_command, ...(r.cli_command ?? [])].every(v => typeof v === "string" && v.length > 0)) {
    throw new ZCodeGoalError("Invalid ZCode binding operation");
  }
  return r;
}
function paths(request: NativeRequest) {
  return {endpoint: request.state_path + ".endpoint", lock: request.state_path + ".owner"};
}
async function runJSON(command: string[], args: string[], request: NativeRequest, stdin?: unknown): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const child = execFile(command[0], [...command.slice(1), ...args], {
      cwd: request.project, windowsHide: true, timeout: ADMISSION_TIMEOUT_MS,
      maxBuffer: 1024 * 1024, encoding: "utf8",
      env: {...process.env, LOOPX_USAGE_PING: "0"},
    }, (error, stdout) => {
      if (error) return reject(new ZCodeGoalError("LoopX authority or quota check failed"));
      try {
        const value = JSON.parse(stdout);
        if (!value || typeof value !== "object" || value.ok === false) throw new ZCodeGoalError();
        resolve(value);
      } catch { reject(new ZCodeGoalError("LoopX returned no usable authority or quota response")); }
    });
    if (stdin !== undefined) child.stdin?.end(JSON.stringify(stdin));
    else child.stdin?.end();
  });
}
async function validate(request: NativeRequest, cleanupOnly = false): Promise<void> {
  const reply = await runJSON(request.validation_command, [], request, cleanupOnly ? {...request, action: "pause"} : {...request, action: "bind"});
  if (reply.goal_id !== request.goal_id || reply.agent_id !== request.agent_id
    || (reply.goal_ref as NativeRequest["goal_ref"])?.goal_id !== request.goal_ref.goal_id
    || (reply.goal_ref as NativeRequest["goal_ref"])?.goal_instance_id !== request.goal_ref.goal_instance_id
    || reply.goal_creation_operation_id !== request.goal_creation_operation_id) {
    throw new ZCodeGoalError("LoopX binding authority changed");
  }
}
async function quota(request: NativeRequest) {
  const reply = await runJSON(request.loopx_command, ["--registry", request.registry, "--format", "json",
    "quota", "should-run", "--goal-id", request.goal_id, "--agent-id", request.agent_id,
    "--runtime-profile", "generic_cli", "--available-capability", "shell",
    "--available-capability", "filesystem_write"], request);
  if (typeof reply.should_run !== "boolean") throw new ZCodeGoalError("Quota response omitted its admission decision");
  return {should_run: reply.should_run, reason: typeof reply.reason === "string" ? reply.reason : undefined,
    checked_at: new Date().toISOString()};
}
function endpointCall(endpoint: Endpoint, operation: NativeRequest): Promise<ZCodeGoalReadback> {
  return new Promise((resolve, reject) => {
    const encoded = JSON.stringify(operation);
    const req = httpRequest({hostname: "127.0.0.1", port: endpoint.port, method: "POST", path: "/operation",
      headers: {authorization: "Bearer " + endpoint.token, "content-type": "application/json",
        "content-length": Buffer.byteLength(encoded)}, timeout: 40000}, async res => {
      try {
        const body = await input(res, MAX_RESPONSE);
        if (res.statusCode !== 200) throw new ZCodeGoalError("ZCode controller rejected the operation");
        resolve(JSON.parse(body) as ZCodeGoalReadback);
      } catch (error) { reject(error); }
    });
    req.on("timeout", () => req.destroy(new ZCodeGoalError("ZCode controller timed out")));
    req.on("error", reject);
    req.end(encoded);
  });
}
async function readEndpoint(request: NativeRequest): Promise<Endpoint | null> {
  try {
    const value = JSON.parse(await readFile(paths(request).endpoint, "utf8")) as Endpoint;
    if (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535
      || typeof value.token !== "string" || !BARE_SHA256_PATTERN.test(value.token) || !Number.isInteger(value.pid)) {
      throw new ZCodeGoalError("Malformed ZCode controller endpoint");
    }
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}
function alive(pid: number): boolean {
  try { process.kill(pid, 0); return true; }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    return true; // Unknown ownership remains held.
  }
}
async function claim(request: NativeRequest): Promise<() => Promise<void>> {
  const {lock, endpoint} = paths(request);
  // Reuse Core's token/inode guarded stale-owner protocol, rather than deleting a PID lock.
  const owner = await acquireFileMutationLock(lock, process.pid, 0);
  await unlink(endpoint).catch(error => {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;});
  return async () => {
    await unlink(endpoint).catch(() => {});
    await releaseFileMutationLock(owner.targetPath, owner.token);
  };
}
async function serve(request: NativeRequest): Promise<void> {
  const release = await claim(request);
  let controller: NativeGoalController | undefined;
  let ending = false;
  let monitor: ReturnType<typeof setTimeout> | undefined;
  let idle: ReturnType<typeof setTimeout> | undefined;
  let executionDeadline: ReturnType<typeof setTimeout> | undefined;
  const server = createServer();
  let shutdownPromise: Promise<void> | undefined;
  const shutdown = (): Promise<void> => {
    if (shutdownPromise) return shutdownPromise;
    ending = true;
    clearTimeout(monitor); clearTimeout(idle); clearTimeout(executionDeadline);
    server.close();
    shutdownPromise = (async () => {
      try { await controller?.close(); } finally { await release(); }
    })();
    return shutdownPromise;
  };
  try {
    const state = await readState(request.state_path);
    if (state && !sameBinding(request, state.request)) throw new ZCodeGoalError("Existing ZCode binding belongs to a different Goal instance");
    const selected = state?.request ?? request;
    if (!selected.cli_command?.length) throw new ZCodeGoalError("Bind an available ZCode CLI before starting");
    const storage = request.state_path + ".host";
    await mkdir(storage, {recursive: true, mode: 0o700});
    const guardedCommand = [process.execPath, "--no-warnings", "--experimental-strip-types",
      fileURLToPath(new URL("./guard.ts", import.meta.url)), "--", ...selected.cli_command];
    const host = new ZCodeAppServer(guardedCommand, selected.project, {
      ...process.env, ZCODE_STORAGE_DIR: storage, ZCODE_DATA_BASE_DIR: storage, ZCODE_SESSION_DB_PATH: join(storage, "sessions.db"),
    }, {guardian: true, onExit: () => {void shutdown();}});
    controller = new NativeGoalController(request, state, {host,
      validate: cleanupOnly => validate(selected, cleanupOnly), quota: () => quota(selected),
      persist: value => atomicState(request.state_path, value)});
    await controller.initialize(["pause", "stop"].includes(request.action));
    const token = randomBytes(32).toString("hex");
    const scheduleIdle = () => {
      if (controller?.readback().native?.running) {clearTimeout(idle); idle = undefined; return;}
      if (!idle) idle = setTimeout(() => {void shutdown();}, 5 * 60 * 1000);
    };
    server.on("request", async (req, res) => {
      try {
        if (req.method !== "POST" || req.url !== "/operation" || req.headers.authorization !== "Bearer " + token
          || req.headers.origin !== undefined) {
          res.writeHead(403).end(); return;
        }
        const incoming = parseRequest(JSON.parse(await input(req)));
        if (!sameBinding(incoming, selected)
          || (incoming.cli_command && JSON.stringify(incoming.cli_command) !== JSON.stringify(selected.cli_command))) {
          throw new ZCodeGoalError("ZCode controller binding changed");
        }
        clearTimeout(idle); idle = undefined;
        const reply = await controller!.operate(incoming.action, incoming.model_selection, incoming.task_body);
        if (reply.ok && (incoming.action === "start" || incoming.action === "resume")) {
          clearTimeout(executionDeadline);
          if (reply.native?.running || reply.native?.status === "active") {
            // Native Goal has no hard token budget. Bound the controller's execution lifetime instead.
            executionDeadline = setTimeout(async () => {
              await controller?.operate("pause"); scheduleIdle();
            }, 60 * 60 * 1000);
          }
        }
        if (reply.ok && (incoming.action === "pause" || incoming.action === "stop")) clearTimeout(executionDeadline);
        if (incoming.action === "stop" && reply.ok) {
          await shutdown();
          reply.available = false;
          if (reply.binding) reply.binding.connected = false;
          reply.actions = disconnected(incoming, controller!.state).actions;
        } else scheduleIdle();
        res.writeHead(200, {"content-type": "application/json"}).end(JSON.stringify(reply));
      } catch {
        res.writeHead(400, {"content-type": "application/json"}).end(JSON.stringify({ok: false, reason: "Invalid or stale ZCode operation"}));
      }
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (!address || typeof address === "string") throw new ZCodeGoalError("ZCode controller failed to bind loopback");
    await durableWriteJson(paths(request).endpoint, {port: address.port, token, pid: process.pid} as JsonObject);
    const check = async () => {
      if (ending) return;
      await controller!.check();
      scheduleIdle();
      if (!controller!.readback().available) {await shutdown(); return;}
      monitor = setTimeout(check, MONITOR_MS); // Serial checks; no overlapping or scheduled work turns.
    };
    monitor = setTimeout(check, MONITOR_MS);
    scheduleIdle();
    process.once("SIGINT", () => {void shutdown();});
    process.once("SIGTERM", () => {void shutdown();});
  } catch (error) {
    await shutdown();
    throw error;
  }
}
function disconnected(request: NativeRequest, state: BindingState | null): ZCodeGoalReadback {
  return {ok: true, available: false, reason: state ? "controller_disconnected" : "binding_missing",
    goal_id: request.goal_id, goal_ref: request.goal_ref, agent_id: request.agent_id,
    goal_creation_operation_id: request.goal_creation_operation_id ?? null,
    identity_scope: request.goal_ref.goal_instance_id ? "exact_goal_instance" : "legacy_goal_alias",
    binding: state ? {mode: "managed_cli", connected: false, cli_path: state.request.cli_path ?? "", protocol: "zcode-ndjson-session-goal"} : null,
    native: null, quota: null, actions: state ? (state.target_id || state.start_pending ? ["status", "resume", "pause", "stop"] : ["bind", "start", "status"]) : ["bind", "status"]};
}
async function dispatch(request: NativeRequest): Promise<ZCodeGoalReadback> {
  let endpoint = await readEndpoint(request);
  let state = await readState(request.state_path);
  if (state && !sameBinding(request, state.request)) throw new ZCodeGoalError("ZCode binding identity changed");
  const changingCLI = state && request.cli_command
    && JSON.stringify(state.request.cli_command) !== JSON.stringify(request.cli_command);
  if (changingCLI) {
    if (request.action !== "bind") throw new ZCodeGoalError("CLI changes require an explicit bind");
    await validate(request);
    if (endpoint && alive(endpoint.pid)) {
      const current = await endpointCall(endpoint, {...request, cli_command: undefined, action: "status"});
      if (!current.ok || current.native?.target_id || current.native?.running) {
        throw new ZCodeGoalError("Stop the current native Goal before changing the ZCode CLI");
      }
      await endpointCall(endpoint, {...request, cli_command: undefined, action: "stop"});
      // Rebinding waits for the old owner to finish native process cleanup.
      for (let attempt = 0; alive(endpoint.pid) && attempt < 100; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (alive(endpoint.pid)) throw new ZCodeGoalError("The old ZCode controller is still closing; read status and retry bind");
    }
    const owner = await acquireFileMutationLock(paths(request).lock, process.pid, 0);
    try {
      state = await readState(request.state_path);
      if (!state || !sameBinding(request, state.request) || state.target_id || state.start_pending) {
        throw new ZCodeGoalError("Stop and read back the current native Goal before changing the ZCode CLI");
      }
      await unlink(request.state_path);
      await unlink(paths(request).endpoint).catch(error => {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;});
      state = null; endpoint = null;
    } finally {await releaseFileMutationLock(owner.targetPath, owner.token);}
  }
  if (endpoint) {
    try { return await endpointCall(endpoint, request); }
    catch (error) { if (alive(endpoint.pid)) throw error; }
  }
  if (request.action === "status") return disconnected(request, state);
  if (!state && request.action !== "bind") throw new ZCodeGoalError("Explicitly bind the ZCode CLI first");
  if (!state && !request.cli_command) throw new ZCodeGoalError("ZCode CLI was not found");
  await validate(request, ["pause", "stop"].includes(request.action));
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-strip-types",
    fileURLToPath(import.meta.url), "--serve"], {cwd: request.project, detached: true,
      windowsHide: true, stdio: ["pipe", "ignore", "ignore"], env: process.env});
  child.on("error", () => {});
  child.stdin!.end(JSON.stringify(request));
  child.unref();
  for (let attempt = 0; attempt < 350; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 100));
    endpoint = await readEndpoint(request);
    if (endpoint) return endpointCall(endpoint, request);
    if (child.exitCode !== null) throw new ZCodeGoalError("ZCode controller failed to initialize; check CLI availability and protocol compatibility");
  }
  child.kill();
  throw new ZCodeGoalError("ZCode controller did not become ready; read status before retrying");
}
async function main(): Promise<void> {
  const request = parseRequest(JSON.parse(await input(process.stdin)));
  if (process.argv.includes("--serve")) {await serve(request); return;}
  try {process.stdout.write(JSON.stringify(await dispatch(request)) + "\n");}
  catch (error) {
    const state = await readState(request.state_path).catch(() => null);
    const known = state && sameBinding(request, state.request) ? state : null;
    process.stdout.write(JSON.stringify({...disconnected(request, known), ok: false, reason: safeFailure(error)}) + "\n");
    process.exitCode = 1;
  }
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(() => {process.stdout.write(JSON.stringify({ok: false, reason: "Invalid ZCode operation"}) + "\n"); process.exitCode = 1;});
}
