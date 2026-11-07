/** Pure RFC 6901 selection over one captured JSON object. No state or admission. */
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";

// Local selection diagnostics: counts only, never source keys or values.
class SelectionLookupError extends EffectRuntimeRequestError {
  readonly argumentIndex: number;
  readonly parentTokenCount: number;

  constructor(message: string, code: string, argumentIndex: number, parentTokenCount: number) {
    super(message, code);
    this.argumentIndex = argumentIndex;
    this.parentTokenCount = parentTokenCount;
  }
}

/** Initial navigation may share a complete parent instead of repeating every
 * child address. The selected facts stay exact; unselected children prevent
 * sharing. Explicit selectors and discovery keep their original addresses. */
function coalescedInitialPointers(context: JsonObject, pointers: string[]): string[] {
  if (pointers.length < 2) return pointers;
  const selected = new Set(pointers), positions = new Map<string, number>(), visited = new Set<string>();
  pointers.forEach((pointer, position) => {
    if (!positions.has(pointer)) positions.set(pointer, position);
    for (let index = 0; index < pointer.length; index++) {
      if (pointer[index] !== "/") continue;
      const parent = pointer.slice(0, index);
      if (!positions.has(parent)) positions.set(parent, position);
    }
  });
  const visit = (value: unknown, pointer: string): {complete: boolean; pointers: string[]} => {
    if (selected.has(pointer)) {
      visited.add(pointer);
      return {complete: true, pointers: [pointer]};
    }
    if (!positions.has(pointer) || value === null || typeof value !== "object")
      return {complete: false, pointers: []};
    const children = Object.entries(value).map(([key, child]) =>
      visit(child, pointer + "/" + key.replaceAll("~", "~0").replaceAll("/", "~1")));
    const fields = children.flatMap(child => child.pointers);
    // A whole array replays its original element order. Keep prioritized
    // elements separate when merging would undo the owner's navigation order;
    // propagate that boundary so an ancestor cannot merge them back either.
    const preservesOrder = !Array.isArray(value) || fields.every((field, index) =>
      index === 0 || positions.get(fields[index - 1])! < positions.get(field)!);
    const complete = children.length > 0 && children.every(child => child.complete) && preservesOrder;
    // A single child needs no wrapper; grouping it can increase output bytes.
    return {complete, pointers: complete && fields.length > 1 ? [pointer] : fields};
  };
  const result = visit(context, "").pointers;
  // Keep malformed/missing or overlapping addresses for the ordinary validator;
  // an optimization must not turn a rejected read into a successful subset.
  // A merged parent takes its first selected child's navigation position.
  return visited.size === selected.size
    ? result.sort((left, right) => positions.get(left)! - positions.get(right)!) : pointers;
}

export function capturedContextSelection(request: JsonObject, initialPointers: string[] = []): JsonObject {
  if (request.recover_selection_errors !== undefined && typeof request.recover_selection_errors !== "boolean")
    throw new EffectRuntimeRequestError("recover-selection-errors must be boolean");
  if (request.preserve_selection_order !== undefined && typeof request.preserve_selection_order !== "boolean")
    throw new EffectRuntimeRequestError("preserve-selection-order must be boolean");
  try {
    return resolveSelection(request, initialPointers);
  } catch (error) {
    if (!(error instanceof SelectionLookupError) || request.recover_selection_errors !== true) throw error;
    // No partial selections survive. The IO adapter can bind a discovery
    // command to this verified prefix of the caller's original argument.
    return {rejected_selection: {message: error.message, code: error.code,
      argument_index: error.argumentIndex, parent_token_count: error.parentTokenCount}};
  }
}

function resolveSelection(request: JsonObject, initialPointers: string[]): JsonObject {
  const context = requireJsonObject(request.context, "captured context");
  if (request.initial_read !== undefined && typeof request.initial_read !== "boolean")
    throw new EffectRuntimeRequestError("initial-read must be boolean");
  if (request.initial_read === true && (request.select !== undefined || request.list_fields === true || request.list_depth !== undefined))
    throw new EffectRuntimeRequestError("initial-read cannot be combined with select or list");
  const initial = request.initial_read === true ? coalescedInitialPointers(context, initialPointers) : [];
  const selectors = request.initial_read === true ? initial : request.select;
  if (request.initial_read === true && !(selectors as string[]).length)
    throw new EffectRuntimeRequestError("initial-read is unavailable for this capture schema; use generic discovery");
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
  // Initial navigation follows the owner's priority, including merged parents.
  // Ordinary explicit selectors retain their canonical order.
  const pointers = [...argumentsByPointer.keys()];
  if (request.initial_read !== true) pointers.sort();
  const selections = pointers.map(pointer => {
    const argument = `--select argument ${argumentsByPointer.get(pointer)}`;
    if (pointer !== "" && !pointer.startsWith("/") || /~(?:[^01]|$)/.test(pointer))
      throw new EffectRuntimeRequestError(`context selection requires RFC 6901 pointers (${argument}; positions are 1-based)`,
        "context_selection_invalid_pointer");
    const tokens = pointer === "" ? [] : pointer.slice(1).split("/").map(token => token.replace(/~1/g, "/").replace(/~0/g, "~"));
    const path: (string | number)[] = [];
    let value: unknown = context;
    let parentTokenCount = 0;
    for (const token of tokens) {
      if (value !== null && typeof value === "object") parentTokenCount = path.length;
      if (Array.isArray(value)) {
        if (!/^(0|[1-9][0-9]*)$/.test(token) || !Number.isSafeInteger(Number(token)) || Number(token) >= value.length)
          throw new SelectionLookupError(`context selection array index is missing or invalid (${argument}, token ${path.length + 1}; positions are 1-based)`,
            "context_selection_invalid_index", argumentsByPointer.get(pointer)!, parentTokenCount);
        path.push(Number(token));
        value = value[Number(token)];
      } else if (value !== null && typeof value === "object" && Object.hasOwn(value, token)) {
        path.push(token);
        value = (value as JsonObject)[token];
      } else throw new SelectionLookupError(`context selection pointer does not exist (${argument}, token ${path.length + 1}; positions are 1-based)`,
        "context_selection_missing_pointer", argumentsByPointer.get(pointer)!, parentTokenCount);
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
  // A pinned view may record a different rendering order. Validate the same
  // canonical batch first so rejected reads keep their original diagnostics.
  if (request.preserve_selection_order === true)
    selections.sort((left, right) => argumentsByPointer.get(left.pointer)! - argumentsByPointer.get(right.pointer)!);
  // Discovery follows the validated, deduplicated selection. Repeating the root
  // cannot drop its navigation; ordinary explicit reads need no initial plan.
  const navigation = request.list_fields === true && pointers.length === 1 && pointers[0] === ""
    ? coalescedInitialPointers(context, initialPointers) : [];
  return {selections, ...(navigation.length ? {initial_read_pointers: navigation} : {})};
}
