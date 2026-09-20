#!/usr/bin/env -S node --no-warnings --experimental-strip-types

import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import type {
  AuthorityStoreCommit,
  AuthorityStoreCommitResult,
} from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  canonicalAuthorityObject,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  NoKVAuthorityStore,
  NoKVTransportProtocolError,
  NoKVTransportUnavailableError,
  type NoKVBlobCasRequest,
  type NoKVBlobCasResult,
  type NoKVBlobReadResult,
  type NoKVBlobTransport,
  type NoKVStoreIdentityResult,
} from "../../loopx/control_plane/coordination/nokv_authority_store.ts";
import {
  NoKVJsonLinesTransport,
} from "../../loopx/control_plane/coordination/nokv_jsonl_transport.ts";

const REPORT_SCHEMA = "loopx_nokv_authority_live_qualification_v0";
export const QUALIFICATION_SCOPE = "stage_2a_single_node_store_conformance";
export const QUALIFIED_NOKV_SDK_VERSION = "0.11.1";
export const QUALIFIED_NOKV_API_VERSION = 1;
const REPOSITORY_HELPER = fileURLToPath(
  new URL("../../loopx/control_plane/coordination/nokv_jsonl_helper.py", import.meta.url),
);
const COMPETITION_BARRIER_TIMEOUT_MS = 10_000;

export class QualificationFailure extends Error {
  readonly reasonCode: string;

  constructor(reasonCode: string, message: string) {
    super(message);
    this.reasonCode = reasonCode;
  }
}

export interface QualificationTransport extends NoKVBlobTransport {
  close(): Promise<void>;
}

export interface QualificationOptions {
  python_executable: string;
  client_config: JsonObject;
  tenant_id: string;
  goal_id: string;
  workbench: string;
  request_timeout_ms?: number;
}

export interface QualificationReport {
  schema_version: typeof REPORT_SCHEMA;
  qualification_scope: typeof QUALIFICATION_SCOPE;
  ok: true;
  checks: readonly { id: string; status: "passed" }[];
  final_generation: number;
  final_cursor: string;
  durable_test_data_left: true;
  authority_source_changed: false;
  availability_or_ha_proven: false;
  nokv_sdk_version: typeof QUALIFIED_NOKV_SDK_VERSION;
  nokv_api_version: typeof QUALIFIED_NOKV_API_VERSION;
}

export interface QualificationSequenceResult {
  checks: readonly { id: string; status: "passed" }[];
  final_generation: number;
  final_cursor: string;
}

export interface QualificationCliArguments {
  configJsonPath: string;
  pythonExecutable: string;
  tenantId: string;
  goalId: string;
  workbench: string;
  requestTimeoutMs?: number;
}

type QualificationTransportFactory = () => Promise<QualificationTransport>;

function fail(reasonCode: string, message: string): never {
  throw new QualificationFailure(reasonCode, message);
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0) {
    return fail("invalid_arguments", `${name} must be a non-empty trimmed string`);
  }
  return value;
}

/** Interpreter flag: ignore PYTHONPATH, PYTHONHOME, and user site-packages. */
export const HELPER_INTERPRETER_ISOLATION_FLAG = "-I";

/**
 * Build the only helper argv accepted by the destructive live probe.
 *
 * The interpreter runs in isolated mode, so the `nokv` module can come only
 * from the chosen executable's own environment. An environment variable cannot
 * substitute a stand-in SDK for live evidence.
 */
export function qualificationHelperArgv(
  pythonExecutable: string,
): readonly [string, string, string] {
  const executable = requiredString(pythonExecutable, "Python executable");
  if (!isAbsolute(executable)) {
    return fail("invalid_arguments", "Python executable must be an absolute path");
  }
  return [executable, HELPER_INTERPRETER_ISOLATION_FLAG, REPOSITORY_HELPER];
}

function positiveSafeInteger(value: unknown, name: string): number {
  const parsed = typeof value === "string" && /^[1-9]\d*$/.test(value)
    ? Number(value)
    : value;
  if (!Number.isSafeInteger(parsed) || (parsed as number) < 1) {
    return fail("invalid_arguments", `${name} must be a positive safe integer`);
  }
  return parsed as number;
}

function expect(
  condition: unknown,
  reasonCode: string,
  message: string,
): asserts condition {
  if (!condition) fail(reasonCode, message);
}

