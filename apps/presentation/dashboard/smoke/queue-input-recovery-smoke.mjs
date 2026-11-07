import assert from "node:assert/strict";
import { listQueueInputRecovery, queueInputKeys, readQueueInputRecovery, retireQueueInputRequest } from "../src/features/personal-workspace/queue-input-recovery";

class MemoryStorage {
  rows = new Map();
  get length() {return this.rows.size;}
  key(index) {return [...this.rows.keys()][index] ?? null;}
  getItem(key) {return this.rows.get(key) ?? null;}
  setItem(key, value) {this.rows.set(key, value);}
  removeItem(key) {this.rows.delete(key);}
}
const storage = new MemoryStorage(), session = "original-session", turn = "original-turn";
const keys = queueInputKeys(session, turn);
const request = {action: "edit", operation_id: "original-operation", expected_revision: 0, message: "First correction"};
function retain(editor = {message: request.message, revision: 0}) {
  storage.setItem(keys.request, JSON.stringify(request));
  storage.setItem(keys.draft, JSON.stringify(editor));
}
retain();
assert.deepEqual(listQueueInputRecovery(storage, "other-session").entries, [], "Session isolation applies to local recovery");
assert.equal(listQueueInputRecovery(storage, session).entries.length, 1, "Request and draft describe one retained input");
// While A's response is pending, another page changes its draft to B.
retain({message: "Newer correction from another page", revision: 0});
retireQueueInputRequest(storage, session, turn, request, true);
assert.equal(readQueueInputRecovery(storage, session, turn).editor.message, "Newer correction from another page");
assert.equal(readQueueInputRecovery(storage, session, turn).request, null);
// A's response must not retire a later operation identity either.
const newer = {...request, operation_id: "later-operation", message: "Later request"};
storage.setItem(keys.request, JSON.stringify(newer));
retireQueueInputRequest(storage, session, turn, request, true);
assert.deepEqual(readQueueInputRecovery(storage, session, turn).request, newer);
// A request retained before IO can be rejected after the original starts.
storage.removeItem(keys.draft); storage.setItem(keys.request, JSON.stringify(request));
retireQueueInputRequest(storage, session, turn, request, false);
assert.equal(readQueueInputRecovery(storage, session, turn).editor.message, request.message);
assert.equal(readQueueInputRecovery(storage, session, turn).request, null);
// Successful cleanup removes only matching text and revision.
retain({message: request.message, revision: 1});
retireQueueInputRequest(storage, session, turn, request, true);
assert.equal(readQueueInputRecovery(storage, session, turn).editor.revision, 1);
retain(); retireQueueInputRequest(storage, session, turn, request, true);
assert.deepEqual(listQueueInputRecovery(storage, session).entries, []);
// Failure while retiring the draft keeps the original retry identity.
retain();
const remove = storage.removeItem.bind(storage);
storage.removeItem = key => {if (key === keys.draft) throw new Error("Storage unavailable"); remove(key);};
assert.throws(() => retireQueueInputRequest(storage, session, turn, request, true));
assert.equal(readQueueInputRecovery(storage, session, turn).request.operation_id, request.operation_id);
storage.removeItem = remove;
storage.setItem(keys.draft, "0");
assert.equal(listQueueInputRecovery(storage, session).failed, true, "Invalid recovery is not an empty success");
assert.equal(listQueueInputRecovery({get length() {throw new Error("Storage blocked");}}, session).failed, true);
console.log("queue-input-recovery smoke ok");
