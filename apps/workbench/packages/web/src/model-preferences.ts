import { useEffect, useMemo, useReducer } from 'react';
import type { Api } from './api';
import { applyModelPreference, type DraftModelPreference, type DraftModelPreferences, type ModelPreferencePatch } from '../../daemon/src/model-preferences';

export { applyModelPreference, type DraftModelPreference, type DraftModelPreferences, type ModelPreferencePatch };

/** The daemon owns preferences. Pending explicit edits are replayed over its last answer. */
export function useModelPreferences(api: Api, onError: (error: unknown) => void) {
  const [, render] = useReducer((value: number) => value + 1, 0);
  const state = useMemo(() => ({
    saved: {} as DraftModelPreferences,
    pending: [] as Array<{ agent: string; patch: ModelPreferencePatch }>,
    status: (api.demo ? 'ready' : 'loading') as 'loading' | 'ready' | 'error',
    saving: false, revision: 0, request: 0, live: true,
  }), [api]);
  const current = () => state.pending.reduce((all, { agent, patch }) => ({
    ...all, [agent]: applyModelPreference(all[agent], patch),
  }), state.saved);
  const refresh = async () => {
    if (api.demo || state.pending.length) return;
    const revision = state.revision;
    const request = ++state.request;
    try {
      const saved = await api.draftModelPreferences();
      if (!state.live || revision !== state.revision || request !== state.request) return;
      state.saved = saved;
      state.status = 'ready';
      render();
    } catch (error) {
      if (state.live && request === state.request) {
        if (state.status !== 'ready') state.status = 'error';
        onError(error); render();
      }
    }
  };
  const flush = async () => {
    if (state.saving) return;
    state.saving = true;
    while (state.pending.length) {
      const { agent, patch } = state.pending[0];
      try {
        const saved = await api.patchDraftModelPreference(agent, patch);
        state.saved = { ...state.saved, [agent]: saved };
      } catch (error) { if (state.live) onError(error); }
      state.pending.shift();
      state.revision++;
      if (state.live) render();
    }
    state.saving = false;
    if (state.live) void refresh();
  };
  const change = (agent: string, patch: ModelPreferencePatch) => {
    if (!agent) return;
    state.revision++;
    if (api.demo) state.saved = { ...state.saved, [agent]: applyModelPreference(state.saved[agent], patch) };
    else { state.pending.push({ agent, patch }); void flush(); }
    render();
  };
  useEffect(() => {
    state.live = true;
    void refresh();
    const focus = () => { if (!document.hidden) void refresh(); };
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      state.live = false;
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
    // Each API owns its queue and answers, including requests settling after a host switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);
  return { preferences: current(), status: state.status, change, refresh };
}
