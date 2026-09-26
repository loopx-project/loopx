/** Read the two project-registry wire forms used by the Python registry codec. */

import { createHash } from 'node:crypto'
import { TextDecoder } from 'node:util'

export class ProjectRegistryWireError extends Error {}

interface JsonNode {
  readonly value: unknown
  readonly canonical: string
  readonly items?: readonly JsonNode[]
}

function fail(message = 'strict project registry is invalid'): never {
  throw new ProjectRegistryWireError(message)
}

function pythonNumber(raw: string): string {
  if (!/[.eE]/u.test(raw)) return BigInt(raw).toString()
  const number = Number(raw)
  if (!Number.isFinite(number)) fail('strict project registry contains non-finite number')
  if (Object.is(number, -0)) return '-0.0'
  if (number === 0) return '0.0'

  // Python json.dumps uses the shortest binary64 representation, switching
  // to exponent notation below 1e-4 and at 1e16. Keep its two-digit exponent.
  const sign = number < 0 ? '-' : ''
  const [coefficient, exponentText] = Math.abs(number).toExponential().split('e')
  if (coefficient === undefined || exponentText === undefined) fail()
  const exponent = Number(exponentText)
  if (exponent < -4 || exponent >= 16) {
    const magnitude = String(Math.abs(exponent)).padStart(2, '0')
    return `${sign}${coefficient}e${exponent < 0 ? '-' : '+'}${magnitude}`
  }
  const digits = coefficient.replace('.', '')
  const decimalAt = exponent + 1
  if (decimalAt <= 0) return `${sign}0.${'0'.repeat(-decimalAt)}${digits}`
  if (decimalAt >= digits.length) {
    return `${sign}${digits}${'0'.repeat(decimalAt - digits.length)}.0`
  }
  return `${sign}${digits.slice(0, decimalAt)}.${digits.slice(decimalAt)}`
}

function compareCodePoints(left: string, right: string): number {
  const a = Array.from(left, character => character.codePointAt(0) as number)
  const b = Array.from(right, character => character.codePointAt(0) as number)
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    if (a[index] !== b[index]) return (a[index] as number) - (b[index] as number)
  }
  return a.length - b.length
}

/** Parse strict JSON without losing duplicate keys, large integers, or float kind. */
function parseStrictJson(text: string): JsonNode {
  let cursor = 0
  const whitespace = () => {
    while (/[\t\n\r ]/u.test(text[cursor] ?? '')) cursor += 1
  }
  const string = (): string => {
    if (text[cursor] !== '"') fail()
    const start = cursor
    cursor += 1
    while (cursor < text.length) {
      if (text[cursor] === '\\') {
        cursor += 2
        continue
      }
      if (text[cursor] === '"') {
        cursor += 1
        const value: unknown = JSON.parse(text.slice(start, cursor))
        if (typeof value !== 'string') fail()
        return value
      }
      cursor += 1
    }
    return fail()
  }
  const value = (): JsonNode => {
    whitespace()
    const token = text[cursor]
    if (token === '"') {
      const parsed = string()
      return { value: parsed, canonical: JSON.stringify(parsed) }
    }
    if (token === '[') {
      cursor += 1
      whitespace()
      const items: JsonNode[] = []
      if (text[cursor] !== ']') {
        while (true) {
          items.push(value())
          whitespace()
          if (text[cursor] !== ',') break
          cursor += 1
        }
      }
      if (text[cursor] !== ']') fail()
      cursor += 1
      return {
        value: items.map(item => item.value),
        canonical: `[${items.map(item => item.canonical).join(',')}]`,
        items,
      }
    }
    if (token === '{') {
      cursor += 1
      whitespace()
      const entries: Array<readonly [string, JsonNode]> = []
      const object: Record<string, unknown> = Object.create(null) as Record<string, unknown>
      if (text[cursor] !== '}') {
        while (true) {
          const key = string()
          if (Object.hasOwn(object, key)) fail('strict project registry contains duplicate key')
          whitespace()
          if (text[cursor] !== ':') fail()
          cursor += 1
          const child = value()
          object[key] = child.value
          entries.push([key, child])
          whitespace()
          if (text[cursor] !== ',') break
          cursor += 1
          whitespace()
        }
      }
      if (text[cursor] !== '}') fail()
      cursor += 1
      entries.sort(([left], [right]) => compareCodePoints(left, right))
      return {
        value: object,
        canonical: `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${child.canonical}`).join(',')}}`,
      }
    }
    for (const [literal, parsed] of [
      ['true', true], ['false', false], ['null', null],
    ] as const) {
      if (text.startsWith(literal, cursor)) {
        cursor += literal.length
        return { value: parsed, canonical: literal }
      }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/u.exec(text.slice(cursor))
    if (match === null) fail()
    cursor += match[0].length
    return { value: Number(match[0]), canonical: pythonNumber(match[0]) }
  }

  const root = value()
  whitespace()
  if (cursor !== text.length) fail()
  return root
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function decodeGoalBarProjectRegistry(raw: Buffer): unknown {
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(raw)
  } catch {
    return fail('project registry is invalid UTF-8')
  }
  if (text.trimStart().startsWith('{')) {
    try {
      return JSON.parse(text) as unknown
    } catch {
      return fail('project registry is invalid')
    }
  }
  let root: JsonNode
  try {
    root = parseStrictJson(text)
  } catch (error: unknown) {
    if (error instanceof ProjectRegistryWireError) throw error
    return fail('strict project registry is invalid')
  }
  if (!Array.isArray(root.value) || root.items?.length !== 2) fail()
  const [header, payload] = root.value as unknown[]
  if (!isRecord(header) || !isRecord(payload)) fail()
  if (Object.keys(header).sort().join(',')
    !== 'minimum_writer_protocol,payload_sha256,schema_version') fail()
  if (header.schema_version !== 'loopx_project_registry_envelope_v1'
    && header.schema_version !== 'loopx_project_registry_envelope_v2') {
    fail('strict project registry schema_version is unsupported')
  }
  if (typeof header.minimum_writer_protocol !== 'string'
    || !header.minimum_writer_protocol) fail()
  if (typeof header.payload_sha256 !== 'string'
    || !/^sha256:[0-9a-f]{64}$/u.test(header.payload_sha256)) fail()
  const canonicalPayload = root.items[1]?.canonical
  if (canonicalPayload === undefined) fail()
  const digest = `sha256:${createHash('sha256').update(canonicalPayload).digest('hex')}`
  if (header.payload_sha256 !== digest) {
    fail('strict project registry payload digest does not match')
  }
  if (header.schema_version === 'loopx_project_registry_envelope_v2'
    || payload.profile_id === 'source_session_v1') {
    fail('lifecycle-only project registry is not a Goal runtime registry')
  }
  return payload
}
