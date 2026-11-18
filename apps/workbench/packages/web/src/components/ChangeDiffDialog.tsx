import { useEffect, useState } from 'react';
import type { Api, ChangedFile } from '../api';
import { diffsOf, type FileDiff } from '../timeline';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { DiffView } from './ThreadView';
import { IconClose } from './icons';

type Ref = { turnId: string; id: number; version: number };
type Loaded = { ref: Ref; diffs: FileDiff[] | null; failed: boolean };

const refsOf = (file: ChangedFile): Ref[] =>
  (file.details ?? []).filter((ref) => ref && typeof ref.turnId === 'string' && typeof ref.id === 'number');

/**
 * Every change to one file in this thread, a section per turn, read only while the dialog is open.
 * A change that cannot be read says so beside its turn rather than passing for a turn with no diff.
 */
export function ChangeDiffDialog({ api, threadId, file, turnOrder, onClose, onJumpTurn }: {
  api: Api; threadId: string; file: ChangedFile; turnOrder: string[];
  onClose: () => void; onJumpTurn: (turnId: string) => void;
}) {
  const refs = refsOf(file);
  const [loaded, setLoaded] = useState<Loaded[]>([]);
  const [loading, setLoading] = useState(refs.length > 0);
  const [attempt, setAttempt] = useState(0);
  const key = refs.map((ref) => `${ref.turnId}:${ref.id}:${ref.version}`).join('|');

  useEffect(() => {
    const list = refsOf(file);
    if (!list.length) { setLoaded([]); setLoading(false); return; }
    let live = true;
    setLoading(true);
    // Newest first: the last change is the one being asked about. The daemon lists the references
    // oldest first, so a turn the loaded history does not reach (both index 0) falls back to that
    // order, reversed.
    const order = list
      .map((ref, i) => ({ ref, i }))
      .sort((a, b) => turnIndex(b.ref.turnId, turnOrder) - turnIndex(a.ref.turnId, turnOrder) || b.i - a.i)
      .map(({ ref }) => ref);
    Promise.all(order.map(async (ref): Promise<Loaded> => {
      try {
        // The daemon matches the path as the overview reports it, and keeps the diff's own spelling
        // in the content: every diff it returns for this reference is this file's.
        const detail = await api.historyDetail(threadId, ref, undefined, file.path);
        return { ref, diffs: diffsOf(detail.content), failed: false };
      } catch {
        return { ref, diffs: null, failed: true };
      }
    })).then((value) => { if (live) { setLoaded(value); setLoading(false); } });
    return () => { live = false; };
    // The refs are what is read; the file object around them changes with every overview refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, threadId, file.path, key, attempt]);

  // One section per turn: a turn that changed the file twice holds both changes under one heading.
  const sections = groupTurns(loaded);
  // The daemon keeps the newest 20 references of a heavily edited file; the rest are only counted.
  const older = Math.max(0, (file.detailsTotal ?? 0) - refs.length);
  return <DialogSurface labelledBy="change-diff-title" className="change-diff-dialog" onClose={onClose}>
    <header className="change-diff-head">
      <span className="change-diff-path" id="change-diff-title" title={file.path}>{file.path}</span>
      <span className="diff-added">+{file.added}</span>
      <span className="diff-removed">−{file.removed}</span>
      <span className="change-diff-turns">{t('overview.diffTurns', file.turns)}</span>
      <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
        <IconClose size={18} />
      </button>
    </header>
    <div className="change-diff-body">
      {loading && <div className="history-status" role="status">{t('common.loading')}</div>}
      {!loading && sections.length === 0 && <div className="history-status" role="status">{t('overview.diffEmpty')}</div>}
      {!loading && sections.map((section, i) => {
        const turn = turnIndex(section.turnId, turnOrder);
        return <section className="change-diff-turn" key={section.turnId}>
          <h3>{turn > 0 ? t('overview.diffTurn', turn) : t('overview.diffPart', i + 1)}</h3>
          {section.parts.map((part) => part.failed
            ? <div key={part.ref.id} className="history-status" role="status">{t('overview.diffFailed')}</div>
            : part.diffs && part.diffs.length ? <DiffView key={part.ref.id} diffs={part.diffs} />
            : <div key={part.ref.id} className="history-status" role="status">{t('overview.diffEmpty')}</div>)}
        </section>;
      })}
    </div>
    <footer className="change-diff-foot">
      {older > 0 && <span className="change-diff-note">{t('overview.diffWindow', refs.length, older)}</span>}
      <button type="button" className="text-btn" onClick={() => { onClose(); onJumpTurn(file.turnId); }}>{t('overview.diffLast')}</button>
      {loaded.some((part) => part.failed) && <button type="button" className="text-btn" onClick={() => setAttempt((n) => n + 1)}>{t('common.retry')}</button>}
    </footer>
  </DialogSurface>;
}

/** The read references grouped by turn, keeping the newest-first order they were read in. */
function groupTurns(loaded: Loaded[]): Array<{ turnId: string; parts: Loaded[] }> {
  const out: Array<{ turnId: string; parts: Loaded[] }> = [];
  for (const part of loaded) {
    const last = out[out.length - 1];
    if (last?.turnId === part.ref.turnId) last.parts.push(part);
    else out.push({ turnId: part.ref.turnId, parts: [part] });
  }
  return out;
}

/** A turn's place in the conversation, 1-based; 0 when the loaded history does not reach it. */
function turnIndex(turnId: string, order: string[]): number {
  const at = order.indexOf(turnId);
  return at < 0 ? 0 : at + 1;
}
