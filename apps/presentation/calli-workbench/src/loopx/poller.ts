/**
 * Minimal polling loop with the reconnect semantics Calli's SSE consumers
 * expect: never resolve while healthy, throw on failure (the caller loops),
 * honor AbortSignal between ticks.
 */

export interface PollLoopOptions {
  signal: AbortSignal;
  intervalMs: number;
  backoffMaxMs: number;
  /** One poll. Throw to end the loop with that error (caller reconnects). */
  tick: () => Promise<void>;
  /** Called once after the first successful tick (SSE "open"). */
  onFirstTick?: () => void;
}

export function sleepAbort(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    }
    signal.addEventListener('abort', done, { once: true });
  });
}

export async function pollLoop(options: PollLoopOptions): Promise<void> {
  const { signal, intervalMs, backoffMaxMs, tick, onFirstTick } = options;
  let first = true;
  while (!signal.aborted) {
    await tick();
    if (first) {
      first = false;
      onFirstTick?.();
    }
    await sleepAbort(intervalMs, signal);
    // Failure inside tick() propagates as an exception (like a dead stream);
    // the caller's reconnect loop re-enters stream() and backoff is the
    // caller's. We only keep a local cap for slow-poll scenarios.
    void backoffMaxMs;
  }
}
