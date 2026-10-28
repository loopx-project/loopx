/** Pure RFC 6901 selection over one captured JSON object. No state or admission. */
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";

export function capturedContextSelection(request: JsonObject): JsonObject {
  const context = requireJsonObject(request.context, "captured context");
  const selectors = request.select;
  if (!Array.isArray(selectors) || !selectors.length || selectors.some(value => typeof value !== "string"))
    throw new EffectRuntimeRequestError("context selection requires a nonempty array of JSON pointers");
  const pointers = [...new Set(selectors as string[])].sort();
  const selections = pointers.map(pointer => {
    if (pointer !== "" && !pointer.startsWith("/") || /~(?:[^01]|$)/.test(pointer))
      throw new EffectRuntimeRequestError("context selection requires RFC 6901 pointers");
    const tokens = pointer === "" ? [] : pointer.slice(1).split("/").map(token => token.replace(/~1/g, "/").replace(/~0/g, "~"));
    const path: (string | number)[] = [];
    let value: unknown = context;
    for (const token of tokens) {
      if (Array.isArray(value)) {
        if (!/^(0|[1-9][0-9]*)$/.test(token) || !Number.isSafeInteger(Number(token)) || Number(token) >= value.length)
          throw new EffectRuntimeRequestError("context selection array index is missing or invalid");
        path.push(Number(token));
        value = value[Number(token)];
      } else if (value !== null && typeof value === "object" && Object.hasOwn(value, token)) {
        path.push(token);
        value = (value as JsonObject)[token];
      } else throw new EffectRuntimeRequestError("context selection pointer does not exist");
    }
    // Return the verified address, not a JS-number-coerced copy of the value.
    // The file adapter retains original JSON values and owns byte serialization.
    return {pointer, path};
  });
  return {selections};
}
