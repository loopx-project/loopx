import { lstatSync, mkdirSync, type Stats } from "node:fs";
import { enableCompileCache, getCompileCacheDir } from "node:module";
import { dirname, isAbsolute, join } from "node:path";

function privateDirectory(stat: Stats): boolean {
  return stat.isDirectory() && !stat.isSymbolicLink() &&
    (typeof process.getuid !== "function" ||
      (stat.uid === process.getuid() && (stat.mode & 0o077) === 0));
}

// Preload before the server's static imports. This caches compilation, never
// requests, source fingerprints, authority reads, or decisions. Node validates
// source contents and keeps caches from different Node versions separate.
function enableRuntimeCompileCache(): void {
  if (process.env.NODE_DISABLE_COMPILE_CACHE === "1" ||
      process.env.NODE_V8_COVERAGE || getCompileCacheDir() !== undefined) return;
  const index = process.argv.indexOf("--info");
  const info = index < 0 ? undefined : process.argv[index + 1];
  if (!info || !isAbsolute(info)) return;
  try {
    // The launcher owns this private namespace. Never repair permissions on an
    // existing cache, follow a cache symlink, or create a second runtime root.
    const root = dirname(info);
    if (!privateDirectory(lstatSync(root))) return;
    const cache = join(root, "compile-cache");
    mkdirSync(cache, { recursive: true, mode: 0o700 });
    if (!privateDirectory(lstatSync(cache))) return;
    enableCompileCache(cache);
  } catch {
    // An unavailable cache is only a missed optimization. The original server
    // still loads, and its configuration/authentication failures remain fatal.
  }
}

enableRuntimeCompileCache();
