/** Draft, explicit encrypted transport for the planned-transfer composition.
 * Native authority archives still own history. This directory envelope only
 * protects selected bytes for a recipient; it grants no execution or credential
 * adoption. Security/release review of the profile remains required before it
 * is advertised as portable recovery (complete-state recovery RFC, section 12).
 */
import {constants} from "node:fs";
import {lstat, mkdir, mkdtemp, open, readdir, realpath, rm, type FileHandle} from "node:fs/promises";
import {createCipheriv, createDecipheriv, createHash, createPrivateKey, createPublicKey, generateKeyPair,
  privateDecrypt, publicEncrypt, randomBytes, constants as cryptoConstants, type KeyObject} from "node:crypto";
import {dirname, isAbsolute, join, resolve} from "node:path";
import {tmpdir} from "node:os";
import {Readable} from "node:stream";
import {promisify} from "node:util";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {durableWriteJson, publishNewDirectory, withFileMutationLock} from "../effect_runtime_io.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {requireJsonObject} from "../runtime_decode.ts";

const RECIPIENT_SCHEMA = "loopx_planned_transfer_recipient_v0";
const CONTAINER_SCHEMA = "loopx_planned_transfer_encrypted_v0";
const CONTENT_KEY_SCHEMA = "loopx_planned_transfer_content_key_v0";
const SUITE = "RSA-OAEP-SHA256/AES-256-GCM";
// Resource bounds match the existing local recovery reader. They neither
// promote that profile nor certify this draft transport's maximum-load behavior.
const MAX_MEMBER_BYTES = 128 * 1024 * 1024, MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const MAX_METADATA_BYTES = 16 * 1024 * 1024, MAX_MEMBERS = 20_000;
const generatePair = promisify(generateKeyPair);
function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function hash(bytes: Uint8Array): string { return createHash("sha256").update(bytes).digest("hex"); }
function exact(value: JsonObject, keys: string[]): void {
  if (!hasExactAuthorityKeys(value, keys)) reject("Encrypted transfer fields are invalid or unsupported");
}
function hex(value: unknown, bytes: number): Buffer {
  if (typeof value !== "string" || !new RegExp(`^[a-f0-9]{${bytes * 2}}$`).test(value)) reject("Encrypted transfer encoding is invalid");
  return Buffer.from(value, "hex");
}
function number(value: unknown, limit: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > limit) reject("Encrypted transfer exceeds its local capacity");
  return value;
}
export async function requirePhysicalTransferPath(raw: unknown): Promise<string> {
  if (typeof raw !== "string" || !isAbsolute(raw) || resolve(raw) !== raw || raw.includes("\0")) reject("Transfer requires a physical normalized path");
  for (let path = raw; ; path = dirname(path)) {
    try { if (await realpath(path) !== path) reject("Transfer path contains a linked ancestor"); break; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if (dirname(path) === path) reject("Transfer path has no physical parent");
  }
  return raw;
}
async function regular(path: string, limit: number, secret = false): Promise<FileHandle> {
  await requirePhysicalTransferPath(path);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > limit || (secret && ((info.mode & 0o077) !== 0 || info.uid !== process.getuid?.()))) {
      reject("Transfer input must be a bounded regular file; secret keys require private ownership and permissions");
    }
    return handle;
  } catch (error) { await handle.close(); throw error; }
}
async function jsonFile(path: string, limit = MAX_METADATA_BYTES, secret = false): Promise<JsonObject> {
  const handle = await regular(path, limit, secret);
  try {
    const bytes = await boundedBytes(handle, limit);
    return requireJsonObject(JSON.parse(bytes.toString("utf8")), "transfer metadata");
  } finally { await handle.close(); }
}
async function boundedBytes(handle: FileHandle, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = []; let bytes = 0;
  for await (const chunk of handle.createReadStream({autoClose: false, emitClose: false})) {
    bytes += chunk.length;
    if (bytes > limit) reject("Transfer metadata grew beyond its local capacity");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, bytes);
}
export async function inspectTransferFile(path: string): Promise<{sha256: string; bytes: number}> {
  const handle = await regular(path, MAX_MEMBER_BYTES);
  const digest = createHash("sha256"); let size = 0;
  try {
    for await (const chunk of handle.createReadStream({autoClose: false, emitClose: false})) {
      size += chunk.length; number(size, MAX_MEMBER_BYTES); digest.update(chunk);
    }
    return {sha256: digest.digest("hex"), bytes: size};
  } finally { await handle.close(); }
}
function recipient(raw: unknown): {descriptor: JsonObject; key: KeyObject} {
  const descriptor = requireJsonObject(raw, "transfer recipient");
  exact(descriptor, ["schema_version", "key_id", "public_key_pem"]);
  if (descriptor.schema_version !== RECIPIENT_SCHEMA || typeof descriptor.public_key_pem !== "string" ||
      descriptor.public_key_pem.length > 8192) reject("Unsupported transfer recipient");
  let key: KeyObject;
  try { key = createPublicKey(descriptor.public_key_pem); } catch { reject("Invalid transfer recipient public key"); }
  if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails?.modulusLength !== 3072 ||
      key.export({format: "pem", type: "spki"}).toString() !== descriptor.public_key_pem ||
      hash(key.export({format: "der", type: "spki"})) !== descriptor.key_id) reject("Transfer recipient identity or key format changed");
  return {descriptor, key};
}
export function validatePlannedTransferRecipient(raw: unknown): JsonObject { return recipient(raw).descriptor; }
export async function createPlannedTransferRecipient(value: JsonObject): Promise<JsonObject> {
  exact(value, ["destination", "execute"]);
  if (process.platform === "win32" || value.execute !== true) reject("Explicit recipient creation requires the local POSIX profile");
  const destination = await requirePhysicalTransferPath(value.destination);
  return withFileMutationLock(destination, async () => {
    try {
      await lstat(destination);
      const descriptor = await jsonFile(join(destination, "recipient.json"));
      const privateHandle = await regular(join(destination, "private-key.pem"), 16 * 1024, true);
      try {
        const privateKey = createPrivateKey(await boundedBytes(privateHandle, 16 * 1024));
        if (hash(createPublicKey(privateKey).export({format: "der", type: "spki"})) !== recipient(descriptor).descriptor.key_id) reject("Recipient key readback changed");
      } finally { await privateHandle.close(); }
      return descriptor;
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    // An occupied incomplete directory is never repaired by issuing new keys.
    try { await lstat(destination); reject("Recipient is incomplete; recover its original private key"); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    await mkdir(dirname(destination), {recursive: true, mode: 0o700});
    const staging = await mkdtemp(join(dirname(destination), ".loopx-recipient-"));
    try {
      const pair = await generatePair("rsa", {modulusLength: 3072});
      const descriptor = {schema_version: RECIPIENT_SCHEMA, key_id: hash(pair.publicKey.export({format: "der", type: "spki"})),
        public_key_pem: pair.publicKey.export({format: "pem", type: "spki"}).toString()};
      const handle = await open(join(staging, "private-key.pem"), "wx", 0o600);
      try { await handle.writeFile(pair.privateKey.export({format: "pem", type: "pkcs8"})); await handle.sync(); }
      finally { await handle.close(); }
      await durableWriteJson(join(staging, "recipient.json"), descriptor);
      await publishNewDirectory(staging, destination);
      return descriptor;
    } finally { await rm(staging, {recursive: true, force: true}); }
  });
}

interface Member extends JsonObject {id: string; sha256: string; bytes: number}
export interface TransferContainerFile {path: string; sha256: string; bytes: number}
export interface VerifiedTransferContainer {
  metadata: JsonObject; files: ReadonlyMap<string, string>; members: readonly Member[]; envelopeSha256: string;
}
function base(envelope: JsonObject): JsonObject {
  return {schema_version: envelope.schema_version, suite: envelope.suite, artifact_id: envelope.artifact_id,
    recipient_id: envelope.recipient_id};
}
function aad(envelope: JsonObject, id: string): Buffer {
  return canonicalAuthorityBytes({...base(envelope), wrapped_key_base64: envelope.wrapped_key_base64, member_id: id});
}
async function encryptMember(input: Readable, output: string, envelope: JsonObject, id: string,
  key: Buffer, expected: {sha256: string; bytes: number}): Promise<JsonObject> {
  const nonce = randomBytes(12), cipher = createCipheriv("aes-256-gcm", key, nonce, {authTagLength: 16});
  cipher.setAAD(aad(envelope, id));
  const plainHash = createHash("sha256"), cipherHash = createHash("sha256"), plain = {bytes: 0}, encrypted = {bytes: 0};
  let handle: FileHandle | undefined;
  try {
    handle = await open(output, "wx", 0o600);
    const write = async (chunk: Buffer) => {
      encrypted.bytes += chunk.length; number(encrypted.bytes, expected.bytes);
      cipherHash.update(chunk); await handle!.writeFile(chunk);
    };
    for await (const chunk of input) {
      plain.bytes += chunk.length; number(plain.bytes, expected.bytes); plainHash.update(chunk);
      await write(cipher.update(chunk));
    }
    await write(cipher.final());
    await handle.sync();
  } finally { input.destroy(); await handle?.close(); }
  if (plain.bytes !== expected.bytes || plainHash.digest("hex") !== expected.sha256) reject("Reviewed transfer material changed during export");
  return {id, nonce: nonce.toString("hex"), tag: cipher.getAuthTag().toString("hex"),
    sha256: cipherHash.digest("hex"), bytes: encrypted.bytes};
}
async function withContainer<T>(directory: string, expectedSha: unknown,
  keyFor: (envelope: JsonObject) => Promise<Buffer>, consume: (verified: VerifiedTransferContainer) => Promise<T>): Promise<T> {
  if (process.platform === "win32") reject("Encrypted transfer requires the local POSIX profile");
  await requirePhysicalTransferPath(directory);
  const envelope = await jsonFile(join(directory, "envelope.json"));
  exact(envelope, ["schema_version", "suite", "artifact_id", "recipient_id", "wrapped_key_base64", "entries"]);
  const envelopeSha = canonicalAuthoritySha256(envelope);
  if (envelope.schema_version !== CONTAINER_SCHEMA || envelope.suite !== SUITE ||
      (expectedSha !== undefined && expectedSha !== envelopeSha) || !Array.isArray(envelope.entries) ||
      envelope.entries.length < 2 || envelope.entries.length > MAX_MEMBERS) reject("Transfer format or reviewed container changed");
  hex(envelope.artifact_id, 32); hex(envelope.recipient_id, 32);
  if (typeof envelope.wrapped_key_base64 !== "string" || Buffer.from(envelope.wrapped_key_base64, "base64").length !== 384 ||
      Buffer.from(envelope.wrapped_key_base64, "base64").toString("base64") !== envelope.wrapped_key_base64) reject("Invalid transfer key envelope");
  const entries = envelope.entries.map((raw, index) => {
    const row = requireJsonObject(raw, "encrypted member"); exact(row, ["id", "nonce", "tag", "sha256", "bytes"]);
    if (row.id !== index.toString(16).padStart(8, "0")) reject("Transfer members are missing, duplicated or reordered");
    hex(row.nonce, 12); hex(row.tag, 16); hex(row.sha256, 32);
    number(row.bytes, index === 0 ? MAX_METADATA_BYTES : MAX_MEMBER_BYTES);
    return row;
  });
  number(entries.reduce((total, row) => total + Number(row.bytes), 0), MAX_TOTAL_BYTES);
  if (new Set(entries.map(row => row.nonce)).size !== entries.length) reject("Transfer member nonce is reused");
  const expectedNames = ["envelope.json", ...entries.map(row => `${row.id}.enc`)].sort();
  if (!canonicalAuthorityBytes((await readdir(directory)).sort()).equals(canonicalAuthorityBytes(expectedNames))) reject("Transfer contains missing or unexpected members");
  const temporary = await realpath(await mkdtemp(join(tmpdir(), "loopx-private-transfer-")));
  let key: Buffer | undefined;
  try {
    key = await keyFor(envelope);
    const files = new Map<string, string>();
    for (const row of entries) {
      const id = String(row.id), source = await regular(join(directory, `${id}.enc`), Number(row.bytes));
      const output = join(temporary, id); let handle: FileHandle | undefined;
      try {
        handle = await open(output, "wx", 0o600);
        const decipher = createDecipheriv("aes-256-gcm", key, hex(row.nonce, 12), {authTagLength: 16});
        decipher.setAAD(aad(envelope, id)); decipher.setAuthTag(hex(row.tag, 16));
        const encrypted = {bytes: 0}, cipherHash = createHash("sha256");
        for await (const chunk of source.createReadStream({autoClose: false, emitClose: false})) {
          encrypted.bytes += chunk.length; number(encrypted.bytes, Number(row.bytes)); cipherHash.update(chunk);
          await handle.writeFile(decipher.update(chunk));
        }
        await handle.writeFile(decipher.final());
        if (encrypted.bytes !== row.bytes || cipherHash.digest("hex") !== row.sha256) reject("Encrypted transfer member changed");
      } catch { reject("Transfer authentication failed; no destination authority was admitted"); }
      finally { await source.close(); await handle?.close(); }
      files.set(id, output);
    }
    const document = await jsonFile(files.get("00000000")!);
    exact(document, ["metadata", "members"]);
    if (!Array.isArray(document.members) || document.members.length !== entries.length - 1) reject("Transfer payload inventory changed");
    const members: Member[] = [];
    for (const [index, raw] of document.members.entries()) {
      const row = requireJsonObject(raw, "transfer member"); exact(row, ["id", "sha256", "bytes"]);
      if (row.id !== (index + 1).toString(16).padStart(8, "0") || !BARE_SHA256_PATTERN.test(String(row.sha256))) reject("Invalid transfer payload identity");
      const observed = await inspectTransferFile(files.get(String(row.id))!);
      if (observed.sha256 !== row.sha256 || observed.bytes !== row.bytes) reject("Authenticated transfer payload differs from its original bytes");
      members.push(row as Member);
    }
    // No caller sees even a plaintext path until every member authenticated.
    return await consume({metadata: requireJsonObject(document.metadata, "transfer payload metadata"), files,
      members, envelopeSha256: envelopeSha});
  } finally { key?.fill(0); await rm(temporary, {recursive: true, force: true}); }
}
export async function withVerifiedTransferContainer<T>(directory: string, privateKeyPath: string, expectedSha: unknown,
  consume: (verified: VerifiedTransferContainer) => Promise<T>): Promise<T> {
  return withContainer(directory, expectedSha, async envelope => {
    const handle = await regular(privateKeyPath, 16 * 1024, true);
    try {
      const key = createPrivateKey(await boundedBytes(handle, 16 * 1024));
      if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails?.modulusLength !== 3072) reject("Unsupported transfer recipient key");
      if (hash(createPublicKey(key).export({format: "der", type: "spki"})) !== envelope.recipient_id) reject("This transfer belongs to a different recipient");
      const contentKey = privateDecrypt({key, padding: cryptoConstants.RSA_PKCS1_OAEP_PADDING,
        oaepHash: "sha256", oaepLabel: canonicalAuthorityBytes(base(envelope))}, Buffer.from(String(envelope.wrapped_key_base64), "base64"));
      if (contentKey.length !== 32) { contentKey.fill(0); reject("Invalid transfer content key"); }
      return contentKey;
    } finally { await handle.close(); }
  }, consume);
}

/** The source operation keeps its content key privately for exact-operation
 * retry. That secret record and the recipient private key are outside the
 * transported directory and are never returned in an RPC result. */
export async function writeTransferContainer(destination: string, recipientValue: unknown, metadata: JsonObject,
  inputs: TransferContainerFile[], keyRecordPath: string): Promise<JsonObject> {
  if (process.platform === "win32") reject("Encrypted transfer requires the local POSIX profile");
  const receiver = recipient(recipientValue);
  await requirePhysicalTransferPath(destination); await requirePhysicalTransferPath(keyRecordPath);
  if (inputs.length < 1 || inputs.length + 1 > MAX_MEMBERS) reject("Transfer member capacity exceeded");
  const members = inputs.map((item, index) => ({id: (index + 1).toString(16).padStart(8, "0"),
    sha256: item.sha256, bytes: number(item.bytes, MAX_MEMBER_BYTES)}));
  for (const row of members) hex(row.sha256, 32);
  const document = {metadata, members}, documentBytes = canonicalAuthorityBytes(document);
  number(documentBytes.length, MAX_METADATA_BYTES);
  number(documentBytes.length + members.reduce((sum, row) => sum + row.bytes, 0), MAX_TOTAL_BYTES);
  const identity = canonicalAuthoritySha256({destination, recipient: receiver.descriptor, document});
  let keyRecord: JsonObject;
  try { keyRecord = await jsonFile(keyRecordPath, 16 * 1024, true); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    try { await lstat(destination); reject("Original transfer content key is missing; recover it without replacing the exported artifact"); }
    catch (occupied) { if ((occupied as NodeJS.ErrnoException).code !== "ENOENT") throw occupied; }
    keyRecord = {schema_version: CONTENT_KEY_SCHEMA, identity_sha256: identity,
      artifact_id: randomBytes(32).toString("hex"), content_key_hex: randomBytes(32).toString("hex")};
    await mkdir(dirname(keyRecordPath), {recursive: true, mode: 0o700});
    await durableWriteJson(keyRecordPath, keyRecord);
  }
  exact(keyRecord, ["schema_version", "identity_sha256", "artifact_id", "content_key_hex"]);
  if (keyRecord.schema_version !== CONTENT_KEY_SCHEMA || keyRecord.identity_sha256 !== identity) reject("Transfer export belongs to different reviewed inputs; recover the original operation");
  const key = hex(keyRecord.content_key_hex, 32); hex(keyRecord.artifact_id, 32);
  const summarize = (verified: VerifiedTransferContainer): JsonObject => {
    if (!canonicalAuthorityBytes(verified.metadata).equals(canonicalAuthorityBytes(metadata)) ||
        !canonicalAuthorityBytes(verified.members).equals(canonicalAuthorityBytes(members))) reject("Existing transfer export differs from the original operation");
    return {schema_version: CONTAINER_SCHEMA, artifact_sha256: verified.envelopeSha256,
      recipient_id: receiver.descriptor.key_id, encrypted_member_count: members.length + 1,
      protected_transport_verified: true, execution_authority_granted: false, credential_adoption_performed: false};
  };
  try {
    let occupied = false;
    try { await lstat(destination); occupied = true; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if (occupied) return await withContainer(destination, undefined, async envelope => {
        if (envelope.artifact_id !== keyRecord.artifact_id || envelope.recipient_id !== receiver.descriptor.key_id) reject("Transfer destination is occupied");
        return Buffer.from(key);
      }, async verified => summarize(verified));
    await mkdir(dirname(destination), {recursive: true, mode: 0o700});
    const staging = await mkdtemp(join(dirname(destination), ".loopx-encrypted-transfer-"));
    try {
      const header: JsonObject = {schema_version: CONTAINER_SCHEMA, suite: SUITE,
        artifact_id: keyRecord.artifact_id, recipient_id: receiver.descriptor.key_id};
      header.wrapped_key_base64 = publicEncrypt({key: receiver.key, padding: cryptoConstants.RSA_PKCS1_OAEP_PADDING,
        oaepHash: "sha256", oaepLabel: canonicalAuthorityBytes(base(header))}, key).toString("base64");
      const entries = [await encryptMember(Readable.from([documentBytes]), join(staging, "00000000.enc"),
        header, "00000000", key, {bytes: documentBytes.length, sha256: hash(documentBytes)})];
      for (const [index, input] of inputs.entries()) {
        const handle = await regular(input.path, MAX_MEMBER_BYTES);
        try { entries.push(await encryptMember(handle.createReadStream({autoClose: false, emitClose: false}),
          join(staging, `${members[index]!.id}.enc`), header, members[index]!.id, key, members[index]!)); }
        finally { await handle.close(); }
      }
      header.entries = entries;
      await durableWriteJson(join(staging, "envelope.json"), header);
      await withContainer(staging, canonicalAuthoritySha256(header), async () => Buffer.from(key), async verified => summarize(verified));
      await publishNewDirectory(staging, destination);
      return {schema_version: CONTAINER_SCHEMA, artifact_sha256: canonicalAuthoritySha256(header),
        recipient_id: receiver.descriptor.key_id, encrypted_member_count: members.length + 1,
        protected_transport_verified: true, execution_authority_granted: false, credential_adoption_performed: false};
    } finally { await rm(staging, {recursive: true, force: true}); }
  } finally { key.fill(0); }
}
