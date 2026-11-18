import type { UserQuestion } from './types.js';

/** Codex and zcode expose native question lists, with different option value conventions. */
export function nativeQuestions(questions: any): UserQuestion[] {
  if (!Array.isArray(questions)) return [];
  return questions.filter(q => typeof q?.question === 'string').map((q, i) => ({
    id: String(q.id ?? i), question: q.question, header: q.header,
    options: (Array.isArray(q.options) ? q.options : []).filter((o: any) => typeof o?.label === 'string').map((o: any) => ({ value: String(o.value ?? o.label), label: o.label, description: o.description })),
    multiple: q.multiSelect === true, freeText: true,
  }));
}

/** Keep schema field identities intact; companion custom-answer fields remain separate fields. */
export function formQuestions(schema: any, message: string): UserQuestion[] | null {
  if (schema?.type !== 'object' || !schema.properties) return null;
  const result: UserQuestion[] = [];
  for (const [id, field] of Object.entries(schema.properties) as Array<[string, any]>) {
    if (field.type !== 'string' && field.type !== 'array' && field.type !== 'boolean') return null;
    const choices = field.type === 'array' ? field.items : field;
    const enums = choices?.oneOf ?? choices?.anyOf;
    const options = field.type === 'boolean' ? [{ value: 'true', label: '是' }, { value: 'false', label: '否' }]
      : Array.isArray(enums) ? enums.map((o: any) => ({ value: String(o.const), label: String(o.title ?? o.const), description: o.description }))
      : Array.isArray(choices?.enum) ? choices.enum.map((v: string) => ({ value: String(v), label: String(v) })) : [];
    result.push({ id, question: field.description || field.title || message, header: field.title,
      options, multiple: field.type === 'array', freeText: field.type === 'string' && !options.length,
      required: (schema.required ?? []).includes(id) });
  }
  return result;
}
