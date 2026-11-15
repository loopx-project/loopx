// Accepts the RAH_* and LIMI_* names from before the rename to Calli, the way the daemon does
// (adoptLegacyEnv in src/config.ts). Imported first, so the other modules see the new names.
const renamed = { LIMI_SOCKET: 'CALLI_TEAM_SOCKET', LIMI_KEY: 'CALLI_TEAM_KEY' };
for (const [key, value] of Object.entries(process.env)) {
  const name = renamed[key] ?? (/^(RAH|LIMI)_/.test(key) ? key.replace(/^(RAH|LIMI)_/, 'CALLI_') : null);
  if (name && process.env[name] === undefined) process.env[name] = value;
}
