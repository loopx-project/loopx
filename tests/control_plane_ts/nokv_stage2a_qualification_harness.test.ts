import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  exerciseQualificationSequence,
  parseQualificationArguments,
  qualificationHelperArgv,
  QUALIFICATION_SCOPE,
  QUALIFIED_NOKV_API_VERSION,
  QUALIFIED_NOKV_SDK_VERSION,
  QualificationFailure,
  type QualificationTransport,
} from "../../examples/nokv-authority-store/live-qualification.ts";
import {
  NoKVTransportProtocolError,
  NoKVTransportUnavailableError,
  type NoKVBlobCasRequest,
  type NoKVBlobCasResult,
  type NoKVBlobReadResult,
  type NoKVStoreIdentityResult,
} from "../../loopx/control_plane/coordination/nokv_authority_store.ts";
import {
  NoKVJsonLinesTransport,
} from "../../loopx/control_plane/coordination/nokv_jsonl_transport.ts";

const REPOSITORY_HELPER = fileURLToPath(
  new URL("../../loopx/control_plane/coordination/nokv_jsonl_helper.py", import.meta.url),
);
const PYTHON = process.env.LOOPX_TEST_PYTHON ?? "python3";

/** Minimal module that satisfies helper admission and records that it was imported. */
const STAND_IN_SDK_SOURCE = `import os

__version__ = "0.11.1"
API_VERSION = 1

_marker = os.environ.get("LOOPX_TEST_STAND_IN_MARKER")
if _marker:
    with open(_marker, "w", encoding="utf-8") as handle:
        handle.write("stand-in nokv imported")


class RoutingConfig:
    @staticmethod
    def etcd(*values):
        return ("etcd", values)

    @staticmethod
    def seeds(*values):
        return ("seeds", values)

    @staticmethod
    def static(*values):
        return ("static", values)


class ObjectStoreConfig:
    @staticmethod
    def memory():
        return ("memory",)

    @staticmethod
    def s3(**values):
        return ("s3", values)


class WorkspaceIncarnationMismatch(RuntimeError):
    def __init__(self, message, expected):
        super().__init__(message)
        self.expected = expected


class Client:
    def __init__(self, **values):
        self.values = values

    def publish_bytes(self, workbench, path, data, *, expected_workspace_incarnation_id=None, **values):
        raise NotImplementedError
`;

function absolutePythonExecutable(): string | null {
  const probe = spawnSync(PYTHON, ["-c", "import sys; print(sys.executable)"], {
    encoding: "utf8",
  });
  if (probe.status !== 0) return null;
  const executable = probe.stdout.trim();
  return isAbsolute(executable) ? executable : null;
}

interface Backend {
  blob: { bytes: Uint8Array; generation: number } | null;
  ignoreCas: boolean;
  /** Models an owner that accepts a stale incarnation fence instead of refusing it. */
  ignoreIncarnationFence?: boolean;
  publishCalls: number;
  pretendAppliedWithoutWriteOnCall: number | null;
}

const FAKE_INCARNATION = "a".repeat(32);

class FakeQualificationTransport implements QualificationTransport {
  readonly backend: Backend;
  closed = false;

  constructor(backend: Backend) {
    this.backend = backend;
  }

  async storeIdentity(workbench: string): Promise<NoKVStoreIdentityResult> {
    return {
      status: "available",
      store_identity: `nokv:${workbench}:${FAKE_INCARNATION}`,
    };
  }

  async readBlob(_workbench: string, _path: string): Promise<NoKVBlobReadResult> {
    return this.backend.blob
      ? {
        status: "loaded",
        bytes: this.backend.blob.bytes.slice(),
        generation: this.backend.blob.generation,
      }
      : { status: "missing" };
  }

