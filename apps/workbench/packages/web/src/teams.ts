import type { TeamDefinition } from './api';

/** Renaming a role carries its links and entry selection with it. */
export function renameTeamMember(team: TeamDefinition, index: number, name: string): TeamDefinition {
  const previous = team.members[index].name;
  return { ...team, entry: team.entry === previous ? name : team.entry,
    members: team.members.map((m, i) => i === index ? { ...m, name } : m),
    relations: team.relations.map(r => ({ ...r, from: r.from.map(n => n === previous ? name : n), to: r.to === previous ? name : r.to })) };
}
export function removeTeamMember(team: TeamDefinition, index: number): TeamDefinition {
  const name = team.members[index].name;
  const members = team.members.filter((_, i) => i !== index);
  return { ...team, members, entry: team.entry === name ? members[0]?.name ?? '' : team.entry,
    relations: team.relations.filter(r => r.to !== name).map(r => ({ ...r, from: r.from.filter(n => n !== name) })).filter(r => r.from.length) };
}
export function teamRecipient(value: string): string | null { return value.startsWith('team:') ? value.slice(5) : null; }
