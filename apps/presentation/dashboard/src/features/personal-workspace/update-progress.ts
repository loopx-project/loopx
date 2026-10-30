// Presentation-only observations. The updater remains the authority for bytes,
// phase and outcome; missing totals never become a fabricated percentage.
export type TransferSample = {
  phase: string; received: number | null; startedAt: number; changedAt: number;
  sampledAt: number; bytesPerSecond: number | null;
};

export function observeTransfer(previous: TransferSample | null, phase: string, bytes: number | undefined, now: number): TransferSample {
  const received = typeof bytes === "number" && Number.isFinite(bytes) && bytes >= 0 ? bytes : null;
  if (!previous || previous.phase !== phase || (received !== null && previous.received !== null && received < previous.received)) {
    return { phase, received, startedAt: now, changedAt: now, sampledAt: now, bytesPerSecond: null };
  }
  if (received === previous.received) return previous;
  const elapsed = (now - previous.sampledAt) / 1000;
  return { ...previous, received, sampledAt: now, changedAt: now,
    bytesPerSecond: received !== null && previous.received !== null && elapsed > 0 ? (received - previous.received) / elapsed : null };
}

export function transferText(sample: TransferSample, total: number | undefined, now: number, zh: boolean): string {
  const amount = sample.received === null ? (zh ? "等待下载数据" : "Waiting for download data") : `${(sample.received / 1048576).toFixed(1)} MiB`;
  const length = typeof total === "number" && total > 0 ? `${(total / 1048576).toFixed(1)} MiB` : (zh ? "总量未知" : "size unknown");
  const rate = sample.bytesPerSecond === null ? "" : ` · ${((now - sample.changedAt >= 5_000 ? 0 : sample.bytesPerSecond) / 1024).toFixed(0)} KiB/s`;
  const elapsed = Math.max(0, Math.floor((now - sample.startedAt) / 1000));
  const waiting = now - sample.changedAt >= 10_000 ? (zh ? " · 10 秒内无新进展" : " · no new progress for 10s") : "";
  return `${amount} / ${length}${rate} · ${elapsed}s${waiting}`;
}
