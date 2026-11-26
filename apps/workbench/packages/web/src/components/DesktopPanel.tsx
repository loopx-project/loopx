/// <reference path="../novnc.d.ts" />
// The machine's Linux desktop, through the VNC server already running there. On a computer it is
// view only until the person takes control, so a stray click cannot disturb an agent working on
// it. On a touch screen it opens ready to use, as Codex's remote desktop does: the finger clicks
// where it lands, one finger scrolls, the phone's own keyboard types, and copy and paste are one
// tap each; view only and the touchpad are kept behind the menu. The picture is scaled to the
// window; the desktop's own resolution is never changed from here.
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type RFB from '@novnc/novnc';
import type { Api } from '../api';
import { copyText } from '../clipboard';
import { clampZoom, fillZoom, fitSize, isTap, LONG_PRESS_MS, pointerDelta, scrollAxis, scrollForZoom, softwareKeyboardUp, TAP_SLOP_PX, textEdit, twoFingerKind, type Size } from '../desktopTouch';
import { useIsTouch } from '../hooks';
import { t } from '../i18n';
import { DesktopPassword } from './DesktopPassword';
import { Popover } from './Popover';
import {
  IconArrowDown,
  IconArrowLeft,
  IconArrowRight,
  IconArrowUp,
  IconCheck,
  IconChevron,
  IconClipboard,
  IconClose,
  IconCopyStack,
  IconDesktop,
  IconEye,
  IconFit,
  IconKeyboard,
  IconKeys,
  IconLaptop,
  IconMinus,
  IconNext,
  IconPasteList,
  IconPointer,
  IconPrev,
  IconRefresh,
  IconTouchpad,
} from './icons';

interface Props {
  api: Api;
  /** Minimized: out of sight, still connected, so coming back needs no reconnect. */
  hidden?: boolean;
  onMinimize: () => void;
  /** Disconnects. */
  onClose: () => void;
}

type Phase = 'connecting' | 'connected' | 'disconnected' | 'unavailable' | 'failed';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// X keysyms for the keys a phone keyboard lacks, and the modifiers sent round shortcuts.
const KEY = {
  esc: 0xff1b,
  tab: 0xff09,
  backspace: 0xff08,
  enter: 0xff0d,
  left: 0xff51,
  up: 0xff52,
  right: 0xff53,
  down: 0xff54,
  home: 0xff50,
  end: 0xff57,
  pageUp: 0xff55,
  pageDown: 0xff56,
  del: 0xffff,
  ctrl: 0xffe3,
  alt: 0xffe9,
};
const MODIFIER_CODE: Record<number, string> = { [KEY.ctrl]: 'ControlLeft', [KEY.alt]: 'AltLeft' };
/** Keys a hardware keyboard on a tablet sends that are not text; the text comes through the field. */
const NAMED_KEYS: Record<string, number> = {
  Enter: KEY.enter,
  Tab: KEY.tab,
  Escape: KEY.esc,
  ArrowLeft: KEY.left,
  ArrowUp: KEY.up,
  ArrowRight: KEY.right,
  ArrowDown: KEY.down,
  Home: KEY.home,
  End: KEY.end,
  PageUp: KEY.pageUp,
  PageDown: KEY.pageDown,
  Delete: KEY.del,
};

/** On a Mac, ⌘ is the shortcut key; the Linux desktop's is Control. */
const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
/** How long after Ctrl/⌘+C the desktop's new clipboard is still copied here on its own. */
const COPY_WINDOW_MS = 3000;
/** How long the copy button waits for the desktop's clipboard after sending Ctrl+C. */
const COPY_WAIT_MS = 1000;
/** A moment for the desktop to own pasted text before the app there is asked to paste it. */
const PASTE_DELAY_MS = 100;
/** Two taps this close in time are a double tap. */
const DOUBLE_TAP_MS = 300;
/** On the touchpad, the view follows the pointer to keep it this far inside the edge. */
const FOLLOW_MARGIN = 48;
/** Desktop wheel pixels per desktop pixel a finger drags: a little less, so pages do not race. */
const SCROLL_GAIN = 0.8;
/**
 * What the hidden field holds between keystrokes. A phone keyboard reports Backspace only as text
 * taken away (Android names no key), so there must always be something to take away.
 */
const IME_FILL = '_'.repeat(32);
/** Whether the special keys are out, kept on this device. Storage may be blocked. */
const KEYS_KEY = 'calli.desktop.keys';
const keysShown = () => {
  try {
    return localStorage.getItem(KEYS_KEY) === '1';
  } catch {
    return false;
  }
};

/** The keysym that types a character: Latin-1 is its own code, the rest of Unicode is offset. */
const keysym = (ch: string): number => {
  if (ch === '\n') return KEY.enter;
  const cp = ch.codePointAt(0) ?? 0;
  return cp < 0x100 ? cp : 0x1000000 + cp;
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * A mouse event at a point on the screen, given to noVNC's canvas, which sends it on; the point is
 * kept on the picture. A press makes noVNC cover the page with a capture layer, gone only when a
 * release reaches the window; one sent to the canvas stops there. Sent to the window, noVNC passes
 * it to the canvas itself and lifts the layer, which would otherwise sit over the touch layer.
 */
function mouseAt(canvas: HTMLCanvasElement, type: 'mousedown' | 'mouseup' | 'mousemove', clientX: number, clientY: number, buttons: number, button = 0) {
  const r = canvas.getBoundingClientRect();
  const ev = new MouseEvent(type, { clientX: clamp(clientX, r.left, r.right - 0.5), clientY: clamp(clientY, r.top, r.bottom - 0.5), buttons, button, bubbles: true, cancelable: true });
  (type === 'mouseup' ? window : canvas).dispatchEvent(ev);
}

/** The wheel at a point on the picture; noVNC turns every 50 pixels into one notch. */
function wheelAt(canvas: HTMLCanvasElement, clientX: number, clientY: number, deltaX: number, deltaY: number) {
  const r = canvas.getBoundingClientRect();
  canvas.dispatchEvent(new WheelEvent('wheel', { deltaX, deltaY, deltaMode: 0, clientX: clamp(clientX, r.left, r.right - 0.5), clientY: clamp(clientY, r.top, r.bottom - 0.5), bubbles: true, cancelable: true }));
}

/**
 * Copies text that is still on its way. Safari lets a tap write to the clipboard only while the
 * tap is being handled, so the write starts at once with the text to follow; where that is not
 * possible (plain http) the copy is made when the text comes, still within the tap's permission
 * on most browsers. Resolves false when nothing came or the copy was refused.
 */
async function copyLater(text: Promise<string | null>): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
    const blob = text.then((s) => {
      if (s === null) throw new Error('nothing to copy');
      return new Blob([s], { type: 'text/plain' });
    });
    blob.catch(() => {});
    try {
      await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
      return true;
    } catch {
      // Refused, or nothing came: the plain copy below.
    }
  }
  const s = await text;
  return s !== null && copyText(s);
}

/** The VNC password is kept on this device, per machine, like the token. Storage may be blocked. */
const passwordKey = (host: string | null) => `calli.vnc.${host ?? 'local'}`;
function loadPassword(host: string | null): string | null {
  try {
    return localStorage.getItem(passwordKey(host));
  } catch {
    return null;
  }
}
function savePassword(host: string | null, password: string | null): void {
  try {
    if (password) localStorage.setItem(passwordKey(host), password);
    else localStorage.removeItem(passwordKey(host));
  } catch {
    // Storage unavailable: asked again next time.
  }
}

