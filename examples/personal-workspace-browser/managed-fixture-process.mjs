import {createInterface} from "node:readline";

// Own the fixture's pipes until close, including when startup or the browser
// fails. A cleanup write must never replace the original scenario failure.
export async function withManagedFixture(child, action, {readTimeoutMs = 20_000, stopTimeoutMs = 2000} = {}) {
  let stderr = "", processError, pipeError, actionFailed = false;
  let failed;
  const failure = new Promise(resolve => {failed = resolve;});
  const closed = new Promise(resolve => child.once("close", resolve));
  child.on("error", error => {processError = error; failed(error);});
  child.stdin.on("error", error => {pipeError = error; failed(error);});
  child.stderr.on("data", chunk => {stderr += chunk;});
  const lines = createInterface({input: child.stdout});
  const iterator = lines[Symbol.asyncIterator]();
  const diagnostic = (message, cause) => new Error(
    `${message} (exit=${child.exitCode}, signal=${child.signalCode})${stderr ? `:\n${stderr}` : ""}`,
    {cause: cause ?? processError ?? pipeError},
  );
  async function next() {
    let timer;
    try {
      return await Promise.race([
        (async () => {
          for (;;) {
            const line = await iterator.next();
            if (line.done) {await closed; throw diagnostic("Managed fixture closed");}
            if (line.value.startsWith("{")) return JSON.parse(line.value);
          }
        })(),
        failure.then(error => {throw diagnostic("Managed fixture transport failed", error);}),
        new Promise((_, reject) => {timer = setTimeout(() => reject(diagnostic("Managed fixture timed out")), readTimeoutMs);}),
      ]);
    } finally {clearTimeout(timer);}
  }
  async function control(command) {
    try {
      await new Promise((resolve, reject) => child.stdin.write(`${command}\n`, error => error ? reject(error) : resolve()));
    } catch (error) {throw diagnostic("Managed fixture control failed", error);}
    return next();
  }
  async function waitForClose() {
    let timer;
    try {
      return await Promise.race([closed.then(() => true), new Promise(resolve => {timer = setTimeout(() => resolve(false), stopTimeoutMs);})]);
    } finally {clearTimeout(timer);}
  }
  try {
    return await action({next, control, get stderr() {return stderr;}});
  } catch (error) {actionFailed = true; throw error;}
  finally {
    // Register close/error observers before any write; exit may race shutdown.
    if (child.exitCode === null && child.signalCode === null && !processError) {
      if (!child.stdin.destroyed && !child.stdin.writableEnded) child.stdin.end("stop\n");
      if (!(await waitForClose())) {
        child.kill("SIGTERM");
        if (!(await waitForClose())) child.kill("SIGKILL");
      }
    }
    await closed;
    lines.close();
    if (!actionFailed && (processError || pipeError || child.exitCode !== 0)) {
      throw diagnostic("Managed fixture shutdown failed");
    }
  }
}
