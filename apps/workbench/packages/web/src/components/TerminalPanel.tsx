// A real terminal on the host, one per daemon for now: closing the panel hides it, typing `exit`
// or the daemon's idle sweep ends it. The panel reattaches to whatever terminal is alive, so
// closing and reopening keeps the screen; the daemon replays its backlog on every connect.
import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import type { Api, TerminalFrame, TerminalInfo } from '../api';
import { copyText } from '../clipboard';
import { folderName } from '../paths';
import { useIsTouch } from '../hooks';
import { IconArrowDown, IconArrowLeft, IconArrowRight, IconArrowUp, IconCheck, IconClose, IconCopy, IconSend, IconTerminal } from './icons';
import { DockPanel } from './DockPanel';
import { t } from '../i18n';

interface Props {
  api: Api;
  /** Where a terminal starts. Only used when none is alive; after that the shell goes where it pleases. */
  cwd: string;
  /** Attach to this terminal (a login just started); null attaches to the newest live one. */
  terminalId?: string | null;
  /** Render in the right-hand side dock on desktop instead of covering the viewport. */
  docked?: boolean;
  onClose: () => void;
  onError: (e: unknown) => void;
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(t);
      resolve();
    });
  });

const bytes = (b64: string): Uint8Array => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Terminal colours come from the page's own variables, so themes apply without a mapping of our own. */
function themeFromPage(): { theme: Record<string, string>; dark: boolean } {
  const style = getComputedStyle(document.documentElement);
  const v = (name: string) => style.getPropertyValue(name).trim();
  const m = /^#([0-9a-f]{6})$/i.exec(v('--bg'));
  const n = m ? parseInt(m[1], 16) : 0;
  const dark = !m || (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) < 128;
  // The ANSI 16 are picked by hand for the two modes: xterm's defaults are made for black screens.
  const ansi = dark
    ? ['#161514', '#f97066', '#3f9a72', '#f0935a', '#7ba7d9', '#c39ac9', '#6fc3c9', '#9a978f', '#6f6c65', '#f97066', '#3f9a72', '#f0935a', '#7ba7d9', '#c39ac9', '#6fc3c9', '#ecebe7']
    : ['#1f1e1c', '#b42318', '#2f7d5b', '#8a6a1f', '#3b6ea5', '#8f4f7d', '#20707a', '#6f6c65', '#a9a69e', '#b42318', '#2f7d5b', '#8a6a1f', '#3b6ea5', '#8f4f7d', '#20707a', '#1f1e1c'];
  const names = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white', 'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite'];
  const theme: Record<string, string> = {
    background: v('--bg'),
    foreground: v('--text'),
    cursor: v('--text'),
    cursorAccent: v('--bg'),
    selectionBackground: v('--active'),
  };
  names.forEach((name, i) => (theme[name] = ansi[i]));
  return { theme, dark };
}

/** The keys a phone keyboard lacks, as the bytes a terminal sends for them. */
function keys(appCursor: boolean): Array<[string, string, JSX.Element?]> {
  const arrow = (c: string) => (appCursor ? `\x1bO${c}` : `\x1b[${c}`);
  return [
    ['Esc', '\x1b'],
    ['Tab', '\t'],
    ['Ctrl+C', '\x03'],
    [t('term.up'), arrow('A'), <IconArrowUp size={16} />],
    [t('term.down'), arrow('B'), <IconArrowDown size={16} />],
    [t('term.left'), arrow('D'), <IconArrowLeft size={16} />],
    [t('term.right'), arrow('C'), <IconArrowRight size={16} />],
  ];
}