export function DesktopPanel({ api, hidden = false, onMinimize, onClose }: Props) {
  // Bumped by Reconnect: a new ticket and a new connection.
  const [nonce, setNonce] = useState(0);
  const [phase, setPhase] = useState<Phase>('connecting');
  const [failure, setFailure] = useState('');
  const [asking, setAsking] = useState<null | 'first' | 'wrong'>(null);
  const touch = useIsTouch();
  // The desktop opens ready to use, as Chrome Remote Desktop does: a hover moves the machine's
  // cursor, a click lands there, typing goes straight in; the Control button steps back to
  // just watching.
  const [control, setControl] = useState(true);
  const [fit, setFit] = useState(true);
  // On a touch screen: the picture's zoom (1 fits it whole), the desktop's own size, the room it
  // is shown in, and in control whether the finger points (the default) or the screen is a touchpad.
  const [zoom, setZoom] = useState(1);
  const [fb, setFb] = useState<Size>({ w: 0, h: 0 });
  const [box, setBox] = useState<Size>({ w: 0, h: 0 });
  const [touchpad, setTouchpad] = useState(false);
  const [cursor, setCursor] = useState({ x: 0, y: 0 });
  const viewportRef = useRef<HTMLDivElement>(null);
  const padRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(1);
  zoomRef.current = zoom;
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  /** Where the view scrolls once the new zoom is laid out, so a pinch stays under the fingers. */
  const pendingScrollRef = useRef<{ left: number; top: number } | null>(null);
  // The phone's keyboard: up while the hidden field has focus. While it is, the picture keeps the
  // height it had without it, so it neither shrinks nor jumps; the keyboard covers its lower part.
  const imeRef = useRef<HTMLTextAreaElement>(null);
  const [keyboard, setKeyboard] = useState(false);
  const keyboardRef = useRef(false);
  keyboardRef.current = keyboard;
  const panelRef = useRef<HTMLDivElement>(null);
  const [restHeight, setRestHeight] = useState(0);
  // The keys a phone keyboard lacks, in a strip over the pills when asked for from the menu. Ctrl,
  // latched there, goes with the next key.
  const [keysOpen, setKeysOpenState] = useState(() => keysShown());
  const setKeysOpen = (open: boolean) => {
    setKeysOpenState(open);
    try {
      localStorage.setItem(KEYS_KEY, open ? '1' : '0');
    } catch {
      // Not kept: closed again next time.
    }
  };
  const [ctrlLatched, setCtrlLatched] = useState(false);
  const ctrlRef = useRef(false);
  // What the panel calls the machine: its name in the hub. Never the name the VNC server reports
  // (TigerVNC's is user@host), which would put both in every screenshot.
  const [machine, setMachine] = useState(() => (api.host ? '' : t('desktop.thisComputer')));
  // A moment's answer to copy or paste: a tick on the button, or a line above the buttons.
  const [done, setDone] = useState<null | 'copy' | 'paste'>(null);
  const [copying, setCopying] = useState(false);
  const [toast, setToast] = useState('');
  const noteTimerRef = useRef(0);
  // The clipboard sheet on a touch screen, opened when the clipboard here cannot be reached.
  const [sheetSignal, setSheetSignal] = useState(0);
  const toolsRef = useRef<HTMLDivElement>(null);
  // The desktop's clipboard as last heard, and whether it changed since the person looked.
  const [remoteClip, setRemoteClip] = useState('');
  const [clipFresh, setClipFresh] = useState(false);
  const clipFreshRef = useRef(false);
  clipFreshRef.current = clipFresh;
  const remoteClipRef = useRef('');
  const copyWantedRef = useRef(0);
  /** The copy button, waiting for the desktop's answer to Ctrl+C. */
  const clipWaitRef = useRef<((text: string) => void) | null>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const viewRef = useRef({ control, fit });
  viewRef.current = { control, fit };
  // A password the server turned down: the next connection asks again, saying so.
  const wrongRef = useRef(false);
  const pendingRef = useRef<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let rfb: RFB | null = null;
    const screen = screenRef.current;
    if (!screen) return;
    const phaseNow = (p: Phase) => {
      if (!disposed) setPhase(p);
    };
    void (async () => {
      try {
        const { available } = await api.desktop();
        if (disposed) return;
        if (!available) return phaseNow('unavailable');
        // noVNC is loaded only when a desktop is opened.
        const [{ default: Client }, url] = await Promise.all([import('@novnc/novnc'), api.desktopSocketUrl()]);
        if (disposed) return;
        const saved = loadPassword(api.host);
        pendingRef.current = saved;
        rfb = new Client(screen, url, { shared: true, ...(saved ? { credentials: { password: saved } } : {}) });
        rfbRef.current = rfb;
        rfb.background = 'transparent';
        rfb.resizeSession = false;
        rfb.viewOnly = !viewRef.current.control;
        rfb.scaleViewport = viewRef.current.fit;
        rfb.clipViewport = !viewRef.current.fit;
        rfb.dragViewport = !viewRef.current.fit && !viewRef.current.control;
        rfb.addEventListener('connect', () => {
          wrongRef.current = false;
          savePassword(api.host, pendingRef.current);
          phaseNow('connected');
          // The keyboard lands in the desktop right away, without a click into it first.
          if (viewRef.current.control) rfb?.focus();
        });
        rfb.addEventListener('clipboard', (e) => {
          const text = (e as CustomEvent<{ text: string }>).detail.text;
          if (disposed) return;
          clipWaitRef.current?.(text);
          if (text === remoteClipRef.current) return;
          remoteClipRef.current = text;
          setRemoteClip(text);
          // Just after Ctrl/⌘+C the keystroke still counts as the person's: the copy lands here too.
          if (Date.now() - copyWantedRef.current < COPY_WINDOW_MS) {
            copyWantedRef.current = 0;
            void copyText(text).then((ok) => {
              setClipFresh(!ok);
              rfbRef.current?.focus();
            });
          } else {
            setClipFresh(true);
          }
        });
        rfb.addEventListener('credentialsrequired', () => {
          if (!disposed) setAsking(wrongRef.current ? 'wrong' : 'first');
        });
        // Why the server turned the connection away, when it is not a wrong password.
        let refused = '';
        rfb.addEventListener('securityfailure', (e) => {
          const reason = (e as CustomEvent<{ reason?: string }>).detail.reason ?? '';
          // TigerVNC's word for a wrong password; anything else (too many failures) is a refusal.
          if (reason && !/authentication/i.test(reason)) {
            refused = /too many/i.test(reason) ? t('desktop.blocked') : t('desktop.refused', reason);
            return;
          }
          savePassword(api.host, null);
          wrongRef.current = true;
        });
        rfb.addEventListener('disconnect', () => {
          if (disposed) return;
          rfbRef.current = null;
          setAsking(null);
          if (refused) {
            setFailure(refused);
            phaseNow('failed');
          } else if (wrongRef.current) {
            // A wrong password closes the connection; a new one asks for the password again.
            setNonce((n) => n + 1);
          } else {
            phaseNow('disconnected');
          }
        });
      } catch (e) {
        if (disposed) return;
        setFailure(message(e));
        phaseNow('failed');
      }
    })();
    return () => {
      disposed = true;
      rfbRef.current = null;
      rfb?.disconnect();
    };
  }, [api, nonce]);

  useEffect(() => {
    const rfb = rfbRef.current;
    if (!rfb) return;
    rfb.viewOnly = !control;
    // On a touch screen the picture always fits its box; zooming makes the box bigger.
    rfb.scaleViewport = touch || fit;
    rfb.clipViewport = !touch && !fit;
    // At full size the picture is panned by dragging, while dragging is not the desktop's own.
    rfb.dragViewport = !touch && !fit && !control;
    if (control && !touch && !hidden) rfb.focus();
  }, [control, fit, phase, touch, hidden]);

  // The desktop's size, from noVNC's canvas, and the room the picture has: both decide the zoom.
  useEffect(() => {
    const canvas = screenRef.current?.querySelector('canvas');
    const viewport = viewportRef.current;
    if (!touch || !canvas || !viewport || phase !== 'connected') return;
    const readFb = () => {
      setFb({ w: canvas.width, h: canvas.height });
      setCursor((c) => (c.x || c.y ? c : { x: canvas.width / 2, y: canvas.height / 2 }));
    };
    const readBox = () => setBox({ w: viewport.clientWidth, h: viewport.clientHeight });
    readFb();
    readBox();
    const mo = new MutationObserver(readFb);
    mo.observe(canvas, { attributes: true, attributeFilter: ['width', 'height'] });
    const ro = new ResizeObserver(readBox);
    ro.observe(viewport);
    return () => {
      mo.disconnect();
      ro.disconnect();
    };
  }, [touch, phase, hidden]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const pending = pendingScrollRef.current;
    if (!viewport || !pending) return;
    viewport.scrollLeft = pending.left;
    viewport.scrollTop = pending.top;
    pendingScrollRef.current = null;
  }, [zoom]);

  const fitted = fitSize(fb, box);
  /** Zooms to `z`, keeping the point at (clientX, clientY) where it is on the screen. */
  const zoomTo = (z1: number, clientX: number, clientY: number) => {
    const viewport = viewportRef.current;
    if (!viewport || !fitted.w) return;
    const z0 = zoomRef.current;
    z1 = clampZoom(z1);
    if (Math.abs(z1 - z0) < 0.001) return;
    const rect = viewport.getBoundingClientRect();
    const offset = (z: number, room: number, size: number) => Math.max(0, (room - size * z) / 2);
    pendingScrollRef.current = {
      left: scrollForZoom(viewport.scrollLeft, clientX - rect.left, z0, z1, offset(z0, box.w, fitted.w), offset(z1, box.w, fitted.w)),
      top: scrollForZoom(viewport.scrollTop, clientY - rect.top, z0, z1, offset(z0, box.h, fitted.h), offset(z1, box.h, fitted.h)),
    };
    zoomRef.current = z1;
    setZoom(z1);
  };
  /** Double tap, or the fit button: between the whole picture and one that fills the height. */
  const toggleZoom = (clientX: number, clientY: number) => {
    const fill = fillZoom(fb, box);
    zoomTo(zoomRef.current > 1.05 ? 1 : fill > 1.05 ? fill : 2.5, clientX, clientY);
  };
  // The gesture handlers below live as long as the mode does; through these they see the latest sizes.
  const zoomToRef = useRef(zoomTo);
  zoomToRef.current = zoomTo;
  const toggleZoomRef = useRef(toggleZoom);
  toggleZoomRef.current = toggleZoom;

  // A phone opens with the picture big enough to work on: filled to the height, the rest of its
  // width under a finger's pan. Chosen once, when the desktop's size is first known, and only if
  // no hand has zoomed already; a double tap still finds the whole picture, and a pinch's zoom
  // stays. On a computer screen the whole picture remains the fit.
  const openedRef = useRef(false);
  useEffect(() => {
    if (!touch || openedRef.current || phase !== 'connected' || !fb.w || !fitted.w) return;
    openedRef.current = true;
    const fill = fillZoom(fb, box);
    if (fill <= 1.05 || zoomRef.current !== 1) return;
    pendingScrollRef.current = { left: (fitted.w * fill - box.w) / 2, top: 0 };
    zoomRef.current = fill;
    setZoom(fill);
  }, [touch, phase, fb, fitted.w, box]);

  // View only on a touch screen: one finger scrolls the view (the browser's own), two pinch to
  // zoom it, a double tap zooms in on a spot or back out. Nothing reaches the desktop.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!touch || control || !viewport || phase !== 'connected') return;
    let pinch: { dist: number; zoom: number } | null = null;
    let lastTap = { t: 0, x: 0, y: 0 };
    let start = { t: 0, x: 0, y: 0 };
    const dist = (e: TouchEvent) => Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    const mid = (e: TouchEvent) => ({ x: (e.touches[0].clientX + e.touches[1].clientX) / 2, y: (e.touches[0].clientY + e.touches[1].clientY) / 2 });
    const onStart = (e: TouchEvent) => {
      if (e.touches.length === 2) pinch = { dist: dist(e), zoom: zoomRef.current };
      else if (e.touches.length === 1) start = { t: Date.now(), x: e.touches[0].clientX, y: e.touches[0].clientY };
    };
    const onMove = (e: TouchEvent) => {
      if (!pinch || e.touches.length !== 2) return;
      e.preventDefault();
      const m = mid(e);
      zoomToRef.current(pinch.zoom * (dist(e) / pinch.dist), m.x, m.y);
    };
    const onEnd = (e: TouchEvent) => {
      if (pinch) {
        if (e.touches.length < 2) pinch = null;
        return;
      }
      const t = e.changedTouches[0];
      if (!t || !isTap(Date.now() - start.t, Math.hypot(t.clientX - start.x, t.clientY - start.y))) return;
      const now = Date.now();
      if (now - lastTap.t < DOUBLE_TAP_MS && Math.hypot(t.clientX - lastTap.x, t.clientY - lastTap.y) < 40) {
        toggleZoomRef.current(t.clientX, t.clientY);
        lastTap = { t: 0, x: 0, y: 0 };
      } else {
        lastTap = { t: now, x: t.clientX, y: t.clientY };
      }
    };
    viewport.addEventListener('touchstart', onStart, { passive: true });
    viewport.addEventListener('touchmove', onMove, { passive: false });
    viewport.addEventListener('touchend', onEnd);
    return () => {
      viewport.removeEventListener('touchstart', onStart);
      viewport.removeEventListener('touchmove', onMove);
      viewport.removeEventListener('touchend', onEnd);
    };
  }, [touch, control, phase, hidden]);

  // iOS keeps a focused field's keyboard over the page and, while it is up, hands the picture
  // neither touches nor scrolls. A finger put on the picture or the pad takes the field's focus
  // away, the way any iOS app dismisses its keyboard on a touch outside the text; the keyboard
  // button brings it back. Where no software keyboard covers the window the focus is left alone.
  const dismissKeyboardWithTouch = () => {
    if (softwareKeyboardUp(window.innerHeight, window.visualViewport?.height ?? null)) imeRef.current?.blur();
  };

  // The touchpad, chosen from the menu: a finger moves the pointer (drawn
  // here, the desktop's own cursor is not shown on touch), a tap clicks, two fingers tapped
  // right-click, two moving scroll, two spreading zoom, and a long press starts a drag. The
  // gestures become mouse and wheel events on noVNC's canvas, which sends them on.
  useEffect(() => {
    const pad = padRef.current;
    const canvas = screenRef.current?.querySelector('canvas');
    const viewport = viewportRef.current;
    if (!touch || !control || !touchpad || !pad || !canvas || !viewport || phase !== 'connected') return;
    // The touchpad's events happen where its pointer is, not where the finger is.
    const fire = (type: 'mousedown' | 'mouseup' | 'mousemove', buttons: number, button = 0) => {
      const rect = canvas.getBoundingClientRect();
      const s = rect.width / (canvas.width || 1);
      const { x, y } = cursorRef.current;
      mouseAt(canvas, type, rect.left + (x + 0.5) * s, rect.top + (y + 0.5) * s, buttons, button);
    };
    const click = (button: 0 | 2) => {
      const mask = button === 2 ? 2 : 1;
      fire('mousedown', mask, button);
      fire('mouseup', 0, button);
    };
    const wheel = (deltaY: number) => {
      const rect = canvas.getBoundingClientRect();
      const s = rect.width / (canvas.width || 1);
      const { x, y } = cursorRef.current;
      wheelAt(canvas, rect.left + x * s, rect.top + y * s, 0, deltaY);
    };
    // Keeps the pointer in sight when zoomed in: the view scrolls after it near the edge.
    const follow = () => {
      const rect = canvas.getBoundingClientRect();
      const vp = viewport.getBoundingClientRect();
      const s = rect.width / (canvas.width || 1);
      const px = rect.left + cursorRef.current.x * s - vp.left;
      const py = rect.top + cursorRef.current.y * s - vp.top;
      if (px < FOLLOW_MARGIN) viewport.scrollLeft += px - FOLLOW_MARGIN;
      else if (px > vp.width - FOLLOW_MARGIN) viewport.scrollLeft += px - vp.width + FOLLOW_MARGIN;
      if (py < FOLLOW_MARGIN) viewport.scrollTop += py - FOLLOW_MARGIN;
      else if (py > vp.height - FOLLOW_MARGIN) viewport.scrollTop += py - vp.height + FOLLOW_MARGIN;
    };
    const moveBy = (dx: number, dy: number, ms: number) => {
      const rect = canvas.getBoundingClientRect();
      const perPx = (canvas.width || 1) / (rect.width || 1);
      const c = cursorRef.current;
      const next = {
        x: Math.min(canvas.width - 1, Math.max(0, c.x + pointerDelta(dx, ms, perPx))),
        y: Math.min(canvas.height - 1, Math.max(0, c.y + pointerDelta(dy, ms, perPx))),
      };
      cursorRef.current = next;
      setCursor(next);
    };

    type Mode = 'idle' | 'one' | 'drag' | 'two' | 'zoom' | 'scroll';
    let mode: Mode = 'idle';
    let started = 0;
    let last = { x: 0, y: 0, t: 0 };
    let travelled = 0;
    let longTimer = 0;
    let two = { dist: 0, mid: { x: 0, y: 0 }, lastMid: { x: 0, y: 0 }, zoom: 1 };
    const dist = (e: TouchEvent) => Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    const mid = (e: TouchEvent) => ({ x: (e.touches[0].clientX + e.touches[1].clientX) / 2, y: (e.touches[0].clientY + e.touches[1].clientY) / 2 });

    const onStart = (e: TouchEvent) => {
      e.preventDefault();
      dismissKeyboardWithTouch();
      if (e.touches.length === 1 && mode === 'idle') {
        mode = 'one';
        started = Date.now();
        travelled = 0;
        last = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: started };
        longTimer = window.setTimeout(() => {
          if (mode !== 'one' || travelled >= TAP_SLOP_PX) return;
          mode = 'drag';
          fire('mousedown', 1);
          navigator.vibrate?.(10);
        }, LONG_PRESS_MS);
      } else if (e.touches.length === 2 && (mode === 'one' || mode === 'idle')) {
        window.clearTimeout(longTimer);
        mode = 'two';
        started = Date.now();
        const m = mid(e);
        two = { dist: dist(e), mid: m, lastMid: m, zoom: zoomRef.current };
      }
    };
    const onMove = (e: TouchEvent) => {
      e.preventDefault();
      if ((mode === 'one' || mode === 'drag') && e.touches.length === 1) {
        const t = e.touches[0];
        const now = Date.now();
        const dx = t.clientX - last.x;
        const dy = t.clientY - last.y;
        travelled += Math.hypot(dx, dy);
        if (travelled >= TAP_SLOP_PX) window.clearTimeout(longTimer);
        moveBy(dx, dy, now - last.t);
        last = { x: t.clientX, y: t.clientY, t: now };
        fire('mousemove', mode === 'drag' ? 1 : 0);
        follow();
      } else if (e.touches.length === 2 && (mode === 'two' || mode === 'zoom' || mode === 'scroll')) {
        const m = mid(e);
        if (mode === 'two') {
          const kind = twoFingerKind(two.dist, dist(e), Math.hypot(m.x - two.mid.x, m.y - two.mid.y));
          if (kind) mode = kind;
        }
        if (mode === 'zoom') zoomToRef.current(two.zoom * (dist(e) / two.dist), m.x, m.y);
        // Fingers up scroll the page down, as on a phone's own lists.
        if (mode === 'scroll') wheel((two.lastMid.y - m.y) * 2);
        two.lastMid = m;
      }
    };
    const onEnd = (e: TouchEvent) => {
      e.preventDefault();
      if (e.touches.length > 0) return;
      window.clearTimeout(longTimer);
      const ms = Date.now() - started;
      if (mode === 'one' && isTap(ms, travelled)) click(0);
      else if (mode === 'two' && ms < 300) click(2);
      else if (mode === 'drag') fire('mouseup', 0);
      mode = 'idle';
    };
    pad.addEventListener('touchstart', onStart, { passive: false });
    pad.addEventListener('touchmove', onMove, { passive: false });
    pad.addEventListener('touchend', onEnd, { passive: false });
    pad.addEventListener('touchcancel', onEnd, { passive: false });
    return () => {
      window.clearTimeout(longTimer);
      pad.removeEventListener('touchstart', onStart);
      pad.removeEventListener('touchmove', onMove);
      pad.removeEventListener('touchend', onEnd);
      pad.removeEventListener('touchcancel', onEnd);
    };
  }, [touch, control, touchpad, phase, hidden]);

  // Direct touch, the default in control: the finger is the pointer. A tap clicks where it lands
  // (two quick ones double-click), a long press right-clicks, and a long press then a drag holds
  // the button down (selecting text, moving a window). One finger dragged only moves the picture
  // here, to look elsewhere once zoomed in; it never reaches the desktop, so a slip cannot click or
  // scroll there. Two fingers pinch to zoom, or move together to scroll what is under them.
  useEffect(() => {
    const pad = padRef.current;
    const canvas = screenRef.current?.querySelector('canvas');
    const viewport = viewportRef.current;
    if (!touch || !control || touchpad || !pad || !canvas || !viewport || phase !== 'connected') return;
    const inside = (x: number, y: number) => {
      const r = canvas.getBoundingClientRect();
      return x >= r.left && x < r.right && y >= r.top && y < r.bottom;
    };
    const click = (x: number, y: number, button: 0 | 2) => {
      mouseAt(canvas, 'mousedown', x, y, button === 2 ? 2 : 1, button);
      mouseAt(canvas, 'mouseup', x, y, 0, button);
    };
    // Desktop pixels per screen pixel: the same drag scrolls less of a picture zoomed in.
    const perPx = () => (canvas.width || 1) / (canvas.getBoundingClientRect().width || 1);

    // one: a finger down, not yet a tap or anything else; held: past the long press, still;
    // two: two fingers, not yet a pinch or a scroll; done: nothing more until all are lifted.
    type Mode = 'idle' | 'one' | 'held' | 'drag' | 'pan' | 'two' | 'zoom' | 'scroll' | 'done';
    let mode: Mode = 'idle';
    let start = { x: 0, y: 0 };
    let last = { x: 0, y: 0 };
    let axis: 'x' | 'y' = 'y';
    let longTimer = 0;
    let lastTap = { t: 0, x: 0, y: 0 };
    let two = { dist: 1, zoom: 1, start: { x: 0, y: 0 }, mid: { x: 0, y: 0 } };
    const dist = (e: TouchEvent) => Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY) || 1;
    const mid = (e: TouchEvent) => ({ x: (e.touches[0].clientX + e.touches[1].clientX) / 2, y: (e.touches[0].clientY + e.touches[1].clientY) / 2 });

    const tap = (x: number, y: number) => {
      if (!inside(x, y)) return;
      const now = Date.now();
      // The second tap lands where the first did, so the desktop takes the two as a double click.
      if (now - lastTap.t < DOUBLE_TAP_MS && Math.hypot(x - lastTap.x, y - lastTap.y) < 30) {
        x = lastTap.x;
        y = lastTap.y;
        lastTap = { t: 0, x: 0, y: 0 };
      } else {
        lastTap = { t: now, x, y };
      }
      click(x, y, 0);
    };
    const onStart = (e: TouchEvent) => {
      e.preventDefault();
      dismissKeyboardWithTouch();
      if (e.touches.length === 1 && mode === 'idle') {
        mode = 'one';
        start = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        last = start;
        longTimer = window.setTimeout(() => {
          if (mode !== 'one') return;
          mode = 'held';
          navigator.vibrate?.(10);
        }, LONG_PRESS_MS);
      } else if (e.touches.length === 2 && (mode === 'one' || mode === 'pan')) {
        window.clearTimeout(longTimer);
        mode = 'two';
        const m = mid(e);
        two = { dist: dist(e), zoom: zoomRef.current, start: m, mid: m };
      }
    };
    const onMove = (e: TouchEvent) => {
      e.preventDefault();
      if (e.touches.length === 1) {
        const t = e.touches[0];
        const moved = Math.hypot(t.clientX - start.x, t.clientY - start.y) >= TAP_SLOP_PX;
        if (mode === 'one' && moved) {
          window.clearTimeout(longTimer);
          mode = 'pan';
        } else if (mode === 'held' && moved) {
          mode = 'drag';
          mouseAt(canvas, 'mousedown', start.x, start.y, 1);
        }
        if (mode === 'pan') {
          // The picture follows the finger; at the whole-picture zoom there is nowhere to go.
          viewport.scrollLeft -= t.clientX - last.x;
          viewport.scrollTop -= t.clientY - last.y;
        } else if (mode === 'drag') {
          mouseAt(canvas, 'mousemove', t.clientX, t.clientY, 1);
        }
        if (mode !== 'one' && mode !== 'held') last = { x: t.clientX, y: t.clientY };
      } else if (e.touches.length === 2 && (mode === 'two' || mode === 'zoom' || mode === 'scroll')) {
        const m = mid(e);
        if (mode === 'two') {
          const kind = twoFingerKind(two.dist, dist(e), Math.hypot(m.x - two.start.x, m.y - two.start.y));
          if (kind === 'scroll') axis = scrollAxis(m.x - two.start.x, m.y - two.start.y);
          // Scrolling counts from where the fingers began, so the first stretch is not lost.
          if (kind === 'scroll') two.mid = two.start;
          if (kind) mode = kind;
        }
        if (mode === 'zoom') {
          zoomToRef.current(two.zoom * (dist(e) / two.dist), m.x, m.y);
        } else if (mode === 'scroll') {
          // Content follows the fingers, as in a phone's own lists: moving up scrolls down.
          const k = perPx() * SCROLL_GAIN;
          if (axis === 'y') wheelAt(canvas, two.start.x, two.start.y, 0, (two.mid.y - m.y) * k);
          else wheelAt(canvas, two.start.x, two.start.y, (two.mid.x - m.x) * k, 0);
        }
        two.mid = m;
      }
    };
    const onEnd = (e: TouchEvent) => {
      e.preventDefault();
      if (e.touches.length > 0) {
        if (mode === 'two' || mode === 'zoom' || mode === 'scroll') mode = 'done';
        return;
      }
      window.clearTimeout(longTimer);
      if (mode === 'one') tap(start.x, start.y);
      else if (mode === 'held' && inside(start.x, start.y)) click(start.x, start.y, 2);
      else if (mode === 'drag') mouseAt(canvas, 'mouseup', last.x, last.y, 0);
      mode = 'idle';
    };
    const onCancel = () => {
      window.clearTimeout(longTimer);
      if (mode === 'drag') mouseAt(canvas, 'mouseup', last.x, last.y, 0);
      mode = 'idle';
    };
    pad.addEventListener('touchstart', onStart, { passive: false });
    pad.addEventListener('touchmove', onMove, { passive: false });
    pad.addEventListener('touchend', onEnd, { passive: false });
    pad.addEventListener('touchcancel', onCancel);
    return () => {
      window.clearTimeout(longTimer);
      if (mode === 'drag') mouseAt(canvas, 'mouseup', last.x, last.y, 0);
      pad.removeEventListener('touchstart', onStart);
      pad.removeEventListener('touchmove', onMove);
      pad.removeEventListener('touchend', onEnd);
      pad.removeEventListener('touchcancel', onCancel);
    };
  }, [touch, control, touchpad, phase, hidden]);

  // The phone's own keyboard types through a hidden field. What the input method commits is sent
  // as keys a character at a time, Chinese included; while it is still composing (pinyin, kana)
  // nothing is. The field is emptied back to its fill after each change, so Backspace always has
  // something to rub out and the field never grows.
  useEffect(() => {
    const ime = imeRef.current;
    if (!touch || !control || !ime || phase !== 'connected') return;
    let composing = false;
    const refill = () => {
      ime.value = IME_FILL;
      ime.setSelectionRange(IME_FILL.length, IME_FILL.length);
    };
    const flush = () => {
      const { erased, typed } = textEdit(IME_FILL, ime.value);
      for (let i = 0; i < erased; i++) pressRef.current(KEY.backspace);
      for (const ch of typed) pressRef.current(keysym(ch));
      refill();
    };
    const onCompositionStart = () => {
      composing = true;
    };
    const onCompositionEnd = () => {
      composing = false;
      flush();
    };
    const onInput = (e: Event) => {
      if (composing || (e as InputEvent).isComposing) return;
      flush();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (composing || e.isComposing || e.keyCode === 229) return;
      // A tablet's hardware keyboard: Control shortcuts and the keys that are not text.
      if (e.ctrlKey && !e.altKey && e.key.length === 1) {
        e.preventDefault();
        chordRef.current(keysym(e.key.toLowerCase()), [KEY.ctrl]);
        return;
      }
      const sym = NAMED_KEYS[e.key];
      if (!sym) return;
      e.preventDefault();
      pressRef.current(sym);
    };
    const onFocus = () => {
      refill();
      setKeyboard(true);
    };
    const onBlur = () => setKeyboard(false);
    refill();
    ime.addEventListener('compositionstart', onCompositionStart);
    ime.addEventListener('compositionend', onCompositionEnd);
    ime.addEventListener('input', onInput);
    ime.addEventListener('keydown', onKeyDown);
    ime.addEventListener('focus', onFocus);
    ime.addEventListener('blur', onBlur);
    if (document.activeElement === ime) setKeyboard(true);
    return () => {
      ime.removeEventListener('compositionstart', onCompositionStart);
      ime.removeEventListener('compositionend', onCompositionEnd);
      ime.removeEventListener('input', onInput);
      ime.removeEventListener('keydown', onKeyDown);
      ime.removeEventListener('focus', onFocus);
      ime.removeEventListener('blur', onBlur);
    };
  }, [touch, control, phase]);

  // Out of sight or view only, the keyboard goes away; a field taken away while focused reports no blur.
  useEffect(() => {
    if (!hidden && control && phase === 'connected') return;
    imeRef.current?.blur();
    setKeyboard(false);
  }, [hidden, control, phase]);

  useEffect(() => () => window.clearTimeout(noteTimerRef.current), []);

  useEffect(() => {
    if (!api.host) return setMachine(t('desktop.thisComputer'));
    let gone = false;
    setMachine('');
    api.hosts().then(
      (hosts) => {
        if (!gone) setMachine(hosts.find((h) => h.id === api.host)?.label || t('desktop.title'));
      },
      () => {
        if (!gone) setMachine(t('desktop.title'));
      },
    );
    return () => {
      gone = true;
    };
  }, [api]);

  useEffect(() => {
    const panel = panelRef.current;
    if (!touch || !panel) return;
    const read = () => {
      if (!keyboardRef.current) setRestHeight(panel.clientHeight);
    };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(panel);
    return () => ro.disconnect();
  }, [touch]);

  // With a real keyboard the shortcuts carry the clipboard: Ctrl/⌘+V pastes this device's text on
  // the desktop, Ctrl/⌘+C copies the desktop's back. On a Mac, ⌘ stands in for Control.
  useEffect(() => {
    const screen = screenRef.current;
    const rfb = rfbRef.current;
    if (!screen || !rfb || !control || touch || phase !== 'connected') return;
    const swallowed = new Set<string>();
    let pasteTimer = 0;
    // Control is already down on the desktop unless ⌘ stood in for it.
    const chord = (key: string) => {
      if (MAC) rfb.sendKey(KEY.ctrl, 'ControlLeft', true);
      rfb.sendKey(keysym(key), null);
      if (MAC) rfb.sendKey(KEY.ctrl, 'ControlLeft', false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      // ⌘ alone is not passed on as the desktop's Super key: it only ever means Control here.
      if (MAC && e.key === 'Meta') {
        e.stopPropagation();
        return;
      }
      const shortcut = MAC ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
      if (!shortcut || e.altKey || e.key.length !== 1) return;
      const key = e.key.toLowerCase();
      e.stopPropagation();
      swallowed.add(e.code);
      if (key === 'v') {
        // Not prevented: the browser pastes, and the paste event below brings the text. Without
        // one (nothing to paste here) the desktop pastes what it already has.
        pasteTimer = window.setTimeout(() => chord('v'), 150);
        return;
      }
      e.preventDefault();
      if (key === 'c' || key === 'x') copyWantedRef.current = Date.now();
      chord(key);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if ((MAC && e.key === 'Meta') || swallowed.delete(e.code)) e.stopPropagation();
    };
    const onPaste = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData('text/plain') ?? '';
      window.clearTimeout(pasteTimer);
      e.preventDefault();
      if (text) {
        rfb.clipboardPasteFrom(text);
        remoteClipRef.current = text;
        setRemoteClip(text);
      }
      // A moment for the desktop to own the new text before the app there asks for it.
      pasteTimer = window.setTimeout(() => chord('v'), 50);
    };
    screen.addEventListener('keydown', onKeyDown, true);
    screen.addEventListener('keyup', onKeyUp, true);
    screen.addEventListener('paste', onPaste, true);
    return () => {
      window.clearTimeout(pasteTimer);
      screen.removeEventListener('keydown', onKeyDown, true);
      screen.removeEventListener('keyup', onKeyUp, true);
      screen.removeEventListener('paste', onPaste, true);
    };
  }, [control, touch, phase]);

  const sendClip = (text: string) => {
    rfbRef.current?.clipboardPasteFrom(text);
    remoteClipRef.current = text;
    setRemoteClip(text);
  };

  const reconnect = () => {
    setFailure('');
    setPhase('connecting');
    setNonce((n) => n + 1);
  };

  /** A key with modifiers held round it. */
  const chord = (sym: number, mods: number[] = []) => {
    const rfb = rfbRef.current;
    if (!rfb) return;
    for (const m of mods) rfb.sendKey(m, MODIFIER_CODE[m] ?? null, true);
    rfb.sendKey(sym, null);
    for (const m of [...mods].reverse()) rfb.sendKey(m, MODIFIER_CODE[m] ?? null, false);
  };
  /** A key from the phone; Ctrl, if latched, goes with it and is let go. */
  const press = (sym: number) => {
    if (!ctrlRef.current) return chord(sym);
    ctrlRef.current = false;
    setCtrlLatched(false);
    chord(sym, [KEY.ctrl]);
  };
  const chordRef = useRef(chord);
  chordRef.current = chord;
  const pressRef = useRef(press);
  pressRef.current = press;

  const flash = (kind: 'copy' | 'paste') => {
    window.clearTimeout(noteTimerRef.current);
    setToast('');
    setDone(kind);
    noteTimerRef.current = window.setTimeout(() => setDone(null), 1400);
  };
  const say = (text: string) => {
    window.clearTimeout(noteTimerRef.current);
    setDone(null);
    setToast(text);
    noteTimerRef.current = window.setTimeout(() => setToast(''), 2200);
  };
  const openSheet = () => setSheetSignal((n) => n + 1);

  /** Text from here, pasted on the desktop: its clipboard first, then Ctrl+V there. */
  const pasteText = (text: string) => {
    sendClip(text);
    window.setTimeout(() => chord(keysym('v'), [KEY.ctrl]), PASTE_DELAY_MS);
  };

  // Copy: Ctrl+C on the desktop, and what its clipboard says in answer comes here. When nothing
  // answers in time, what it put on its clipboard by itself since last looked at is taken instead:
  // text selected in a terminal, where Ctrl+C does not copy. Without either there is nothing to copy.
  const copyFromDesktop = () => {
    if (!rfbRef.current) return;
    const text = new Promise<string | null>((resolve) => {
      const timer = window.setTimeout(() => {
        clipWaitRef.current = null;
        resolve(clipFreshRef.current ? remoteClipRef.current : null);
      }, COPY_WAIT_MS);
      clipWaitRef.current = (s) => {
        window.clearTimeout(timer);
        clipWaitRef.current = null;
        resolve(s);
      };
    });
    chord(keysym('c'), [KEY.ctrl]);
    setCopying(true);
    void text.then(() => setCopying(false));
    void copyLater(text).then(async (ok) => {
      if ((await text) === null) return say(t('desktop.copyNothing'));
      setClipFresh(false);
      // Refused here: the sheet shows the text, with its own button to copy it.
      if (ok) flash('copy');
      else openSheet();
    });
  };

  // Paste: this device's clipboard, read in the tap itself (Safari asks first, and only allows it
  // during the tap). Where it cannot be read (plain http, refused) the sheet takes it by hand.
  const pasteFromHere = () => {
    const read = window.isSecureContext && navigator.clipboard?.readText ? navigator.clipboard.readText() : Promise.reject(new Error('no clipboard'));
    read.then(
      (text) => {
        if (!text) return say(t('desktop.pasteEmpty'));
        pasteText(text);
        flash('paste');
      },
      () => openSheet(),
    );
  };

  const toggleKeyboard = () => {
    const ime = imeRef.current;
    if (!ime) return;
    // Focused within the tap, or iOS shows no keyboard.
    if (keyboard) ime.blur();
    else ime.focus({ preventScroll: true });
  };
  /** The floating buttons keep the keyboard up: a press on them does not take the field's focus. */
  const keepFocus = (e: ReactPointerEvent) => e.preventDefault();

  const keys: Array<[string, number, JSX.Element?]> = [
    ['Esc', KEY.esc],
    ['Tab', KEY.tab],
    [t('term.left'), KEY.left, <IconArrowLeft size={18} />],
    [t('term.up'), KEY.up, <IconArrowUp size={18} />],
    [t('term.down'), KEY.down, <IconArrowDown size={18} />],
    [t('term.right'), KEY.right, <IconArrowRight size={18} />],
  ];
  const live = phase === 'connected';
  const padOn = touch && control;
  const dockOn = touch && control && live;

  const clipboardSheet = (pastes: boolean) => (
    <ClipboardSheet remote={remoteClip} hint={!touch} pastes={pastes} onSeen={() => setClipFresh(false)} onSend={pastes ? pasteText : sendClip} />
  );

  return (
    <div className={`dialog-scrim term-scrim${touch ? ' desktop-scrim-touch' : ''}`} hidden={hidden}>
      <div className={`panel term-panel desktop-panel${touch ? ' touch' : ''}`} role="dialog" aria-modal="true" aria-label={t('desktop.title')} ref={panelRef}>
        {touch ? (
          // A touch screen, as Codex lays it out: close, the machine (its menu holds the rest), reconnect.
          <header className="desktop-touch-head">
            <button type="button" className="desktop-round" aria-label={t('desktop.close')} title={t('desktop.close')} onClick={onClose}>
              <IconClose size={22} />
            </button>
            <Popover
              label={t('desktop.more')}
              triggerClassName="desktop-machine desktop-more"
              triggerLabel={t('desktop.machineMenu', machine || t('desktop.title'))}
              placement="below"
              panelClassName="desktop-menu"
              trigger={
                <>
                  <IconLaptop size={22} />
                  <span className="desktop-machine-name">{machine}</span>
                  {live && !control && <span className="desktop-touch-mode">{t('desktop.viewOnly')}</span>}
                </>
              }
            >
              {(close) => (
                <>
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={touchpad}
                    className="pop-item"
                    disabled={!live || !control}
                    title={t('desktop.touchpadHint')}
                    onClick={() => {
                      close();
                      setTouchpad((v) => !v);
                    }}
                  >
                    <span className="pop-item-icon"><IconTouchpad size={19} /></span>
                    <span className="pop-item-text"><span className="pop-item-label">{t('desktop.touchpad')}</span></span>
                    {touchpad && <span className="pop-item-check"><IconCheck size={16} /></span>}
                  </button>
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={!control}
                    className="pop-item"
                    disabled={!live}
                    onClick={() => {
                      close();
                      setControl((c) => !c);
                    }}
                  >
                    <span className="pop-item-icon"><IconEye size={19} /></span>
                    <span className="pop-item-text">
                      <span className="pop-item-label">{t('desktop.viewOnly')}</span>
                      <small className="pop-item-hint">{t('desktop.viewOnlyHint')}</small>
                    </span>
                    {!control && <span className="pop-item-check"><IconCheck size={16} /></span>}
                  </button>
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={keysOpen}
                    className="pop-item"
                    disabled={!live || !control}
                    onClick={() => {
                      close();
                      setKeysOpen(!keysOpen);
                    }}
                  >
                    <span className="pop-item-icon"><IconKeyboard size={19} /></span>
                    <span className="pop-item-text"><span className="pop-item-label">{t('desktop.keys')}</span></span>
                    {keysOpen && <span className="pop-item-check"><IconCheck size={16} /></span>}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="pop-item"
                    disabled={!live || !control}
                    onClick={() => {
                      close();
                      openSheet();
                    }}
                  >
                    <span className="pop-item-icon"><IconClipboard size={19} /></span>
                    <span className="pop-item-text"><span className="pop-item-label">{t('desktop.clipboard')}</span></span>
                  </button>
                  <div className="pop-divider" />
                  <button
                    type="button"
                    role="menuitem"
                    className="pop-item"
                    onClick={() => {
                      close();
                      onMinimize();
                    }}
                  >
                    <span className="pop-item-icon"><IconMinus size={19} /></span>
                    <span className="pop-item-text"><span className="pop-item-label">{t('desktop.minimize')}</span></span>
                  </button>
                </>
              )}
            </Popover>
            <button
              type="button"
              className="desktop-round"
              aria-label={t('desktop.reconnect')}
              title={t('desktop.reconnect')}
              disabled={phase === 'connecting'}
              onClick={reconnect}
            >
              <IconRefresh size={22} />
            </button>
          </header>
        ) : (
          <header className="panel-head">
            <div className="term-head-main">
              <h2 className="panel-title">
                <IconDesktop size={20} /> {t('desktop.title')}
              </h2>
              {machine && (
                <span className="desktop-name" title={machine}>
                  {machine}
                </span>
              )}
            </div>
            <div className="desktop-actions">
              <button
                className={`desktop-control${control ? ' on' : ''}`}
                aria-pressed={control}
                title={t('desktop.controlHint')}
                disabled={!live}
                onClick={() => setControl((c) => !c)}
              >
                <IconPointer size={16} />
                {/* The pill filled shows the machine is being driven; the label is where it goes. */}
                <span>{control ? t('desktop.viewOnly') : t('desktop.control')}</span>
              </button>
              <button
                className={`icon-btn${fit ? '' : ' on'}`}
                aria-pressed={!fit}
                aria-label={fit ? t('desktop.actual') : t('desktop.fit')}
                title={fit ? t('desktop.actual') : t('desktop.fit')}
                disabled={!live}
                onClick={() => setFit((f) => !f)}
              >
                <IconFit size={18} />
              </button>
              {/* noVNC passes no clipboard either way while view only: it is part of taking control. */}
              {control && (
                <Popover
                  label={t('desktop.clipboard')}
                  triggerClassName={`icon-btn desktop-clip-btn${clipFresh ? ' fresh' : ''}`}
                  triggerLabel={clipFresh ? t('desktop.clipNew') : t('desktop.clipboard')}
                  triggerTitle={clipFresh ? t('desktop.clipNew') : t('desktop.clipboard')}
                  panelClassName="desktop-clip"
                  align="end"
                  disabled={!live}
                  trigger={<IconClipboard size={18} />}
                >
                  {() => clipboardSheet(false)}
                </Popover>
              )}
              <button className="icon-btn desktop-minimize" aria-label={t('desktop.minimize')} title={t('desktop.minimizeHint')} onClick={onMinimize}>
                <IconMinus size={18} />
              </button>
              <button className="icon-btn" aria-label={t('desktop.close')} title={t('desktop.close')} onClick={onClose}>
                <IconClose size={18} />
              </button>
            </div>
          </header>
        )}
        <div className={`desktop-stage${touch ? ' touch' : ''}`} style={touch && keyboard && restHeight ? { height: restHeight } : undefined}>
          <div className="desktop-viewport" ref={viewportRef}>
            <div className="desktop-content" style={touch && fitted.w ? { width: fitted.w * zoom, height: fitted.h * zoom } : undefined}>
              <div className={`desktop-screen${control ? ' control' : ''}${padOn ? ' under-pad' : ''}`} ref={screenRef} />
              {padOn && touchpad && live && fb.w > 0 && (
                <svg className="desktop-cursor" style={{ left: `${(cursor.x / fb.w) * 100}%`, top: `${(cursor.y / fb.h) * 100}%` }} width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
                  <path d="M2 1.5l12.5 7-5.6 1.4L6.6 15.5z" />
                </svg>
              )}
            </div>
          </div>
          {padOn && live && <div className={`desktop-pad${touchpad ? ' touchpad' : ''}`} ref={padRef} aria-label={touchpad ? t('desktop.touchpadHint') : undefined} />}
        </div>
        {/* The phone's keyboard types into this; it is never seen. At the top, so focusing it gives the
            browser nothing to scroll into view; 16px keeps iOS from zooming to it. */}
        {touch && control && (
          <textarea
            ref={imeRef}
            className="desktop-ime"
            aria-label={t('desktop.input')}
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            rows={1}
            tabIndex={-1}
          />
        )}
        {dockOn && (
          <div className={`desktop-dock${keyboard ? ' over' : ''}`}>
            {keysOpen && (
              <div className="desktop-keys" role="toolbar" aria-label={t('desktop.keys')}>
                {keys.slice(0, 2).map(([label, sym]) => (
                  <button key={label} type="button" onPointerDown={keepFocus} onClick={() => press(sym)}>
                    {label}
                  </button>
                ))}
                <button
                  type="button"
                  className={ctrlLatched ? 'on' : undefined}
                  aria-pressed={ctrlLatched}
                  onPointerDown={keepFocus}
                  onClick={() => {
                    ctrlRef.current = !ctrlRef.current;
                    setCtrlLatched(ctrlRef.current);
                  }}
                >
                  Ctrl
                </button>
                {keys.slice(2).map(([label, sym, icon]) => (
                  <button key={label} type="button" aria-label={label} onPointerDown={keepFocus} onClick={() => press(sym)}>
                    {icon}
                  </button>
                ))}
                <button type="button" aria-label={t('desktop.keysClose')} onPointerDown={keepFocus} onClick={() => setKeysOpen(false)}>
                  <IconClose size={16} />
                </button>
              </div>
            )}
            <div className="desktop-pill" role="toolbar" aria-label={t('desktop.history')}>
              <button type="button" aria-label={t('desktop.back')} title={t('desktop.back')} onPointerDown={keepFocus} onClick={() => chord(KEY.left, [KEY.alt])}>
                <IconPrev size={24} />
              </button>
              <button type="button" aria-label={t('desktop.forward')} title={t('desktop.forward')} onPointerDown={keepFocus} onClick={() => chord(KEY.right, [KEY.alt])}>
                <IconNext size={24} />
              </button>
            </div>
            <div className="desktop-pill desktop-tools" role="toolbar" aria-label={t('desktop.tools')} ref={toolsRef}>
              <button
                type="button"
                className="desktop-kb-btn"
                aria-label={keyboard ? t('desktop.keyboardHide') : t('desktop.keyboard')}
                title={keyboard ? t('desktop.keyboardHide') : t('desktop.keyboard')}
                onPointerDown={keepFocus}
                onClick={toggleKeyboard}
              >
                {keyboard ? <IconChevron open size={24} /> : <IconKeys size={24} />}
              </button>
              <button
                type="button"
                className={`desktop-copy-btn${clipFresh ? ' fresh' : ''}`}
                aria-label={done === 'copy' ? t('desktop.copied') : t('desktop.copy')}
                title={t('desktop.copy')}
                disabled={copying}
                onPointerDown={keepFocus}
                onClick={copyFromDesktop}
              >
                {done === 'copy' ? <IconCheck size={24} /> : <IconCopyStack size={24} />}
              </button>
              <button
                type="button"
                className="desktop-paste-btn"
                aria-label={done === 'paste' ? t('desktop.pasted') : t('desktop.paste')}
                title={t('desktop.paste')}
                onPointerDown={keepFocus}
                onClick={pasteFromHere}
              >
                {done === 'paste' ? <IconCheck size={24} /> : <IconPasteList size={24} />}
              </button>
            </div>
            {toast && (
              <div className="desktop-toast" role="status">
                {toast}
              </div>
            )}
          </div>
        )}
        {touch && (
          <Popover label={t('desktop.clipboard')} anchorRef={toolsRef} openSignal={sheetSignal} panelClassName="desktop-clip viewer" align="end">
            {() => clipboardSheet(true)}
          </Popover>
        )}
        {phase !== 'connected' && !asking && (
          <div className="term-note" role="status">
            {phase === 'connecting' && <p>{t('desktop.connecting')}</p>}
            {phase === 'unavailable' && (
              <>
                <p>{t('desktop.unavailable')}</p>
                <p className="desktop-hint">{t('desktop.unavailableHint')}</p>
                <button onClick={reconnect}>{t('common.retry')}</button>
              </>
            )}
            {phase === 'disconnected' && (
              <>
                <p>{t('desktop.disconnected')}</p>
                <button onClick={reconnect}>{t('desktop.reconnect')}</button>
              </>
            )}
            {phase === 'failed' && (
              <>
                <p>{failure || t('desktop.failed')}</p>
                <button onClick={reconnect}>{t('common.retry')}</button>
              </>
            )}
          </div>
        )}
        {asking && (
          <DesktopPassword
            wrong={asking === 'wrong'}
            onConnect={(password) => {
              setAsking(null);
              pendingRef.current = password;
              rfbRef.current?.sendCredentials({ password });
            }}
            onCancel={() => {
              setAsking(null);
              wrongRef.current = false;
              rfbRef.current?.disconnect();
            }}
          />
        )}
      </div>
    </div>
  );
}

