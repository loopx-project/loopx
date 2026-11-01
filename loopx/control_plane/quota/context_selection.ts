/** Pure RFC 6901 selection over one captured JSON object. No state or admission. */
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";

export function capturedContextSelection(request: JsonObject): JsonObject {
  const context = requireJsonObject(request.context, "captured context");
  const selectors = request.select;
  if (!Array.isArray(selectors) || !selectors.length || selectors.some(value => typeof value !== "string"))
    throw new EffectRuntimeRequestError("context selection requires a nonempty array of JSON pointers");
  const depth = request.list_depth === undefined ? 1 : request.list_depth;
  if (typeof depth !== "number" || !Number.isInteger(depth) || depth < 1 || depth > 8
      || request.list_depth !== undefined && request.list_fields !== true)
    throw new EffectRuntimeRequestError("list-depth requires --list and an integer from 1 to 8");
  // Keep caller positions before canonical sorting/deduplication. Diagnostics
  // identify a failed address without echoing possibly sensitive keys or values.
  const argumentsByPointer = new Map<string, number>();
  (selectors as string[]).forEach((pointer, index) => {
    if (!argumentsByPointer.has(pointer)) argumentsByPointer.set(pointer, index + 1);
  });
  const pointers = [...argumentsByPointer.keys()].sort();
  const selections = pointers.map(pointer => {
    const argument = `--select argument ${argumentsByPointer.get(pointer)}`;
    if (pointer !== "" && !pointer.startsWith("/") || /~(?:[^01]|$)/.test(pointer))
      throw new EffectRuntimeRequestError(`context selection requires RFC 6901 pointers (${argument}; positions are 1-based)`,
        "context_selection_invalid_pointer");
    const tokens = pointer === "" ? [] : pointer.slice(1).split("/").map(token => token.replace(/~1/g, "/").replace(/~0/g, "~"));
    const path: (string | number)[] = [];
    let value: unknown = context;
    for (const token of tokens) {
      if (Array.isArray(value)) {
        if (!/^(0|[1-9][0-9]*)$/.test(token) || !Number.isSafeInteger(Number(token)) || Number(token) >= value.length)
          throw new EffectRuntimeRequestError(`context selection array index is missing or invalid (${argument}, token ${path.length + 1}; positions are 1-based)`,
            "context_selection_invalid_index");
        path.push(Number(token));
        value = value[Number(token)];
      } else if (value !== null && typeof value === "object" && Object.hasOwn(value, token)) {
        path.push(token);
        value = (value as JsonObject)[token];
      } else throw new EffectRuntimeRequestError(`context selection pointer does not exist (${argument}, token ${path.length + 1}; positions are 1-based)`,
        "context_selection_missing_pointer");
    }
    // Return the verified address, not a JS-number-coerced copy of the value.
    // The file adapter retains original JSON values and owns byte serialization.
    if (request.list_fields === true) {
      const children: {pointer: string; path: (string | number)[]}[] = [];
      const visit = (parent: unknown, address: string, parentPath: (string | number)[], remaining: number): void => {
        if (remaining === 0 || parent === null || typeof parent !== "object") return;
        for (const key of Object.keys(parent)) {
          const child = {pointer: address + "/" + key.replaceAll("~", "~0").replaceAll("/", "~1"),
            path: [...parentPath, Array.isArray(parent) ? Number(key) : key]};
          children.push(child);
          visit((parent as JsonObject)[key], child.pointer, child.path, remaining - 1);
        }
      };
      visit(value, pointer, path, depth);
      return {pointer, path, children};
    }
    return {pointer, path};
  });
  return {selections};
}
