/** One native session owner. Quota admission and revocation reuse Core CLI decisions. */
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {durableWriteJson} from "../control_plane/effect_runtime_io.ts";
import {BARE_SHA256_PATTERN} from "../control_plane/content_digest.ts";
import type {JsonObject} from "../control_plane/effect_program.ts";
import type {NativeObservation, NativeRequest, QuotaObservation, ZCodeModelSelection, ZCodeGoalAction, ZCodeGoalReadback} from "./contract.ts";
import {sameBinding, ZCodeGoalError, safeFailure} from "./contract.ts";

export interface NativeHost {
  initialize(): Promise<unknown>;
  create(): Promise<NativeObservation>;
  resumeSession(id: string): Promise<NativeObservation>;
  readGoal(id: string): Promise<NativeObservation>;
  setGoal(id: string, objective: string): Promise<NativeObservation>;
  pauseGoal(id: string): Promise<NativeObservation>;
  resumeGoal(id: string): Promise<NativeObservation>;
  clearGoal(id: string): Promise<NativeObservation>;
  selectModel(id: string, selection: ZCodeModelSelection): Promise<NativeObservation>;
  close(): Promise<void>;
}
export type BindingState = {
  schema: "loopx_zcode_native_binding_v0";
  request: NativeRequest;
  session_id: string;
  target_id: string | null;
  objective_sha256: string;
  start_pending?: boolean;
  last_execution_error?: "zcode_native_execution_failed";
};
export type ControllerDependencies = {
  host: NativeHost;
  validate: (cleanupOnly?: boolean) => Promise<void>;
  quota: () => Promise<QuotaObservation>;
  persist: (state: BindingState) => Promise<void>;
};

