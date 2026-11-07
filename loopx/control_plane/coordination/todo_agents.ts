import {AuthorityStoreProtocolError} from "./authority_store_codec.ts";
import {compactPythonWhitespace} from "./python_whitespace.ts";
export {compactPythonWhitespace, hasPythonNonWhitespaceText, stripPythonWhitespace} from "./python_whitespace.ts";

export function normalizeTodoAgent(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new AuthorityStoreProtocolError(`${label} must be a public-safe agent id`);
  }
  // Trim and collapse every Python-equivalent whitespace run to one "-" so ids
  // typed with any Python whitespace (including U+0085 NEL, U+001C..U+001F,
  // tabs, and NBSP) fold exactly like the Python kernel's compact_todo_text path
  // (loopx/control_plane/todos/contract.py normalize_todo_claimed_by).
  const candidate = compactPythonWhitespace(value).toLowerCase().replaceAll(" ", "-");
  if (!/^[a-z][a-z0-9_.:@-]{0,79}$/u.test(candidate)) {
    throw new AuthorityStoreProtocolError(`${label} must be a public-safe agent id`);
  }
  return candidate;
}

export function normalizeRegisteredTodoAgents(value: readonly string[]): string[] {
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError("registered_agents must be an array");
  }
  const normalized = value.map((agent, index) =>
    normalizeTodoAgent(agent, `registered_agents[${index}]`)
  );
  if (new Set(normalized).size !== normalized.length) {
    throw new AuthorityStoreProtocolError(
      "registered_agents must contain unique public-safe agent ids",
    );
  }
  return normalized;
}
