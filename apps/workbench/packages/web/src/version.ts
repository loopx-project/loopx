/**
 * Whether the daemon now serves a newer build of this page than the one running. A page left open
 * (or a phone app resumed from the background) keeps running the code it loaded; nothing tells it
 * that a deploy happened unless it asks.
 */

/** The entry script an index.html names. Pure, so it can be tested directly. */
export function entryScriptIn(html: string): string | null {
  return /<script\b[^>]*\bsrc="([^"]+)"/.exec(html)?.[1] ?? null;
}

/** The entry script this page was loaded with. */
function loadedEntryScript(): string | null {
  return document.querySelector('script[src]')?.getAttribute('src') ?? null;
}

/** True when index.html, fetched fresh, names another entry script than the one running. */
export async function newerBuildAvailable(): Promise<boolean> {
  const loaded = loadedEntryScript();
  if (!loaded) return false;
  try {
    const res = await fetch('/', { cache: 'no-store' });
    if (!res.ok) return false;
    const served = entryScriptIn(await res.text());
    return served !== null && served !== loaded;
  } catch {
    return false; // offline or mid-restart: ask again next time
  }
}
