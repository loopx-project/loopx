import { HttpError } from './errors.js';
import type { PersonalService } from './personal.js';

interface PrepState { events: string[]; sent: Record<string, string> }
export class PersonalPrep {
  private busy = false;
  private lastTick = -Infinity;
  constructor(private readonly personal: PersonalService) {}
  settings(): { enabled: boolean; leadMin: number } { return this.personal.store.hubSetting('personalPrepSettings', { enabled: true, leadMin: 30 }); }
  save(input: Record<string, unknown>) {
    const next = { ...this.settings(), ...input };
    if (typeof next.enabled !== 'boolean' || !Number.isInteger(next.leadMin) || next.leadMin < 1 || next.leadMin > 1440) throw new HttpError(400, 'prep requires enabled boolean and leadMin 1–1440');
    const settings = { enabled: next.enabled, leadMin: next.leadMin };
    this.personal.store.saveHubSetting('personalPrepSettings', settings); return settings;
  }
  private state(): PrepState { return this.personal.store.hubSetting('personalPrep', { events: [], sent: {} }); }
  add(id: unknown) {
    if (typeof id !== 'string' || !id.trim() || id.length > 240 || /[\s\x00-\x1f]/.test(id)) throw new HttpError(400, 'event id is required');
    const state = this.state();
    if (!state.events.includes(id)) state.events.push(id);
    this.personal.store.saveHubSetting('personalPrep', state);
    return { eventId: id };
  }
  async tick(now = new Date()): Promise<void> {
    if (this.busy || now.getTime() - this.lastTick < 300_000 || !this.settings().enabled) return;
    this.busy = true; this.lastTick = now.getTime();
    try {
      if (!(await this.personal.resolvedSettings()).agent) return;
      // Include tomorrow for lead times crossing local midnight or a month boundary.
      const settings = this.settings();
      const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
      const agendas = await Promise.all([this.personal.plan.calendar('hour', now), ...(settings.leadMin > 240 ? [this.personal.plan.calendar('day', now), this.personal.plan.calendar('day', tomorrow)] : [])]);
      for (const meeting of agendas.flatMap(agenda => agenda.state === 'ok' ? agenda.items : [])) {
        const until = Date.parse(meeting.start) - now.getTime();
        const state = this.state(), key = JSON.stringify([meeting.id, meeting.start]);
        if (until <= 0 || until > settings.leadMin * 60_000 || (!meeting.recurring && !state.events.includes(meeting.id)) || state.sent[key]) continue;
        state.sent[key] = now.toISOString();
        this.personal.store.saveHubSetting('personalPrep', state);
        try { await this.personal.router.receiveMeetingPrep(meeting); }
        catch (error) { const latest = this.state(); delete latest.sent[key]; this.personal.store.saveHubSetting('personalPrep', latest); throw error; }
      }
    } finally { this.busy = false; }
  }
}
