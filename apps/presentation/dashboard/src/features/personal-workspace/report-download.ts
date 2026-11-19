/** Keep a saved readback recognizable without treating its ref as a filesystem path. */
export function reportDownloadName(ref: string, sha256: string): string {
  const leaf = ref.split(/[\\/]/).pop() || "report";
  const extension = /\.(md|markdown|json|txt)$/i.exec(leaf)?.[1].toLowerCase() || "txt";
  const stem = leaf.replace(/\.[^.]*$/, "").normalize("NFC")
    .replace(/[^\p{L}\p{N}_-]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "report";
  const version = /^[a-f0-9]{64}$/i.test(sha256) ? sha256.slice(0, 12).toLowerCase() : "readback";
  return `report-${stem}-${version}.${extension}`;
}

/** Save exactly the displayed UTF-8 source; no network request or owner mutation. */
export function downloadReport(ref: string, sha256: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"}));
  const link = document.createElement("a");
  try {
    link.href = url;
    link.download = reportDownloadName(ref, sha256);
    document.body.append(link);
    link.click();
  } finally {
    link.remove();
    // Allow the browser to consume the URL before releasing this bounded copy.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
