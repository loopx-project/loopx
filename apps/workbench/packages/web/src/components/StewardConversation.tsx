import React, { useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MARKDOWN_REHYPE_PLUGINS, preserveNonHtmlTags } from '../markdown-html';
import brandIconLight from '../assets/loo-icon-paper.svg';
import brandIconDark from '../assets/loo-icon-dark.svg';
import { IconSend, IconStop } from './icons';
import {
  applyManagerEvent, interruptManagerTurn, openManagerSession, readManagerCapabilities, readManagerHistory,
  restoreTrustedOwnerProfile, sendManagerTurn, streamManagerEvents, switchManagerAgent,
  ManagerError, TRUSTED_OWNER_ENDPOINTS, type ManagerCapabilities, type ManagerEvent, type ManagerMessage, type ManagerTurn,
} from '../loopx/manager';
import type { LoopxStatusFailure } from '../loopx/status';

const STEWARD_REMARK_PLUGINS = [remarkGfm, preserveNonHtmlTags];

/** The questions the greeting offers: one tap starts the steward on them. */
const GREETING_ASKS = () => [
  t('loopx.stewardAsk.status'),
  t('loopx.stewardAsk.risks'),
  t('loopx.stewardAsk.push'),
];

/**
 * The steward conversation: the Python manager's own session, spoken to through the proxy. The page
 * is the chat, drawn with the thread's own parts — the person's words as user rows, the steward's
 * answers as assistant rows in the thread's markdown, the composer as the app's composer shell with
 * its send and stop buttons. An empty history opens on a centered question with one-tap asks.
 */
