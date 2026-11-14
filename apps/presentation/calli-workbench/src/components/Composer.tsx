import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { ApiError, MAX_QUEUED, type ModelQueue, type OutgoingImage, type QueuedMessage, type SlashCommand } from '../api';
import { commandDraft, searchCommands, slashQuery, type HubAction, type MenuRow } from '../commands';
import { useIsNarrow, useIsTouch } from '../hooks';
import { MAX_IMAGES, prepareImage, restoreImage, type PreparedImage } from '../images';
import { channelRows } from '../timeline';
import { Popover } from './Popover';
import { ComposerMenu, type GoalAction, type GoalCapability } from './ComposerMenu';
import { GoalCard } from './GoalCard';
import { Lightbox } from './Lightbox';
import {
  IconArchive,
  IconClose,
  IconCheck,
  IconChevron,
  IconCopy,
  IconCube,
  IconEdit,
  IconFork,
  IconHelp,
  IconImport,
  IconInfo,
  IconList,
  IconPaperclip,
  IconPin,
  IconPlus,
  IconSend,
  IconSearch,
  IconShield,
  IconSlash,
  IconSparkle,
  IconStop,
  IconTarget,
} from './icons';
import { t } from '../i18n';

/** A send in progress: the text the message goes as, and the user's latest turn in the thread when it set off (null: none). */
interface Sending {
  text: string;
  after: string | null;
}

/** What is left unsent in a box: the words and pictures. */
interface Draft {
  text: string;
  images: PreparedImage[];
  /** The message is on its way out. */
  sending?: Sending;
  /** A newly seeded draft should put the cursor in its input. */
  focus?: boolean;
}

/** A box's content as the one text it is sent as. */
const asMessage = (text: string) => text.trim();

/** How long a send may take before the button says it is still going. */
const SLOW_SEND_MS = 400;
/** How far a finger can drift and still count as tapping the mobile send button. */
const SEND_TAP_SLOP = 12;
/** Mobile browsers may deliver the compatibility click after pointerup; keep it from sending twice. */
const SEND_CLICK_SUPPRESS_MS = 500;

/** Safari may report composition as ended on the Enter keydown that still belongs to the IME. */
export const isImeComposing = (event: Pick<KeyboardEvent<HTMLElement>, 'nativeEvent'>): boolean =>
  event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229;

/**
 * Unsent drafts by thread, so what is typed in one thread stays there: switching away keeps it,
 * switching back brings it back, and another thread starts with its own. Drafts last as long as the
 * page: nothing is written to the device.
 */
const drafts = new Map<string, Draft>();

function loadDraft(key: string): Draft | undefined {
  return drafts.get(key);
}

function keepDraft(key: string, draft: Draft | null): void {
  if (draft && (draft.text.trim() || draft.images.length)) drafts.set(key, draft);
  else drafts.delete(key);
}

/** Seeds a newly created thread before the composer opens it. */
export function prefillComposerDraft(key: string, text: string): void {
  keepDraft(key, { text, images: [], focus: true });
}

interface Props {
  /**
   * Whose draft the box holds: a thread's id, or one key for the new thread being started. Without
   * one the box keeps nothing once it is gone, as for a side chat, which is new each time.
   */
  draftKey?: string;
  running: boolean;
  disabled?: boolean;
  placeholder: string;
  onSend: (text: string, images: PreparedImage[]) => Promise<void> | void;
  onStop: () => void;
  /** Strip above the text area: which agent and which directory. */
  header: ReactNode;
  /** Native planning is offered in the add menu only when the agent supports it. */
  planMode?: { active: boolean; disabled: boolean; onChange: (active: boolean) => void };
  /** Controls at the left of the bottom bar, after the add menu. */
  left: ReactNode;
  /** Controls at the right of the bottom bar, before the send button. */
  right: ReactNode;
  /** The agent's own slash commands. */
  commands: SlashCommand[];
  goal?: GoalCapability;
  /** The thread's native goal right now, when the agent's state can be followed live. */
  goalState?: import('../api').GoalView | null;
  /** Things the hub does itself, offered in the same menu. */
  actions: HubAction[];
  /** Agent commands left out because a hub action replaces them. */
  hiddenCommands: string[];
  onAction: (id: string) => void;
  /** Why pictures cannot be attached right now, or null when they can. */
  attachBlocked: string | null;
  /** Something worth knowing about how attached pictures will be read, shown on the attach button. */
  attachNote?: string | null;
  onError: (message: string) => void;
  /** Messages sent while a turn was running, waiting to go out. */
  queued: QueuedMessage[];
  /** The native model queue for the running turn, such as Trae's live queue position. */
  modelQueue?: ModelQueue | null;
  onRemoveQueued: (id: string) => void;
  /** Takes a waiting message out of the queue and returns it, to be edited in the box. */
  onEditQueued: (id: string) => Promise<{ text: string; images: OutgoingImage[] }>;
  /** Interrupt the running turn and send this waiting message next. */
  onSendQueuedNow: (id: string) => void;
  /** Text to put back in the box, such as messages that were waiting when the user pressed stop. */
  restore: { text: string; nonce: number } | null;
  /** The thread's latest message from the user, as it was sent; null when there is none or it is not known yet. */
  lastSent?: { turnId: string; text: string } | null;
}

