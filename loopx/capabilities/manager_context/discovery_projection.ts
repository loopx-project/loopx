import type {JsonObject} from "../../control_plane/effect_program.ts";
import {requireJsonObject, requireInteger} from "../../control_plane/runtime_decode.ts";
import {EffectRuntimeRequestError} from "../../control_plane/effect_runtime_errors.ts";

/** A source-qualified directory, never topic ownership or execution authority. */
export function projectAgentDiscovery(input: JsonObject): JsonObject {
  const args = requireJsonObject(input.arguments, "discovery arguments");
  const limit = requireInteger(args.limit ?? 8, "discovery limit");
  if (limit < 1 || limit > 12 || !Array.isArray(input.sources) || !Array.isArray(input.pages)) {
    throw new EffectRuntimeRequestError("invalid discovery projection bounds");
  }
  const sources = input.sources.map(value => requireJsonObject(value, "discovery source"));
  const pages = input.pages.map(value => requireJsonObject(value, "discovery page"));
  const usable = sources.map((_, index) => {
    const page = pages[index];
    return page?.ok === true && Array.isArray(page.rows)
      && Number.isSafeInteger(page.matched) && Number(page.matched) >= page.rows.length;
  });
  const counts = sources.map(() => 0);
  const rows: JsonObject[] = [];
  // A large local registry cannot crowd every other host out of the first page.
  for (let rank = 0; rank < limit && rows.length < limit; rank++) {
    sources.forEach((source, index) => {
      const candidates = usable[index] ? pages[index].rows as JsonObject[] : [];
      if (rows.length < limit && rank < candidates.length) {
        rows.push({...requireJsonObject(candidates[rank], "discovery row"),
          source_id: source.source_id, source_host: source.source_host});
        counts[index]++;
      }
    });
  }
  const nextReads: JsonObject[] = [];
  const sourcePages = sources.map((source, index) => {
    const page = pages[index];
    const known = usable[index];
    const more = !known || counts[index] < Number(page.matched);
    const read = {...args, source_id: source.source_id, offset: counts[index]};
    if (more) nextReads.push(read);
    return {...source, status: known ? "read" : page ? "unavailable" : "not_read",
      matched: known ? page.matched : null, included: counts[index],
      unknown: !known || page.unknown === true, source: page?.source ?? null,
      gaps: page?.gaps ?? [], error: page?.error ?? null,
      reason_code: page?.reason_code ?? null, reason: page?.reason ?? null,
      ...(more ? {next_read: read} : {})};
  });
  return {ok: true, view: "agents", rows, included: rows.length,
    matched: usable.every(Boolean) ? pages.reduce((sum, page) => sum + Number(page.matched), 0) : null,
    unknown: sourcePages.some(page => page.unknown), source_pages: sourcePages,
    next_reads: nextReads,
    note: "Local and SSH sources have equal discovery scope. Follow next_reads for remaining source pages; "
      + "copy each row's source_id for details. This searches registered identities and responsibilities, "
      + "not Goal requirements or Todo text. A zero match does not resolve shorthand: browse registrations "
      + "and read relevant source-qualified Todos. Unread or failed sources remain unknown."};
}