export function StewardConversation({ token, failure, deferSessionOpen = false }: { token: string | undefined; failure: LoopxStatusFailure | null; deferSessionOpen?: boolean }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ManagerMessage[] | null>(() => deferSessionOpen ? [] : null);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<ManagerCapabilities | null>(null);
  const [switchingAgent, setSwitchingAgent] = useState<string | null>(null);
  const [turn, setTurn] = useState<ManagerTurn | null>(null);
  const [draft, setDraft] = useState('');
  const [sessionOpening, setSessionOpening] = useState(false);
  const openingSession = useRef<AbortController | null>(null);
  useEffect(() => {
    setSessionOpening(false);
    return () => { openingSession.current?.abort(); openingSession.current = null; };
  }, [token]);
  const sessionRef = useRef<string | null>(null);
  const selectedAgentRef = useRef<string | undefined>(undefined);
  const turnRef = useRef<ManagerTurn | null>(null);
  const attemptRef = useRef(0);
  const cursorRef = useRef<string>('');
  const sendAbort = useRef<AbortController | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const scrollAnchor = useRef<HTMLDivElement | null>(null);

  turnRef.current = turn;
  /** Sets the live turn and its ref together, so code that runs before the next render sees it. */
  const showTurn = (next: ManagerTurn | null) => { turnRef.current = next; setTurn(next); };
  const busy = sessionOpening || turn?.state === 'sending' || turn?.state === 'streaming' || turn?.state === 'stopping';
  const stoppable = turn?.state === 'streaming' || turn?.state === 'stopping';

  const recordOpenFailure = (cause: unknown, signal?: AbortSignal) => {
    setError(managerErrorText(cause, null));
    const failedAgent = (cause instanceof ManagerError && (cause.code === 'agent_endpoint_unavailable' || cause.code === 'manager_runtime_endpoint_unsupported'))
      ? parseUnavailableEndpoint(cause)?.agentId ?? null
      : null;
    void readManagerCapabilities(token, failedAgent, signal).then(caps => {
      if (signal?.aborted || !caps) return;
      setCapabilities(caps);
      setError(managerErrorText(cause, caps));
    }).catch(() => undefined);
  };

  const openedFor = useRef<string | undefined | null>(null);
  const sessionOwner = useRef(token);
  const coreDown = failure !== null;
  useEffect(() => {
    // Opened once per token. A core that was down when the page opened is retried once it is back;
    // a later status refresh does not open another session over the one already open.
    if (sessionOwner.current !== token) {
      sessionOwner.current = token;
      attemptRef.current++;
      sendAbort.current?.abort();
      streamAbort.current?.abort();
      sessionRef.current = null;
      openedFor.current = null;
      setSessionId(null);
      selectedAgentRef.current = undefined;
      setDraft(''); setError(null); setCapabilities(null);
      setMessages(deferSessionOpen ? [] : null);
      showTurn(null);
    }
    if (deferSessionOpen || openingSession.current || coreDown || (sessionRef.current && openedFor.current === token)) return;
    let alive = true;
    const controller = new AbortController();
    setError(null);
    setCapabilities(null);
    if (!sessionRef.current) setMessages(null);
    openManagerSession(token, controller.signal, selectedAgentRef.current)
      .then(({ sessionId: id, messages: history }) => {
        if (!alive) return;
        sessionRef.current = id;
        openedFor.current = token;
        setSessionId(id);
        setMessages(history);
      })
      .catch(cause => {
        if (!alive || controller.signal.aborted) return;
        recordOpenFailure(cause, controller.signal);
      });
    return () => { alive = false; controller.abort(); };
    // A core outage after the session opened surfaces in the send path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, coreDown, deferSessionOpen]);

  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({ block: 'end' });
  }, [messages?.length, turn?.steps.length, turn?.answer]);

  useEffect(() => () => {
    attemptRef.current++;
    sendAbort.current?.abort();
    streamAbort.current?.abort();
  }, []);

  const finishTurn = (ended: ManagerTurn | null, id: string, attempt: number) => {
    if (!ended || attemptRef.current !== attempt || sessionRef.current !== id) return;
    // The stored history is the truth; the live turn is only its preview. Read it back once.
    const stillCurrent = () => attemptRef.current === attempt && sessionRef.current === id && turnRef.current?.turnId === ended.turnId;
    readManagerHistory(token, id)
      .then(history => { if (stillCurrent()) { setMessages(history); showTurn(null); } })
      .catch(() => {
        // The turn ended but the history did not come back; keep what streamed rather than blanking.
        if (!stillCurrent()) return;
        showTurn(ended.answer ? { ...ended, state: 'idle', steps: [] } : null);
      });
  };

  const runStream = async (id: string, turnId: string, eventsPath: string, attempt: number, after = '') => {
    const ownsTurn = () => attemptRef.current === attempt && sessionRef.current === id && turnRef.current?.turnId === turnId;
    if (!ownsTurn()) return;
    cursorRef.current = after;
    for (let retry = 0; ; retry++) {
      if (!ownsTurn()) return;
      const controller = new AbortController();
      streamAbort.current = controller;
      try {
        await streamManagerEvents(token, eventsPath, {
          signal: controller.signal,
          after: cursorRef.current || undefined,
          onEvent: (event: ManagerEvent) => {
            if (!ownsTurn()) return;
            if (event.id) cursorRef.current = event.id;
            // Folded here, not in a state updater (React may defer those to the next render): when the
            // stream closes, finishTurn reads the ref before any render.
            showTurn(applyManagerEvent(turnRef.current!, event));
          },
        });
        if (!ownsTurn()) return;
        // The stream closed: the turn ended (interrupted turns close without a terminal event).
        finishTurn(turnRef.current, id, attempt);
        return;
      } catch (cause) {
        if (controller.signal.aborted || !ownsTurn()) return;
        if (retry >= 2 || cause instanceof ManagerError) {
          showTurn({ ...turnRef.current!, state: 'idle', error: managerErrorText(cause) });
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 700 * (retry + 1)));
      } finally {
        if (streamAbort.current === controller) streamAbort.current = null;
      }
    }
  };

  const sendInSession = async (text: string) => {
    const body = text.trim();
    const active = turnRef.current;
    const activeBusy = active !== null && active.state !== 'idle';
    const id = sessionRef.current ?? sessionId;
    // Only a turn still sending or streaming holds the conversation. One that ended without its history
    // coming back (a dropped stream, a failed read) stays on screen but does not block the next message.
    if (!body || activeBusy || !id || failure) return;
    const attempt = ++attemptRef.current;
    sendAbort.current?.abort();
    streamAbort.current?.abort();
    const ownsAttempt = () => attemptRef.current === attempt && (sessionRef.current ?? sessionId) === id;
    if (active) {
      if (active.answer) setMessages(current => [...(current ?? []), { id: `turn-${active.turnId}`, role: 'agent', text: active.answer, createdAt: null, turnId: active.turnId }]);
    }
    setError(null);
    const localId = `local-${Date.now()}-${attempt}`;
    const clientTurnId = (crypto.randomUUID?.() ?? `${Date.now()}-${attempt}`);
    showTurn({ turnId: clientTurnId, eventsPath: '', state: 'sending', steps: [], answer: '', error: null });
    setDraft('');
    setMessages(current => [...(current ?? []), { id: localId, role: 'user', text: body, createdAt: null, turnId: null }]);
    const postController = new AbortController();
    sendAbort.current = postController;
    try {
      const { turnId, eventsPath } = await sendManagerTurn(token, id, body, clientTurnId, postController.signal);
      if (!ownsAttempt()) return;
      showTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
      await runStream(id, turnId, eventsPath, attempt);
    } catch (cause) {
      if (postController.signal.aborted || !ownsAttempt()) return;
      if (cause instanceof ManagerError && cause.status === 409 && typeof cause.body.active_turn_id === 'string') {
        // A turn is already running (this page or the dashboard opened it); show it instead of failing.
        // This message was not sent: it goes back to the draft for after that turn.
        setMessages(current => (current ?? []).filter(m => m.id !== localId));
        setDraft(body);
        const turnId = cause.body.active_turn_id;
        const eventsPath = `/api/loopx/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/events`;
        showTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
        await runStream(id, turnId, eventsPath, attempt);
        return;
      }
      showTurn(null);
      const message = managerErrorText(cause);
      setError(message);
      setMessages(current => (current ?? []).filter(m => m.id !== localId));
      setDraft(body);
    } finally {
      if (sendAbort.current === postController) sendAbort.current = null;
    }
  };

  /** Goal-reader entry does not create a chat session until the person actually sends. */
  const send = async (text: string) => {
    const active = turnRef.current;
    if (!text.trim() || failure || openingSession.current || (active && active.state !== 'idle')) return;
    if (sessionRef.current ?? sessionId) { await sendInSession(text); return; }
    if (!deferSessionOpen) return;
    const controller = new AbortController();
    openingSession.current = controller;
    setSessionOpening(true);
    setError(null);
    try {
      const opened = await openManagerSession(token, controller.signal, selectedAgentRef.current);
      if (controller.signal.aborted) return;
      sessionRef.current = opened.sessionId;
      openedFor.current = token;
      setSessionId(opened.sessionId);
      setMessages(opened.messages);
      await sendInSession(text);
    } catch (cause) {
      if (!controller.signal.aborted) recordOpenFailure(cause, controller.signal);
    } finally {
      if (openingSession.current === controller) { openingSession.current = null; setSessionOpening(false); }
    }
  };

  const stop = () => {
    const current = turnRef.current;
    if (!current || !sessionId || current.state !== 'streaming') return;
    showTurn({ ...current, state: 'stopping' });
    void interruptManagerTurn(token, sessionId, current.turnId).catch(() => {
      // The stream closing below ends the turn even when the interrupt request itself fails.
    });
  };

  if (failure) {
    return <div className="steward-chat-failure">
      <p role="alert">{failure.kind === 'not-connected' ? t('loopx.notConnected')
        : failure.detail === 'loopx_core_unavailable' ? t('loopx.coreDown') : t('loopx.unavailable', failure.detail)}</p>
    </div>;
  }
  if (error && !sessionId) {
    const alternatives = capabilities?.availableAlternatives ?? [];
    const isRestricted = capabilities?.runtimeProfile === 'restricted';
    return <div className="steward-chat-failure">
      <p role="alert">{error}</p>
      {isRestricted && (
        <p className="steward-chat-failure-notice">
          {t('loopx.stewardCurrentProfile', 'restricted')}
        </p>
      )}
      <div className="steward-chat-failure-actions">
        <button type="button" disabled={switchingAgent !== null} onClick={() => {
          setError(null); setMessages(null); setCapabilities(null);
          openManagerSession(token, undefined, selectedAgentRef.current)
            .then(({ sessionId: id, messages: history }) => { sessionRef.current = id; openedFor.current = token; setSessionId(id); setMessages(history); })
            .catch(cause => recordOpenFailure(cause));
        }}>{t('common.retry')}</button>
        {isRestricted && (
          <button type="button" disabled={switchingAgent !== null} onClick={() => {
            setSwitchingAgent('restore-profile');
            restoreTrustedOwnerProfile(token)
              .then(() => readManagerCapabilities(token, null))
              .then(caps => { if (caps) setCapabilities(caps); })
              .catch(cause => recordOpenFailure(cause))
              .finally(() => setSwitchingAgent(null));
          }}>{t('loopx.stewardRestoreTrustedOwner')}</button>
        )}
        {alternatives.map(alt => {
          const willRestrict = !TRUSTED_OWNER_ENDPOINTS.has(alt.agentId) && capabilities?.runtimeProfile === 'trusted_owner';
          return (
            <button key={alt.agentId} type="button" disabled={switchingAgent !== null} onClick={() => {
              setSwitchingAgent(alt.agentId); setError(null); setMessages(null);
              switchManagerAgent(token, alt.agentId, capabilities)
                .then(({ sessionId: id, messages: history }) => {
                  selectedAgentRef.current = alt.agentId;
                  sessionRef.current = id;
                  openedFor.current = token;
                  setCapabilities(null);
                  setSessionId(id);
                  setMessages(history);
                })
                .catch(cause => recordOpenFailure(cause))
                .finally(() => setSwitchingAgent(null));
            }}>
              {willRestrict ? t('loopx.stewardSwitchAgentRestricted', alt.displayName) : t('loopx.stewardSwitchAgent', alt.displayName)}
            </button>
          );
        })}
      </div>
    </div>;
  }

  const empty = messages !== null && messages.length === 0 && !turn;
  return <div className="steward-chat">
    {empty ? <div className="steward-greeting">
      <span className="steward-greeting-mark" aria-hidden="true">
        <img className="loo-light" src={brandIconLight} alt="" width="44" height="44" />
        <img className="loo-dark" src={brandIconDark} alt="" width="44" height="44" />
      </span>
      <h2>{t('loopx.stewardHelloTitle')}</h2>
      <div className="steward-greeting-asks" aria-label={t('loopx.stewardAskLabel')}>
        {GREETING_ASKS().map(ask => (
          <button key={ask} type="button" className="steward-ask" disabled={busy} onClick={() => void send(ask)}>{ask}</button>
        ))}
      </div>
    </div> : null}
    <div className="steward-messages" hidden={empty}>
      {messages === null ? <p className="steward-hint">{t('common.loading')}</p>
        : messages.map(message => message.role === 'user'
          ? <div key={message.id} className="row user"><div className="bubble">{message.text}</div></div>
          : <div key={message.id} className="row assistant"><div className="markdown"><Markdown remarkPlugins={STEWARD_REMARK_PLUGINS} rehypePlugins={MARKDOWN_REHYPE_PLUGINS}>{message.text}</Markdown></div></div>)}
      {turn ? <>
        {turn.steps.length ? <ul className="steward-steps" aria-label={t('loopx.stewardSteps')}>
          {turn.steps.map((step, index) => <li key={index}>{step}</li>)}
        </ul> : null}
        {turn.answer ? <div className={`row assistant${turn.state === 'streaming' ? ' streaming' : ''}`}>
          <div className="markdown"><Markdown remarkPlugins={STEWARD_REMARK_PLUGINS} rehypePlugins={MARKDOWN_REHYPE_PLUGINS}>{turn.answer}</Markdown></div>
        </div> : null}
        {turn.error ? <div className="row channel-note"><span role="alert">{turn.error}</span></div> : null}
      </> : null}
      {error && sessionId ? <div className="row channel-note"><span role="alert">{error}</span></div> : null}
      <div ref={scrollAnchor} />
    </div>
    {/* The app's composer shell, carrying only what a manager conversation has: words, send, stop. */}
    <form className="composer steward-composer" onSubmit={event => { event.preventDefault(); void send(draft); }}>
      <div className="composer-anchor">
        <div className="composer-box">
          <textarea
            rows={1}
            value={draft}
            placeholder={t('loopx.stewardAskInput')}
            disabled={busy || messages === null}
            aria-label={t('composer.message')}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(draft); }
            }}
          />
          <div className="composer-bar">
            <div className="spacer" />
            {stoppable ? <button type="button" className="send-btn stop" onClick={stop} disabled={turn?.state === 'stopping'} aria-label={t('composer.stop')} title={t('composer.stopTurn')}><IconStop size={16} /></button> : null}
            {!stoppable ? <button type="submit" className="send-btn" disabled={busy || !draft.trim() || messages === null} aria-label={t('common.send')}><IconSend size={16} /></button> : null}
          </div>
        </div>
      </div>
    </form>
  </div>;
}

