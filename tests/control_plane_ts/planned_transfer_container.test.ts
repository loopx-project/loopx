import assert from "node:assert/strict";
import test, {type TestContext} from "node:test";
import {chmod, cp, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, stat, symlink, truncate, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {createHash} from "node:crypto";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {createPlannedTransferRecipient, inspectTransferFile, validatePlannedTransferRecipient,
  withVerifiedTransferContainer, writeTransferContainer} from "../../loopx/control_plane/goals/planned_transfer_container.ts";

async function fixture(t: TestContext) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-encrypted-transfer-test-")));
  t.after(() => rm(root, {recursive: true, force: true}));
  const recipientRoot = join(root, "recipient"), bundle = join(root, "bundle"), keyRecord = join(root, "source-key.json");
  const descriptor = await createPlannedTransferRecipient({destination: recipientRoot, execute: true});
  const privateKey = join(recipientRoot, "private-key.pem"), source = join(root, "material");
  const content = Buffer.from("Original unfinished work and private source path\n".repeat(6000));
  await writeFile(source, content);
  const inputs = [{path: source, ...await inspectTransferFile(source)}];
  const metadata = {goal: "original-private-goal", source_path: source, constraints: "Retain the original decision"};
  const exported = await writeTransferContainer(bundle, descriptor, metadata, inputs, keyRecord);
  return {root, recipientRoot, descriptor, privateKey, source, content, inputs, metadata, bundle, keyRecord, exported};
}

test("encrypted bytes are copyable without source paths or either private key; awaited readback owns plaintext lifetime", async t => {
  const f = await fixture(t), target = join(f.root, "destination-copy");
  assert.deepEqual((await readdir(f.bundle)).sort(), ["00000000.enc", "00000001.enc", "envelope.json"]);
  for (const name of await readdir(f.bundle)) {
    const bytes = await readFile(join(f.bundle, name));
    for (const secret of [f.source, f.metadata.goal, f.metadata.constraints, "PRIVATE KEY", "content_key_hex"]) {
      assert.equal(bytes.includes(Buffer.from(secret)), false, "plaintext must stay outside the transported directory");
    }
  }
  const keyBytes = await readFile(f.privateKey), sourceKeyBytes = await readFile(f.keyRecord);
  assert.equal((await stat(f.privateKey)).mode & 0o077, 0);
  assert.equal((await stat(f.keyRecord)).mode & 0o077, 0);
  await cp(f.bundle, target, {recursive: true});
  await rm(f.source); await rm(f.bundle, {recursive: true}); await rm(f.keyRecord);
  let plaintextPath = "";
  await withVerifiedTransferContainer(target, f.privateKey, f.exported.artifact_sha256, async verified => {
    assert.deepEqual(verified.metadata, f.metadata); plaintextPath = verified.files.get("00000001")!;
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(await readFile(plaintextPath), f.content);
    assert.equal((await lstat(plaintextPath)).mode & 0o077, 0);
  });
  await assert.rejects(stat(plaintextPath), {code: "ENOENT"});
  assert.deepEqual(await readFile(f.privateKey), keyBytes);
  assert.equal(sourceKeyBytes.includes(Buffer.from("content_key_hex")), true);
});

test("recipient retry retains its original key and incomplete or insecure custody is refused", async t => {
  const f = await fixture(t), before = await readFile(f.privateKey);
  assert.deepEqual(await createPlannedTransferRecipient({destination: f.recipientRoot, execute: true}), f.descriptor);
  assert.deepEqual(await readFile(f.privateKey), before);
  assert.throws(() => validatePlannedTransferRecipient({...f.descriptor, public_key_pem: before.toString()}), /format changed/);
  await chmod(f.privateKey, 0o644);
  await assert.rejects(withVerifiedTransferContainer(f.bundle, f.privateKey, f.exported.artifact_sha256, async () => null), /private ownership/);
  await chmod(f.privateKey, 0o600);
  await rm(f.privateKey);
  await assert.rejects(createPlannedTransferRecipient({destination: f.recipientRoot, execute: true}), /incomplete/);
  assert.equal((await readdir(f.recipientRoot)).includes("private-key.pem"), false);
});

test("wrong recipients, linked key paths and wrong reviewed digests never reach the consumer", async t => {
  const f = await fixture(t), other = join(f.root, "other-recipient");
  await createPlannedTransferRecipient({destination: other, execute: true});
  let calls = 0;
  const consume = async () => { calls++; return null; };
  await assert.rejects(withVerifiedTransferContainer(f.bundle, join(other, "private-key.pem"), f.exported.artifact_sha256, consume), /different recipient/);
  const linked = join(f.root, "linked-key"); await symlink(f.privateKey, linked);
  await assert.rejects(withVerifiedTransferContainer(f.bundle, linked, f.exported.artifact_sha256, consume), /linked ancestor/);
  await assert.rejects(withVerifiedTransferContainer(f.bundle, f.privateKey, "0".repeat(64), consume), /reviewed container changed/);
  assert.equal(calls, 0);
});

