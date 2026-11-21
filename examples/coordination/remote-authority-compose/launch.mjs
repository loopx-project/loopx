import {readFileSync} from "node:fs";
import {callRemoteAuthority} from "./loopx/control_plane/coordination/remote_authority_http.ts";

const [mode, action = "read"] = process.argv.slice(2);
const port = "8443";
const read = (directory, file) => readFileSync(`/secrets/${directory}/${file}`, "utf8");

async function main() {
  if (mode === "gateway" || mode === "reader-gateway") {
    const password = read(mode, "database-password");
    process.env.LOOPX_AUTHORITY_DATABASE_URL = `postgresql://loopx_gateway:${encodeURIComponent(password)}@db:5432/authority`;
    process.argv.splice(2, Infinity, "--policy", `/secrets/${mode}/policy.json`,
      "--cert", `/secrets/${mode}/cert.pem`, "--key", `/secrets/${mode}/key.pem`,
      "--host", "0.0.0.0", "--port", port);
    await import("./examples/coordination/remote-authority-service.ts");
    return;
  }
  const directory = mode === "health" ? "health" : "client";
  const descriptor = read(directory, "request.json");
  const ca = read(directory, "ca.pem");
  if (action === "descriptor" || action === "ca") {
    process.stdout.write(action === "descriptor" ? descriptor + "\n" : ca);
    return;
  }
  const endpoint = mode === "health" || mode === "reader-health" ? `https://127.0.0.1:${port}`
    : `https://${process.env.LOOPX_SERVICE_HOST ?? "gateway"}:${port}`;
  const credential = read(directory, "credential");
  if (action === "request" || action === "recover") {
    process.env.LOOPX_AUTHORITY_CREDENTIAL = credential;
    process.argv.splice(2, Infinity, "--endpoint", endpoint, "--ca", `/secrets/${directory}/ca.pem`);
    if (action === "recover") process.argv.push("--recover");
    await import("./examples/coordination/remote-authority-client.ts");
    return;
  }
  if (action !== "read") throw new Error("Unknown command");
  const result = await callRemoteAuthority(endpoint, credential, JSON.parse(descriptor), {ca: Buffer.from(ca)});
  if (mode === "client") process.stdout.write(JSON.stringify(result) + "\n");
  if (result.status !== "found") process.exitCode = 1;
}

main().catch(() => {
  console.error("LoopX command failed");
  process.exitCode = 1;
});
