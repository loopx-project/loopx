import { readdir } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Server dependencies are not implicitly installed user agents (OAR bundles a Pi SDK). */
export function isWorkbenchDependency(command: string): boolean {
  try {
    const file = realpathSync(command);
    const app = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
    return [join(app, 'node_modules'), join(app, 'packages/daemon/node_modules')].some(directory => {
      try { return file.startsWith(realpathSync(directory) + sep); } catch { return false; }
    });
  } catch { return false; }
}

/** Resolve only a known directory setting; never include arbitrary instance environment in a handover. */
export function configDirectory(env: Record<string, string>, key: string, fallback: string[], cwd: string): string {
  const value = env[key] ?? process.env[key];
  return value ? resolve(cwd, value) : resolve(env.HOME ?? process.env.HOME ?? homedir(), ...fallback);
}

export function quoteArg(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/** Search only the adapter's session tree, without following symlinks or reading transcript bodies. */
export async function findSessionFile(root: string, id: string | null, depth: number, matches: (name: string, id: string) => boolean): Promise<string | null> {
  if (!id || !/^[a-z0-9_-]+$/i.test(id)) return null;
  let remaining = 20_000;
  const visit = async (dir: string, levels: number): Promise<string | null> => {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return null;
    }
    for (const entry of entries) {
      if (--remaining < 0) return null;
      const path = join(dir, entry.name);
      if (entry.isFile() && matches(entry.name, id)) return path;
      if (levels > 0 && entry.isDirectory()) {
        const found = await visit(path, levels - 1);
        if (found) return found;
      }
    }
    return null;
  };
  return visit(root, depth);
}