/**
 * Both directions of the clipboard: the desktop's text to copy here, and a field to send text there.
 * On a touch screen sending also pastes it, as the paste button would have.
 */
function ClipboardSheet({ remote, hint, pastes, onSeen, onSend }: { remote: string; hint: boolean; pastes: boolean; onSeen: () => void; onSend: (text: string) => void }) {
  const [copied, setCopied] = useState<null | boolean>(null);
  const [text, setText] = useState('');
  const [sent, setSent] = useState(false);
  useEffect(onSeen, [remote]);
  return (
    <div className="desktop-clip-body">
      <section>
        <h3>{t('desktop.clipRemote')}</h3>
        {remote ? <pre className="desktop-clip-text">{remote}</pre> : <p className="desktop-clip-empty">{t('desktop.clipEmpty')}</p>}
        {remote && (
          <button type="button" className="ghost" onClick={() => void copyText(remote).then(setCopied)}>
            {t('desktop.clipCopy')}
          </button>
        )}
        {copied !== null && <p className={`desktop-clip-note${copied ? '' : ' bad'}`}>{copied ? t('desktop.clipCopied') : t('desktop.clipCopyFailed')}</p>}
      </section>
      <section>
        <h3>{t('desktop.clipLocal')}</h3>
        <textarea
          value={text}
          rows={3}
          placeholder={t('desktop.clipPaste')}
          aria-label={t('desktop.clipPaste')}
          onChange={(e) => {
            setText(e.target.value);
            setSent(false);
          }}
        />
        <button
          type="button"
          className="ghost"
          disabled={!text}
          onClick={() => {
            onSend(text);
            setSent(true);
          }}
        >
          {pastes ? t('desktop.clipSendPaste') : t('desktop.clipSend')}
        </button>
        {sent && <p className="desktop-clip-note">{pastes ? t('desktop.pasted') : t('desktop.clipSent')}</p>}
      </section>
      {hint && <p className="desktop-clip-hint">{t('desktop.clipHint')}</p>}
    </div>
  );
}
