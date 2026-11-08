/** Local-only path eligibility. Paths never enter an outgoing payload. */
import { realpathSync } from "node:fs";
import { homedir, platform, tmpdir } from "node:os";
import { dirname, isAbsolute, join, parse, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

type Env = Record<string, string | undefined>;

function physicalPath(value: string): string {
  let parent = resolve(value);
  const missing: string[] = [];
  // A first-use state file need not exist yet. Resolve its nearest existing
  // ancestor, including symlinked homes and macOS /var -> /private/var.
  while (true) {
    try { return join(realpathSync(parent), ...missing); }
    catch (error) {
      if (!["ENOENT", "ENOTDIR"].includes(String((error as NodeJS.ErrnoException).code))) throw error;
      const next = dirname(parent);
      if (next === parent) return resolve(value);
      missing.unshift(parent.slice(next.length).replace(/^[/\\]+/, ""));
      parent = next;
    }
  }
}

export function temporaryUsageEnvironment(path: string, env: Env): boolean {
  const windows = platform() === "win32";
  const home = env.HOME || env.USERPROFILE || homedir();
  const roots = [tmpdir(), env.TMPDIR, env.TEMP, env.TMP,
    ...(windows ? [join(env.SystemRoot || env.WINDIR || "C:\\Windows", "Temp"),
      join(env.LOCALAPPDATA || join(home, "AppData", "Local"), "Temp")]
      : ["/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp",
        ...(platform() === "darwin" ? ["/var/folders", "/private/var/folders"] : [])])];
  const candidates = [path, home, env.USERPROFILE, env.CODEX_HOME,
    env.XDG_STATE_HOME, env.XDG_DATA_HOME, env.XDG_CONFIG_HOME,
    fileURLToPath(import.meta.url)];
  // Unreadable/looping routes throw into the existing unavailable-settings
  // boundary; do not misclassify an unknown route as a temporary one.
  const normalize = (value: string) => windows ? value.toLowerCase() : value;
  const directories = [...new Set(roots.filter((root): root is string => !!root && isAbsolute(root))
    .map(physicalPath).filter(root => root !== parse(root).root).map(normalize))];
  return candidates.filter((value): value is string => !!value).some(value => {
    const candidate = normalize(physicalPath(value));
    return directories.some(root => {
      const child = relative(root, candidate);
      return child === "" || (!isAbsolute(child) && child !== ".." && !child.startsWith(".." + sep));
    });
  });
}