function parseUnavailableEndpoint(cause: ManagerError): { agentId: string; reason: string } | null {
  const match = /^The Agent endpoint '([^']+)' cannot serve this request:\s*([a-z0-9_]+)\.?$/i.exec(cause.message.trim());
  return match ? { agentId: match[1], reason: match[2] } : null;
}

function defaultAgentLabel(agentId: string): string {
  if (agentId === 'codex') return 'Codex';
  if (agentId === 'claude-code') return 'Claude Code';
  if (agentId === 'kiro-cli') return 'Kiro CLI';
  return agentId;
}

function managerErrorText(cause: unknown, capabilities: ManagerCapabilities | null = null): string {
  if (cause instanceof ManagerError) {
    if (cause.status === 502 || cause.body.error === 'loopx_core_unavailable') return t('loopx.coreDown');
    if (cause.code === 'session_queue_full') return t('loopx.queueFull');
    if (cause.code === 'agent_endpoint_unavailable') {
      const parsed = parseUnavailableEndpoint(cause);
      if (parsed && parsed.reason === 'executable_not_found') {
        const adapter = capabilities?.adapters.find(a => a.agentId === parsed.agentId);
        const label = adapter?.displayName ?? defaultAgentLabel(parsed.agentId);
        const cmd = adapter?.executable ?? parsed.agentId;
        return t('loopx.stewardExecutableNotFound', label, cmd);
      }
    }
    return cause.message;
  }
  return cause instanceof Error ? cause.message : String(cause);
}
