import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent } from 'react';
import type { Api } from './api';
import { pastedPointer, type PastedPointer } from './clipboard';
import type { ContextReference } from './context-references';
import { t } from './i18n';

const machines = new WeakMap<Api, { id?: string; request?: Promise<string> }>();

/** Cache installation metadata, not authority, before a clipboard click needs its user gesture. */
export function referenceMachine(api: Api): Promise<string> {
  const cache = machines.get(api) ?? {};
  machines.set(api, cache);
  if (cache.id) return Promise.resolve(cache.id);
  return cache.request ??= api.referenceMachine().then(value => {
    if (typeof value.machineId !== 'string' || !value.machineId) throw new Error(t('references.loadFailed'));
    return cache.id = value.machineId;
  }).finally(() => { cache.request = undefined; });
}

export function useReferenceMachine(api: Api | null | undefined): string | null {
  const [value, setValue] = useState<{ api: Api; id: string } | null>(null);
  useEffect(() => {
    if (!api) return;
    let live = true;
    const load = () => { void referenceMachine(api).then(id => { if (live) setValue({ api, id }); }, () => {}); };
    load();
    window.addEventListener('focus', load);
    return () => { live = false; window.removeEventListener('focus', load); };
  }, [api]);
  return api ? machines.get(api)?.id ?? (value?.api === api ? value.id : null) : null;
}

/** Resolve only visible pending metadata. Creating it never grants the recipient access. */
export function usePendingReferences(api: Api | undefined, scope: string, references: ContextReference[], resolve: (id: string, reference: ContextReference) => void, onError: (message: string) => void, readReferences?: () => ContextReference[], resolvePending?: (reference: ContextReference) => Promise<ContextReference> | undefined) {
  const current = useRef({ references, resolve, onError, readReferences, resolvePending });
  current.current = { references, resolve, onError, readReferences, resolvePending };
  const visible = () => current.current.readReferences?.() ?? current.current.references;
  const epoch = useRef(0);
  const attempted = useRef(new Set<string>());
  useLayoutEffect(() => {
    epoch.current++;
    attempted.current.clear();
    return () => { epoch.current++; attempted.current.clear(); };
  }, [api, scope]);
  useEffect(() => {
    if (!api) return;
    const generation = epoch.current;
    for (const reference of visible()) {
      if (!reference.pending || attempted.current.has(reference.id)) continue;
      attempted.current.add(reference.id);
      const load = () => {
        if (epoch.current !== generation || !visible().some(item => item.id === reference.id && item.pending)) throw new Error('Reference scope retired');
        const custom = current.current.resolvePending?.(reference);
        if (custom) return custom;
        if (reference.kind !== 'thread' || !reference.sourceId) throw new Error(t('references.invalidPointer'));
        return api.createReference({ kind: 'thread', sourceId: reference.sourceId, pointer: true,
          ...(reference.machineId ? { machineId: reference.machineId, portable: true } : {}),
          ...(reference.turnId ? { turnId: reference.turnId } : {}) });
      };
      void Promise.resolve().then(load).then(value => {
        if (epoch.current === generation && visible().some(item => item.id === reference.id && item.pending)) {
          current.current.resolve(reference.id, value);
        }
      }).catch(error => {
        if (epoch.current === generation && visible().some(item => item.id === reference.id && item.pending)) {
          current.current.onError(error instanceof Error ? error.message : t('references.loadFailed'));
        }
      });
    }
  }, [api, scope, references]);
}

interface PasteDraft {
  text: string;
  /** Read edits committed in the same event, before React renders them. */
  readText?: () => string;
  changeText: (text: string) => void;
  /** Called atomically with removal of the untouched portable text. It may reject an attachment limit. */
  attach: (reference: ContextReference, pointer: PastedPointer, text: string) => void;
  onError: (message: string) => void;
  /** Synchronous too: a send in the same event must not race React's pending render. */
  onPendingChange?: (count: number) => void;
}

/** Only a whole, machine-scoped clipboard pointer becomes an attachment; ordinary typed text never does. */
export function useReferencePaste(api: Api | undefined, scope: string, draft: PasteDraft) {
  const current = useRef(draft);
  current.current = draft;
  const epoch = useRef(0);
  const seq = useRef(0);
  const requests = useRef(new Map<number, { epoch: number; raw: string }>());
  const [pending, setPending] = useState(0);
  const notify = () => { const count = requests.current.size; current.current.onPendingChange?.(count); setPending(count); };
  const invalidate = () => { epoch.current++; requests.current.clear(); notify(); };
  useLayoutEffect(() => {
    invalidate();
    return () => { epoch.current++; requests.current.clear(); current.current.onPendingChange?.(0); };
  }, [api, scope]);
  useLayoutEffect(() => {
    for (const [id, request] of requests.current) {
      if (!draft.text.includes(request.raw)) requests.current.delete(id);
    }
    notify();
  }, [draft.text]);

  const paste = (event: ClipboardEvent<HTMLTextAreaElement | HTMLInputElement>): boolean => {
    const raw = event.clipboardData.getData('text/plain');
    const pointer = pastedPointer(raw);
    const trimmed = raw.trimStart();
    if (!pointer && !((trimmed.startsWith('LoopX:') || trimmed.startsWith('Calli:')) && (raw.includes('loopx://thread/') || raw.includes('calli://thread/')))) return false;
    event.preventDefault();
    const input = event.currentTarget;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    const text = input.value.slice(0, start) + raw + input.value.slice(end);
    current.current = { ...current.current, text };
    current.current.changeText(text);
    if (!pointer || !pointer.machineId || !api) {
      current.current.onError(t(!pointer ? 'references.invalidPointer' : !pointer.machineId ? 'references.unscopedPointer' : 'references.pasteUnavailable'));
      return true;
    }
    const id = ++seq.current;
    const request = { epoch: epoch.current, raw };
    requests.current.set(id, request);
    notify();
    void api.createReference({ kind: 'thread', sourceId: pointer.threadId, machineId: pointer.machineId, portable: true, pointer: true,
      ...(pointer.turnId ? { turnId: pointer.turnId } : {}) }).then(reference => {
      if (epoch.current !== request.epoch || requests.current.get(id) !== request) return;
      const text = current.current.readText?.() ?? current.current.text;
      const at = text.indexOf(raw);
      // Edits, deletion or an ambiguous duplicate cancel the conversion, not the user's words.
      if (at < 0 || text.indexOf(raw, at + raw.length) >= 0) return;
      current.current.attach(reference, pointer, text.slice(0, at) + text.slice(at + raw.length));
    }).catch(error => {
      if (epoch.current === request.epoch && requests.current.get(id) === request) {
        current.current.onError(t('references.resolveFailed', error instanceof Error ? error.message : t('references.loadFailed')));
      }
    }).finally(() => {
      if (requests.current.get(id) !== request) return;
      requests.current.delete(id);
      notify();
    });
    return true;
  };
  return { paste, pending, isPending: () => requests.current.size > 0, invalidate };
}