function commit(
  expectedProviderRevision: string | null,
  runId: string,
  operationId: string,
  sequence: number,
  candidate: string,
): AuthorityStoreCommit {
  return {
    expected_provider_revision: expectedProviderRevision,
    operation_id: operationId,
    events: [{
      schema_version: "loopx_nokv_authority_qualification_event_v0",
      run_id: runId,
      sequence,
      candidate,
    }],
    next_projection: {
      schema_version: "loopx_nokv_authority_qualification_head_v0",
      run_id: runId,
      sequence,
      candidate,
    },
    receipts: [{
      schema_version: "loopx_nokv_authority_qualification_receipt_v0",
      run_id: runId,
      operation_id: operationId,
      sequence,
      candidate,
    }],
  };
}

class OneShotBarrier {
  private arrivals = 0;
  private readonly released: Promise<void>;
  private release!: () => void;
  private readonly timer: NodeJS.Timeout;

  constructor() {
    this.released = new Promise<void>((resolveBarrier) => {
      this.release = resolveBarrier;
    });
    this.timer = setTimeout(() => {
      this.release();
    }, COMPETITION_BARRIER_TIMEOUT_MS);
    this.timer.unref();
  }

  async arrive(): Promise<void> {
    this.arrivals += 1;
    if (this.arrivals === 2) {
      clearTimeout(this.timer);
      this.release();
    }
    await this.released;
    if (this.arrivals !== 2) {
      fail("competition_barrier_failed", "both competitors did not reach the NoKV CAS");
    }
  }
}

class BarrierTransport implements NoKVBlobTransport {
  readonly inner: NoKVBlobTransport;
  readonly barrier: OneShotBarrier;

  constructor(inner: NoKVBlobTransport, barrier: OneShotBarrier) {
    this.inner = inner;
    this.barrier = barrier;
  }

  async storeIdentity(workbench: string): Promise<NoKVStoreIdentityResult> {
    return await this.inner.storeIdentity(workbench);
  }

  async readBlob(workbench: string, path: string): Promise<NoKVBlobReadResult> {
    return await this.inner.readBlob(workbench, path);
  }

  async casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult> {
    await this.barrier.arrive();
    return await this.inner.casPublishBlob(request);
  }
}

/**
 * Turn one proven lower-layer success into an unknown transport outcome.
 *
 * The wrapper is deliberately above the real NoKV transport: the durable CAS
 * still runs, while the TypeScript AuthorityStore must settle the result from
 * the persisted operation receipt instead of trusting the lost response.
 */
class LoseOneAppliedResponseTransport implements NoKVBlobTransport {
  readonly inner: NoKVBlobTransport;
  appliedResponseDropped = false;

  constructor(inner: NoKVBlobTransport) {
    this.inner = inner;
  }

  async storeIdentity(workbench: string): Promise<NoKVStoreIdentityResult> {
    return await this.inner.storeIdentity(workbench);
  }

  async readBlob(workbench: string, path: string): Promise<NoKVBlobReadResult> {
    return await this.inner.readBlob(workbench, path);
  }

  async casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult> {
    const result = await this.inner.casPublishBlob(request);
    if (!this.appliedResponseDropped && result.status === "applied") {
      this.appliedResponseDropped = true;
      throw new NoKVTransportUnavailableError(
        "qualification injected response loss after an applied NoKV CAS",
      );
    }
    return result;
  }
}

function applied(
  result: AuthorityStoreCommitResult,
  reasonCode: string,
): Extract<AuthorityStoreCommitResult, { status: "applied" }> {
  expect(result.status === "applied", reasonCode, "authority commit was not applied");
  return result;
}

/** A 32-hex incarnation that differs from `current` in its first digit. */
function staleIncarnation(current: string): string {
  return `${current.startsWith("0") ? "1" : "0"}${current.slice(1)}`;
}

/** A fresh lower-layer publication identity; the probe never reuses one. */
function freshPhysicalIdentity(): string {
  return randomUUID().replaceAll("-", "");
}

async function rawGeneration(
  transport: NoKVBlobTransport,
  store: NoKVAuthorityStore,
  expectedGeneration: number,
  reasonCode: string,
): Promise<void> {
  const result = await transport.readBlob(store.workbench, store.path);
  expect(
    result.status === "loaded" && result.generation === expectedGeneration,
    reasonCode,
    `authority envelope did not read back at generation ${expectedGeneration}`,
  );
}

/**
 * Exercise the destructive sequence against three independent handles.
 *
 * This lower-level export intentionally does not return a live qualification
 * report: tests may supply a deterministic transport factory, which proves the
 * sequence but cannot prove a reachable NoKV backend.
 */