  async casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult> {
    this.backend.publishCalls += 1;
    if (
      !this.backend.ignoreIncarnationFence &&
      request.expected_workspace_incarnation_id !== FAKE_INCARNATION
    ) {
      return {
        status: "failed",
        reason_code: "store_identity_mismatch",
        reason: "workbench incarnation does not match the expected incarnation",
      };
    }
    const current = this.backend.blob?.generation ?? null;
    if (!this.backend.ignoreCas && current !== request.expected_generation) {
      return { status: "conflict", current_generation: current };
    }
    const generation = (current ?? 0) + 1;
    if (this.backend.pretendAppliedWithoutWriteOnCall === this.backend.publishCalls) {
      return { status: "applied", generation };
    }
    this.backend.blob = { bytes: request.bytes.slice(), generation };
    return { status: "applied", generation };
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}

const BASE_OPTIONS = {
  python_executable: "/usr/bin/python3",
  client_config: {
    root_id: "0".repeat(32),
    routing: { kind: "etcd" },
    object_store: { kind: "memory" },
  },
  tenant_id: "qualification-tenant",
  goal_id: "qualification-goal",
  workbench: "authority-workbench",
} as const;

test("Stage 2A qualification harness requires explicit write opt-in", () => {
  assert.throws(
    () => parseQualificationArguments([
      "--config-json", "/tmp/client.json",
      "--python-executable", "/usr/bin/python3",
      "--tenant-id", "qualification-tenant",
      "--goal-id", "qualification-goal",
      "--workbench", "authority-workbench",
    ]),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "live_opt_in_required");
      return true;
    },
  );
});

test("Stage 2A qualification harness fixes the executable, isolation flag, and repository helper", () => {
  const executable = "/opt/loopx-qualification/bin/python";

  assert.deepEqual(qualificationHelperArgv(executable), [executable, "-I", REPOSITORY_HELPER]);
  const parsed = parseQualificationArguments([
    "--execute-live",
    "--config-json", "/tmp/client.json",
    "--python-executable", executable,
    "--tenant-id", "qualification-tenant",
    "--goal-id", "qualification-goal",
    "--workbench", "authority-workbench",
  ]);
  assert.equal(parsed.pythonExecutable, executable);
});

test("Stage 2A qualification harness rejects a relative Python executable", () => {
  assert.throws(
    () => qualificationHelperArgv("python3"),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "invalid_arguments");
      return true;
    },
  );
});

test("Stage 2A qualification harness names the exact NoKV SDK contract", () => {
  assert.equal(QUALIFICATION_SCOPE, "stage_2a_single_node_store_conformance");
  assert.equal(QUALIFIED_NOKV_SDK_VERSION, "0.11.1");
  assert.equal(QUALIFIED_NOKV_API_VERSION, 1);
});

test("qualification proves create, ambiguous reconciliation, contention, and fresh readback", async () => {
  const backend: Backend = {
    blob: null,
    ignoreCas: false,
    publishCalls: 0,
    pretendAppliedWithoutWriteOnCall: null,
  };
  const opened: FakeQualificationTransport[] = [];
  const report = await exerciseQualificationSequence(BASE_OPTIONS, async () => {
    const transport = new FakeQualificationTransport(backend);
    opened.push(transport);
    return transport;
  });

  assert.equal(report.final_generation, 3);
  assert.equal(report.final_cursor, "3");
  assert.deepEqual(report.checks.map((check) => check.id), [
    "existing_workbench_identity",
    "fresh_authority_target",
    "create_applied",
    "create_generation_one",
    "stale_incarnation_fence_rejected",
    "stale_incarnation_fence_left_generation_unchanged",
    "response_lost_success_reconciled",
    "generation_cas_applied",
    "generation_two_readback",
    "competing_generation_cas_one_winner",
    "competition_did_not_double_advance",
    "independent_transport_readback",
    "ambiguous_commit_receipt_retained",
    "winner_receipt_retained",
    "loser_receipt_absent",
  ]);
  assert.deepEqual(report.checks.map((check) => check.status),
    Array(report.checks.length).fill("passed"));
  assert.equal(opened.length, 3);
  assert.ok(opened.every((transport) => transport.closed));
});

test("qualification rejects a backend that accepts a stale incarnation fence", async () => {
  const backend: Backend = {
    blob: null,
    ignoreCas: false,
    ignoreIncarnationFence: true,
    publishCalls: 0,
    pretendAppliedWithoutWriteOnCall: null,
  };
  await assert.rejects(
    exerciseQualificationSequence(BASE_OPTIONS, async () =>
      new FakeQualificationTransport(backend)),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "stale_incarnation_fence_not_enforced");
      return true;
    },
  );
  // The unfenced owner applied the stale write, so the envelope moved to
  // generation 2 without a LoopX commit: exactly the outcome the fence removes.
  assert.equal(backend.blob?.generation, 2);
});

