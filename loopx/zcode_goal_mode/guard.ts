/** Pipe guardian: loss of its broker's stdin revokes the owned CLI process tree. */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { terminateOwnedProcess } from "./app-server.ts";

export async function guard(command: readonly string[]): Promise<void> {
  if (!command.length || command.some((part) => !part)) throw new Error("Invalid guarded command");
  const child = spawn(command[0], command.slice(1), { stdio: "pipe", shell: false, windowsHide: true, detached: process.platform !== "win32" });
  let closing: Promise<void> | undefined;
  const close = (): Promise<void> => {
    if (closing) return closing;
    process.stdin.unpipe(child.stdin);
    child.stdout.unpipe(process.stdout);
    process.stdin.pause();
    closing = terminateOwnedProcess(child);
    return closing;
  };
  const finish = () => { void close().then(() => process.exit(0), () => process.exit(1)); };
  child.stderr.on("data", () => {});
  child.on("error", () => { void close().finally(() => process.exit(1)); });
  child.stdin.on("error", finish);
  child.stdout.on("error", finish);
  process.stdin.on("end", finish);
  process.stdin.on("error", finish);
  process.stdout.on("error", finish);
  process.once("SIGINT", finish);
  process.once("SIGTERM", finish);
  child.once("exit", (code) => {
    if (!closing) void close().then(() => process.exit(code === 0 ? 0 : 1), () => process.exit(1));
  });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  if (process.stdin.readableEnded) finish();
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const command = process.argv.slice(2);
  if (command[0] === "--") command.shift();
  guard(command).catch(() => { process.exitCode = 1; });
}
