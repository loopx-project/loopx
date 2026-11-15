// Accepts the RAH_*, LIMI_* and CALLI_* names from before the rename to LooChat, the way the
// daemon does (adoptLegacyEnv in src/config.ts). Imported first, so the other modules see the new names.
const renamed = { LIMI_SOCKET: 'LOOCHAT_TEAM_SOCKET', LIMI_KEY: 'LOOCHAT_TEAM_KEY' };
for (const [key, value] of Object.entries(process.env)) {
  const name = renamed[key] ?? (/^(RAH|LIMI|CALLI)_/.test(key) ? key.replace(/^(RAH|LIMI|CALLI)_/, 'LOOCHAT_') : null);
  if (name && process.env[name] === undefined) process.env[name] = value;
}
