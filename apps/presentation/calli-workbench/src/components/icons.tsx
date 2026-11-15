import type { ReactNode } from 'react';

interface IconProps {
  size?: number;
  children: ReactNode;
}

function Svg({ size = 18, children }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

/** An agent's own mark, drawn from the path its adapter sends. Filled, unlike the line icons below. */
export const AgentMark = ({ path, size = 16 }: { path: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" fillRule="evenodd" aria-hidden="true">
    <path d={path} />
  </svg>
);

export const IconMenu = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 7h16M4 12h16M4 17h10" />
  </Svg>
);
export const IconPlus = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconCompact = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3M8 9h8M8 15h5" />
  </Svg>
);

export const IconTarget = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="4" />
    <circle cx="12" cy="12" r="1" />
  </Svg>
);

/** A clock face with hands, for scheduled tasks. */
export const IconClock = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);

/** A play triangle, for "run now". */
export const IconPlay = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M8 5.5v13l10-6.5z" />
  </Svg>
);
export const IconSend = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Svg>
);
export const IconStop = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="7" y="7" width="10" height="10" rx="2" />
  </Svg>
);
export const IconPause = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="7" y="5" width="3.5" height="14" rx="1.2" />
    <rect x="13.5" y="5" width="3.5" height="14" rx="1.2" />
  </Svg>
);
export const IconWaveform = (p: { size?: number }) => (
  <Svg {...p}><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10v4" /></Svg>
);
export const IconMic = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3M8 22h8" />
  </Svg>
);
export const IconMicOff = (p: { size?: number }) => (
  <Svg {...p}>
    <line x1="2" y1="2" x2="22" y2="22" />
    <path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2" />
    <path d="M5 10v2a7 7 0 0 0 12 5" />
    <path d="M15 9.34V5a3 3 0 0 0-5.68-1.33" />
    <path d="M9 9v3a3 3 0 0 0 5.12 2.12" />
    <line x1="12" y1="19" x2="12" y2="22" />
    <line x1="8" y1="22" x2="16" y2="22" />
  </Svg>
);
export const IconChevron = (p: { size?: number; open?: boolean }) => (
  <Svg {...p}>
    <path d={p.open ? 'M6 9l6 6 6-6' : 'M9 6l6 6-6 6'} />
  </Svg>
);
export const IconBack = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M15 5l-7 7 7 7" />
  </Svg>
);
export const IconTerminal = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 7l4 4-4 4M12 17h7" />
  </Svg>
);
/** A screen on a stand: the machine's desktop. */
export const IconDesktop = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M9 20h6M12 16v4" />
  </Svg>
);
export const IconKeyboard = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="2.5" y="6" width="19" height="12" rx="2" />
    <path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M8 14h8" />
  </Svg>
);
/** A pointer: taking control of the desktop's mouse and keyboard. */
export const IconPointer = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 3.5l13 6.5-5.5 1.8L10.7 17.5z" />
    <path d="M12.6 11.9l4.6 4.6" />
  </Svg>
);
/** A touchpad: the phone's screen moving the desktop's pointer. */
export const IconTouchpad = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="3.5" y="4.5" width="17" height="13" rx="2.5" />
    <path d="M8 20.5h8M12 9.5v4" />
  </Svg>
);
/** Fit to the window, or show at full size. */
export const IconFit = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Svg>
);
export const IconFile = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M7 3h7l5 5v13H7zM14 3v5h5" />
  </Svg>
);
export const IconRead = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 6.5C9.5 4.6 6.5 4 3.5 4.5v14c3-.5 6 .1 8.5 2 2.5-1.9 5.5-2.5 8.5-2v-14c-3-.5-6 .1-8.5 2ZM12 6.5v14M7 4.5v11" />
  </Svg>
);
/** A command window; the plain terminal prompt remains available for navigation. */
export const IconCommand = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="3.5" y="3.5" width="17" height="17" rx="3" />
    <path d="m7.5 8 3 3-3 3M13.5 15.5h3" />
  </Svg>
);
export const IconTool = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M14 4a5 5 0 0 0-6 6l-4 4a3 3 0 0 0 6 6l4-4a5 5 0 0 0 6-6l-4 3-3-3 3-4a5 5 0 0 0-2-2Z" />
  </Svg>
);
export const IconSearch = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="6" />
    <path d="M20 20l-4.5-4.5" />
  </Svg>
);
export const IconEdit = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4" />
  </Svg>
);
/** A pen nib: writing mode. */
export const IconPen = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 19l7-7 3 3-7 7zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18zM2 2l7.6 7.6" />
    <circle cx="11" cy="11" r="2" />
  </Svg>
);
export const IconFolder = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M3 6h6l2 2h10v11H3z" />
  </Svg>
);
export const IconShield = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" />
  </Svg>
);
export const IconShieldAlert = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" />
    <path d="M12 8.5v4M12 15.5v.01" />
  </Svg>
);
export const IconHand = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M8 12V6.5a1.5 1.5 0 013 0V11M11 11V5a1.5 1.5 0 013 0v6M14 11V6.5a1.5 1.5 0 013 0V14a6 6 0 01-6 6h-.5a5 5 0 01-4.2-2.3L4.6 14a1.4 1.4 0 012.3-1.6L8 13.6" />
  </Svg>
);
export const IconCheck = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
);
export const IconPin = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M9 4h6l-1 6 3 3H7l3-3zM12 13v7" />
  </Svg>
);
export const IconClose = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
);
export const IconRefresh = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M19 12a7 7 0 1 1-2-4.9M17 3v4.3h-4.3" />
  </Svg>
);
export const IconBolt = (p: { size?: number }) => (
  <Svg {...p}><path d="m13 2-9 12h7l-1 8 10-12h-7z" /></Svg>
);
export const IconCompress = (p: { size?: number }) => (
  <Svg {...p}><path d="m5 3 7 7 7-7M5 21l7-7 7 7M4 12h16" /></Svg>
);
export const IconExternal = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M14 5h5v5M19 5l-7 7M17 14v5H5V7h5" />
  </Svg>
);
export const IconGauge = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 14a8 8 0 1 1 16 0M12 14l4-4" />
  </Svg>
);
/** A pulse line: what the agents have been doing, the overview page. */
export const IconActivity = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M3 12h4l3-7 4 14 3-7h4" />
  </Svg>
);
export const IconArchive = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 5h16v4H4zM6 9v10h12V9M10 13h4" />
  </Svg>
);
export const IconTrash = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13M10 11v6M14 11v6" />
  </Svg>
);
/** A clipboard: the desktop's, shared with this device. */
export const IconClipboard = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="5" y="4.5" width="14" height="16" rx="2.5" />
    <path d="M9 4.5V4a1 1 0 011-1h4a1 1 0 011 1v.5a1 1 0 01-1 1h-4a1 1 0 01-1-1zM9 11h6M9 15h4" />
  </Svg>
);
export const IconCopy = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2.5" />
    <path d="M5.5 15H5a1 1 0 01-1-1V5a1 1 0 011-1h9a1 1 0 011 1v.5" />
  </Svg>
);
export const IconFork = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="7" cy="5.5" r="2" />
    <circle cx="7" cy="18.5" r="2" />
    <circle cx="17" cy="8.5" r="2" />
    <path d="M7 7.5v9M17 10.5c0 3-3.5 3.5-10 4" />
  </Svg>
);
/** A task passed from one agent to the next. */
export const IconHandover = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 8.5h14M14.5 5 18 8.5 14.5 12M20 15.5H6M9.5 12 6 15.5 9.5 19" />
  </Svg>
);
export const IconImport = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 4v10M8 10.5l4 4 4-4M5 15v3.5A1.5 1.5 0 006.5 20h11a1.5 1.5 0 001.5-1.5V15" />
  </Svg>
);
export const IconInfo = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8v.01" />
  </Svg>
);
export const IconHelp = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M9.6 9.5a2.5 2.5 0 014.8 1c0 1.7-2.4 2-2.4 3.5M12 16.8v.01" />
  </Svg>
);
export const IconDot = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="8" />
  </Svg>
);
export const IconPaperclip = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M19 11.5l-7.2 7.2a4.5 4.5 0 01-6.4-6.4l7.6-7.6a3 3 0 014.3 4.3l-7.5 7.5a1.5 1.5 0 01-2.2-2.1l6.9-6.9" />
  </Svg>
);
export const IconArrowDown = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 5v14M6 13l6 6 6-6" />
  </Svg>
);
export const IconMore = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M6 12h.01M12 12h.01M18 12h.01" strokeWidth={2.6} />
  </Svg>
);
/** Six dots: the handle a row is dragged by. */
export const IconGrip = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" strokeWidth={2.6} />
  </Svg>
);
export const IconArrowUp = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Svg>
);
export const IconArrowLeft = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Svg>
);
export const IconArrowRight = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
);
export const IconList = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M9 7h11M9 12h11M9 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" />
  </Svg>
);
/** Three columns of cards: the board, a view of threads by what they need. */
export const IconBoard = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="4" y="4" width="4" height="13" rx="1.2" />
    <rect x="10" y="4" width="4" height="8" rx="1.2" />
    <rect x="16" y="4" width="4" height="16" rx="1.2" />
  </Svg>
);
/** An empty tray: a page with nothing in it right now. */
export const IconTray = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 13.5l2.4-7.2A2 2 0 0 1 8.3 5h7.4a2 2 0 0 1 1.9 1.3L20 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 13.5h4.5l1.2 2.5h4.6l1.2-2.5H20" />
  </Svg>
);
/** A piece that slots in: extensions. */
export const IconPuzzle = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M10 4.5a2 2 0 1 1 4 0V6h3.5a1 1 0 0 1 1 1v3.5H17a2 2 0 1 0 0 4h1.5V18a1 1 0 0 1-1 1H14v-1.5a2 2 0 1 0-4 0V19H6.5a1 1 0 0 1-1-1v-3.5H7a2 2 0 1 0 0-4H5.5V7a1 1 0 0 1 1-1H10z" />
  </Svg>
);
export const IconSparkle = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 4l1.8 4.7L18.5 10.5l-4.7 1.8L12 17l-1.8-4.7L5.5 10.5l4.7-1.8zM18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
  </Svg>
);
export const IconRestore = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 8v5h5M4.6 13a8 8 0 101.9-6.2L4 9" />
  </Svg>
);
export const IconCube = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9" />
  </Svg>
);
export const IconSlash = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="16" rx="4" />
    <path d="M10 16l4-8" />
  </Svg>
);
export const IconGlobe = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.6 2.4 3.9 5.2 3.9 8.5s-1.3 6.1-3.9 8.5c-2.6-2.4-3.9-5.2-3.9-8.5s1.3-6.1 3.9-8.5z" />
  </Svg>
);
/** A character and a letter: the language the hub speaks. */
export const IconLanguage = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M3 5.5h10M8 3.5v2M5 9.5c1.6 2.6 3.7 4.4 6.5 5.5M11.5 5.5c-.9 4.4-3.5 7.7-8 9.5M13 20.5l4-9.5 4 9.5M14.4 17.5h5.2" />
  </Svg>
);
export const IconComputer = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="12" rx="2" />
    <path d="M4 16l-2 4h20l-2-4M9 20h6" />
  </Svg>
);
/** A machine: two units in a rack. */
export const IconServer = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="4" y="4.5" width="16" height="6.5" rx="2" />
    <rect x="4" y="13" width="16" height="6.5" rx="2" />
    <path d="M7.6 7.75h.01M7.6 16.25h.01" />
  </Svg>
);
export const IconSettings = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3.5l1.6 2.3 2.7-.6.8 2.7 2.6 1-1 2.6 1 2.6-2.6 1-.8 2.7-2.7-.6L12 20.5l-1.6-2.3-2.7.6-.8-2.7-2.6-1 1-2.6-1-2.6 2.6-1 .8-2.7 2.7.6z" />
  </Svg>
);
/** A git worktree: the fork's branch, ending in a checkout of its own. */
export const IconWorktree = (p: { size?: number }) => (
  <Svg {...p}>
    <circle cx="6.5" cy="5.5" r="2" />
    <circle cx="6.5" cy="18.5" r="2" />
    <path d="M6.5 7.5v9M6.5 14.5c7 0 10-1 10-4" />
    <rect x="12.5" y="3.5" width="8" height="7" rx="1.6" />
  </Svg>
);
export const IconPalette = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 3.5a8.5 8.5 0 000 17c1 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.4-.7-.4-1.1 0-.9.7-1.7 1.7-1.7h1.9a4.1 4.1 0 004.1-4.2c0-3.9-3.8-7.1-8.5-7.1z" />
    <path d="M8 9.6h.01M11.6 7.4h.01M15.6 8.8h.01M7.6 13.6h.01" />
  </Svg>
);
export const IconSignOut = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M10 4H6a2 2 0 00-2 2v12a2 2 0 002 2h4M15 8l4 4-4 4M19 12H9" />
  </Svg>
);
/** Going in: the arrow into the door, the mirror of signing out. */
export const IconSignIn = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M14 4h4a2 2 0 012 2v12a2 2 0 01-2 2h-4M10 8l4 4-4 4M14 12H4" />
  </Svg>
);
export const IconQuote = (p: { size?: number }) => (
  <svg width={p.size ?? 18} height={p.size ?? 18} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M10.6 6.2c-3.1 1.6-4.7 4.2-4.7 7.6 0 2.3 1.1 3.9 2.9 3.9 1.5 0 2.6-1.1 2.6-2.5 0-1.4-.9-2.4-2.2-2.4-.2 0-.4 0-.5.1.2-1.7 1.3-3 3-4zM19.6 6.2c-3.1 1.6-4.7 4.2-4.7 7.6 0 2.3 1.1 3.9 2.9 3.9 1.5 0 2.6-1.1 2.6-2.5 0-1.4-.9-2.4-2.2-2.4-.2 0-.4 0-.5.1.2-1.7 1.3-3 3-4z" />
  </svg>
);
export const IconSideChat = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M13.5 5H7a3 3 0 00-3 3v6a3 3 0 003 3h1v3l3.5-3h2" />
    <path d="M13 3h4a3 3 0 013 3v5a3 3 0 01-3 3h-1" />
  </Svg>
);
/** A window with its side panel: shows or hides the thread list. */
export const IconSidebar = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="3.5" />
    <path d="M9.5 4.5v15" />
  </Svg>
);
/** What is going on across the threads: the list by activity. */
export const IconBell = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M6.5 16.5v-5.6a5.5 5.5 0 0111 0v5.6l1.5 1.5H5z" />
    <path d="M10 20.5a2.2 2.2 0 004 0" />
  </Svg>
);
export const IconMinus = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M5 12h14" />
  </Svg>
);
/** Save to this device. */
export const IconDownload = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14" />
  </Svg>
);
/** Long lines folded onto the next row. */
export const IconWrap = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M4 6h16M4 12h13a3 3 0 010 6h-5M14 15.5L11.5 18l2.5 2.5M4 18h4" />
  </Svg>
);
/** The thread's files: a folder with a page in front. */
export const IconFiles = (p: { size?: number }) => (
  <Svg {...p}>
    <path d="M3 18V5h5l2 2h7v2" />
    <path d="M9 11h12v9H9z" />
  </Svg>
);

