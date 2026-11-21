import type { Api, BriefSettings, ExploreRecommendation, LarkChannelSettings, PersonalSettings, ProactiveSettings, PushSettings } from './api';
import { t } from './i18n';
import { withPostMeetingEvents } from './lark';

export interface ExploreSnapshot {
  personal: PersonalSettings;
  lark: LarkChannelSettings;
  brief: BriefSettings;
  push: PushSettings;
  proactive: ProactiveSettings;
  profile: Awaited<ReturnType<Api['personalProfile']>>;
}
export interface ExplorePayloads {
  personal: PersonalSettings;
  lark: LarkChannelSettings;
  brief: BriefSettings;
  push: PushSettings;
  proactive: Pick<ProactiveSettings, 'mode' | 'repos'>;
  profile: { text: string; rev: number };
}
export type ExploreItem = keyof ExplorePayloads;

/** Unknown readings never mean an empty profile or an empty watch list. */
export function needsExplore(personal: PersonalSettings | null, template: boolean | null, lark: LarkChannelSettings | null): boolean | null {
  if (!personal) return null;
  if (!personal.agent) return true;
  if (template === false) return false;
  if (template === null || !lark) return null;
  return lark.people.length === 0 && lark.chats.length === 0;
}

/** Keep existing subscriptions visible and selected when exploring again. Nothing is removed unseen. */
export function exploreDraft(recommendation: ExploreRecommendation, current: ExploreSnapshot): ExploreRecommendation {
  const draft = structuredClone(recommendation);
  draft.profile.replace = draft.profile.replace && current.profile.isTemplate;
  draft.push.enabled = draft.push.available && draft.push.enabled;
  for (const person of current.lark.people) {
    const found = draft.people.find(item => item.id === person.id);
    const role = person.role === 'boss' || person.role === 'pm' ? person.role : 'other';
    if (found) { found.selected = true; found.role = role; }
    else draft.people.push({ ...person, role, selected: true, why: t('explore.already'), evidence: [] });
  }
  for (const chat of current.lark.chats) {
    const found = draft.chats.find(item => item.id === chat.id);
    if (found) found.selected = true;
    else draft.chats.push({ ...chat, selected: true, why: t('explore.already'), evidence: [] });
  }
  return draft;
}

export function exploreCounts(draft: ExploreRecommendation): { people: number; chats: number } {
  return { people: draft.people.filter(item => item.selected).length, chats: draft.chats.filter(item => item.selected).length };
}
export function exploreSummary(draft: ExploreRecommendation): string {
  const { people, chats } = exploreCounts(draft);
  return t('explore.done', people, chats);
}

/** Payloads preserve every field outside the checklist, including work hours, recipients and repos. */
export function explorePayloads(draft: ExploreRecommendation, current: ExploreSnapshot): ExplorePayloads {
  return {
    personal: { ...current.personal, agent: draft.agent.agent, model: draft.agent.model, directory: current.personal.directoryIsDefault ? null : current.personal.directory },
    lark: withPostMeetingEvents({ ...current.lark, enabled: true, intervalMin: draft.intervalMin.value,
      people: draft.people.filter(item => item.selected).map(({ id, name, role }) => ({ id, name, role })),
      chats: draft.chats.filter(item => item.selected).map(({ id, name }) => ({ id, name })),
    }, draft.meetings.on),
    brief: { enabled: draft.brief.enabled, morning: draft.brief.morning, evening: draft.brief.evening },
    push: { ...current.push, enabled: draft.push.available && draft.push.enabled },
    proactive: { mode: draft.proactive.mode, repos: current.proactive.repos },
    profile: { text: draft.profile.draft, rev: current.profile.rev },
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const watch = (value: LarkChannelSettings) => ({
  enabled: value.enabled, intervalMin: value.intervalMin,
  people: value.people.map(({ id, name, role }) => ({ id, name, role: role ?? 'other' })).sort((a, b) => a.id.localeCompare(b.id)),
  chats: value.chats.map(({ id, name }) => ({ id, name })).sort((a, b) => a.id.localeCompare(b.id)), events: [...(value.events ?? [])].sort(),
});
/** A successful save becomes the next baseline, so retrying never repeats unchanged successful items. */
export function exploreChanges(draft: ExploreRecommendation, current: ExploreSnapshot): ExploreItem[] {
  const next = explorePayloads(draft, current);
  const changed: ExploreItem[] = [];
  if (next.personal.agent !== current.personal.agent || next.personal.model !== current.personal.model) changed.push('personal');
  if (!same(watch(next.lark), watch(current.lark))) changed.push('lark');
  if (next.brief.enabled !== current.brief.enabled || next.brief.morning !== current.brief.morning || next.brief.evening !== current.brief.evening) changed.push('brief');
  if (next.push.enabled !== current.push.enabled) changed.push('push');
  if (next.proactive.mode !== current.proactive.mode) changed.push('proactive');
  if (draft.profile.replace && next.profile.text !== current.profile.text) changed.push('profile');
  return changed;
}
