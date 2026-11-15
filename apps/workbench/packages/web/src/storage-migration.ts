/**
 * Before the rename to Calli this app kept its keys under `rah.`, `rah_`, `rah:` and `limi.`. Each
 * one is copied once to its `calli` name, unless that is set already, so the saved token, drafts
 * and preferences carry over. The old keys stay, for a version from before to read. Imported
 * before anything else, since some modules read their keys when they load.
 */
const PREFIXES: [string, string][] = [['rah.', 'calli.'], ['rah_', 'calli_'], ['rah:', 'calli:'], ['limi.', 'calli.']];
const DONE = 'calli.storageMigrated';

export function migrateStorage(storage: Storage): void {
  // Only once: a key removed since, like the token on signing out, must not come back.
  if (storage.getItem(DONE)) return;
  const keys = Array.from({ length: storage.length }, (_, i) => storage.key(i)).filter((k): k is string => k !== null);
  for (const key of keys) {
    const prefix = PREFIXES.find(([from]) => key.startsWith(from));
    if (!prefix) continue;
    const next = prefix[1] + key.slice(prefix[0].length);
    const value = storage.getItem(key);
    if (value !== null && storage.getItem(next) === null) storage.setItem(next, value);
  }
  storage.setItem(DONE, '1');
}

try { migrateStorage(localStorage); } catch { /* storage unavailable: nothing to carry over */ }