test("tamper, member substitution and future formats fail before any plaintext consumer or target effect", async t => {
  const f = await fixture(t);
  const other = join(f.root, "other-bundle");
  await writeTransferContainer(other, f.descriptor, {...f.metadata, goal: "different original Goal"}, f.inputs, join(f.root, "other-key.json"));
  const otherHeader = JSON.parse(await readFile(join(other, "envelope.json"), "utf8"));
  const scenarios: [string, (directory: string, header: JsonObject) => Promise<void>][] = [
    ["ciphertext", async (directory, header) => {
      const name = join(directory, "00000001.enc"), bytes = await readFile(name); bytes[0] ^= 1; await writeFile(name, bytes);
      (header.entries as JsonObject[])[1]!.sha256 = createHash("sha256").update(bytes).digest("hex");
    }],
    ["authentication tag", async (_directory, header) => { (header.entries as JsonObject[])[1]!.tag = "00".repeat(16); }],
    ["member from another transfer", async (directory, header) => {
      await writeFile(join(directory, "00000001.enc"), await readFile(join(other, "00000001.enc")));
      (header.entries as JsonObject[])[1] = otherHeader.entries[1];
    }],
    ["wrapped key", async (_directory, header) => { header.wrapped_key_base64 = Buffer.alloc(384).toString("base64"); }],
    ["source identity", async (_directory, header) => { header.artifact_id = "11".repeat(32); }],
    ["duplicate nonce", async (_directory, header) => { (header.entries as JsonObject[])[1]!.nonce = (header.entries as JsonObject[])[0]!.nonce; }],
    ["future format", async (_directory, header) => { header.schema_version = "unsupported_future_transfer"; }],
    ["member order", async (_directory, header) => { (header.entries as JsonObject[]).reverse(); }],
    ["extra file", async directory => { await writeFile(join(directory, "private-leak.txt"), "must refuse"); }],
    ["missing file", async directory => { await rm(join(directory, "00000001.enc")); }],
    ["linked member", async directory => {
      await rm(join(directory, "00000001.enc")); await symlink(join(f.bundle, "00000001.enc"), join(directory, "00000001.enc"));
    }],
  ];
  for (const [index, [name, corrupt]] of scenarios.entries()) {
    await t.test(name, async () => {
      const directory = join(f.root, `corrupt-${index}`); await cp(f.bundle, directory, {recursive: true});
      const header = JSON.parse(await readFile(join(directory, "envelope.json"), "utf8"));
      await corrupt(directory, header); await writeFile(join(directory, "envelope.json"), JSON.stringify(header));
      let invoked = false;
      await assert.rejects(withVerifiedTransferContainer(directory, f.privateKey, canonicalAuthoritySha256(header), async () => { invoked = true; }));
      assert.equal(invoked, false);
    });
  }
});

test("source mutation, occupied outputs and lost custody never overwrite or recreate an exported operation", async t => {
  const f = await fixture(t), initialHeader = await readFile(join(f.bundle, "envelope.json"));
  const occupied = join(f.root, "occupied"), unusedKey = join(f.root, "unused-key.json");
  await writeFile(occupied, "Retain this destination owner's work");
  await assert.rejects(writeTransferContainer(occupied, f.descriptor, f.metadata, f.inputs, unusedKey), /content key is missing/);
  assert.equal(await readFile(occupied, "utf8"), "Retain this destination owner's work");
  await assert.rejects(stat(unusedKey), {code: "ENOENT"});
  assert.deepEqual(await writeTransferContainer(f.bundle, f.descriptor, f.metadata, f.inputs, f.keyRecord), f.exported);
  await assert.rejects(writeTransferContainer(f.bundle, f.descriptor, {...f.metadata, constraints: "changed"}, f.inputs, f.keyRecord), /different reviewed inputs/);
  assert.deepEqual(await readFile(join(f.bundle, "envelope.json")), initialHeader);
  await rm(join(f.bundle, "00000001.enc"));
  await assert.rejects(writeTransferContainer(f.bundle, f.descriptor, f.metadata, f.inputs, f.keyRecord), /missing or unexpected/);
  await assert.rejects(stat(join(f.bundle, "00000001.enc")), {code: "ENOENT"});
  await rm(f.keyRecord);
  await assert.rejects(writeTransferContainer(f.bundle, f.descriptor, f.metadata, f.inputs, f.keyRecord), /content key is missing/);
  await assert.rejects(stat(f.keyRecord), {code: "ENOENT"});
  const newOutput = join(f.root, "new-output"), newKey = join(f.root, "new-source-key.json");
  await writeFile(f.source, "different material bytes");
  await assert.rejects(writeTransferContainer(newOutput, f.descriptor, f.metadata, f.inputs, newKey), /material changed|capacity/);
  await assert.rejects(stat(newOutput), {code: "ENOENT"});
  await writeFile(f.source, Buffer.concat([f.content, Buffer.from("material grew after review")]));
  await assert.rejects(writeTransferContainer(newOutput, f.descriptor, f.metadata, f.inputs, newKey), /material changed|capacity/);
  await assert.rejects(stat(newOutput), {code: "ENOENT"});
  await writeFile(f.source, f.content);
  await writeTransferContainer(newOutput, f.descriptor, f.metadata, f.inputs, newKey);
  const sparse = join(f.root, "oversized"); await writeFile(sparse, ""); await truncate(sparse, 128 * 1024 * 1024 + 1);
  await assert.rejects(inspectTransferFile(sparse), /bounded regular file/);
});
