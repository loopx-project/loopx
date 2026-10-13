/** Canonical grammar only: existing callers retain normalization and error policy. */
export function isCanonicalTodoId(value: string): boolean {
  return /^todo_[a-z0-9_-]{3,64}$/u.test(value);
}
