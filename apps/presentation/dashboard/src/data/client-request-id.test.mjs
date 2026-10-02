import assert from "node:assert/strict";
import { clientRequestId } from "./client-request-id.js";

assert.equal(clientRequestId({ randomUUID: () => "host-native-identity" }), "host-native-identity");
const insecureContext = { getRandomValues: (bytes) => {
  for (let index = 0; index < bytes.length; index += 1) bytes[index] = index;
  return bytes;
} };
assert.equal(clientRequestId(insecureContext), "00010203-0405-4607-8809-0a0b0c0d0e0f");
assert.match(clientRequestId(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
console.log("Client request identity works without secure-context randomUUID");
