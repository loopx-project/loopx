import { referencePreview, mergeReferences, splitReferences, withReferences, type ContextReference } from '../context-references';
import { ReferenceChips, ReferencePicker, type ReferenceSource } from './ContextReferences';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react';
import { flushSync } from 'react-dom';
import { ApiError, MAX_QUEUED, type AgentMention, type ModelQueue, type OutgoingImage, type QueuedMessage, type SlashCommand } from '../api';
import { commandDraft, searchCommands, slashQuery, type HubAction, type MenuRow } from '../commands';
import { useIsNarrow, useIsTouch } from '../hooks';
import { readDrafts, SendCancelled, writeDraft, type Sending } from '../drafts';
import { sizeLabel } from '../files';
import { mentionAt } from '../mentions';
import { takeoverMention } from '../handover';
import { MAX_IMAGES, prepareImage, restoreImage, type PreparedImage } from '../images';
import { channelRows, splitNotes, withNotes, type Note } from '../timeline';
import { Popover } from './Popover';
import { ComposerMenu, goalMenuActions, type GoalAction, type GoalCapability, type WritingMode } from './ComposerMenu';
import { GoalCard } from './GoalCard';
import { Lightbox } from './Lightbox';
import { VideoPreview } from './VideoPreview';
import { isPlayable, isSupportedVideo, MAX_UPLOADS, MAX_VIDEOS, prepareFile, prepareVideo, type PreparedVideo } from '../videos';
import {
  IconArchive,
  IconAgents,
  AgentMark,
  IconClose,
  IconCheck,
  IconChevron,
  IconComputer,
  IconCopy,
  IconCube,
  IconEdit,
  IconFolder,
  IconFork,
  IconHand,
  IconHelp,
  IconImport,
  IconInfo,
  IconList,
  IconPaperclip,
  IconPen,
  IconFile,
  IconPlay,
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

/** A passage noted for the next message. The id ties it to its highlight in the conversation. */
export interface DraftNote extends Note {
  id: string;
}

/** What is left unsent in a box: the words, noted passages and pictures. */
interface Draft {
  mentions?: AgentMention[];
  references?: ContextReference[];
  text: string;
  notes: DraftNote[];
  images: PreparedImage[];
  videos?: PreparedVideo[];
  /** The message is on its way out. See `StoredDraft.sending`. */
  sending?: Sending;
  /** A newly prepared handover should put the cursor in its input. */
  focus?: boolean;
}

/** A box's content as the one text it is sent as: the notes, numbered, then the words. */
const asMessage = (text: string, notes: Note[], references: ContextReference[] = []) => withNotes(notes, withReferences(references, text));

/** How long a send may take before the button says it is still going. */
const SLOW_SEND_MS = 400;
/** How far a finger can drift and still count as tapping the mobile send button. */
const SEND_TAP_SLOP = 12;
/** Mobile browsers may deliver the compatibility click after pointerup; keep it from sending twice. */
const SEND_CLICK_SUPPRESS_MS = 500;

/** Safari may report composition as ended on the Enter keydown that still belongs to the IME. */
export const isImeComposing = (event: Pick<KeyboardEvent<HTMLElement>, 'nativeEvent'>): boolean =>
  event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229;

let noteSeq = 0;
const noteId = () => `note-${Date.now()}-${++noteSeq}`;

/**
 * Unsent drafts by thread, so what is typed in one thread stays there: switching away keeps it,
 * switching back brings it back, and another thread starts with its own. Words and notes are also
 * kept on the device and outlast a reload; pictures only last as long as the page.
 */
const drafts = new Map<string, Draft>();

const storage = (): Storage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

function loadDraft(key: string): Draft | undefined {
  const inMemory = drafts.get(key);
  if (inMemory) return inMemory;
  const store = storage();
  const stored = store ? readDrafts(store)[key] : undefined;
  return stored && { text: stored.text, references: stored.references, mentions: stored.mentions, notes: stored.notes.map((n) => ({ ...n, id: noteId() })), images: [], sending: stored.sending };
}

function keepDraft(key: string, draft: Draft | null): void {
  if (draft && (draft.text.trim() || draft.notes.length || draft.images.length || draft.videos?.length || draft.mentions?.length || draft.references?.length)) drafts.set(key, draft);
  else drafts.delete(key);
  const store = storage();
  if (store) writeDraft(store, key, draft);
}

/** Seeds a newly created thread before the composer opens it, including an immediate durable copy. */
export function prefillComposerDraft(key: string, text: string): void {
  keepDraft(key, { text, notes: [], images: [], focus: true });
}

/** How long typing pauses before the draft is written down. */
const SAVE_DELAY_MS = 400;

interface Props {
  referenceSource?: ReferenceSource;
  reference?: { value: ContextReference; nonce: number } | null;
  onReferenceConsumed?: (id: string) => void;
  /**
   * Whose draft the box holds: a thread's id, or one key for the new thread being started. Without
   * one the box keeps nothing once it is gone, as for a side chat, which is new each time.
   */
  draftKey?: string;
  running: boolean;
  disabled?: boolean;
  placeholder: string;
  mentionOptions?: AgentMention[];
  collaboration?: (select: (mention: AgentMention) => void, selected: AgentMention[], anchorRef: RefObject<HTMLButtonElement>, openSignal: number) => ReactNode;
  collaborationActive?: boolean;
  mentionDirectories?: string[];
  /** Browse another machine's folders for an instance there; `choose` sets that one's folder. */
  onBrowseMentionDir?: (mention: AgentMention, choose: (cwd: string) => void) => void;
  /** A control of the app's own on an agent's line, such as the model its member starts with. */
  mentionExtra?: (mention: AgentMention) => ReactNode;
  /** The mark of the agent behind a choice, for the @ list. */
  mentionIcon?: (mention: AgentMention) => string | undefined;
  /** Report the created thread's scoped draft key before uploads, independently of navigation. */
  onSend: (text: string, images: PreparedImage[], mentions?: AgentMention[], videos?: PreparedVideo[], onCreated?: (draftKey: string) => void) => Promise<void> | void;
  onStop: () => void;
  /** Strip above the text area: which agent and which directory. */
  header: ReactNode;
  /** Native planning is offered in the add menu only when the agent supports it. */
  planMode?: { active: boolean; disabled: boolean; onChange: (active: boolean) => void };
  /** Writing mode, offered next to plan mode. */
  writingMode?: WritingMode;
  /**
   * A band above the input, such as writing mode's stages. As a function it is handed `take`, which
   * empties the box and returns what was written, for a control that carries the draft elsewhere.
   */
  banner?: ReactNode | ((take: () => string) => ReactNode);
  /**
   * The thread's agent (`current`, shown as `from`) is out of plan: one instance brought in with @
   * takes the thread over through `go`, with what was written as its note, instead of being sent work.
   */
  takeover?: { from: string; current: string; go: (agent: string, note: string) => Promise<void> };
  /** Controls at the left of the bottom bar, after the add menu. */
  left: ReactNode;
  /** Controls at the right of the bottom bar, before the send button. */
  right: ReactNode;
  /** Host-owned extension actions stay reachable when the phone composer is collapsed. */
  composerActions?: ReactNode;
  voiceControls?: ReactNode;
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
  attachmentsBlocked?: string;
  /** New team tasks accept pictures but cannot upload other files before a thread exists. */
  uploadsBlocked?: string;
  /** Something worth knowing about how attached pictures will be read, shown on the attach button. */
  attachNote?: string | null;
  onError: (message: string) => void;
  /** Messages sent while a turn was running, waiting to go out. */
  queued: QueuedMessage[];
  /** The native model queue for the running turn, such as Trae's live queue position. */
  modelQueue?: ModelQueue | null;
  onRemoveQueued: (id: string) => void;
  /** Takes a waiting message out of the queue and returns it, to be edited in the box. */
  /** Videos and files may come later than the text: restoring them can mean downloading them again. */
  onEditQueued: (id: string) => Promise<{ text: string; images: OutgoingImage[]; videos?: PreparedVideo[] | Promise<PreparedVideo[]>; mentions?: AgentMention[] }>;
  /** Interrupt the running turn and send this waiting message next. */
  onSendQueuedNow: (id: string) => void;
  /** Text to put back in the box, such as messages that were waiting when the user pressed stop. */
  restore: { text: string; nonce: number; mentions?: AgentMention[] } | null;
  /** A passage noted out of a message, to go along with what is typed. Each one adds to the list. */
  note: (DraftNote & { nonce: number }) | null;
  /** Acknowledge delivery so the parent does not replay the note after this box remounts. */
  onNoteConsumed?: (id: string) => void;
  /** Told which notes are in the box, so their passages can stay highlighted in the conversation. */
  onNotesChange?: (notes: DraftNote[]) => void;
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
  writingMode,
  banner,
  takeover,
  left,
  right,
  composerActions,
  voiceControls,
  commands,
  goal,
  goalState,
  actions,
  hiddenCommands,
  onAction,
  attachBlocked,
  attachmentsBlocked,
  uploadsBlocked,
  attachNote,
  onError,
  queued: queuedProp,
  modelQueue = null,
  onRemoveQueued,
  onEditQueued,
  onSendQueuedNow,
  restore,
  note,
  onNoteConsumed,
  onNotesChange,
  lastSent = null,
  mentionOptions = [],
  collaboration,
  collaborationActive,
  mentionDirectories = [],
  onBrowseMentionDir,
  mentionIcon,
  mentionExtra,
  referenceSource,
  reference,
  onReferenceConsumed,
}: Props) {
  const narrow = useIsNarrow();
  const [text, setText] = useState('');
  const [references, setReferences] = useState<ContextReference[]>([]);
  const [referenceSignal, setReferenceSignal] = useState(0);
  const [referenceQuery, setReferenceQuery] = useState('');
  const [mentions, setMentions] = useState<AgentMention[]>([]);
  const [mentionActive, setMentionActive] = useState(0);
  const [mentionDismissed, setMentionDismissed] = useState(false);
  const [selection, setSelection] = useState({ text: '', start: 0, end: 0 });
  const [notes, setNotes] = useState<DraftNote[]>([]);
  const [busy, setBusy] = useState(false);
  const [menuBusy, setMenuBusy] = useState(false);
  /** The send has been going for a while: a picture uploading over a slow line, an agent waking up. */
  const [slow, setSlow] = useState(false);
  const [images, setImages] = useState<PreparedImage[]>([]);
  const [videos, setVideos] = useState<PreparedVideo[]>([]);
  // Waiting messages being taken back into the box, hidden before the thread list catches up.
  const [taking, setTaking] = useState<string[]>([]);
  const queued = useMemo(() => taking.length ? queuedProp.filter(q => !taking.includes(q.id)) : queuedProp, [queuedProp, taking]);
  useEffect(() => {
    setTaking(current => current.length && current.some(id => !queuedProp.some(q => q.id === id)) ? current.filter(id => queuedProp.some(q => q.id === id)) : current);
  }, [queuedProp]);
  const [videoPreview, setVideoPreview] = useState<PreparedVideo | null>(null);
  const [preparing, setPreparing] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [collaborationSignal, setCollaborationSignal] = useState(0);
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
  useEffect(() => { setCollaborationSignal(0); }, [draftKey]);
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
    if (referenceSource) additions.push({ id: 'composer:reference', label: t('references.add'), hint: '', keywords: ['reference', '引用', '目录', '会话'] });
    if (collaboration) additions.push({ id: 'composer:collaboration', label: t('app.collab'), hint: '', keywords: ['collaboration', 'agents', '协作'] });
    if (planMode) additions.push({ id: 'composer:plan', label: t('composerMenu.planMode'), hint: planMode.disabled ? t('composer.planAfterTurn') : planMode.active ? t('composer.planActive') : t('composerMenu.planOn'), keywords: ['plan', '计划'], disabled: planMode.disabled });
    if (writingMode) additions.push({ id: 'composer:writing', label: t('writing.mode'), hint: writingMode.active ? t('writing.off') : t('writing.modeHint'), keywords: ['writing', 'write', '写作', '文章'], disabled: writingMode.disabled });
    if (goal) {
      for (const { action, blocked } of goalMenuActions(goal, running, !!planMode?.active)) {
        additions.push({ id: `composer:goal:${action}`, label: t(`goal.${action}`), hint: blocked || (action === 'set' ? t('composer.goalSetHint') : ''), keywords: ['goal', '目标', 'target'], disabled: !!blocked });
      }
    }
    const group = (label: string, entries: HubAction[]): MenuRow[] => searchCommands(query, entries, []).map(row => row.type === 'heading' ? { ...row, label } : row);
    // Both desktop entry points share one catalog, ordering and filtering behavior.
    return [
      ...(!narrow ? group(t('composer.add'), additions) : []),
      ...searchCommands(query, actions, commands, hiddenCommands),
    ];
  }, [addOpen, addQuery, catalog, text, narrow, actions, commands, hiddenCommands, planMode, writingMode, goal, running, collaboration, referenceSource]);
  const selectable = useMemo(() => rows.filter((r): r is Selectable => r.type !== 'heading' && !(r.type === 'action' && r.action.disabled)), [rows]);
  const menuOpen = addOpen || (rows.length > 0 && !dismissed);

  const readSelection = (el: HTMLTextAreaElement) => setSelection({ text: el.value, start: el.selectionStart, end: el.selectionEnd });
  const mentionMatch = mentionAt(text, selection.text === text ? selection.start : text.length, selection.text === text ? selection.end : text.length);
  const mentionRows = mentionOptions.filter(m => !mentions.some(v => v.id === m.id && v.kind === m.kind) && (!mentionMatch?.query || `${m.label} ${m.id}`.toLowerCase().includes(mentionMatch.query.toLowerCase())));
  const mentionOpen = !referenceSource && Boolean(mentionMatch && mentionRows.length && !mentionDismissed && !addOpen);
  const openReferences = (query = '') => { setReferenceQuery(query); setReferenceSignal(value => value + 1); setMentionDismissed(true); };
  const referenceMention = useRef<{ draftKey: string | undefined; prefix: string } | null>(null);
  useEffect(() => {
    const previous = referenceMention.current;
    if (previous && (previous.draftKey !== draftKey || !text.startsWith(previous.prefix))) referenceMention.current = null;
    if (!referenceSource || !mentionMatch || addOpen) return;
    const prefix = text.slice(0, mentionMatch.start + 1);
    // Closing a picker dismisses this @ even if focus/caret events or a parent refresh arrive later.
    if (referenceMention.current?.prefix === prefix) return;
    referenceMention.current = { draftKey, prefix };
    openReferences(mentionMatch.query);
  }, [referenceSource, text, draftKey, mentionMatch?.start, mentionMatch?.query, addOpen]);
  const chooseReference = (reference: ContextReference) => {
    if (references.length >= 12 && !references.some(r => r.id === reference.id)) { onError(t('references.limit', 12)); return; }
    setReferences(current => mergeReferences(current, [reference]));
    if (mentionMatch) setText(current => current.slice(0, mentionMatch.start) + current.slice(mentionMatch.end));
    setMentionDismissed(true);
    ref.current?.focus();
  };
  const restoreMessage = (value: string) => {
    const noted = splitNotes(value);
    const referenced = splitReferences(noted.body);
    setReferences(current => mergeReferences(referenced.references, current));
    setNotes(current => [...noted.notes.map(n => ({ ...n, id: noteId() })), ...current]);
    setText(current => [referenced.body, current].filter(s => s.trim()).join('\n\n'));
  };
  const taskRefs = useRef<Array<HTMLTextAreaElement | null>>([]);
  const mentionListRef = useRef<HTMLDivElement>(null);
  const chooseMention = (mention: AgentMention, replaceQuery = true) => {
    const existing = mentions.findIndex(m => m.id === mention.id && m.kind === mention.kind);
    const target = existing < 0 ? mentions.length : existing;
    // Mount and focus inside the tap gesture so mobile browsers can open the keyboard.
    flushSync(() => {
      if (existing < 0) setMentions(prev => [...prev, mention]);
      if (replaceQuery && mentionMatch) setText(prev => prev.slice(0, mentionMatch.start) + prev.slice(mentionMatch.end));
      setMentionDismissed(true);
    });
    taskRefs.current[target]?.focus();
  };
  // Turned away once, the list stays away for that @: the word after it is the user's own. A new @ asks again.
  const mentioning = mentionMatch !== null;
  useEffect(() => { setMentionActive(0); }, [text, selection.start]);
  useEffect(() => { if (!mentioning) setMentionDismissed(false); }, [mentioning]);
  useEffect(() => { setMentionDismissed(false); }, [mentionMatch?.start, draftKey]);
  useEffect(() => {
    if (mentionOpen) mentionListRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [mentionActive, mentionOpen, mentionMatch?.query]);

  // Typing changes the list, so the highlight goes back to the first row and a dismissed menu can reopen.
  useEffect(() => {
    setActive(0);
    setDismissed(false);
  }, [text]);
  useEffect(() => { setActive(0); }, [addQuery, addOpen]);

  // A passage noted out of a message joins the others. Keyed on the nonce: the same passage can be noted twice.
  useEffect(() => {
    if (!note) return;
    const { nonce: _nonce, ...added } = note;
    setNotes((prev) => prev.some((existing) => existing.id === added.id) ? prev : [...prev, added]);
    onNoteConsumed?.(added.id);
    ref.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note?.nonce]);
  useEffect(() => onNotesChange?.(notes), [notes, onNotesChange]);
  useEffect(() => {
    if (!reference) return;
    if (references.length >= 12 && !references.some(value => value.id === reference.value.id)) onError(t('references.limit', 12));
    else setReferences(current => mergeReferences(current, [reference.value]));
    onReferenceConsumed?.(reference.value.id);
    ref.current?.focus();
    // Delivery is keyed to one action, like a picked note.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reference?.nonce]);

  useEffect(() => {
    if (!revealSelection.current) return;
    revealSelection.current = false;
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);
  useEffect(() => { if (menuOpen) paletteRef.current?.scrollTo({ top: 0 }); }, [addQuery, text, menuOpen]);

  // Text handed back goes in front of whatever is being typed, so nothing is lost either way.
  useEffect(() => {
    if (!restore) return;
    if (restore.mentions?.length) setMentions(current => [...restore.mentions!, ...current]);
    restoreMessage(restore.text);
    ref.current?.focus();
    // Keyed on the nonce: the same text can be handed back twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restore?.nonce]);

  // Each thread has its own draft. Swapped before paint, so the old text never flashes in the new
  // thread. The pictures' object URLs go with the draft; they are released once it is sent.
  // A send this page never saw succeed: the page was reloaded meanwhile, or the answer got lost on
  // the way back. The box stays marked for as long as it still holds exactly what was sent.
  const inFlight = useRef<Sending | null>(null);
  const unsettled = inFlight.current !== null && asMessage(text, notes, references) === inFlight.current.text;
  const draftRef = useRef<Draft>({ text, notes, references, images, videos });
  draftRef.current = { text, notes, references, images, videos, mentions, ...(unsettled ? { sending: inFlight.current! } : {}) };
  const sendingRef = useRef<{ key: string | undefined; mark: Sending } | null>(null);
  const showDraft = (saved: Draft | undefined) => {
    inFlight.current = saved?.sending ?? null;
    setText(saved?.text ?? '');
    setReferences(saved?.references ?? []);
    setMentions(saved?.mentions ?? []);
    setNotes(saved?.notes ?? []);
    setImages(saved?.images ?? []);
    setVideos(saved?.videos ?? []);
    setVideoPreview(null);
    if (saved?.focus) {
      saved.focus = false;
      ref.current?.focus();
    }
  };
  useLayoutEffect(() => {
    if (draftKey === undefined) return;
    showDraft(loadDraft(draftKey));
    return () => {
      const draft = draftRef.current;
      // A creation receipt or send completion may already have moved/cleared this marked draft.
      // A late layout cleanup must not resurrect it or replace a newer draft.
      if (draft.sending && loadDraft(draftKey)?.sending !== draft.sending) return;
      keepDraft(draftKey, draft);
    };
  }, [draftKey]);

  const takeDraft = () => {
    const message = asMessage(text, notes, references);
    setText('');
    setNotes([]);
    setReferences([]);
    // The box may be gone before it renders again; its last word on the draft must be the empty one.
    draftRef.current = { ...draftRef.current, text: '', notes: [], references: [] };
    if (draftKey !== undefined) keepDraft(draftKey, draftRef.current);
    return message;
  };

  // Written down shortly after typing pauses, so a reload keeps it. The timer reads the box as it
  // is when it fires, so a switch to another thread in between saves under the right one.
  const keyRef = useRef(draftKey);
  keyRef.current = draftKey;
  useEffect(() => {
    if (draftKey === undefined) return;
    const timer = setTimeout(() => {
      if (keyRef.current !== undefined && sendingRef.current?.key !== keyRef.current) keepDraft(keyRef.current, draftRef.current);
    }, SAVE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [draftKey, text, notes, references, mentions]);
  useEffect(() => {
    if (draftKey === undefined) return;
    const flush = () => {
      if (keyRef.current !== undefined && sendingRef.current?.key !== keyRef.current) keepDraft(keyRef.current, draftRef.current);
    };
    const flushWhenHidden = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    window.addEventListener('pagehide', flush);
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', flushWhenHidden);
    return () => {
      window.removeEventListener('pagehide', flush);
      window.removeEventListener('beforeunload', flush);
      document.removeEventListener('visibilitychange', flushWhenHidden);
    };
  }, [draftKey]);
  // Whether that message arrived is for the thread to say. Once a new turn of the user's carries
  // this very text, it is no draft: left in the box it would look unsent, and be sent a second time.
  useEffect(() => {
    const mark = inFlight.current;
    if (!unsettled || !mark || !lastSent) return;
    if (lastSent.turnId === mark.after || lastSent.text.trim() !== mark.text.trim()) return;
    inFlight.current = null;
    draftRef.current.images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    draftRef.current.videos?.forEach(video => URL.revokeObjectURL(video.previewUrl));
    setVideos([]);
    setImages([]);
    setText('');
    setNotes([]);
    setReferences([]);
    setMentions([]);
    if (draftKey !== undefined) keepDraft(draftKey, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unsettled, lastSent?.turnId, lastSent?.text, draftKey]);
  // Without a draft to keep them, thumbnails left in the box are released when it goes away.
  useEffect(
    () => () => {
      if (draftKey === undefined) {
        draftRef.current.images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
        draftRef.current.videos?.forEach(video => URL.revokeObjectURL(video.previewUrl));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const addFiles = async (files: File[]) => {
    if (attachmentsBlocked) return onError(attachmentsBlocked);
    // Pictures go to the model as pictures, videos can be previewed here; anything else is a plain file.
    const pictures = files.filter((f) => f.type.startsWith('image/'));
    const uploads = files.filter((f) => !f.type.startsWith('image/'));
    if (uploads.length && uploadsBlocked) onError(uploadsBlocked);
    if (uploads.length && !uploadsBlocked) {
      let room = MAX_UPLOADS - videos.length;
      let videoRoom = MAX_VIDEOS - videos.filter(isPlayable).length;
      const ready: PreparedVideo[] = [];
      for (const file of uploads) {
        const video = isSupportedVideo(file);
        if (room <= 0) { onError(t('composer.maxUploads', MAX_UPLOADS)); break; }
        if (video && videoRoom <= 0) { onError(t('composer.maxVideos', MAX_VIDEOS)); continue; }
        try {
          ready.push(video ? prepareVideo(file) : prepareFile(file));
          room -= 1;
          if (video) videoRoom -= 1;
        } catch (error) { onError(error instanceof Error ? error.message : t(video ? 'composer.videoFailed' : 'composer.fileFailed')); }
      }
      setVideos(current => [...current, ...ready]);
    }
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
      if (taken.mentions?.length) setMentions(current => [...taken.mentions!, ...current]);
      restoreMessage(taken.text);
      const room = MAX_IMAGES - images.length - preparing;
      if (taken.images.length > room) onError(t('composer.maxImagesNotRestored', MAX_IMAGES));
      const back = taken.images.slice(0, Math.max(0, room)).map(restoreImage);
      if (back.length) setImages((prev) => [...back, ...prev]);
      ref.current?.focus();
      const returnedVideos = await (taken.videos ?? []);
      // What is in the box by now, not when the edit began: these may arrive a while later.
      const videoRoom = Math.max(0, MAX_UPLOADS - (draftRef.current.videos?.length ?? 0));
      if (returnedVideos.length > videoRoom) onError(t('composer.maxUploads', MAX_UPLOADS));
      returnedVideos.slice(videoRoom).forEach(video => URL.revokeObjectURL(video.previewUrl));
      setVideos(current => [...returnedVideos.slice(0, videoRoom), ...current]);
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
      else if (id === 'composer:reference') openReferences();
      else if (id === 'composer:collaboration') setCollaborationSignal(value => value + 1);
      else if (id === 'composer:plan') planMode?.onChange(!planMode.active);
      else if (id === 'composer:writing') writingMode?.onChange(!writingMode.active);
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

  // The person can say no, and the @ goes back to asking for work; a new @ asks again.
  const [takeoverOff, setTakeoverOff] = useState(false);
  useEffect(() => { if (!mentions.length) setTakeoverOff(false); }, [mentions.length]);
  const taker = takeover && !takeoverOff ? takeoverMention(mentions, true, takeover.current) : null;
  const hasContent = !!taker || text.trim() !== '' || images.length > 0 || videos.length > 0 || notes.length > 0 || references.length > 0 || mentions.some((m) => m.task?.trim());
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
  // A portaled picker still belongs to its trigger, including during touch focus changes.
  const ownsTarget = (target: EventTarget | null) => {
    if (target instanceof Node && boxRef.current?.contains(target)) return true;
    const panel = target instanceof Element ? target.closest('.pop-panel[id]') : null;
    return Boolean(panel && [...(boxRef.current?.querySelectorAll('[aria-controls]') ?? [])].some(button => button.getAttribute('aria-controls') === panel.id));
  };
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
      if (e.target instanceof Node && !ownsTarget(e.target)) {
        endBoxPress();
        if (!ownsTarget(document.activeElement)) leave();
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
  const collapsed = !voiceControls && narrow && !header && !hasContent && queued.length === 0 && !modelQueue && preparing === 0 && !focused;
  const goalControl = text.trim() === '/goal' ? 'show' : text.trim() === '/goal pause' ? 'pause' : text.trim() === '/goal clear' ? 'clear' : null;
  const immediate = !!goalControl && !!goal?.controls.includes(goalControl) && !images.length && !videos.length && !notes.length && !references.length && !mentions.length;
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
    if (attachmentsBlocked && (images.length || videos.length)) return onError(attachmentsBlocked);
    if (uploadsBlocked && videos.length) return onError(uploadsBlocked);
    if (images.length && attachBlocked) return onError(attachBlocked);
    if (taker && (images.length || videos.length)) return onError(t('composer.takeoverAttachments'));
    setBusy(true);
    const message = asMessage(text, notes, references);
    const mark: Sending = { text: message, after: lastSent?.turnId ?? null };
    const sendOwner = { key: draftKey, mark };
    const sentDraft = { text, notes, references, images, videos, mentions, sending: mark };
    sendingRef.current = sendOwner;
    // Carry the send identity when the new draft becomes its created conversation.
    inFlight.current = mark;
    draftRef.current = sentDraft;
    // On the device the draft now says it is on its way. Should the page be reloaded before the
    // send comes back, it is neither lost nor taken for something still to send.
    if (sendOwner.key !== undefined) keepDraft(sendOwner.key, sentDraft);
    const onCreated = (createdKey: string) => {
      if (sendingRef.current !== sendOwner || createdKey === sendOwner.key) return;
      const previousKey = sendOwner.key;
      sendOwner.key = createdKey;
      const saved = loadDraft(createdKey);
      if (!saved) keepDraft(createdKey, sentDraft);
      if (previousKey !== undefined && loadDraft(previousKey)?.sending === mark) keepDraft(previousKey, null);
      if (keyRef.current === createdKey && !saved) showDraft(sentDraft);
    };
    const slowTimer = setTimeout(() => setSlow(true), SLOW_SEND_MS);
    try {
      if (taker) await takeover!.go(taker.id, [message, taker.task?.trim()].filter(Boolean).join('\n\n'));
      else await onSend(message, images, mentions, videos, onCreated);
      const ownsDraft = sendOwner.key === undefined || loadDraft(sendOwner.key)?.sending === mark;
      if (sendOwner.key !== undefined && ownsDraft) keepDraft(sendOwner.key, null);
      images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      videos.forEach(video => URL.revokeObjectURL(video.previewUrl));
      // The same composer may now hold another conversation's draft.
      if (!ownsDraft || keyRef.current !== sendOwner.key || inFlight.current !== mark) return;
      if (draftRef.current.sending !== mark) {
        // Edits made while sending are a new draft, not part of the completed message.
        inFlight.current = null;
        if (sendOwner.key !== undefined) keepDraft(sendOwner.key, draftRef.current);
        return;
      }
      setMentions([]);
      inFlight.current = null;
      draftRef.current = { text: '', notes: [], images: [] };
      setNotes([]);
      setReferences([]);
      setVideos([]);
      setImages([]);
      setText(current => current === text ? '' : current);
    } catch (error) {
      const sentKey = sendOwner.key;
      if (error instanceof SendCancelled) {
        if (sentKey !== undefined && loadDraft(sentKey)?.sending === mark) {
          const draft = keyRef.current === sentKey ? draftRef.current : { text, notes, references, images, videos, mentions };
          keepDraft(sentKey, { ...draft, sending: undefined });
        }
        if (keyRef.current === sentKey && inFlight.current === mark) inFlight.current = null;
      } else {
        // A failed read can follow a successful send, so retain the mark until history resolves it.
        if (keyRef.current === sentKey && (sentKey === undefined || loadDraft(sentKey)?.sending === mark)) inFlight.current = mark;
      }
    } finally {
      clearTimeout(slowTimer);
      setSlow(false);
      if (sendingRef.current === sendOwner) sendingRef.current = null;
      setBusy(false);
      if (keyRef.current === sendOwner.key) ref.current?.focus();
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
    if (mentionOpen && !isImeComposing(e)) {
      if (e.key === 'Escape') { e.preventDefault(); setMentionDismissed(true); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); setMentionActive(i => (i + (e.key === 'ArrowDown' ? 1 : -1) + mentionRows.length) % mentionRows.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); chooseMention(mentionRows[mentionActive] ?? mentionRows[0]); return; }
    }
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
    <div className={`composer${voiceControls ? ' voice-composer' : ''}`}>
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
              const writing = isAction && row.action.id === 'composer:writing';
              const checked = plan ? planMode?.active : writing ? writingMode?.active : undefined;
              const Icon = isAction ? row.action.id === 'composer:attach' ? IconPaperclip : row.action.id === 'composer:collaboration' ? IconAgents : plan ? IconList : writing ? IconPen : row.action.id.startsWith('composer:goal:') ? IconTarget : (ACTION_ICONS[row.action.id] ?? IconSlash) : row.command.kind === 'skill' ? IconSparkle : IconSlash;
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

        {typeof banner === 'function' ? banner(takeDraft) : banner}
        {taker && <div className="takeover-note" role="status">
          <IconHand size={14} />
          <span>{t('composer.takeover', takeover!.from, taker.label)}</span>
          <button type="button" className="icon-btn" aria-label={t('composer.takeoverOff')} title={t('composer.takeoverOff')} onClick={() => setTakeoverOff(true)}><IconClose size={13} /></button>
        </div>}
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
            if (!ownsTarget(e.relatedTarget) && boxPress.current === null) leave();
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
                  <span className={`queue-text${q.channel?.parts ? ' parts' : ''}`} title={referencePreview(q.text)}>
                    {q.channel?.parts ? channelRows(q.channel, q.text).map((row, index) => <span key={index} className={row.kind === 'note' ? 'muted' : undefined}>{index > 0 && ' · '}{row.text}</span>) : referencePreview(q.text) || (q.targets?.length ? t('app.collab') : q.files ? t('uploads.file') : q.videos ? t('videos.video') : t('images.image'))}
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

          {(images.length > 0 || videos.length > 0 || preparing > 0) && (
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
              {videos.map(video => <div key={video.id} className="attachment video-attachment">
                {isPlayable(video)
                  ? <button type="button" className="video-card" aria-label={t('composer.previewVideo', video.name)} onClick={() => setVideoPreview(video)}>
                    <IconPlay size={20} /><span><span className="video-name">{video.name}</span><span className="video-size">{t('videos.video')} · {sizeLabel(video.file.size)}</span></span>
                  </button>
                  : <div className="video-card file-card" title={video.name}>
                    <IconFile size={20} /><span><span className="video-name">{video.name}</span><span className="video-size">{sizeLabel(video.file.size)}</span></span>
                  </div>}
                <button type="button" className="attachment-remove" aria-label={t('composer.remove', video.name)} onClick={() => { URL.revokeObjectURL(video.previewUrl); setVideos(current => current.filter(v => v.id !== video.id)); }}><IconClose size={12} /></button>
              </div>)}
              {Array.from({ length: preparing }, (_, i) => (
                <div key={`p-${i}`} className="attachment pending" aria-label={t('composer.processing')} />
              ))}
            </div>
          )}

          <ReferenceChips references={references} onRemove={id => setReferences(current => current.filter(r => r.id !== id))} />
          {notes.length > 0 && (
            <div className="notes" aria-label={t('composer.notes', notes.length)}>
              {notes.map((n, i) => (
                <div key={n.id} className="note-strip">
                  <span className="note-num">{i + 1}</span>
                  <span className="note-body">
                    {n.file && <span className="note-file" title={n.file}>{n.file}</span>}
                    <span className="note-quote" title={n.quote}>
                      {n.quote}
                    </span>
                    {n.comment && <span className="note-comment">{n.comment}</span>}
                  </span>
                  <button
                    type="button"
                    className="quote-remove"
                    aria-label={t('composer.removeNote', i + 1)}
                    title={t('composer.removeNoteTitle')}
                    onClick={() => setNotes((prev) => prev.filter((x) => x.id !== n.id))}
                  >
                    <IconClose size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {mentionOpen && <div ref={mentionListRef} className="coord-mention-menu" role="listbox" aria-label={t('collab.choose')}>
            {(['member', 'agent'] as const).map(kind => <div key={kind}>
              {mentionRows.some(m => m.kind === kind) && <div className="coord-caption">
                <span>{kind === 'member' ? t('composer.thisCollab') : t('composer.newMember')}</span>
                {kind === mentionRows[0]?.kind && <button type="button" className="icon-btn" aria-label={t('composer.noMention')} title={t('composer.noMentionHint')} onClick={() => { setMentionDismissed(true); ref.current?.focus(); }}><IconClose size={13} /></button>}
              </div>}
              {mentionRows.map((m, i) => m.kind === kind && (
                <button type="button" role="option" aria-selected={i === mentionActive} className={`pop-item compact${i === mentionActive ? ' selected' : ''}`} key={`${m.kind}:${m.id}`} onClick={() => chooseMention(m)}>
                  <span className="pop-item-icon">{mentionIcon?.(m) ? <AgentMark path={mentionIcon(m)!} size={16} /> : null}</span>
                  <span className="pop-item-text"><span className="pop-item-label">{m.label}</span><small className="pop-item-hint">{kind === 'member' ? t('collab.appendTask') : t('composer.createMember')}</small></span>
                </button>
              ))}
            </div>)}
          </div>}
          <textarea
            ref={ref}
            rows={1}
            value={text}
            placeholder={dragOver ? t('composer.drop') : collapsed && running ? t('composer.appendOne') : placeholder}
            onChange={(e) => { setText(e.target.value); readSelection(e.target); }}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={compositionEnd}
            onSelect={(e) => readSelection(e.currentTarget)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            disabled={disabled}
            aria-label={t('composer.message')}
            aria-expanded={menuOpen || mentionOpen}
            aria-autocomplete="list"
          />

          {/* One line per agent brought in: what is written there is for that one alone. */}
          {mentions.length > 0 && (
            <div className="coord-rows">
              {mentions.map((m, i) => (
                <div className="coord-row" key={`${m.kind}:${m.id}:${i}`}>
                  <span className="coord-row-name" title={m.label}>@{m.name ?? m.label}</span>
                  {m.kind === 'agent' && (
                    <Popover label={t('composer.memberDir')} triggerClassName={`coord-row-where${m.cwd ? ' set' : ''}`} triggerTitle={m.cwd ?? t('composer.pickDir')} triggerLabel={t('composer.pickDir')}
                      trigger={<>
                        {m.machine ? <IconComputer size={14} /> : <IconFolder size={14} />}
                        {m.machine && <span className="coord-row-machine">{m.machine}</span>}
                        {(m.cwd || m.machine) && <span className="coord-row-dir">{m.cwd ? m.cwd.split('/').pop() : t('composer.pickDirShort')}</span>}
                      </>}>
                      {close => <>
                        {(m.directories ?? mentionDirectories).map(dir => <button className="pop-item compact" role="menuitemradio" aria-checked={m.cwd === dir} key={dir} onClick={() => { setMentions(prev => prev.map(v => v === m ? { ...v, cwd: dir } : v)); close(); }}>{dir}</button>)}
                        {m.host && onBrowseMentionDir && <button className="pop-item compact" onClick={() => { close(); onBrowseMentionDir(m, cwd => setMentions(prev => prev.map(v => v.kind === m.kind && v.id === m.id ? { ...v, cwd } : v))); }}>{t('composer.browseDir')}</button>}
                      </>}
                    </Popover>
                  )}
                  <span className="coord-row-tools">{m.kind === 'agent' && mentionExtra?.(m)}</span>
                  <button type="button" className="icon-btn coord-row-close" aria-label={t('composer.remove', m.label)} onClick={() => setMentions(prev => prev.filter(v => v !== m))}><IconClose size={13} /></button>
                  <textarea
                    ref={(el) => { taskRefs.current[i] = el; }}
                    rows={1}
                    value={m.task ?? ''}
                    placeholder={taker ? t('composer.takeoverTask') : t('composer.taskPlaceholder')}
                    aria-label={t('composer.taskFor', m.label)}
                    disabled={disabled}
                    onChange={(e) => { const task = e.target.value; setMentions(prev => prev.map(v => v === m ? { ...v, task } : v)); }}
                    onKeyDown={(e) => {
                      // Enter leaves the line for the message; a task is one line of intent, not an essay.
                      if (e.key === 'Enter' && !e.shiftKey && !isImeComposing(e)) { e.preventDefault(); ref.current?.focus(); }
                    }}
                  />
                </div>
              ))}
            </div>
          )}

          <div className="composer-bar">
            <input
              ref={fileRef}
              type="file"
              accept={uploadsBlocked ? 'image/*' : undefined}
              multiple
              hidden
              onChange={(e) => {
                void addFiles([...(e.target.files ?? [])]);
                e.target.value = ''; // so picking the same file again fires a change
              }}
            />
            {narrow ? <Popover
              triggerRef={addRef}
              label={t('composer.addAndMode')}
              triggerClassName={`tool-btn icon-only composer-add${collaborationActive ? ' on' : ''}`}
              triggerLabel={t('composer.addAndMode')}
              triggerTitle={t('composer.addAndModeHint')}
              panelClassName="composer-add-menu"
              trigger={<IconPlus size={20} />}
              disabled={disabled || menuBusy || busy}
            >
              {close => <ComposerMenu close={close} commands={commands} planMode={planMode} writingMode={writingMode} goal={goal} running={running}
                collaboration={collaboration ? { active: !!collaborationActive, open: () => setCollaborationSignal(value => value + 1) } : undefined}
                attachDisabled={Boolean(attachmentsBlocked)}
                attachTitle={attachBlocked ? t('composer.attachBlocked', attachBlocked) : attachNote ?? t('composer.attachHint')}
                onReference={referenceSource ? () => openReferences() : undefined}
                onAttach={() => fileRef.current?.click()} onGoal={chooseGoal} onBrowse={browse} />}
            </Popover> : <button ref={addRef} type="button" className={`tool-btn icon-only composer-add${collaborationActive ? ' on' : ''}`}
              aria-label={t('composer.addAndMode')} aria-haspopup="listbox" aria-expanded={addOpen} title={t('composer.addActionsSkills')} disabled={disabled || menuBusy || busy}
              onClick={() => { setCatalog('all'); setAddQuery(''); setAddOpen(!addOpen); setDismissed(true); }}><IconPlus size={20} /></button>}
            {referenceSource && <Popover label={t('references.add')} triggerLabel={t('references.add')} triggerClassName="tool-btn icon-only"
              trigger={<span aria-hidden="true">@</span>} openSignal={referenceSignal} panelClassName="context-picker-panel" disabled={disabled || busy}>
              {close => <ReferencePicker source={referenceSource} initialQuery={referenceQuery} agents={mentionOptions.filter(m => !mentions.some(v => v.id === m.id && v.kind === m.kind))}
                onAgent={agent => { close(); chooseMention(agent); }} onPick={reference => { close(); chooseReference(reference); }} />}
            </Popover>}
            {!collapsed && left}
            {/* Only active modes take space; their compact marks can turn them off. */}
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
            <div className="spacer" />
            {!collapsed && right}
            {composerActions}
            {running && (
              <button className="send-btn stop" onClick={onStop} aria-label={t('composer.stop')} title={t('composer.stopTurn')}>
                <IconStop size={16} />
              </button>
            )}
            {/* While a turn runs the send button appears only once there is something to queue. */}
            {(voiceControls ? hasContent : !running || hasContent) && (
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
                aria-label={slow ? t('composer.sending') : taker ? t('composer.takeoverSend', taker.label) : running && !immediate ? t('composer.queueSend') : t('common.send')}
                title={slow ? t('composer.sendingEllipsis') : taker ? t('composer.takeoverSend', taker.label) : queueFull ? t('composer.queueMax', MAX_QUEUED) : running ? immediate ? t('composer.goalNow') : t('composer.afterTurn') : undefined}
              >
                {slow ? <span className="send-spinner" aria-hidden="true" /> : <IconSend size={16} />}
              </button>
            )}
          </div>
        </div>
      </div>
      {voiceControls}
      {collaboration?.(mention => chooseMention(mention, false), mentions, addRef, collaborationSignal)}
      {videoPreview && <VideoPreview url={videoPreview.previewUrl} name={videoPreview.name} onClose={() => setVideoPreview(null)} />}
      {preview && <Lightbox url={preview.previewUrl} name={preview.name} onClose={() => setPreview(null)} />}
    </div>
  );
}