export const IconAgents = (p: { size?: number }) => (
  <Svg {...p}><circle cx="9" cy="7" r="3" /><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 4v2" /></Svg>
);

/** The same operation has the same icon in a work summary and its expanded rows. */
export function ActivityIcon({ kind, size = 18 }: { kind: string; size?: number }) {
  const Icon = ACTIVITY_ICONS[kind] ?? IconTool;
  return <Icon size={size} />;
}

const ACTIVITY_ICONS: Record<string, (p: { size?: number }) => ReactNode> = {
  read: IconRead,
  edit: IconEdit,
  write: IconEdit,
  execute: IconCommand,
  search: IconSearch,
  fetch: IconGlobe,
  create: IconFile,
  delete: IconTrash,
  move: IconArrowRight,
  think: IconSparkle,
};

/**
 * A dial for thinking effort: the arc fills and the needle turns as `level` goes from 0 to 1.
 * Null is the agent's own default: no fill, the needle upright and quiet.
 */
export const IconEffortGauge = ({ level, size = 18 }: { level: number | null; size?: number }) => {
  // The dial runs clockwise from bottom-left (135°) over the top to bottom-right (405°).
  const angle = ((135 + 270 * (level ?? 0.5)) * Math.PI) / 180;
  const arc = 'M5.99 18.01A8.5 8.5 0 1 1 18.01 18.01';
  return (
    <Svg size={size}>
      <path d={arc} opacity={0.3} />
      {level !== null && level > 0 && <path d={arc} pathLength={100} strokeDasharray={`${level * 100} 100`} className="gauge-fill" />}
      <path d={`M12 12L${(12 + 5 * Math.cos(angle)).toFixed(2)} ${(12 + 5 * Math.sin(angle)).toFixed(2)}`} opacity={level === null ? 0.55 : 1} />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </Svg>
  );
};

/**
 * How much of a quota window is left, as a ring that empties clockwise from the top.
 * The track and fill take their colours from CSS (`.quota-ring-track`, `.quota-ring-fill`).
 */
export const QuotaRing = ({ remaining, size = 14 }: { remaining: number; size?: number }) => {
  const left = Math.max(0, Math.min(100, remaining));
  return (
    <svg className="quota-ring" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <circle className="quota-ring-track" cx="8" cy="8" r="6" fill="none" strokeWidth={2.4} />
      {left > 0 && (
        <circle className="quota-ring-fill" cx="8" cy="8" r="6" fill="none" strokeWidth={2.4} strokeLinecap={left < 100 ? 'round' : 'butt'}
          pathLength={100} strokeDasharray={`${left} 100`} transform="rotate(-90 8 8)" />
      )}
    </svg>
  );
};

export const IconAgent = (p: { size?: number }) => (
  <Svg {...p}>
    <rect x="5" y="7" width="14" height="13" rx="3" />
    <path d="M12 3v4M2 12v4m20-4v4M9 16h6" />
    <circle cx="9" cy="12" r=".6" /><circle cx="15" cy="12" r=".6" />
  </Svg>
);