export async function exerciseQualificationSequence(
  options: QualificationOptions,
  openTransport: QualificationTransportFactory,
): Promise<QualificationSequenceResult> {
  const tenantId = requiredString(options.tenant_id, "tenant id");
  const goalId = requiredString(options.goal_id, "goal id");
  const workbench = requiredString(options.workbench, "workbench");
  const opened: QualificationTransport[] = [];
  const open = async (): Promise<QualificationTransport> => {
    const transport = await openTransport();
    opened.push(transport);
    return transport;
  };
  const checks: { id: string; status: "passed" }[] = [];
  const passed = (id: string): void => {
    checks.push({ id, status: "passed" });
  };
  const runId = randomUUID();
  const operationIds = {
    create: `${runId}:create`,
    advance: `${runId}:advance`,
    contenderA: `${runId}:contender-a`,
    contenderB: `${runId}:contender-b`,
  };

  try {
    const firstTransport = await open();
    const secondTransport = await open();
    const first = new NoKVAuthorityStore(firstTransport, {
      tenant_id: tenantId,
      goal_id: goalId,
      workbench,
    });
    const responseLossTransport = new LoseOneAppliedResponseTransport(secondTransport);
    const second = new NoKVAuthorityStore(responseLossTransport, {
      tenant_id: tenantId,
      goal_id: goalId,
      workbench,
    });

    const [firstIdentity, secondIdentity] = await Promise.all([
      first.storeIdentity(),
      second.storeIdentity(),
    ]);
    expect(
      firstIdentity.status === "available" &&
        secondIdentity.status === "available" &&
        firstIdentity.store_identity === secondIdentity.store_identity,
      "workbench_identity_failed",
      "independent transports did not resolve the same existing workbench",
    );
    passed("existing_workbench_identity");

    const [firstInitial, secondInitial] = await Promise.all([
      first.loadAuthority(),
      second.loadAuthority(),
    ]);
    expect(
      firstInitial.status === "missing" && secondInitial.status === "missing",
      "qualification_target_not_fresh",
      "qualification requires a fresh tenant and goal target",
    );
    passed("fresh_authority_target");

    const created = applied(
      await first.commitAuthority(
        commit(null, runId, operationIds.create, 1, "create"),
      ),
      "create_failed",
    );
    passed("create_applied");
    await rawGeneration(firstTransport, first, 1, "create_generation_failed");
    passed("create_generation_one");

    // A write prepared against one workbench incarnation must not land after
    // the workbench is restored to another. Send the generation-1 envelope back
    // through the raw transport with a fence naming a different incarnation:
    // NoKV must refuse it typed, before any row or object exists, and the
    // stored generation and the workbench identity must both be unchanged.
    const boundIdentity = firstIdentity.status === "available"
      ? firstIdentity.store_identity
      : fail("workbench_identity_failed", "workbench identity was not available");
    const currentEnvelope = await firstTransport.readBlob(first.workbench, first.path);
    expect(
      currentEnvelope.status === "loaded" && currentEnvelope.generation === 1,
      "stale_incarnation_fence_probe_failed",
      "the generation-1 envelope could not be read for the fence probe",
    );
    const staleFence = await firstTransport.casPublishBlob({
      workbench: first.workbench,
      path: first.path,
      expected_generation: 1,
      expected_workspace_incarnation_id: staleIncarnation(
        boundIdentity.slice(`nokv:${workbench}:`.length),
      ),
      bytes: currentEnvelope.bytes,
      operation_id: freshPhysicalIdentity(),
      artifact_revision_id: freshPhysicalIdentity(),
    });
    expect(
      staleFence.status === "failed" && staleFence.reason_code === "store_identity_mismatch",
      "stale_incarnation_fence_not_enforced",
      "NoKV did not refuse a publication fenced on a stale workbench incarnation",
    );
    passed("stale_incarnation_fence_rejected");
    const identityAfterFence = await first.storeIdentity();
    expect(
      identityAfterFence.status === "available" &&
        identityAfterFence.store_identity === boundIdentity,
      "stale_incarnation_fence_probe_failed",
      "the workbench incarnation changed during the fence probe",
    );
    await rawGeneration(firstTransport, first, 1, "stale_incarnation_fence_wrote");
    passed("stale_incarnation_fence_left_generation_unchanged");

    const advanced = applied(
      await second.commitAuthority(
        commit(created.provider_revision, runId, operationIds.advance, 2, "advance"),
      ),
      "generation_cas_failed",
    );
    expect(
      responseLossTransport.appliedResponseDropped,
      "response_loss_not_injected",
      "qualification did not discard an applied NoKV CAS response",
    );
    passed("response_lost_success_reconciled");
    passed("generation_cas_applied");
    await rawGeneration(firstTransport, first, 2, "generation_two_readback_failed");
    passed("generation_two_readback");

    const barrier = new OneShotBarrier();
    const contenderA = new NoKVAuthorityStore(
      new BarrierTransport(firstTransport, barrier),
      { tenant_id: tenantId, goal_id: goalId, workbench },
    );
    const contenderB = new NoKVAuthorityStore(
      new BarrierTransport(secondTransport, barrier),
      { tenant_id: tenantId, goal_id: goalId, workbench },
    );
    const race = await Promise.all([
      contenderA.commitAuthority(
        commit(advanced.provider_revision, runId, operationIds.contenderA, 3, "a"),
      ),
      contenderB.commitAuthority(
        commit(advanced.provider_revision, runId, operationIds.contenderB, 3, "b"),
      ),
    ]);
    const winnerIndex = race.findIndex((result) => result.status === "applied");
    const loserIndex = race.findIndex((result) => result.status === "conflict");
    expect(
      winnerIndex >= 0 && loserIndex >= 0 && winnerIndex !== loserIndex &&
        race.filter((result) => result.status === "applied").length === 1 &&
        race.filter((result) => result.status === "conflict").length === 1,
      "competition_not_fenced",
      "competing generation CAS did not produce exactly one winner",
    );
    const winner = race[winnerIndex]!;
    const loser = race[loserIndex]!;
    expect(
      winner.status === "applied" &&
        loser.status === "conflict" &&
        loser.conflict_kind === "provider_revision_mismatch" &&
        loser.current_provider_revision === winner.provider_revision &&
        loser.current_cursor === "3",
      "competition_not_fenced",
      "competition conflict was not bound to the winning generation",
    );
    passed("competing_generation_cas_one_winner");
    await rawGeneration(secondTransport, second, 3, "competition_generation_failed");
    passed("competition_did_not_double_advance");

    await Promise.all([firstTransport.close(), secondTransport.close()]);

    const readbackTransport = await open();
    const readback = new NoKVAuthorityStore(readbackTransport, {
      tenant_id: tenantId,
      goal_id: goalId,
      workbench,
    });
    const loaded = await readback.loadAuthority();
    expect(
      loaded.status === "loaded" &&
        loaded.provider_revision === winner.provider_revision &&
        loaded.cursor === "3" &&
        loaded.head.run_id === runId &&
        loaded.head.sequence === 3,
      "independent_readback_failed",
      "a fresh transport did not read the winning authority envelope",
    );
    await rawGeneration(
      readbackTransport,
      readback,
      3,
      "independent_readback_failed",
    );
    const history = await readback.scanCommitted(null, 10);
    expect(
      history.status === "page" &&
        history.transactions.length === 3 &&
        history.next_cursor === "3" &&
        history.has_more === false,
      "independent_readback_failed",
      "a fresh transport did not read the complete committed history",
    );
    passed("independent_transport_readback");

    const winnerOperation = winnerIndex === 0
      ? operationIds.contenderA
      : operationIds.contenderB;
    const loserOperation = winnerIndex === 0
      ? operationIds.contenderB
      : operationIds.contenderA;
    const [ambiguousCommitReceipt, winnerReceipt, loserReceipt] = await Promise.all([
      readback.readReceipt(operationIds.advance),
      readback.readReceipt(winnerOperation),
      readback.readReceipt(loserOperation),
    ]);
    expect(
      ambiguousCommitReceipt.status === "found" &&
        ambiguousCommitReceipt.cursor === "2" &&
        ambiguousCommitReceipt.receipts[0]?.operation_id === operationIds.advance,
      "ambiguous_commit_receipt_missing",
      "the response-lost operation receipt was not retained",
    );
    passed("ambiguous_commit_receipt_retained");
    expect(
      winnerReceipt.status === "found" && winnerReceipt.cursor === "3",
      "winner_receipt_missing",
      "the winning operation receipt was not retained",
    );
    passed("winner_receipt_retained");
    expect(
      loserReceipt.status === "missing",
      "loser_receipt_present",
      "the losing operation unexpectedly acquired a durable receipt",
    );
    passed("loser_receipt_absent");

    return {
      checks,
      final_generation: 3,
      final_cursor: "3",
    };
  } finally {
    await Promise.allSettled(opened.map(async (transport) => await transport.close()));
  }
}

