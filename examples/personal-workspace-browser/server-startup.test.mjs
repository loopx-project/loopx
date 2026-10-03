import assert from "node:assert/strict";
import {createServer} from "node:http";
import {once} from "node:events";

// A healthy foreign page must never certify the candidate bundle.
const foreign = createServer((_request, response) => response.end("unrelated page"));
foreign.listen(0, "127.0.0.1");
await once(foreign, "listening");
try {
  process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
  process.env.LOOPX_PERSONAL_WORKSPACE_PORT = String(foreign.address().port);
  const {startServer} = await import("./fixture.mjs");
  await assert.rejects(startServer(), /Packaged workspace server exited/);
  assert.equal(await (await fetch(`http://127.0.0.1:${foreign.address().port}/chat/`)).text(), "unrelated page");
} finally {
  foreign.closeAllConnections();
  await new Promise((resolveClose, rejectClose) => foreign.close((error) => error ? rejectClose(error) : resolveClose()));
}
console.log("Packaged workspace rejects occupied-port readiness without touching the other server");
