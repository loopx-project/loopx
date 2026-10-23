/** Browser-safe display projection for benchmark-toolkit's existing analysis.
 * It never mutates, classifies or adds an Explore record. */
import {parseExploreFindingSections, type ExploreFindingSections} from '../presentation/explore_timeline.ts';
import type {ExploreHistoryEvent} from '../presentation/explore_history.ts';
import type {TrajectoryDisplay, TrajectoryDisplayField} from './trajectory_assessment_types.ts';

export function trajectoryDisplayFields(events: readonly ExploreHistoryEvent[]): TrajectoryDisplayField[] {
  const fields: TrajectoryDisplayField[] = [];
  for (const event of events) {
    const add = (field: string, source_text: unknown) => {
      if (typeof source_text === 'string' && source_text.trim()) fields.push({event_id: event.event_id, field, source_text});
    };
    add('title', event.title); add('blocked_reason', event.blocked_reason);
    const sections = parseExploreFindingSections(event.summary);
    if (!sections) add('summary', event.summary);
    else {
      add('applicability', sections.applicability); add('observation', sections.observation);
      if (!sections.decision) add('interpretation', sections.interpretation);
      else for (const key of ['changed', 'stopped', 'retained'] as const) {
        sections.decision[key].forEach((text, index) => add(`${key}.${index}`, text));
      }
    }
    add('execution.change_reason', event.execution_observation?.change_reason);
  }
  return fields;
}

/** Same normalized, ordered event bytes on the server and WebCrypto reader.
 * Includes identities, statuses, versions, measurements and references, even
 * though only narrative fields are translatable. A correction invalidates it. */
export const trajectoryDisplaySnapshotText = (events: readonly ExploreHistoryEvent[], goalId: string, sourceNamespace: string) =>
  JSON.stringify([sourceNamespace, goalId, events]);

/** Protect literal code, links, numeric expressions and technical identifiers.
 * This checks exact-token integrity, not translation quality or entailment. */
export function maskTrajectoryDisplayText(text: string) {
  const protectedText: string[] = [];
  const masked = text.replace(/⟪LX\d+⟫|`[^`]*`|https?:\/\/[^\s<>]+|\b[a-fA-F0-9]{40,64}\b|[\w./:#%+−-]*\d[\w./:#%+−-]*(?:\s+(?:ms|μs|ns|s|sec|min|h|KiB|MiB|GiB|KB|MB|GB|TB|tokens|USD|CNY)\b)?|\p{Number}+|\b[A-Za-z]+(?:[_-][A-Za-z]+)+\b|\b[a-z]+[A-Z]\w*\b|[+−=<>≤≥-]/gu, literal => {
    protectedText.push(literal); return `⟪LX${protectedText.length - 1}⟫`;
  });
  return {masked, literals: protectedText, restore(translated: unknown): string | null {
    if (typeof translated !== 'string' || !translated.trim() || translated.includes('\0')) return null;
    const tokens = translated.match(/⟪LX\d+⟫/g) ?? [];
    if (tokens.length !== protectedText.length || protectedText.some((_, i) => tokens.filter(t => t === `⟪LX${i}⟫`).length !== 1)) return null;
    // New numeric/code/link tokens outside the protected spans are not a
    // faithful presentation of this field; retain its original instead.
    const rest = translated.replace(/⟪LX\d+⟫/g, '');
    if (/\p{Number}|`|https?:\/\/|[+−=<>≤≥-]/u.test(rest)) return null;
    return translated.replace(/⟪LX(\d+)⟫/g, (_, i: string) => protectedText[Number(i)]!);
  }};
}

/** Apply only a qualified exact-snapshot artifact; missing rows keep originals.
 * Canonical events remain separately reachable through the original view. */
export function projectTrajectoryDisplay(events: readonly ExploreHistoryEvent[], display: TrajectoryDisplay | undefined,
  snapshotKey: string, language: string) {
  const valid = display && snapshotKey && display.snapshot_key === snapshotKey && display.language === language;
  if (!valid) return {events, sections: new Map<string, ExploreFindingSections | null>()};
  const rows = new Map(display.fields.map(row => [JSON.stringify([row.event_id, row.field]), row]));
  const sections = new Map<string, ExploreFindingSections | null>();
  const projected = events.map(event => {
    const text = (field: string, original: string) => {
      const row = rows.get(JSON.stringify([event.event_id, field]));
      return row?.source_text === original ? row.text : original;
    };
    // Decode only the original owner's serialization. Never serialize model
    // prose and parse it again: a translated "Outcome:" or list marker must
    // remain text, not become another decision or change a list's cardinality.
    const source = parseExploreFindingSections(event.summary);
    sections.set(event.event_id, source ? {...source, applicability: text('applicability', source.applicability),
      observation: text('observation', source.observation), interpretation: source.decision ? source.interpretation : text('interpretation', source.interpretation),
      decision: source.decision ? {...source.decision, ...Object.fromEntries((['changed', 'stopped', 'retained'] as const)
        .map(key => [key, source.decision![key].map((item, i) => text(`${key}.${i}`, item))]))} : null} : null);
    return {...event, title: text('title', event.title), blocked_reason: text('blocked_reason', event.blocked_reason),
      summary: source ? event.summary : text('summary', event.summary),
      ...(event.execution_observation ? {execution_observation: {...event.execution_observation,
        ...(typeof event.execution_observation.change_reason === 'string' ? {change_reason: text('execution.change_reason', event.execution_observation.change_reason)} : {})}} : {})};
  });
  return {events: projected, sections};
}