const ACTION_ICONS: Record<string, (p: { size?: number }) => JSX.Element> = {
  new: IconEdit,
  model: IconCube,
  permission: IconShield,
  rename: IconEdit,
  pin: IconPin,
  archive: IconArchive,
  fork: IconFork,
  import: IconImport,
  status: IconInfo,
  export: IconCopy,
  help: IconHelp,
};

type Selectable = Exclude<MenuRow, { type: 'heading' }>;

export function Composer({
  draftKey,
  running,
  disabled,
  placeholder,
  onSend,
  onStop,
  header,
  planMode,
  left,
  right,
  commands,
  goal,
  goalState,
  actions,
  hiddenCommands,
  onAction,
  attachBlocked,
  attachNote,
  onError,
  queued: queuedProp,
  modelQueue = null,
  onRemoveQueued,
  onEditQueued,
  onSendQueuedNow,
  restore,
  lastSent = null,
}: Props) {
  const narrow = useIsNarrow();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [menuBusy, setMenuBusy] = useState(false);
  /** The send has been going for a while: a picture uploading over a slow line, an agent waking up. */
  const [slow, setSlow] = useState(false);
  const [images, setImages] = useState<PreparedImage[]>([]);
  // Waiting messages being taken back into the box, hidden before the thread list catches up.
  const [taking, setTaking] = useState<string[]>([]);
  const queued = useMemo(() => taking.length ? queuedProp.filter(q => !taking.includes(q.id)) : queuedProp, [queuedProp, taking]);
  useEffect(() => {
    setTaking(current => current.length && current.some(id => !queuedProp.some(q => q.id === id)) ? current.filter(id => queuedProp.some(q => q.id === id)) : current);
  }, [queuedProp]);
  const [preparing, setPreparing] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addQuery, setAddQuery] = useState('');
  const [catalog, setCatalog] = useState<'all' | 'actions' | 'skills'>('all');
  /** A picture being looked at full size before it goes out. */
  const [preview, setPreview] = useState<PreparedImage | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const revealSelection = useRef(false);
  const paletteRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const isTouch = useIsTouch();

  useEffect(() => { setAddOpen(false); }, [draftKey, narrow]);
  useEffect(() => {
    if (!addOpen) return;
    const outside = (event: PointerEvent) => {
      if (!paletteRef.current?.contains(event.target as Node) && !addRef.current?.contains(event.target as Node)) {
        setAddOpen(false);
        setDismissed(true);
      }
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setAddOpen(false);
      setDismissed(true);
      (addRef.current ?? ref.current)?.focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [addOpen]);

  const runMenuCommand = async (command: string) => {
    setMenuBusy(true);
    try {
      // Menu controls are independent of the unsent draft and its attachments.
      await onSend(command, []);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally { setMenuBusy(false); }
  };
  // Starting a goal works like Codex: the command lands in the box, the objective is typed
  // right after it and sent as an ordinary message. No form of the composer's own.
  const chooseGoal = (action: GoalAction) => {
    if (action === 'set') {
      setText(current => /^\s*\/goal\b/.test(current) ? current : `/goal ${current}`);
      requestAnimationFrame(() => {
        const el = ref.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      });
      return;
    }
    void runMenuCommand(action === 'show' ? '/goal' : `/goal ${action}`);
  };

  const rows = useMemo(() => {
    const query = addOpen ? addQuery : slashQuery(text);
    if (query === null) return [];
    if (addOpen && catalog !== 'all') {
      const entries = commands.filter(c => catalog === 'skills' ? c.kind !== 'command' : c.kind !== 'skill' && c.kind !== 'prompt');
      return searchCommands(query, catalog === 'actions' ? actions.filter(a => !['help', 'skills'].includes(a.id)) : [], entries, [...hiddenCommands, ...(goal ? ['goal'] : [])]);
    }
    const additions: HubAction[] = [{ id: 'composer:attach', label: t('composerMenu.attach'), hint: '', keywords: ['attach', 'image', 'video', 'file', '附件', '文件'] }];
    if (planMode) additions.push({ id: 'composer:plan', label: t('composerMenu.planMode'), hint: planMode.disabled ? t('composer.planAfterTurn') : planMode.active ? t('composer.planActive') : t('composerMenu.planOn'), keywords: ['plan', '计划'], disabled: planMode.disabled });
    // One entry for every agent, like the phone menu: starting a goal fills the box; what the
    // card cannot show is typed by hand.
    if (goal) {
      const blocked = running ? t('composerMenu.afterTurn') : planMode?.active ? t('composerMenu.planFirst') : '';
      additions.push({ id: 'composer:goal:set', label: t('goal.set'), hint: blocked || t('composer.goalSetHint'), keywords: ['目标', 'target'], disabled: !!blocked });
    }
    const group = (label: string, entries: HubAction[]): MenuRow[] => searchCommands(query, entries, []).map(row => row.type === 'heading' ? { ...row, label } : row);
    // Both desktop entry points share one catalog, ordering and filtering behavior.
    return [
      ...(!narrow ? group(t('composer.add'), additions) : []),
      ...searchCommands(query, actions, commands, hiddenCommands),
    ];
  }, [addOpen, addQuery, catalog, text, narrow, actions, commands, hiddenCommands, planMode, goal, running]);
  const selectable = useMemo(() => rows.filter((r): r is Selectable => r.type !== 'heading' && !(r.type === 'action' && r.action.disabled)), [rows]);
  const menuOpen = addOpen || (rows.length > 0 && !dismissed);

  // Typing changes the list, so the highlight goes back to the first row and a dismissed menu can reopen.
  useEffect(() => {
    setActive(0);
    setDismissed(false);
  }, [text]);
  useEffect(() => { setActive(0); }, [addQuery, addOpen]);

  useEffect(() => {
    if (!revealSelection.current) return;
    revealSelection.current = false;
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);
  useEffect(() => { if (menuOpen) paletteRef.current?.scrollTo({ top: 0 }); }, [addQuery, text, menuOpen]);

  // Text handed back goes in front of whatever is being typed, so nothing is lost either way.
  useEffect(() => {
    if (!restore) return;
    setText((current) => [restore.text, current].filter((s) => s.trim()).join('\n\n'));
    ref.current?.focus();
    // Keyed on the nonce: the same text can be handed back twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restore?.nonce]);

  // Each thread has its own draft. Swapped before paint, so the old text never flashes in the new
  // thread. The pictures' object URLs go with the draft; they are released once it is sent.
  // A send this page never saw succeed: the page was reloaded meanwhile, or the answer got lost on
  // the way back. The box stays marked for as long as it still holds exactly what was sent.
  const inFlight = useRef<Sending | null>(null);
  const unsettled = inFlight.current !== null && asMessage(text) === inFlight.current.text;
  const draftRef = useRef<Draft>({ text, images });
  draftRef.current = { text, images, ...(unsettled ? { sending: inFlight.current! } : {}) };
  const sendingRef = useRef(false);
  const previousDraftKey = useRef(draftKey);
  useLayoutEffect(() => {
    if (draftKey === undefined) return;
    const previous = previousDraftKey.current;
    previousDraftKey.current = draftKey;
    // Creating a thread changes its draft key before uploads finish. Keep that first message
    // in the new thread so an interrupted upload still has an editable, retryable draft.
    const firstMessage = sendingRef.current && (previous === 'new' || previous?.endsWith(':new'));
    const saved = loadDraft(draftKey) ?? (firstMessage ? draftRef.current : undefined);
    inFlight.current = saved?.sending ?? null;
    setText(saved?.text ?? '');
    setImages(saved?.images ?? []);
    if (saved?.focus) {
      saved.focus = false;
      ref.current?.focus();
    }
    return () => {
      const draft = draftRef.current;
      // A message on its way out is not a draft: sending a new thread's first message opens the
      // thread mid-send, and the text must not stay behind in the next new thread.
      keepDraft(draftKey, sendingRef.current ? null : draft);
    };
  }, [draftKey]);

  const keyRef = useRef(draftKey);
  keyRef.current = draftKey;
  // Whether that message arrived is for the thread to say. Once a new turn of the user's carries
  // this very text, it is no draft: left in the box it would look unsent, and be sent a second time.
  useEffect(() => {
    const mark = inFlight.current;
    if (!unsettled || !mark || !lastSent) return;
    if (lastSent.turnId === mark.after || lastSent.text.trim() !== mark.text.trim()) return;
    inFlight.current = null;
    draftRef.current.images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    setImages([]);
    setText('');
    if (draftKey !== undefined) keepDraft(draftKey, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unsettled, lastSent?.turnId, lastSent?.text, draftKey]);
  // Without a draft to keep them, thumbnails left in the box are released when it goes away.
  useEffect(
    () => () => {
      if (draftKey === undefined) {
        draftRef.current.images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const addFiles = async (files: File[]) => {
    // Only pictures can be attached; anything else is turned away.
    const pictures = files.filter((f) => f.type.startsWith('image/'));
    if (pictures.length < files.length) onError(t('composer.fileFailed'));
    if (pictures.length === 0) return;
    if (attachBlocked) return onError(attachBlocked);
    const room = MAX_IMAGES - images.length - preparing;
    if (room <= 0) return onError(t('composer.maxImages', MAX_IMAGES));
    if (pictures.length > room) onError(t('composer.maxImagesDropped', MAX_IMAGES));
    const picked = pictures.slice(0, room);

    setPreparing((n) => n + picked.length);
    const results = await Promise.allSettled(picked.map(prepareImage));
    setPreparing((n) => n - picked.length);

    const ready = results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []));
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected');
    if (failed) onError(failed.reason instanceof Error ? failed.reason.message : t('composer.imageFailed'));
    if (ready.length) setImages((prev) => [...prev, ...ready]);
    ref.current?.focus();
  };

  // A waiting message goes back in the box, in front of anything typed, pictures included.
  const editQueued = async (id: string) => {
    // The row goes at once; waiting for the thread list to say so left it standing beside its own text.
    setTaking(current => [...current, id]);
    try {
      const taken = await onEditQueued(id);
      setText((current) => [taken.text, current].filter((s) => s.trim()).join('\n\n'));
      const room = MAX_IMAGES - images.length - preparing;
      if (taken.images.length > room) onError(t('composer.maxImagesNotRestored', MAX_IMAGES));
      const back = taken.images.slice(0, Math.max(0, room)).map(restoreImage);
      if (back.length) setImages((prev) => [...back, ...prev]);
      ref.current?.focus();
    } catch (e) {
      // The turn ahead of it may have ended in the meantime, taking the message out with it.
      if (e instanceof ApiError && e.status === 404) onError(t('composer.alreadySent'));
      else {
        setTaking(current => current.filter(other => other !== id));
        onError(e instanceof Error ? e.message : t('composer.takeBackFailed'));
      }
    }
  };

  const removeImage = (id: string) => {
    setImages((prev) => {
      const gone = prev.find((img) => img.id === id);
      if (gone) URL.revokeObjectURL(gone.previewUrl);
      return prev.filter((img) => img.id !== id);
    });
  };

  const choose = (row: Selectable) => {
    if (row.type === 'action' && row.action.disabled) return;
    if (row.type === 'action' && row.action.id === 'help') {
      // Everything there is to run is in this menu; show all of it.
      if (addOpen) { setAddQuery(''); if (!isTouch) searchRef.current?.focus(); }
      else { setText('/'); ref.current?.focus(); }
      return;
    }
    setAddOpen(false);
    setDismissed(true);
    ref.current?.focus();
    if (row.type === 'action') {
      if (!addOpen) setText('');
      const id = row.action.id;
      if (id === 'composer:attach') fileRef.current?.click();
      else if (id === 'composer:plan') planMode?.onChange(!planMode.active);
      else if (id.startsWith('composer:goal:')) chooseGoal(id.slice('composer:goal:'.length) as GoalAction);
      else onAction(id);
    } else {
      setText(current => addOpen ? commandDraft(row.command.name, current, commands) : `/${row.command.name} `);
    }
  };

  const browse = (section: 'actions' | 'skills') => {
    // Mount and focus inside the tap gesture so the browser allows focus at all. On a touch
    // screen the focus would raise the keyboard over the menu, so there the search stays
    // unfocused until the user taps it.
    flushSync(() => { setCatalog(section); setAddQuery(''); setAddOpen(true); setDismissed(true); });
    if (!isTouch) searchRef.current?.focus();
  };

  /**
   * Enter in the menu. A command typed out in full that takes nothing after it runs straight away,
   * as in a terminal; otherwise the highlighted entry is filled in for the user to finish.
   */
  const enter = (row: Selectable) => {
    if (!addOpen && row.type === 'command' && !row.command.hint && text.trim() === `/${row.command.name}`) {
      void submit();
      return;
    }
    choose(row);
  };

  const hasContent = text.trim() !== '' || images.length > 0;
  // On a phone, a box with nothing being written gives up the room: one line, back to two the
  // moment it is touched. Anything in it, waiting in it, or focus keeps the whole box; so does a
  // new thread's strip of agent and directory, which is the choice being made.
  const [focused, setFocused] = useState(false);
  // Folding moves everything above the box down. A tap elsewhere takes the focus at the press, so
  // the box waits for the finger to lift, and the tap to land where it was aimed, before it folds.
  // `leaving` calls off a fold that is still waiting.
  const pressing = useRef(false);
  const leaving = useRef<(() => void) | null>(null);
  const leave = () => {
    leaving.current?.();
    if (!pressing.current) return setFocused(false);
    const off = () => {
      document.removeEventListener('pointerup', fold, true);
      document.removeEventListener('pointercancel', fold, true);
    };
    const fold = () => {
      off();
      const timer = window.setTimeout(() => { leaving.current = null; setFocused(false); }, 0);
      leaving.current = () => window.clearTimeout(timer);
    };
    document.addEventListener('pointerup', fold, true);
    document.addEventListener('pointercancel', fold, true);
    leaving.current = off;
  };
  // A touch on the box itself never folds it. Safari moves the focus at the click, not the
  // press, and a button cannot hold it, so the focus ends up on the page. Folding then would pull
  // the button, and the menu it opened, away. The box stays whole until a tap lands outside it.
  // The press ends with the click, or shortly after the finger lifts.
  const boxRef = useRef<HTMLDivElement>(null);
  const boxPress = useRef<number | null>(null);
  const endBoxPress = () => {
    if (boxPress.current === null) return;
    window.clearTimeout(boxPress.current);
    boxPress.current = null;
  };
  useEffect(() => {
    const down = (e: PointerEvent) => {
      pressing.current = true;
      // With the focus left on the page (see above) there is no blur to fold on; a tap elsewhere is.
      if (e.target instanceof Node && !boxRef.current?.contains(e.target)) {
        endBoxPress();
        if (!boxRef.current?.contains(document.activeElement)) leave();
      }
    };
    const up = () => {
      pressing.current = false;
      if (boxPress.current !== null) {
        window.clearTimeout(boxPress.current);
        boxPress.current = window.setTimeout(endBoxPress, 1000);
      }
    };
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
    return () => {
      document.removeEventListener('pointerdown', down, true);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);
      leaving.current?.();
    };
  }, []);
  const collapsed = narrow && !header && !hasContent && queued.length === 0 && !modelQueue && preparing === 0 && !focused;
  const goalControl = text.trim() === '/goal' ? 'show' : text.trim() === '/goal pause' ? 'pause' : text.trim() === '/goal clear' ? 'clear' : null;
  const immediate = !!goalControl && !!goal?.controls.includes(goalControl) && !images.length;
  const queueFull = running && !immediate && queued.length >= MAX_QUEUED;
  const canSend = hasContent && !busy && !menuBusy && !disabled && preparing === 0 && !queueFull;
  const sendPress = useRef<{ pointerId: number; x: number; y: number; moved: boolean } | null>(null);
  const suppressSendClick = useRef(false);
  const sendClickReset = useRef<number | null>(null);
  const composing = useRef(false);
  const pendingImeSend = useRef(false);
  const imeSendTask = useRef<number | null>(null);
  useEffect(() => () => {
    if (sendClickReset.current !== null) window.clearTimeout(sendClickReset.current);
    if (imeSendTask.current !== null) window.clearTimeout(imeSendTask.current);
    sendClickReset.current = imeSendTask.current = null;
    sendPress.current = null;
    suppressSendClick.current = composing.current = pendingImeSend.current = false;
  }, [draftKey]);
  const modelQueueNotice = modelQueue
    ? {
        text: modelQueue.position === null ? t('composer.modelQueueWaiting') : t('composer.modelQueuePosition', modelQueue.position),
        message: modelQueue.message,
      }
    : null;

  const submit = async () => {
    if (!canSend || sendingRef.current || pendingImeSend.current) return;
    if (composing.current) {
      // Keep this tap: blur asks the native IME to commit, and compositionend resumes the send.
      pendingImeSend.current = true;
      ref.current?.blur();
      return;
    }
    if (images.length && attachBlocked) return onError(attachBlocked);
    setBusy(true);
    sendingRef.current = true;
    const message = asMessage(text);
    const mark: Sending = { text: message, after: lastSent?.turnId ?? null };
    const sentFrom = draftKey;
    // On the device the draft now says it is on its way. Should the page be reloaded before the
    // send comes back, it is neither lost nor taken for something still to send.
    if (sentFrom !== undefined) keepDraft(sentFrom, { text, images, sending: mark });
    const slowTimer = setTimeout(() => setSlow(true), SLOW_SEND_MS);
    try {
      await onSend(message, images);
      // Forget the sent draft at once, but retain words committed while its request was pending.
      const remainingText = draftRef.current.text === text ? '' : draftRef.current.text;
      if (sentFrom !== undefined) keepDraft(sentFrom, null);
      if (remainingText && keyRef.current !== undefined) keepDraft(keyRef.current, { text: remainingText, images: [] });
      inFlight.current = null;
      images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      setImages([]);
      setText(current => current === text ? '' : current);
    } catch {
      // The caller shows the error; keep the draft and the pictures so nothing is lost. The mark
      // stays too: a send can fail on this side (the page being reloaded, the answer lost on a bad
      // line) after the daemon took the message, and then the thread will show it.
      inFlight.current = mark;
    } finally {
      clearTimeout(slowTimer);
      setSlow(false);
      sendingRef.current = false;
      setBusy(false);
      ref.current?.focus();
    }
  };
  const submitRef = useRef(submit);
  submitRef.current = submit;
  const compositionEnd = () => {
    composing.current = false;
    if (!pendingImeSend.current) return;
    if (imeSendTask.current !== null) window.clearTimeout(imeSendTask.current);
    // Some IMEs emit their final input after compositionend. Read the committed DOM value and
    // latest submit in the next task; compositionend, rather than elapsed time, permits sending.
    imeSendTask.current = window.setTimeout(() => {
      imeSendTask.current = null;
      if (composing.current || !pendingImeSend.current || !ref.current) return;
      pendingImeSend.current = false;
      const committedText = ref.current.value;
      flushSync(() => setText(committedText));
      void submitRef.current();
    }, 0);
  };
  const endSendPress = (button: HTMLButtonElement, pointerId: number) => {
    if (button.hasPointerCapture(pointerId)) button.releasePointerCapture(pointerId);
    sendPress.current = null;
    sendClickReset.current = window.setTimeout(() => {
      sendClickReset.current = null;
      suppressSendClick.current = false;
    }, SEND_CLICK_SUPPRESS_MS);
  };
  const sendPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (sendClickReset.current !== null) window.clearTimeout(sendClickReset.current);
    sendClickReset.current = null;
    suppressSendClick.current = false;
    if (e.pointerType === 'mouse' || !canSend) return;
    // On phones, letting the button take focus can make the keyboard close and swallow the click.
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    suppressSendClick.current = true;
    sendPress.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
  };
  const sendPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const press = sendPress.current;
    if (!press || press.pointerId !== e.pointerId) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) > SEND_TAP_SLOP) press.moved = true;
  };
  const sendPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const press = sendPress.current;
    if (!press || press.pointerId !== e.pointerId) return;
    e.preventDefault();
    const target = document.elementFromPoint(e.clientX, e.clientY);
    endSendPress(e.currentTarget, e.pointerId);
    if (!press.moved && target && e.currentTarget.contains(target)) void submit();
  };
  const sendPointerCancel = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (sendPress.current?.pointerId === e.pointerId) {
      endSendPress(e.currentTarget, e.pointerId);
    }
  };

  const menuKeyDown = (e: KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>): boolean => {
    if (menuOpen && !isImeComposing(e)) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const step = e.key === 'ArrowDown' ? 1 : -1;
        revealSelection.current = true;
        if (selectable.length) setActive((i) => (i + step + selectable.length) % selectable.length);
        return true;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const row = selectable[active] ?? selectable[0];
        if (row) { if (e.key === 'Enter') enter(row); else choose(row); }
        return true;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        setAddOpen(false);
        setDismissed(true);
        ref.current?.focus();
        return true;
      }
    }
    return false;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuKeyDown(e)) return;
    // Desktop: Enter sends, Shift+Enter inserts a newline. Touch keyboards get the send button instead.
    const fine = typeof window !== 'undefined' && window.matchMedia?.('(pointer: fine)').matches;
    if (fine && e.key === 'Enter' && !e.shiftKey && !isImeComposing(e)) {
      e.preventDefault();
      void submit();
    }
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...e.clipboardData.files];
    if (files.length) {
      e.preventDefault();
      void addFiles(files);
    }
  };

  const hasFiles = (e: DragEvent) => [...e.dataTransfer.types].includes('Files');
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    setDragOver(false);
    void addFiles([...e.dataTransfer.files]);
  };

  return (
    <div className="composer">
      <div className="composer-anchor">
        {menuOpen && (
          <div className={`command-menu${addOpen && catalog !== 'all' ? ' catalog-menu' : ''}`} ref={paletteRef}>
            {addOpen && <label className="pop-search command-search"><IconSearch size={16} /><input ref={searchRef} autoFocus={!isTouch} aria-label={catalog === 'all' ? t('composer.searchAll') : catalog === 'skills' ? t('skills.search') : t('composer.searchActions')} placeholder={catalog === 'all' ? t('composer.searchAllPlaceholder') : catalog === 'skills' ? t('composer.searchSkillsPlaceholder') : t('composer.searchActionsPlaceholder')} value={addQuery} onChange={e => setAddQuery(e.target.value)} onKeyDown={menuKeyDown} /></label>}
            <div className="command-menu-list" role="listbox" aria-label={t('commands.kind.command')} ref={listRef}>
            {rows.map((row) => {
              if (row.type === 'heading') {
                return (
                  <div key={`h-${row.label}`} className="command-heading" role="presentation">
                    {row.label}
                  </div>
                );
              }
              const index = selectable.indexOf(row);
              const selected = index >= 0 && row === (selectable[active] ?? selectable[0]);
              const isAction = row.type === 'action';
              const plan = isAction && row.action.id === 'composer:plan';
              const checked = plan ? planMode?.active : undefined;
              const Icon = isAction ? row.action.id === 'composer:attach' ? IconPaperclip : plan ? IconList : row.action.id.startsWith('composer:goal:') ? IconTarget : (ACTION_ICONS[row.action.id] ?? IconSlash) : row.command.kind === 'skill' ? IconSparkle : IconSlash;
              return (
                <button
                  key={isAction ? `a-${row.action.id}` : `c-${row.command.name}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  aria-checked={checked}
                  disabled={isAction && row.action.disabled}
                  data-active={selected}
                  className={`command-item${selected ? ' active' : ''}`}
                  onPointerMove={e => { if (e.pointerType === 'mouse' && index >= 0) { revealSelection.current = false; setActive(index); } }}
                  // Keep focus in the input so the keyboard stays open on phones.
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => choose(row)}
                >
                  <span className="command-icon">
                    <Icon size={16} />
                  </span>
                  <span className="command-content">{isAction ? (
                    <>
                      <span className="command-label">{row.action.label}</span>
                      <span className="command-desc">{row.action.hint}</span>
                    </>
                  ) : (
                    <>
                      <span className="command-name">
                        /{row.command.name}
                        {row.command.hint && <span className="command-hint"> {row.command.hint}</span>}
                      </span>
                      <span className="command-desc">{row.command.description}</span>
                    </>
                  )}</span>
                  {checked && <span className="command-check"><IconCheck size={16} /></span>}
                </button>
              );
            })}
            {!rows.length && <p className="pop-note">{t('composer.noMatch')}</p>}
            </div>
            {addOpen && catalog === 'skills' && actions.some(a => a.id === 'skills') && <button type="button" className="composer-menu-footer" onClick={() => { setAddOpen(false); setDismissed(true); onAction('skills'); }}>{t('composer.manageSkills')}<IconChevron size={14} /></button>}
          </div>
        )}

        {goalState && goal?.live && <GoalCard goal={goalState} running={running} actions={goal.actions} controls={goal.controls} onAction={chooseGoal} />}
        <div
          ref={boxRef}
          className={`composer-box${dragOver ? ' drag-over' : ''}${collapsed ? ' collapsed' : ''}`}
          onPointerDownCapture={(e) => {
            if (e.pointerType === 'mouse') return;
            if (boxPress.current !== null) window.clearTimeout(boxPress.current);
            boxPress.current = window.setTimeout(endBoxPress, 1500);
          }}
          onClickCapture={() => {
            if (boxPress.current !== null) window.setTimeout(endBoxPress, 0);
          }}
          onFocus={() => {
            leaving.current?.();
            leaving.current = null;
            setFocused(true);
          }}
          onBlur={(e) => {
            // A tap on a button in the box moves the focus to it; the box stays whole so the
            // change of shape cannot pull the button out from under the finger.
            if (!e.currentTarget.contains(e.relatedTarget) && boxPress.current === null) leave();
          }}
          onDragOver={(e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
          }}
          onDrop={onDrop}
        >
          {header && <div className="composer-context">{header}</div>}

          {modelQueueNotice && (
            <div className="queue model-queue" aria-label={t('composer.modelQueue')} aria-live="polite">
              <div className="queue-item model-queue-item">
                <span className="queue-badge">{t('composer.modelQueue')}</span>
                <span className="queue-text model-queue-text" title={modelQueueNotice.message ?? modelQueueNotice.text}>{modelQueueNotice.text}</span>
              </div>
            </div>
          )}

          {queued.length > 0 && (
            <div className="queue" aria-label={t('composer.queued')}>
              {queued.map((q) => (
                <div key={q.id} className="queue-entry">
                <div className="queue-item">
                  <span className={`queue-badge${q.error ? ' failed' : ''}`}>{t(q.error ? 'composer.notSent' : 'composer.queue')}</span>
                  <span className={`queue-text${q.channel?.parts ? ' parts' : ''}`} title={q.text}>
                    {q.channel?.parts ? channelRows(q.channel, q.text).map((row, index) => <span key={index} className={row.kind === 'note' ? 'muted' : undefined}>{index > 0 && ' · '}{row.text}</span>) : q.text || (q.files ? t('uploads.file') : q.videos ? t('videos.video') : t('images.image'))}
                    {q.images > 0 && q.text && <span className="muted"> · {t('composer.images', q.images)}</span>}
                    {!!q.videos && <span className="muted"> · {t('composer.videos', q.videos!)}</span>}
                    {!!q.files && <span className="muted"> · {t('composer.files', q.files!)}</span>}
                  </span>
                  {!!q.targets?.length && <span className="queue-targets" title={t('composer.queueTargets', q.targets.join(', '))} aria-label={t('composer.queueTargets', q.targets.join(', '))}><span className="queue-target-names">@{q.targets.join(', ')}</span><span className="queue-target-count">{t('composer.queueTargetCount', q.targets.length)}</span></span>}
                  <button type="button" className="queue-now" title={t(q.error ? 'common.retry' : 'composer.sendNowHint')} onClick={() => onSendQueuedNow(q.id)}>
                    {t(q.error ? 'common.retry' : 'composer.sendNow')}
                  </button>
                  <button type="button" className="queue-remove" aria-label={t('composer.editQueued')} title={t('composer.edit')} onClick={() => void editQueued(q.id)}>
                    <IconEdit size={13} />
                  </button>
                  <button type="button" className="queue-remove" aria-label={t('composer.cancelQueued')} onClick={() => onRemoveQueued(q.id)}>
                    <IconClose size={13} />
                  </button>
                </div>
                {q.error && <div className="queue-error" role="alert" title={t('composer.queueFailed', q.error)}><span className="queue-error-text">{t('composer.queueFailed', q.error)}</span></div>}
                </div>
              ))}
            </div>
          )}

          {(images.length > 0 || preparing > 0) && (
            <div className="attachments" aria-label={t('composer.attachments')}>
              {images.map((img) => (
                <div key={img.id} className="attachment">
                  <button
                    type="button"
                    className="attachment-view"
                    aria-label={t('preview.label', img.name)}
                    title={t('files.preview')}
                    onClick={() => setPreview(img)}
                  >
                    <img src={img.previewUrl} alt={img.name} />
                  </button>
                  <button type="button" className="attachment-remove" aria-label={t('composer.remove', img.name)} onClick={() => removeImage(img.id)}>
                    <IconClose size={12} />
                  </button>
                </div>
              ))}
              {Array.from({ length: preparing }, (_, i) => (
                <div key={`p-${i}`} className="attachment pending" aria-label={t('composer.processing')} />
              ))}
            </div>
          )}

          <textarea
            ref={ref}
            rows={1}
            value={text}
            placeholder={dragOver ? t('composer.drop') : collapsed && running ? t('composer.appendOne') : placeholder}
            onChange={(e) => setText(e.target.value)}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={compositionEnd}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            disabled={disabled}
            aria-label={t('composer.message')}
            aria-expanded={menuOpen}
            aria-autocomplete="list"
          />

          <div className="composer-bar">
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                void addFiles([...(e.target.files ?? [])]);
                e.target.value = ''; // so picking the same file again fires a change
              }}
            />
            {narrow ? <Popover
              label={t('composer.addAndMode')}
              triggerClassName="tool-btn icon-only composer-add"
              triggerLabel={t('composer.addAndMode')}
              triggerTitle={t('composer.addAndModeHint')}
              panelClassName="composer-add-menu"
              trigger={<IconPlus size={20} />}
              disabled={disabled || menuBusy || busy}
            >
              {close => <ComposerMenu close={close} commands={commands} planMode={planMode} goal={goal} running={running}
                attachDisabled={false}
                attachTitle={attachBlocked ? t('composer.attachBlocked', attachBlocked) : attachNote ?? t('composer.attachHint')}
                onAttach={() => fileRef.current?.click()} onGoal={chooseGoal} onBrowse={browse} />}
            </Popover> : <button ref={addRef} type="button" className="tool-btn icon-only composer-add"
              aria-label={t('composer.addAndMode')} aria-haspopup="listbox" aria-expanded={addOpen} title={t('composer.addActionsSkills')} disabled={disabled || menuBusy || busy}
              onClick={() => { setCatalog('all'); setAddQuery(''); setAddOpen(!addOpen); setDismissed(true); }}><IconPlus size={20} /></button>}
            {/* Plan mode is a pill of its own: what is on shows, and × takes it off. */}
            {planMode?.active && (
              <button
                type="button"
                className="plan-pill"
                aria-label={t('composer.planExit')}
                title={t('composer.planExit')}
                onClick={() => planMode.onChange(false)}
              >
                <IconList size={15} />
                <span>{t('composer.planPill')}</span>
                <IconClose size={13} />
              </button>
            )}
            {!collapsed && left}
            <div className="spacer" />
            {!collapsed && right}
            {running && (
              <button className="send-btn stop" onClick={onStop} aria-label={t('composer.stop')} title={t('composer.stopTurn')}>
                <IconStop size={16} />
              </button>
            )}
            {/* While a turn runs the send button appears only once there is something to queue. */}
            {(!running || hasContent) && (
              <button
                className={`send-btn${slow ? ' sending' : ''}`}
                onPointerDown={sendPointerDown}
                onPointerMove={sendPointerMove}
                onPointerUp={sendPointerUp}
                onPointerCancel={sendPointerCancel}
                onClick={(e) => {
                  // Keyboard and assistive activation have no pointer click count.
                  if (e.detail !== 0 && suppressSendClick.current) {
                    e.preventDefault();
                    return;
                  }
                  void submit();
                }}
                disabled={!canSend}
                aria-label={slow ? t('composer.sending') : running && !immediate ? t('composer.queueSend') : t('common.send')}
                title={slow ? t('composer.sendingEllipsis') : queueFull ? t('composer.queueMax', MAX_QUEUED) : running ? immediate ? t('composer.goalNow') : t('composer.afterTurn') : undefined}
              >
                {slow ? <span className="send-spinner" aria-hidden="true" /> : <IconSend size={16} />}
              </button>
            )}
          </div>
        </div>
      </div>
      {preview && <Lightbox url={preview.previewUrl} name={preview.name} onClose={() => setPreview(null)} />}
    </div>
  );
}