/** Run the live probe only through this checkout's reviewed JSONL transport. */
export async function qualifyNoKVAuthorityStore(
  options: QualificationOptions,
): Promise<QualificationReport> {
  const sequence = await exerciseQualificationSequence(options, async () =>
    await NoKVJsonLinesTransport.open({
      argv: qualificationHelperArgv(options.python_executable),
      config: options.client_config,
      request_timeout_ms: options.request_timeout_ms,
    }));
  return {
    schema_version: REPORT_SCHEMA,
    qualification_scope: QUALIFICATION_SCOPE,
    ok: true,
    ...sequence,
    durable_test_data_left: true,
    authority_source_changed: false,
    availability_or_ha_proven: false,
    nokv_sdk_version: QUALIFIED_NOKV_SDK_VERSION,
    nokv_api_version: QUALIFIED_NOKV_API_VERSION,
  };
}

export function parseQualificationArguments(
  argv: readonly string[],
): QualificationCliArguments {
  let values: ReturnType<typeof parseArgs>["values"];
  try {
    values = parseArgs({
      args: [...argv],
      strict: true,
      allowPositionals: false,
      options: {
        "execute-live": { type: "boolean", default: false },
        "config-json": { type: "string" },
        "python-executable": { type: "string" },
        "tenant-id": { type: "string" },
        "goal-id": { type: "string" },
        workbench: { type: "string" },
        "request-timeout-ms": { type: "string" },
      },
    }).values;
  } catch {
    return fail("invalid_arguments", "qualification arguments are invalid");
  }
  if (values["execute-live"] !== true) {
    return fail(
      "live_opt_in_required",
      "--execute-live is required because this probe writes durable test data",
    );
  }
  return {
    configJsonPath: requiredString(values["config-json"], "--config-json"),
    pythonExecutable: qualificationHelperArgv(
      requiredString(values["python-executable"], "--python-executable"),
    )[0],
    tenantId: requiredString(values["tenant-id"], "--tenant-id"),
    goalId: requiredString(values["goal-id"], "--goal-id"),
    workbench: requiredString(values.workbench, "--workbench"),
    requestTimeoutMs: values["request-timeout-ms"] === undefined
      ? undefined
      : positiveSafeInteger(values["request-timeout-ms"], "--request-timeout-ms"),
  };
}

