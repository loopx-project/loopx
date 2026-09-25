// Cloudflare Worker entry. Stores only the validated ping fields; the client
// IP, user agent and Cloudflare request metadata are never read or persisted.
import { handle, purge, utcDay } from "./collector.js";

export default {
  async fetch(request, env) {
    return handle(request, env.DB);
  },
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(purge(env.DB, utcDay(new Date())));
  },
};
