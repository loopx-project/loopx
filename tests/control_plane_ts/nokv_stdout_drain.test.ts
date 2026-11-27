import assert from "node:assert/strict";
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {once} from "node:events";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {NoKVHelperOpenRefusedError, NoKVJsonLinesTransport} from "../../loopx/control_plane/coordination/nokv_jsonl_transport.ts";
import {NoKVTransportUnavailableError} from "../../loopx/control_plane/coordination/nokv_authority_store.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";

const helperPath = fileURLToPath(new URL("../fixtures/nokv_jsonl_fake_helper.py", import.meta.url));

for (const operation of ["read", "publish"] as const) {
  test(`a completed NoKV ${operation} reply survives process exit before stdout drain`, {timeout: 10_000}, async t => {
    let helper: ChildProcessWithoutNullStreams | undefined;
    const transport = await NoKVJsonLinesTransport.open({
      argv: [resolveTestPython(), helperPath, "reply_then_exit"],
      config: {},
      process_factory(command, args, options) {
        helper = spawn(command, args, {...options, stdio: ["pipe", "pipe", "pipe"]});
        return helper;
      },
    });
    t.after(async () => {helper?.stdout.resume(); await transport.close();});
    assert.ok(helper);
    // Hold pipe delivery while the real helper writes its complete reply and
    // exits. Node flushes unread stdio after publishing the process exit event.
    helper.stdout.pause();
    const order: string[] = [];
    let refusedAfterExit: Promise<void> | undefined;
    helper.once("exit", () => {
      order.push("exit");
      refusedAfterExit = assert.rejects(
        transport.storeIdentity("authority-workbench"), NoKVTransportUnavailableError,
      );
    });
    helper.stdout.on("data", () => {order.push("stdout");});
    const closed = once(helper, "close");
    const response = operation === "read"
      ? transport.readBlob("authority-workbench", "metadata/head.json")
      : transport.casPublishBlob({
        workbench: "authority-workbench", path: "metadata/head.json",
        expected_generation: null, expected_workspace_incarnation_id: "a".repeat(32),
        bytes: Buffer.from("committed payload"), operation_id: "b".repeat(32),
        artifact_revision_id: "c".repeat(32),
      });
    const outcome = await response.then(result => ({result}), error => ({error}));
    await closed;
    await refusedAfterExit;
    assert.equal(order[0], "exit");
    assert.ok(order.includes("stdout"), "the completed helper reply must reach the pipe reader");
    assert.deepEqual(outcome, {result: operation === "read"
      ? {status: "missing"} : {status: "applied", generation: 1}});
    await assert.rejects(transport.readBlob("authority-workbench", "metadata/head.json"), /disconnected/);
  });
}

test("a complete NoKV open refusal retains its typed reason after helper exit", {timeout: 10_000}, async () => {
  let closed: Promise<unknown> | undefined;
  await assert.rejects(NoKVJsonLinesTransport.open({
    argv: [resolveTestPython(), helperPath, "refuse_then_exit"],
    config: {},
    process_factory(command, args, options) {
      const helper = spawn(command, args, {...options, stdio: ["pipe", "pipe", "pipe"]});
      helper.stdout.pause();
      closed = once(helper, "close");
      return helper;
    },
  }), error => {
    assert.ok(error instanceof NoKVHelperOpenRefusedError);
    assert.equal(error.reasonCode, "nokv_sdk_capability_mismatch");
    return true;
  });
  await closed;
});

test("an unterminated NoKV reply remains unavailable after stdout drain", {timeout: 10_000}, async t => {
  const transport = await NoKVJsonLinesTransport.open({
    argv: [resolveTestPython(), helperPath, "partial_then_exit"], config: {},
  });
  t.after(async () => {await transport.close();});
  await assert.rejects(transport.readBlob("authority-workbench", "metadata/head.json"), error => {
    assert.ok(error instanceof NoKVTransportUnavailableError);
    assert.match(error.message, /code=17/);
    return true;
  });
});