test("qualification rejects a backend that does not enforce generation CAS", async () => {
  const backend: Backend = {
    blob: null,
    ignoreCas: true,
    publishCalls: 0,
    pretendAppliedWithoutWriteOnCall: null,
  };
  await assert.rejects(
    exerciseQualificationSequence(BASE_OPTIONS, async () =>
      new FakeQualificationTransport(backend)),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "competition_not_fenced");
      return true;
    },
  );
});

test("qualification rejects an independent transport that cannot read the envelope", async () => {
  const shared: Backend = {
    blob: null,
    ignoreCas: false,
    publishCalls: 0,
    pretendAppliedWithoutWriteOnCall: null,
  };
  let opened = 0;
  await assert.rejects(
    exerciseQualificationSequence(BASE_OPTIONS, async () => {
      opened += 1;
      return new FakeQualificationTransport(
        opened === 3
          ? {
            blob: null,
            ignoreCas: false,
            publishCalls: 0,
            pretendAppliedWithoutWriteOnCall: null,
          }
          : shared,
      );
    }),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "independent_readback_failed");
      return true;
    },
  );
});

test("qualification requires durable readback after the injected response loss", async () => {
  const backend: Backend = {
    blob: null,
    ignoreCas: false,
    publishCalls: 0,
    // create (1), stale fence probe (2), advance (3)
    pretendAppliedWithoutWriteOnCall: 3,
  };
  await assert.rejects(
    exerciseQualificationSequence(BASE_OPTIONS, async () =>
      new FakeQualificationTransport(backend)),
    (error: unknown) => {
      assert.ok(error instanceof QualificationFailure);
      assert.equal(error.reasonCode, "generation_cas_failed");
      return true;
    },
  );
});

test("qualification helper runs Python isolated so PYTHONPATH cannot substitute the nokv module", async (t) => {
  const executable = absolutePythonExecutable();
  if (executable === null) {
    t.skip("no Python interpreter is available for the helper");
    return;
  }
  const root = mkdtempSync(join(tmpdir(), "loopx-nokv-stand-in-"));
  try {
    const marker = join(root, "stand-in-imported");
    mkdirSync(join(root, "nokv"));
    writeFileSync(join(root, "nokv", "__init__.py"), STAND_IN_SDK_SOURCE);
    const config = {
      root_id: "0".repeat(32),
      routing: {
        kind: "etcd",
        endpoints: ["http://127.0.0.1:1"],
        key_prefix: "/loopx-stand-in",
        lease_ttl_seconds: 1,
      },
      object_store: { kind: "memory" },
    };
    const env = {
      ...process.env,
      PYTHONPATH: root,
      LOOPX_TEST_STAND_IN_MARKER: marker,
    };

    // Without isolation the PYTHONPATH stand-in is imported and admitted. This
    // is the vector the qualification argv closes, so prove it exists first.
    const unguarded = await NoKVJsonLinesTransport.open({
      argv: [executable, REPOSITORY_HELPER],
      config,
      env,
      request_timeout_ms: 30_000,
    });
    await unguarded.close();
    assert.ok(existsSync(marker), "stand-in module must be importable without -I");
    rmSync(marker);

    // The qualification argv must never consult the stand-in: the marker stays
    // absent and the open handshake fails closed, either because the isolated
    // interpreter has no nokv module or because the real SDK cannot reach the
    // closed endpoint.
    await assert.rejects(
      NoKVJsonLinesTransport.open({
        argv: qualificationHelperArgv(executable),
        config,
        env,
        request_timeout_ms: 30_000,
      }),
      (error: unknown) =>
        error instanceof NoKVTransportUnavailableError ||
        error instanceof NoKVTransportProtocolError,
    );
    assert.equal(
      existsSync(marker),
      false,
      "isolated interpreter must not import the PYTHONPATH stand-in",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