export function TerminalPanel({ api, cwd, terminalId, docked = false, onClose, onError }: Props) {
  // Bumped by Reopen and Retry: the whole terminal (xterm and stream) is rebuilt from scratch.
  const [nonce, setNonce] = useState(0);
  const [phase, setPhase] = useState<'connecting' | 'running' | 'ended' | 'failed'>('connecting');
  const [failure, setFailure] = useState('');
  const [info, setInfo] = useState<TerminalInfo | null>(null);
  const [hasSelection, setHasSelection] = useState(false);
  const [copiedOut, setCopiedOut] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const copyTimerRef = useRef<number | null>(null);
  // On a touch screen typing goes through a field of our own, not xterm's hidden one: iOS keyboards
  // (Chinese input, autocorrect) lose track of a field whose value the page changes under them,
  // and xterm empties its field on every Enter. This field is only emptied once a line is sent.
  const touch = useIsTouch();
  const touchRef = useRef(touch);
  touchRef.current = touch;
  const [line, setLine] = useState('');
  const sendRef = useRef<((data: string) => void) | null>(null);
  const appCursorRef = useRef<() => boolean>(() => false);

  useEffect(() => {
    let disposed = false;
    const abort = new AbortController();
    const host = hostRef.current;
    if (!host) return;

    const { theme } = themeFromPage();
    const term = new Terminal({
      theme,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace',
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
      convertEol: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(host);
    termRef.current = term;
    const markCopied = () => {
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
      setCopiedOut(true);
      copyTimerRef.current = window.setTimeout(() => setCopiedOut(false), 1500);
    };
    const selectionSub = term.onSelectionChange(() => {
      if (!disposed) setHasSelection(term.hasSelection());
    });
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== 'keydown') return true;
      const key = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && key === 'c') {
        if (term.hasSelection()) {
          const selectedText = term.getSelection();
          e.preventDefault();
          void copyText(selectedText).then((ok) => {
            if (ok) markCopied();
            else onError(t('term.copyFailed'));
          });
          return false;
        }
        if (e.metaKey || e.shiftKey) return false;
      }
      if ((e.metaKey || (e.ctrlKey && e.shiftKey)) && key === 'v') return false;
      return true;
    });
    // Touching the screen selects and scrolls; it does not bring up a keyboard for xterm's field.
    if (touchRef.current) term.textarea?.setAttribute('inputmode', 'none');
    appCursorRef.current = () => term.modes.applicationCursorKeysMode;

    // Themes can change while the panel is open (light/dark switch, another palette).
    const observer = new MutationObserver(() => {
      term.options.theme = themeFromPage().theme;
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-palette'] });

    const phaseNow = (p: 'connecting' | 'running' | 'ended' | 'failed') => {
      if (!disposed) setPhase(p);
    };

    // Keystrokes wait briefly in a batch: a POST per key would hammer the connection.
    let pending = '';
    let flushTimer: number | null = null;
    const flush = (id: string) => {
      flushTimer = null;
      const data = pending;
      pending = '';
      if (data) {
        api.terminalInput(id, data).catch((e) => {
          if (/exited/.test(message(e))) phaseNow('ended');
        });
      }
    };

    void (async () => {
      try {
        // Attach to the newest live terminal; only when there is none does the directory matter.
        const alive = await api.listTerminals();
        const t = (terminalId ? alive.find((x) => x.id === terminalId) : undefined) ?? alive.at(-1) ?? (await api.createTerminal(cwd));
        if (disposed) return;
        setInfo(t);
        if (host.clientWidth === 0) await new Promise((r) => requestAnimationFrame(r));

        const applyFit = () => {
          try {
            fit.fit();
          } catch {
            return; // the container is hidden or has no size yet
          }
          api.terminalSize(t.id, term.cols, term.rows).catch(() => undefined);
        };
        const ro = new ResizeObserver(() => requestAnimationFrame(applyFit));
        ro.observe(host);
        applyFit();

        const send = (data: string) => {
          pending += data;
          if (flushTimer === null) flushTimer = window.setTimeout(() => flush(t.id), 16);
        };
        sendRef.current = send;
        // xterm answers queries it finds in the output (background colour, cursor position) through
        // the same channel as typing. Two kinds of answer reach nobody who asked, and the shell
        // would read them as typed: those to old output being replayed, and those from a page that
        // is not the one in use. Every page attached to the terminal (another tab, the phone)
        // answers; the program reads the first and the rest land at the prompt. A page without
        // the focus cannot be typed into, so whatever its xterm sends then is such an answer.
        let replaying = false;
        term.onData((data) => {
          if (replaying || !document.hasFocus()) return;
          send(data);
        });
        if (!touchRef.current) term.focus();
        phaseNow('running');

        // Reconnect until the shell exits or the panel closes. Every connect replays the backlog,
        // so the screen is repainted whole (term.reset) instead of continuing where it broke off.
        let exited = false;
        const onFrame = (f: TerminalFrame) => {
          if (f.type === 'backlog') {
            term.reset();
            replaying = true;
            term.write(bytes(f.data), () => void (replaying = false));
          } else if (f.type === 'out') {
            term.write(bytes(f.data));
          } else {
            exited = true;
            phaseNow('ended');
          }
        };
        while (!disposed && !exited) {
          try {
            await api.stream<TerminalFrame>(`/api/terminals/${t.id}/stream`, onFrame, abort.signal);
            if (disposed || exited) break;
          } catch (e) {
            if (abort.signal.aborted || disposed) break;
            if (/unknown terminal/.test(message(e))) {
              // Gone for good on the daemon: swept after its grace, or the daemon restarted.
              exited = true;
              phaseNow('ended');
              break;
            }
            if (e instanceof Error && e.name === 'AuthError') {
              onError(e);
              break;
            }
          }
          await sleep(800, abort.signal);
        }
        ro.disconnect();
      } catch (e) {
        if (!disposed) {
          setFailure(message(e));
          phaseNow('failed');
        }
      } finally {
        if (flushTimer !== null) window.clearTimeout(flushTimer);
      }
    })();

    return () => {
      disposed = true;
      termRef.current = null;
      sendRef.current = null;
      abort.abort();
      observer.disconnect();
      selectionSub.dispose();
      if (flushTimer !== null) window.clearTimeout(flushTimer);
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
      term.dispose();
    };
  }, [nonce, terminalId]);

  const restart = () => {
    setFailure('');
    setInfo(null);
    setPhase('connecting');
    setNonce((n) => n + 1);
  };

  // The directory button itself says the copy worked: a check where the path was, then the path back.
  const [copiedDir, setCopiedDir] = useState(false);
  const copyDir = () => {
    if (!info) return;
    void copyText(info.cwd).then((ok) => {
      if (!ok) return onError(t('term.copyFailed'));
      setCopiedDir(true);
      window.setTimeout(() => setCopiedDir(false), 1500);
    });
  };

  const copyOutput = () => {
    const term = termRef.current;
    if (!term) return;
    let text = term.getSelection();
    if (!text) {
      const buf = term.buffer.active;
      const lines: string[] = [];
      for (let i = 0; i < buf.length; i++) {
        const row = buf.getLine(i);
        if (!row) continue;
        const part = row.translateToString(true);
        if (row.isWrapped && lines.length) lines[lines.length - 1] += part;
        else lines.push(part);
      }
      while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
      text = lines.join('\n');
    }
    if (!text) return;
    void copyText(text).then((ok) => {
      if (!ok) return onError(t('term.copyFailed'));
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
      setCopiedOut(true);
      copyTimerRef.current = window.setTimeout(() => setCopiedOut(false), 1500);
    });
  };

  const panel = (
      <div
        className={`panel term-panel${docked ? ' docked' : ''}`}
        role={docked ? 'region' : 'dialog'}
        aria-modal={docked ? undefined : true}
        aria-label={t('term.title')}
      >
        <header className="panel-head">
          <div className="term-head-main">
            <h2 className="panel-title">
              <IconTerminal size={20} /> {t('term.title')}
            </h2>
            {info && (
              <button className="term-cwd" title={info.cwd} onClick={copyDir}>
                {copiedDir && <IconCheck size={14} />}
                {folderName(info.cwd)}
              </button>
            )}
          </div>
          <div className="term-head-actions">
            <button
              type="button"
              className="icon-btn"
              aria-label={t(copiedOut ? 'term.copied' : hasSelection ? 'term.copySelection' : 'term.copy')}
              title={t(copiedOut ? 'term.copied' : hasSelection ? 'term.copySelection' : 'term.copy')}
              onPointerDown={(e) => e.preventDefault()}
              onClick={copyOutput}
            >
              {copiedOut ? <IconCheck size={16} /> : <IconCopy size={16} />}
            </button>
            <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
              <IconClose size={18} />
            </button>
          </div>
        </header>
        <div className="term-body" ref={hostRef} />
        {touch && (
          <div className="term-input">
            <div className="term-keys" role="toolbar" aria-label={t('term.keys')}>
              {keys(false).map(([label, , icon], i) => (
                <button
                  key={label}
                  type="button"
                  aria-label={label}
                  disabled={phase !== 'running'}
                  // Keeps the focus (and the keyboard) in the field below.
                  onPointerDown={(e) => e.preventDefault()}
                  // Arrows depend on the mode the program running now has asked for.
                  onClick={() => sendRef.current?.(keys(appCursorRef.current())[i][1])}
                >
                  {icon ?? label}
                </button>
              ))}
            </div>
            <form
              className="term-line"
              onSubmit={(e) => {
                e.preventDefault();
                sendRef.current?.(line + '\r');
                setLine('');
              }}
            >
              <input
                value={line}
                onChange={(e) => setLine(e.target.value)}
                onKeyDown={(e) => {
                  // An empty field passes Backspace on, to rub out what is already at the prompt.
                  if (e.key === 'Backspace' && line === '' && !e.nativeEvent.isComposing) sendRef.current?.('\x7f');
                }}
                placeholder={t('term.placeholder')}
                aria-label={t('term.input')}
                disabled={phase !== 'running'}
                enterKeyHint="send"
                autoCapitalize="off"
                autoCorrect="off"
                autoComplete="off"
                spellCheck={false}
              />
              <button type="submit" className="term-send" aria-label={t('common.send')} disabled={phase !== 'running'}>
                <IconSend size={16} />
              </button>
            </form>
          </div>
        )}
        {phase !== 'running' && (
          <div className="term-note" role="status">
            {phase === 'connecting' && <p>{t('term.connecting')}</p>}
            {phase === 'ended' && (
              <>
                <p>{t('term.ended')}</p>
                <button onClick={restart}>{t('term.reopen')}</button>
              </>
            )}
            {phase === 'failed' && (
              <>
                <p>{failure || t('term.failed')}</p>
                <button onClick={restart}>{t('common.retry')}</button>
              </>
            )}
          </div>
        )}
      </div>
  );

  if (docked) return <DockPanel label={t('term.title')}>{panel}</DockPanel>;
  return <div className="dialog-scrim term-scrim">{panel}</div>;
}
