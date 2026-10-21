import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {repoRoot} from "./fixture.mjs";

export async function startAuthority({unsettled = true} = {}) {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-old-storage-browser-"));
  const source = "---\ngoal_id: multi-agent-projection\nhandoff_mode: soft_claim\n---\n" +
    "## Agent Todo\n- [ ] Private current requirement\n" +
    "  <!-- loopx:todo todo_id=todo_current role=agent status=open claimed_by=agent-a -->\n" +
    "## Todo Archive\n- [x] Private full archived requirement\n" +
    "  <!-- loopx:todo todo_id=todo_old role=agent status=done -->\n";
  await writeFile(resolve(root, "state.md"), source);
  await writeFile(resolve(root, "registry.json"), JSON.stringify({
    common_runtime_root: resolve(root, "runtime"), goals: [{id: "multi-agent-projection",
      repo: root, state_file: "state.md", coordination: {registered_agents: ["agent-a"]}}],
  }));
  const leases = resolve(root, "runtime/goals/multi-agent-projection/task-leases");
  await mkdir(leases, {recursive: true});
  if (unsettled) await writeFile(resolve(leases, "removed.json"), JSON.stringify({schema_version: "task_lease_v0",
    goal_id: "multi-agent-projection", todo_id: "removed", owner: "agent-a", status: "active",
    idempotency_key: "original-private-key", version: 4, lease_epoch: 2,
    expires_at: "2000-01-01T00:00:00Z", write_scopes: []}));
  const child = spawn(resolveTestPython(), ["-I", "-u", "-c", `
from pathlib import Path
import sys
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path = Path(sys.argv[1]) / "registry.json"
server.runtime_root = Path(sys.argv[1]) / "runtime"
server.runtime_root_override = str(server.runtime_root)
server.verbose = False
print(server.server_port, flush=True)
server.serve_forever()
`, root], {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
  const lines = createInterface({input: child.stdout});
  let diagnostic = "";
  child.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-2000); });
  try {
    const port = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Storage authority timeout: ${diagnostic}`)), 20_000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Storage authority exited: ${diagnostic}`)); });
      lines.once("line", line => { clearTimeout(timeout); accept(Number(line)); });
    });
    assert.ok(Number.isSafeInteger(port) && port > 0);
    return {root, source, url: `http://127.0.0.1:${port}`, async close() {
      lines.close();
      if (child.exitCode === null && child.signalCode === null) {
        const exited = new Promise(accept => child.once("exit", accept));
        child.kill("SIGTERM"); await exited;
      }
      await rm(root, {recursive: true, force: true});
    }};
  } catch (error) {
    lines.close(); child.kill("SIGTERM"); await rm(root, {recursive: true, force: true}); throw error;
  }
}
