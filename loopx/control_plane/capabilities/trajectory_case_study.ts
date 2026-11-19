/** Authored reading companion to a frozen trajectory pack. No execution or scoring authority. */
import {EffectRuntimeRequestError} from '../effect_runtime_errors.ts';
import type {AssessmentCitation, TrajectoryInput, TrajectoryOutputLanguage} from './trajectory_assessment_types.ts';

export interface TrajectoryCaseStudy {
  title: string;
  language: TrajectoryOutputLanguage;
  summary: string;
  citations: AssessmentCitation[];
  sections: Array<{
    id: string; title: string; text: string; citations: AssessmentCitation[];
    table?: {columns: string[]; rows: string[][]};
    series?: {label: string; unit: string; points: Array<{label: string; value: number | null}>};
    timeline?: Array<{label: string; title: string; text: string}>;
  }>;
  limitations: string[];
}

/** Presentation shapes stay local to benchmark-toolkit; sources and pack identity remain the owners. */
export function qualifyTrajectoryCaseStudy(raw: unknown, input: TrajectoryInput): TrajectoryCaseStudy {
  const fail = (field: string): never => {throw new EffectRuntimeRequestError(`invalid case study ${field}`);};
  if (JSON.stringify(raw)?.length > 160_000) fail('reading size');
  const object = (v: unknown, field: string): Record<string, unknown> =>
    v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : fail(field);
  const text = (v: unknown, field: string, max = 12_000) =>
    typeof v === 'string' && v.trim() && !v.includes('\0') && v.length <= max ? v : fail(field);
  const array = (v: unknown, field: string, max = 40): unknown[] =>
    Array.isArray(v) && v.length <= max ? v : fail(field);
  const citations = (v: unknown): AssessmentCitation[] => {
    const rows = array(v, 'citations').map(value => {
      const c = object(value, 'citation');
      const source = input.sources.find(s => s.id === c.source_id);
      if (!source || !Number.isSafeInteger(c.first_line) || !Number.isSafeInteger(c.last_line)
          || Number(c.first_line) < source.first_line || Number(c.last_line) < Number(c.first_line)
          || Number(c.last_line) >= source.first_line + source.text.split('\n').length) fail('citation range');
      return {source_id: source!.id, first_line: Number(c.first_line), last_line: Number(c.last_line)};
    });
    if (!rows.length) fail('missing evidence');
    return rows;
  };
  const doc = object(raw, 'document');
  if (doc.language !== 'zh' && doc.language !== 'en') fail('language');
  const sections = array(doc.sections, 'sections', 24).map(value => {
    const s = object(value, 'section');
    const id = text(s.id, 'section id', 80);
    if (!/^[a-zA-Z0-9_-]+$/.test(id)) fail('section id');
    const section: TrajectoryCaseStudy['sections'][number] = {id, title: text(s.title, 'section title', 240),
      text: text(s.text, 'section text'), citations: citations(s.citations)};
    if (s.table !== undefined) {
      const table = object(s.table, 'table');
      const columns = array(table.columns, 'columns', 8).map(v => text(v, 'column', 120));
      if (!columns.length) fail('empty columns');
      const rows = array(table.rows, 'rows', 40).map(v => {
        const row = array(v, 'row', 8).map(v => text(v, 'cell', 2000));
        if (row.length !== columns.length) fail('row width');
        return row;
      });
      section.table = {columns, rows};
    }
    if (s.series !== undefined) {
      const series = object(s.series, 'series');
      const points = array(series.points, 'points', 40).map(v => {
        const point = object(v, 'point');
        if (point.value !== null && (typeof point.value !== 'number' || !Number.isFinite(point.value))) fail('point value');
        return {label: text(point.label, 'point label', 120), value: point.value as number | null};
      });
      if (!points.length) fail('empty series');
      section.series = {label: text(series.label, 'series label', 240), unit: text(series.unit, 'unit', 80), points};
    }
    if (s.timeline !== undefined) section.timeline = array(s.timeline, 'timeline', 30).map(v => {
      const event = object(v, 'event');
      return {label: text(event.label, 'event label', 120), title: text(event.title, 'event title', 240), text: text(event.text, 'event text', 4000)};
    });
    return section;
  });
  if (!sections.length || new Set(sections.map(s => s.id)).size !== sections.length) fail('section identities');
  return {title: text(doc.title, 'title', 240), language: doc.language as TrajectoryOutputLanguage,
    summary: text(doc.summary, 'summary', 4000), citations: citations(doc.citations), sections,
    limitations: array(doc.limitations, 'limitations', 20).map(v => text(v, 'limitation', 3000))};
}