async function readJson(path: string, reasonCode: string): Promise<unknown> {
  let bytes: string;
  try {
    bytes = await readFile(path, "utf8");
  } catch {
    return fail(reasonCode, "qualification JSON input could not be read");
  }
  try {
    return JSON.parse(bytes);
  } catch {
    return fail(reasonCode, "qualification JSON input is invalid");
  }
}

async function loadQualificationOptions(
  cli: QualificationCliArguments,
): Promise<QualificationOptions> {
  const configValue = await readJson(cli.configJsonPath, "config_json_invalid");
  let clientConfig: JsonObject;
  try {
    clientConfig = canonicalAuthorityObject(configValue, "NoKV client config");
  } catch {
    return fail("config_json_invalid", "NoKV client config must be strict JSON");
  }
  return {
    python_executable: cli.pythonExecutable,
    client_config: clientConfig,
    tenant_id: cli.tenantId,
    goal_id: cli.goalId,
    workbench: cli.workbench,
    request_timeout_ms: cli.requestTimeoutMs,
  };
}

async function main(): Promise<number> {
  try {
    const cli = parseQualificationArguments(process.argv.slice(2));
    const options = await loadQualificationOptions(cli);
    const report = await qualifyNoKVAuthorityStore(options);
    process.stdout.write(`${JSON.stringify(report)}\n`);
    return 0;
  } catch (error) {
    let reasonCode = "qualification_failed";
    let reason = "NoKV authority-store qualification failed";
    if (error instanceof QualificationFailure) {
      reasonCode = error.reasonCode;
      reason = error.message;
    } else if (error instanceof NoKVTransportUnavailableError) {
      reasonCode = "nokv_backend_unavailable";
      reason = "NoKV backend or SDK helper is unavailable";
    } else if (error instanceof NoKVTransportProtocolError) {
      reasonCode = "nokv_transport_protocol_failed";
      reason = "NoKV SDK helper violated the transport protocol";
    }
    process.stderr.write(`${JSON.stringify({
      schema_version: REPORT_SCHEMA,
      ok: false,
      reason_code: reasonCode,
      reason,
    })}\n`);
    return 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  process.exitCode = await main();
}
