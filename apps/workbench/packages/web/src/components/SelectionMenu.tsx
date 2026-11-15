import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type TouchEvent as ReactTouchEvent } from 'react';
import { copyText } from '../clipboard';
import { useIsTouch } from '../hooks';
import { IconArrowUp, IconCopy, IconQuote, IconSideChat } from './icons';
import { t } from '../i18n';
import type { NoteSource } from '../timeline';

interface Props {
  /**
   * A passage noted for the next message, with what the user says about it (may be empty). The
   * range is the passage's place in the conversation, when it has one: a passage picked out of a
   * textarea (the writing page) has none. `file` is the thread-relative path it was picked out of.
   */
  onNote: (quote: string, comment: string, range: Range | null, file?: string) => void;
  onSideAsk: (text: string, threadId?: string) => void;
}

/** Rough size of the menu, so it can be centred on the selection without waiting for layout. */
const MENU_WIDTH = 250;
const TAGGED_WIDTH = 330;
const COMMENT_WIDTH = 320;
const MENU_HEIGHT = 42;
/** The comment card with the passage above the field, for the same purpose. */
const COMMENT_HEIGHT = 96;
/** Kept away from the viewport edges by this much. */
const MARGIN = 8;

/** The quick labels a passage in the writing body can be given with one tap. */
const TAG_KEYS = ['delete', 'todo', 'why', 'example', 'keep'] as const;

/**
 * What the browser reports as a selection worth acting on: text picked out of the thread's
 * messages or the file panel's page, and nowhere text is being edited (the writing page is heard
 * through its own `select` events) or another layer is lying over the thread.
 */
function selectable(node: Node | null): boolean {
  const el = node instanceof Element ? node : node?.parentElement ?? null;
  if (!el?.closest('.thread, .files-panel')) return false;
  return !el.closest('textarea, input, [contenteditable="true"], .side-chat, .lightbox, .dialog-scrim');
}

/** Where a selection sits, when it sits in a file: its path, and whether it is the writing body. */
function picked(el: Element | null): { file?: string; body: boolean } {
  return {
    file: el?.closest('[data-note-file]')?.getAttribute('data-note-file') || undefined,
    body: !!el?.closest('[data-writing-body]'),
  };
}

/**
 * The conversation and turn a passage was picked out of, when that conversation is not the one the
 * note goes to: an earlier one of a handover chain. Answers carry no turn of their own, so the turn
 * is that of the last message above the passage.
 */
export function noteSource(range: Range | null, receiver: string | null | undefined): NoteSource | undefined {
  const start = range?.startContainer;
  const el = start instanceof Element ? start : start?.parentElement;
  const segment = el?.closest<HTMLElement>('[data-history-thread]');
  const threadId = segment?.dataset.historyThread;
  if (!range || !segment || !threadId || threadId === receiver) return undefined;
  const above = [...segment.querySelectorAll<HTMLElement>('[data-turn]')]
    .filter(node => node.contains(range.startContainer) || Boolean(node.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING));
  const turnId = above.at(-1)?.dataset.turn;
  return turnId ? { threadId, turnId } : undefined;
}

/** How far a press may move and still count as a tap rather than a drag. */
const TAP_SLOP = 10;

/**
 * Centred on the passage, above it when there is room, below it otherwise.
 * The rect is on the screen; the menu is placed within the app's box, which a phone keyboard can
 * have moved down the page.
 */
function placeNear(rect: DOMRect, width: number, height = MENU_HEIGHT): { top: number; left: number } {
  const left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, MARGIN), window.innerWidth - width - MARGIN);
  const above = rect.top - height - 12;
  const top = above >= MARGIN ? above : Math.min(rect.bottom + 12, window.innerHeight - height - MARGIN);
  const box = document.querySelector('.app')?.getBoundingClientRect();
  return { top: top - (box?.top ?? 0), left: left - (box?.left ?? 0) };
}

/**
 * The menu that follows a text selection in the conversation or the file panel: note it for the
 * next message (with an optional comment, like Codex), copy it, or ask about it in the side chat.
 * A passage in the writing body first offers the quick labels of writing mode. The browser's own
 * menu (where it shows one) stays as it is.
 */
