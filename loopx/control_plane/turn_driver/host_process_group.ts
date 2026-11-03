/** Private POSIX cleanup observation; signals alone do not prove termination. */
import {execFile, type ChildProcessWithoutNullStreams} from "node:child_process";
import {performance} from "node:perf_hooks";
import {setTimeout as delay} from "node:timers/promises";

const CLEANUP_DEADLINE_MS = 1000;
const DESCENDANT_POLL_MS = 100;

/** Detached SDK children retain their parent identity until the owner stops. */
export async function descendantProcessGroups(parentPid: number, ownerGroup: number): Promise<number[] | null> {
  const snapshot = await new Promise<string>((resolve, reject) => {
    execFile("ps", ["-A", "-o", "pid=", "-o", "ppid=", "-o", "pgid="],
      {encoding: "utf8", timeout: CLEANUP_DEADLINE_MS, killSignal: "SIGKILL", maxBuffer: 1024 * 1024},
      (error, stdout) => error ? reject(new Error("managed Host descendant observation failed")) : resolve(stdout));
  });
  if (!snapshot.trim()) throw new Error("invalid managed Host descendant observation");
  const rows = snapshot.split("\n").filter(line => line.trim()).map(line => {
    const match = /^\s*(\d+)\s+(\d+)\s+(\d+)\s*$/.exec(line);
    if (!match) throw new Error("invalid managed Host descendant observation");
    return {pid: Number(match[1]), parent: Number(match[2]), group: Number(match[3])};
  });
  if (!rows.some(row => row.pid === parentPid)) {
    return null; // Lost ancestry supplies no new ownership evidence.
  }
  const descendants = new Set([parentPid]);
  for (let changed = true; changed;) {
    changed = false;
    for (const row of rows) {
      if (descendants.has(row.parent) && !descendants.has(row.pid)) {
        descendants.add(row.pid); changed = true;
      }
    }
  }
  if (rows.some(row => descendants.has(row.pid) && row.group !== ownerGroup && !descendants.has(row.group))) {
    throw new Error("managed Host descendant joined an unowned process group");
  }
  return [...new Set(rows.filter(row => row.pid !== parentPid && descendants.has(row.pid)
    && row.group > 1 && row.group !== ownerGroup).map(row => row.group))];
}

/** Observe while ancestry exists; an unexpected exit cannot certify unseen children. */
export class HostDescendantTracker {
  private readonly groups = new Set<number>();
  private pending: Promise<void> | undefined;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  private observed = false;
  private ownershipComplete = true;
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly publish: (groups: number[]) => Promise<void>;
  private readonly failed: () => void;
  outputComplete = true;

  constructor(child: ChildProcessWithoutNullStreams,
    publish: (groups: number[]) => Promise<void>, failed: () => void) {
    this.child = child; this.publish = publish; this.failed = failed;
  }

  observe(): Promise<void> {
    if (this.pending) return this.pending;
    if (!this.child.pid || this.child.exitCode !== null || this.child.signalCode !== null) return Promise.resolve();
    this.pending = (async () => {
      const previous = this.groups.size;
      try {
        const groups = await descendantProcessGroups(this.child.pid!, this.child.pid!);
        if (groups === null) return;
        for (const group of groups) this.groups.add(group);
        this.observed = true;
      } catch {
        this.ownershipComplete = false; this.outputComplete = false;
        this.failed();
        return;
      }
      // Only publish new ownership. Repeated empty snapshots are not evidence
      // updates and must not grow the durable record's write rate.
      if (previous !== this.groups.size) {
        try { await this.publish([...this.groups]); }
        catch { this.outputComplete = false; }
      }
    })().finally(() => { this.pending = undefined; });
    return this.pending;
  }

  async start(): Promise<void> {
    await this.observe();
    const next = () => {
      if (this.stopped || this.child.exitCode !== null || this.child.signalCode !== null) return;
      this.timer = setTimeout(() => { void this.observe().then(next); }, DESCENDANT_POLL_MS);
    };
    next();
  }

  unexpectedExit(): void { this.ownershipComplete = false; this.outputComplete = false; }

  get ownershipConfirmed(): boolean { return this.observed && this.ownershipComplete; }

  signal(signal: NodeJS.Signals): void {
    for (const group of this.groups) {
      try { process.kill(-group, signal); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
    }
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    await this.pending;
  }

  async finish(): Promise<boolean> {
    await this.stop();
    this.signal("SIGKILL");
    await Promise.all([...this.groups].map(waitForProcessGroupStop));
    return this.ownershipConfirmed;
  }
}

/** Zombies cannot execute; an unknown/non-zombie state must remain live. */
export function hasLiveGroupMembers(snapshot: string, pgid: number): boolean {
  if (!snapshot.trim()) throw new Error("invalid managed Host process-group observation");
  let live = false;
  for (const line of snapshot.split("\n")) {
    if (!line.trim()) continue;
    const match = /^\s*(\d+)\s+(\S+)\s*$/.exec(line);
    if (!match) throw new Error("invalid managed Host process-group observation");
    if (Number(match[1]) === pgid && !match[2].startsWith("Z")) live = true;
  }
  return live;
}

/** Wait for absence or an all-zombie group, never for a fixed optimistic delay. */
export async function waitForProcessGroupStop(pgid: number): Promise<void> {
  const deadline = performance.now() + CLEANUP_DEADLINE_MS;
  while (true) {
    const remaining = Math.ceil(deadline - performance.now());
    if (remaining <= 0) throw new Error("managed Host process group did not stop before cleanup deadline");
    try { process.kill(-pgid, 0); }
    catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "ESRCH") return;
      // Darwin can return EPERM for an unreaped dead group. It is not proof
      // of death: require the same fresh state observation as a live group.
      if (code !== "EPERM") throw error;
    }
    // POSIX ps exposes only group/state, not argv or environment. Unlike PID
    // absence alone this also works with orphan zombies awaiting init's reap.
    const snapshot = await new Promise<string>((resolve, reject) => {
      execFile("ps", ["-A", "-o", "pgid=", "-o", "stat="],
        {encoding: "utf8", timeout: remaining, killSignal: "SIGKILL", maxBuffer: 1024 * 1024},
        (error, stdout) => error ? reject(new Error(performance.now() >= deadline
          ? "managed Host process group did not stop before cleanup deadline"
          : "managed Host process-group observation failed", {cause: error})) : resolve(stdout));
    });
    // A callback can arrive after the deadline even without an execFile error.
    // Late or empty output must not turn a timed-out observation into absence.
    if (performance.now() >= deadline) throw new Error("managed Host process group did not stop before cleanup deadline");
    if (!hasLiveGroupMembers(snapshot, pgid)) return;
    await delay(Math.min(10, Math.max(0, deadline - performance.now())));
  }
}
