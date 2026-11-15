#!/usr/bin/env node
// Stands in for agent-browser: reports the arguments and the environment Calli gave it.
const env = process.env;
console.log(JSON.stringify({ args: process.argv.slice(2), cdp: env.AGENT_BROWSER_CDP, boundaries: env.AGENT_BROWSER_CONTENT_BOUNDARIES,
  session: env.AGENT_BROWSER_SESSION, idle: env.AGENT_BROWSER_IDLE_TIMEOUT_MS }));
process.exit(Number(env.FAKE_AGENT_BROWSER_EXIT || 0));