export function SelectionMenu({ onNote, onSideAsk }: Props) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  // Where the selection is: in a file (its path), in the writing body (the quick labels), or neither.
  const [pick, setPick] = useState<{ file?: string; body: boolean } | null>(null);
  // Writing a comment: the field takes the focus, which clears the selection, so the passage is
  // held here until the note is added or dropped.
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState('');
  const commentingRef = useRef(false);
  commentingRef.current = commenting;
  const pickRef = useRef<{ file?: string; body: boolean } | null>(null);
  // The selection can vanish between tapping the button and its click; text and range are taken first.
  const textRef = useRef('');
  const rangeRef = useRef<Range | null>(null);
  // A selection inside the writing page's textarea, where the browser gives no range to follow:
  // the passage is held from the element's own `select` event, placed by the pointer that made it.
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  // On a touch screen nothing goes beside the passage: the system's own menu (copy, look up,
  // translate) sits there, above or below depending on the handles, and would cover ours. And when
  // commenting, the keyboard comes up over the lower half and the browser pans to the field. So the
  // menu, and then the field with the passage it is about, sit at the bottom of the app in the
  // composer's place, just above the keyboard.
  const touch = useIsTouch();
  const touchRef = useRef(touch);
  touchRef.current = touch;

  useEffect(() => {
    const read = () => {
      if (commentingRef.current) {
        if (touchRef.current) return;
        // The passage scrolls with the page; the field follows it.
        const range = rangeRef.current;
        if (range) setPos(placeNear(range.getBoundingClientRect(), COMMENT_WIDTH, COMMENT_HEIGHT));
        return;
      }
      // The writing page keeps its own selection (and so its menu) while it holds one; the sweep
      // below would otherwise clear it on every selectionchange the page fires.
      const field = fieldRef.current;
      if (field?.isConnected && field.selectionStart !== field.selectionEnd) return;
      fieldRef.current = null;
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return setPos(null);
      const text = sel.toString().trim();
      if (!text || !selectable(sel.anchorNode) || !selectable(sel.focusNode)) return setPos(null);
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return setPos(null);

      textRef.current = text;
      rangeRef.current = range.cloneRange();
      const where = picked(sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement ?? null);
      pickRef.current = where;
      setPick(where);
      // On a touch screen the menu is docked at the bottom and only needs to know it is showing.
      setPos(touchRef.current ? { top: 0, left: 0 } : placeNear(rect, where.body ? TAGGED_WIDTH : MENU_WIDTH));
    };

    // A textarea's selection never reaches document.getSelection(); its own `select` event tells
    // the passage, and the pointer that just made the selection tells the place.
    const onFieldSelect = (e: Event) => {
      const el = e.target;
      if (!(el instanceof HTMLTextAreaElement) || !el.closest('.files-panel')) return;
      if (commentingRef.current) return;
      const text = el.value.slice(el.selectionStart, el.selectionEnd).trim();
      if (!text) return;
      const where = picked(el);
      textRef.current = text;
      rangeRef.current = null;
      fieldRef.current = el;
      pickRef.current = where;
      setPick(where);
      const anchor = pointerRef.current ?? { x: el.getBoundingClientRect().left + 40, y: el.getBoundingClientRect().top + 24 };
      setPos(touchRef.current
        ? { top: 0, left: 0 }
        : placeNear(new DOMRect(anchor.x, anchor.y, 0, 20), where.body ? TAGGED_WIDTH : MENU_WIDTH));
    };

    const onPointerUp = (e: PointerEvent) => void (pointerRef.current = { x: e.clientX, y: e.clientY });

    document.addEventListener('selectionchange', read);
    document.addEventListener('select', onFieldSelect);
    document.addEventListener('pointerup', onPointerUp);
    // The menu is fixed to the viewport; when the page scrolls under it, it follows the selection.
    window.addEventListener('scroll', read, true);
    window.addEventListener('resize', read);
    return () => {
      document.removeEventListener('selectionchange', read);
      document.removeEventListener('select', onFieldSelect);
      document.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('scroll', read, true);
      window.removeEventListener('resize', read);
    };
  }, []);

  // The field took the focus and with it the selection; the passage stays marked while it is written about.
  useEffect(() => {
    const registry = typeof CSS !== 'undefined' ? (CSS as unknown as { highlights?: Map<string, unknown> }).highlights : undefined;
    const range = rangeRef.current;
    if (!commenting || !range || !registry || typeof Highlight === 'undefined') return;
    registry.set('calli-note-pending', new Highlight(range));
    return () => void registry.delete('calli-note-pending');
  }, [commenting]);

  // A tap anywhere else drops a comment being written; a drag (scrolling the thread to reread the
  // passage, or a scrollbar) does not. So it is decided when the press ends: a press the browser
  // took over for scrolling ends in pointercancel, and one that moved is not a tap. Pointer events,
  // not mouse ones: the tap that opened the field is followed by a compatibility mousedown where
  // the finger was, which with the field docked away from there would close it at once. That tap's
  // pointerdown is over before these listeners exist, so its pointerup finds no press to end.
  useEffect(() => {
    if (!commenting) return;
    let press: { id: number; x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      press = rootRef.current && !e.composedPath().includes(rootRef.current) ? { id: e.pointerId, x: e.clientX, y: e.clientY } : null;
    };
    const onUp = (e: PointerEvent) => {
      if (press?.id === e.pointerId && Math.hypot(e.clientX - press.x, e.clientY - press.y) < TAP_SLOP) close();
      press = null;
    };
    const onCancel = () => void (press = null);
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onCancel);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onCancel);
    };
  }, [commenting]);

  const close = () => {
    setCommenting(false);
    setComment('');
    setPos(null);
    setPick(null);
    pickRef.current = null;
    fieldRef.current = null;
  };

  if (!pos) return null;

  // Touch clears a selection before a tap can land as a click, so touch acts at touch-start;
  // mouse keeps the selection alive until the click by swallowing the mousedown.
  const act = (fn: () => void) => ({
    onMouseDown: (e: ReactMouseEvent) => e.preventDefault(),
    onTouchStart: (e: ReactTouchEvent) => {
      e.preventDefault();
      fn();
    },
    onClick: fn,
  });

  const startComment = () => {
    const range = rangeRef.current;
    if (range && !touch) setPos(placeNear(range.getBoundingClientRect(), COMMENT_WIDTH, COMMENT_HEIGHT));
    setCommenting(true);
  };

  const sendNote = (words: string) => {
    onNote(textRef.current, words.trim(), rangeRef.current, pickRef.current?.file);
    document.getSelection()?.removeAllRanges();
    close();
  };

  // The writing body's quick labels: one tap is the whole note; the last one waits for words.
  const tagRow = pick?.body && (
    <div className="selection-tags" role="group" aria-label={t('selection.tags')}>
      {TAG_KEYS.map((key) => (
        <button key={key} type="button" className="selection-tag" title={t('selection.addNote')} {...act(() => sendNote(t(`selection.tag.${key}`)))}>
          {t(`selection.tag.${key}`)}
        </button>
      ))}
      <button type="button" className="selection-tag" title={t('selection.tagChangeHint')} {...act(startComment)}>
        {t('selection.tagChange')}
      </button>
    </div>
  );

  if (commenting) {
    const field = (
      <>
        <input
          autoFocus
          className="selection-comment"
          value={comment}
          placeholder={t('selection.commentPlaceholder')}
          aria-label={t('selection.commentLabel')}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault();
              sendNote(comment);
            } else if (e.key === 'Escape') {
              e.preventDefault();
              close();
            }
          }}
        />
        <button type="button" className="selection-add" aria-label={t('selection.addNote')} title={t('selection.addNoteEnter')} onClick={() => sendNote(comment)}>
          <IconArrowUp size={15} />
        </button>
      </>
    );
    if (touch) {
      return (
        <div ref={rootRef} className={`selection-menu commenting docked${pick?.body ? ' tagged' : ''}`} role="dialog" aria-label={t('selection.addNote')}>
          {tagRow}
          <div className="selection-field">{field}</div>
        </div>
      );
    }
    return (
      <div
        ref={rootRef}
        className={`selection-menu commenting${pick?.body ? ' tagged' : ''}`}
        style={{ top: pos.top, left: pos.left, width: COMMENT_WIDTH }}
        role="dialog"
        aria-label={t('selection.addNote')}
      >
        {/* The passage above the field: browsers without the Highlight API (Firefox, Safari) show
            no mark on the text once the field takes the focus, and even where they do, the card
            should say what it is about on its own. */}
        <div className="selection-quote">
          <IconQuote size={13} />
          <span>{textRef.current}</span>
        </div>
        {tagRow}
        <div className="selection-field">{field}</div>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className={`selection-menu${touch ? ' docked' : ''}${pick?.body ? ' tagged' : ''}`}
      style={touch ? undefined : { top: pos.top, left: pos.left }}
      role="toolbar"
      aria-label={t('selection.toolbar')}
    >
      {tagRow}
      <div className="selection-actions">
        <button type="button" {...act(startComment)}>
          <IconQuote size={14} />
          {t('selection.note')}
        </button>
        <button
          type="button"
          {...act(() => {
            void copyText(textRef.current);
            document.getSelection()?.removeAllRanges();
          })}
        >
          <IconCopy size={14} />
          {t('selection.copy')}
        </button>
        <button
          type="button"
          className="selection-ask"
          {...act(() => {
            document.getSelection()?.removeAllRanges();
            const node = rangeRef.current?.startContainer;
            const element = node instanceof Element ? node : node?.parentElement;
            onSideAsk(textRef.current, element?.closest<HTMLElement>('[data-history-thread]')?.dataset.historyThread);
          })}
        >
          <IconSideChat size={14} />
          {t('selection.ask')}
        </button>
      </div>
    </div>
  );
}