export async function atomicState(path: string, state: BindingState): Promise<void> {
  await durableWriteJson(path, state as unknown as JsonObject);
}
export async function readState(path: string): Promise<BindingState | null> {
  try {
    const text = await readFile(path, "utf8");
    if (Buffer.byteLength(text) > 1024 * 1024) throw new ZCodeGoalError("ZCode binding exceeds its read limit");
    const state = JSON.parse(text) as BindingState;
    if (state.schema !== "loopx_zcode_native_binding_v0" || typeof state.session_id !== "string" || !state.session_id
      || !state.request || typeof state.request.goal_ref?.goal_id !== "string"
      || typeof state.request.agent_id !== "string" || !Array.isArray(state.request.cli_command)
      || (state.target_id !== null && (typeof state.target_id !== "string" || !state.target_id))
      || (state.last_execution_error !== undefined && state.last_execution_error !== "zcode_native_execution_failed")
      || (state.start_pending !== undefined && typeof state.start_pending !== "boolean")
      || typeof state.objective_sha256 !== "string" || !BARE_SHA256_PATTERN.test(state.objective_sha256)) {
      throw new ZCodeGoalError("Unsupported ZCode binding; inspect or remove it before binding");
    }
    return state;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}
function objectiveDigest(text: string): string {
  return createHash("sha256").update(text.trim()).digest("hex");
}

/** Host status never completes a LoopX Goal or spends credits on the Agent's behalf. */
export class NativeGoalController {
  readonly state: BindingState;
  private dependencies: ControllerDependencies;
  private observed: NativeObservation | null = null;
  private quotaObserved: QuotaObservation | null = null;
  private reason: string | undefined;
  private connected = false;
  private executionAllowed = false;
  private serial: Promise<unknown> = Promise.resolve();

  constructor(request: NativeRequest, state: BindingState | null, deps: ControllerDependencies) {
    if (state && !sameBinding(request, state.request)) throw new ZCodeGoalError("ZCode binding identity changed");
    if (state && request.cli_command && JSON.stringify(request.cli_command) !== JSON.stringify(state.request.cli_command)) {
      throw new ZCodeGoalError("Stop and explicitly rebind before changing the ZCode CLI");
    }
    this.state = state ?? {schema: "loopx_zcode_native_binding_v0", request,
      session_id: "", target_id: null, objective_sha256: objectiveDigest(request.task_body ?? "")};
    this.dependencies = deps;
  }

  async initialize(cleanupOnly = false): Promise<void> {
    if (cleanupOnly && !this.state.session_id) throw new ZCodeGoalError("There is no bound native session to clean up");
    await this.dependencies.validate(cleanupOnly);
    await this.dependencies.host.initialize();
    const observation = this.state.session_id
      ? await this.dependencies.host.resumeSession(this.state.session_id)
      : await this.dependencies.host.create();
    if (this.state.session_id && observation.session_id !== this.state.session_id) {
      throw new ZCodeGoalError("ZCode restored a different session");
    }
    if (!this.state.session_id && observation.target_id !== null) {
      throw new ZCodeGoalError("A new ZCode session unexpectedly owns another goal");
    }
    this.state.session_id = observation.session_id;
    if (observation.target_id && !this.state.target_id) {
      if (!this.state.start_pending || !observation.objective_sha256
        || observation.objective_sha256 !== this.state.objective_sha256) {
        throw new ZCodeGoalError("ZCode has an unjournaled native Goal; inspect the session before rebinding");
      }
      // Recover only an admitted, journaled start whose canonical objective matches exactly.
      this.state.target_id = observation.target_id;
      this.state.start_pending = false;
    }
    this.observe(observation);
    // Cold recovery never resumes work. Pause a persisted active target before publishing ready.
    if (observation.status === "active" || observation.running) {
      this.observe(await this.dependencies.host.pauseGoal(this.state.session_id));
      if (this.observed?.status !== "paused" || this.observed.running) throw new ZCodeGoalError("ZCode recovery could not confirm pause");
    }
    await this.dependencies.validate(cleanupOnly);
    await this.dependencies.persist(this.state);
    this.connected = true;
    this.executionAllowed = !cleanupOnly;
  }

  private observe(next: NativeObservation): void {
    if (next.session_id !== this.state.session_id) throw new ZCodeGoalError("ZCode returned a different session");
    if (this.state.target_id && next.target_id !== this.state.target_id) {
      throw new ZCodeGoalError("ZCode native Goal identity changed; refusing to mutate it");
    }
    this.observed = next;
  }

  private exclusive<T>(fn: () => Promise<T>): Promise<T> {
    const current = this.serial.then(fn, fn);
    this.serial = current.catch(() => {});
    return current;
  }

  private async admission(): Promise<QuotaObservation> {
    await this.dependencies.validate();
    const quota = await this.dependencies.quota();
    await this.dependencies.validate();
    if (typeof quota.should_run !== "boolean") throw new ZCodeGoalError("Quota returned no usable admission decision");
    this.executionAllowed = true;
    this.quotaObserved = quota;
    return quota;
  }

  private async pauseOwned(): Promise<void> {
    if (!this.observed || (!this.observed.running && this.observed.status !== "active")) return;
    this.observe(await this.dependencies.host.pauseGoal(this.state.session_id));
    // Pause receipt can precede cancellation draining: require fresh non-running readback.
    for (let attempt = 0; this.observed.running && attempt < 20; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 50));
      this.observe(await this.dependencies.host.readGoal(this.state.session_id));
    }
    if (this.observed.status !== "paused" || this.observed.running) {
      throw new ZCodeGoalError("ZCode did not confirm that native execution paused");
    }
  }

  private async nativeFailure(): Promise<boolean> {
    if (this.observed?.session_status !== "error") return false;
    this.reason = "zcode_native_execution_failed";
    const firstFailure = !this.state.last_execution_error;
    this.state.last_execution_error = "zcode_native_execution_failed";
    await this.pauseOwned();
    if (firstFailure) await this.dependencies.persist(this.state);
    return true;
  }

  async operate(action: ZCodeGoalAction, model?: ZCodeModelSelection, taskBody?: string): Promise<ZCodeGoalReadback> {
    return this.exclusive(async () => {
      try {
        // Read-only status may show revocation; cleanup of this exact native session remains allowed.
        await this.dependencies.validate(["status", "pause", "stop"].includes(action));
        this.observe(await this.dependencies.host.readGoal(this.state.session_id));
        if (action === "status") {
          try {
            const decision = await this.admission();
            this.reason = decision.should_run ? undefined : decision.reason ?? "quota_denied";
            if (!decision.should_run) await this.pauseOwned();
          } catch {
            this.executionAllowed = false;
            this.reason = "authority_or_quota_unavailable";
            this.quotaObserved = {should_run: false, reason: this.reason, checked_at: new Date().toISOString()};
            await this.pauseOwned();
          }
        } else if (!["pause", "stop"].includes(action)) this.executionAllowed = true;
        if (action === "bind" && this.observed?.target_id) {
          this.reason = "Stop the current native Goal before rebinding";
          return this.readback(false);
        }
        if (action === "select_model") {
          if (!model || this.observed?.running) {
            this.reason = "Select an available model while the session is idle or paused"; return this.readback(false);
          }
          this.observe(await this.dependencies.host.selectModel(this.state.session_id, model));
          this.state.request.model_selection = model;
          await this.dependencies.persist(this.state);
          this.reason = undefined;
        } else if (action === "start" || action === "resume") {
          if (this.observed?.running) {
            this.reason = "ZCode is already executing this native Goal"; return this.readback(false);
          }
          if (action === "start" && this.observed?.target_id) {
            this.reason = "A native Goal already exists; resume or stop it"; return this.readback(false);
          }
          if (action === "resume" && !this.observed?.target_id) {
            this.reason = "There is no native Goal to resume"; return this.readback(false);
          }
          if (!this.observed?.selected_model) {
            this.reason = "Select an available ZCode model before starting"; return this.readback(false);
          }
          const quota = await this.admission();
          if (!quota.should_run) {
            this.reason = quota.reason ?? "quota_denied";
            await this.pauseOwned();
            return this.readback();
          }
          delete this.state.last_execution_error;
          if (action === "start") {
            const objective = (taskBody ?? this.state.request.task_body)?.trim();
            if (!objective?.trim()) throw new ZCodeGoalError("A canonical LoopX task body is required");
            this.state.request.task_body = objective;
            this.state.objective_sha256 = objectiveDigest(objective);
            this.state.start_pending = true;
            await this.dependencies.persist(this.state);
            const next = await this.dependencies.host.setGoal(this.state.session_id, objective);
            if (!next.target_id) throw new ZCodeGoalError("ZCode accepted no native Goal identity");
            this.state.target_id = next.target_id;
            this.state.start_pending = false;
            this.observe(next);
            await this.dependencies.persist(this.state);
          } else {
            this.observe(await this.dependencies.host.resumeGoal(this.state.session_id));
          }
          this.reason = undefined;
          // A fresh observation proves whether execution started; a stored active target does not.
          this.observe(await this.dependencies.host.readGoal(this.state.session_id));
          if (await this.nativeFailure()) return this.readback(false);
        } else if (action === "pause") {
          await this.pauseOwned();
          this.reason = "user_paused";
        } else if (action === "stop") {
          await this.pauseOwned();
          if (this.observed?.target_id) {
            const cleared = await this.dependencies.host.clearGoal(this.state.session_id);
            if (cleared.session_id !== this.state.session_id || cleared.target_id || cleared.running) {
              throw new ZCodeGoalError("ZCode did not confirm that the native Goal was cleared");
            }
            this.state.target_id = null;
            this.observed = cleared;
          }
          this.state.start_pending = false;
          delete this.state.last_execution_error;
          await this.dependencies.persist(this.state);
          this.reason = "user_stopped";
        }
        if (action === "status" && await this.nativeFailure()) return this.readback(false);
        return this.readback();
      } catch (error) {
        this.executionAllowed = false;
        this.reason = safeFailure(error);
        // Failed authority/admission never leaves this provider intentionally free-running.
        try { await this.pauseOwned(); }
        catch { this.connected = false; await this.dependencies.host.close(); }
        return this.readback(false);
      }
    });
  }

  async check(): Promise<void> {
    return this.exclusive(async () => {
      if (!this.connected) return;
      try {
        this.observe(await this.dependencies.host.readGoal(this.state.session_id));
        if (await this.nativeFailure()) return;
        if (this.observed?.running || this.observed?.status === "active") {
          const quota = await this.admission();
          if (!quota.should_run) {
            this.reason = quota.reason ?? "quota_denied";
            await this.pauseOwned();
          }
        } else {
          await this.dependencies.validate();
          this.executionAllowed = true;
        }
      } catch {
        this.executionAllowed = false;
        this.reason = "authority_or_quota_unavailable";
        this.quotaObserved = {should_run: false, reason: this.reason, checked_at: new Date().toISOString()};
        try { await this.pauseOwned(); }
        catch { this.connected = false; await this.dependencies.host.close(); }
      }
    });
  }

  readback(ok = true): ZCodeGoalReadback {
    const actions: ZCodeGoalAction[] = ["status"];
    if (this.connected && this.observed) {
      if (this.executionAllowed && !this.observed.running && this.observed.available_models?.some(model => !model.disabled)) actions.push("select_model");
      if (!this.observed.target_id) {
        if (this.executionAllowed) actions.push("bind");
        if (this.executionAllowed && this.quotaObserved?.should_run !== false && this.observed.selected_model) actions.push("start");
      }
      else {
        actions.push("stop");
        if (this.observed.running || this.observed.status === "active") actions.push("pause");
        if (this.executionAllowed && this.quotaObserved?.should_run !== false && this.observed.selected_model && !this.observed.running && (this.observed.status === "paused" || this.observed.status === "active")) actions.push("resume");
      }
    }
    const request = this.state.request;
    return {ok, available: this.connected, reason: this.reason ?? this.state.last_execution_error,
      goal_id: request.goal_id, goal_ref: request.goal_ref, agent_id: request.agent_id,
      goal_creation_operation_id: request.goal_creation_operation_id ?? null,
      identity_scope: request.goal_ref.goal_instance_id ? "exact_goal_instance" : "legacy_goal_alias",
      binding: {mode: "managed_cli", connected: this.connected,
        cli_path: request.cli_path ?? request.cli_command?.[0] ?? "", protocol: "zcode-ndjson-session-goal"},
      native: this.connected ? this.observed : null, quota: this.quotaObserved, actions};
  }
  async close(): Promise<void> {
    await this.exclusive(async () => {
      try { await this.pauseOwned(); } finally {
        this.connected = false;
        await this.dependencies.host.close();
      }
    });
  }
}
