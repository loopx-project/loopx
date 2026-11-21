import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {createServer} from "node:http";
import {once} from "node:events";
import {resolve} from "node:path";
import {promisify} from "node:util";

// A healthy foreign page must never certify the candidate source or bundle.
let foreignRequests = 0;
const foreign = createServer((_request, response) => {
  foreignRequests++;
  response.end("unrelated page");
});
foreign.listen(0, "127.0.0.1");
await once(foreign, "listening");
try {
  const {port} = foreign.address();
  process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
  process.env.LOOPX_PERSONAL_WORKSPACE_PORT = String(port);
  const {repoRoot, startServer} = await import("./fixture.mjs");
  const source = promisify(execFile)(process.execPath, [resolve(repoRoot, "examples/personal-workspace-browser-smoke.mjs")], {
    cwd: repoRoot, timeout: 30_000,
    env: {...process.env, LOOPX_PERSONAL_WORKSPACE_PACKAGED: "0", LOOPX_DASHBOARD_COVERAGE: "1",
      LOOPX_PERSONAL_WORKSPACE_SCENARIO: "research-results"},
  });
  await assert.rejects(source, error => {
    assert.equal(error.code, 1);
    assert.match(error.stderr, /Dashboard Vite server exited/);
    assert.match(error.stderr, new RegExp(`Port ${port} is already in use`));
    return true;
  });
  await assert.rejects(startServer(), error => {
    assert.match(error.message, /Packaged workspace server exited/);
    const finalError = error.message.trimEnd().split("\n").at(-1);
    assert.match(finalError, /^OSError: \[(?:Errno (?:48|98)|WinError 10048)\]/);
    return true;
  });
  assert.equal(foreignRequests, 0, "Neither launcher probes the foreign listener for readiness");
  assert.equal(await (await fetch(`http://127.0.0.1:${port}/chat/`)).text(), "unrelated page");
} finally {
  foreign.closeAllConnections();
  await new Promise((resolveClose, rejectClose) => foreign.close((error) => error ? rejectClose(error) : resolveClose()));
}
console.log("Dashboard source and packaged launchers reject an occupied port without touching the other server");
