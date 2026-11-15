import { useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import {
  applyManagerEvent, interruptManagerTurn, openManagerSession, readManagerHistory, sendManagerTurn, streamManagerEvents,
  ManagerError, type ManagerEvent, type ManagerMessage, type ManagerTurn,
} from '../loopx/manager';
import type { LoopxStatusFailure } from '../loopx/status';

/**
 * The steward conversation: the Python manager's own session, spoken to through the proxy.
 * The layout follows the thread view's shape — the person's words right, the steward's left,
 * activity steps as a quiet trail above the streaming answer — so it reads as the same product.
 * Reference taken from the thread view and the dashboard's personal workspace, then kept to the
 * minimum a manager conversation needs: no tool detail the core does not send.
 */
export function StewardConversation({ token, failure }: { token: string | undefined; failure: LoopxStatusFailure | null }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ManagerMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState<ManagerTurn | null>(null);
  const [draft, setDraft] = useState('');
  const sessionRef = useRef<string | null>(null);
  const turnRef = useRef<ManagerTurn | null>(null);
  const cursorRef = useRef<string>('');
  const streamAbort = useRef<AbortController | null>(null);
  const scrollAnchor = useRef<HTMLDivElement | null>(null);

  turnRef.current = turn;

  useEffect(() => {
    if (failure) return;
    let alive = true;
    const controller = new AbortController();
    setError(null);
    openManagerSession(token, controller.signal)
      .then(({ sessionId: id, messages: history }) => {
        if (!alive) return;
        sessionRef.current = id;
        setSessionId(id);
        setMessages(history);
      })
      .catch(cause => {
        if (!alive || controller.signal.aborted) return;
        setError(managerErrorText(cause));
      });
    return () => { alive = false; controller.abort(); };
    // The conversation opens once per token; a later core outage surfaces in the send path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({ block: 'end' });
  }, [messages?.length, turn?.steps.length, turn?.answer]);

  useEffect(() => () => streamAbort.current?.abort(), []);

  const finishTurn = (ended: ManagerTurn | null) => {
    if (!sessionRef.current || !ended) return;
    // The stored history is the truth; the live turn is only its preview. Read it back once.
    const id = sessionRef.current;
    readManagerHistory(token, id)
      .then(history => { setMessages(history); setTurn(null); })
      .catch(() => {
        // The turn ended but the history did not come back; keep what streamed rather than blanking.
        setTurn(ended.answer ? { ...ended, state: 'idle', steps: [] } : null);
      });
  };

  const runStream = async (id: string, turnId: string, eventsPath: string, after = '') => {
    cursorRef.current = after;
    for (let attempt = 0; ; attempt++) {
      const controller = new AbortController();
      streamAbort.current = controller;
      try {
        await streamManagerEvents(token, eventsPath, {
          signal: controller.signal,
          after: cursorRef.current || undefined,
          onEvent: (event: ManagerEvent) => {
            if (event.id) cursorRef.current = event.id;
            setTurn(current => (current ? applyManagerEvent(current, event) : current));
          },
        });
        // The stream closed: the turn ended (interrupted turns close without a terminal event).
        finishTurn(turnRef.current);
        return;
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (attempt >= 2 || cause instanceof ManagerError) {
          setTurn(current => (current ? { ...current, state: 'idle', error: managerErrorText(cause) } : current));
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 700 * (attempt + 1)));
      } finally {
        streamAbort.current = null;
      }
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || turn || !sessionId || failure) return;
    setError(null);
    const localId = `local-${Date.now()}`;
    setDraft('');
    setMessages(current => [...(current ?? []), { id: localId, role: 'user', text, createdAt: null, turnId: null }]);
    const clientTurnId = (crypto.randomUUID?.() ?? String(Date.now()));
    try {
      const { turnId, eventsPath } = await sendManagerTurn(token, sessionId, text, clientTurnId);
      setTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
      await runStream(sessionId, turnId, eventsPath);
    } catch (cause) {
      if (cause instanceof ManagerError && cause.status === 409 && typeof cause.body.active_turn_id === 'string') {
        // A turn is already running (this page or the dashboard opened it); show it instead of failing.
        const turnId = cause.body.active_turn_id;
        const eventsPath = `/api/loopx/api/chat/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/events`;
        setTurn({ turnId, eventsPath, state: 'streaming', steps: [], answer: '', error: null });
        await runStream(sessionId, turnId, eventsPath);
        return;
      }
      const message = managerErrorText(cause);
      setError(message);
      setMessages(current => (current ?? []).filter(m => m.id !== localId));
      setDraft(text);
    }
  };

  const stop = () => {
    const current = turnRef.current;
    if (!current || !sessionId || current.state !== 'streaming') return;
    setTurn({ ...current, state: 'stopping' });
    void interruptManagerTurn(token, sessionId, current.turnId).catch(() => {
      // The stream closing below ends the turn even when the interrupt request itself fails.
    });
  };

  if (failure) {
    return <section className="steward-conversation" aria-label={t('loopx.stewardConversation')}>
      <p role="alert" className="steward-hint">{failure.kind === 'not-connected' ? t('loopx.notConnected')
        : failure.detail === 'loopx_core_unavailable' ? t('loopx.coreDown') : t('loopx.unavailable', failure.detail)}</p>
    </section>;
  }
  if (error && !sessionId) {
    return <section className="steward-conversation" aria-label={t('loopx.stewardConversation')}>
      <p role="alert" className="steward-hint">{error}</p>
      <button type="button" className="steward-retry" onClick={() => {
        setError(null); setMessages(null);
        openManagerSession(token).then(({ sessionId: id, messages: history }) => { sessionRef.current = id; setSessionId(id); setMessages(history); }).catch(cause => setError(managerErrorText(cause)));
      }}>{t('common.retry')}</button>
    </section>;
  }

  return <section className="steward-conversation" aria-label={t('loopx.stewardConversation')}>
    <div className="steward-messages">
      {messages === null ? <p className="steward-hint">{t('common.loading')}</p>
        : messages.length === 0 ? <p className="steward-hint">{t('loopx.stewardHello')}</p>
        : messages.map(message => message.role === 'user'
          ? <div key={message.id} className="steward-row-msg user"><div className="bubble">{message.text}</div></div>
          : <div key={message.id} className="steward-row-msg agent"><div className="bubble">{message.text}</div></div>)}
      {turn ? <>
        {turn.steps.length ? <ul className="steward-steps" aria-label={t('loopx.stewardSteps')}>
          {turn.steps.map((step, index) => <li key={index}>{step}</li>)}
        </ul> : null}
        {turn.answer ? <div className="steward-row-msg agent streaming"><div className="bubble">{turn.answer}</div></div> : null}
        {turn.state === 'streaming' || turn.state === 'stopping'
          ? <div className="steward-turn-bar">
            {turn.state === 'stopping' ? <span className="steward-stopping">{t('loopx.stopping')}</span> : null}
            <button type="button" className="steward-stop" onClick={stop} disabled={turn.state === 'stopping'}>{t('composer.stop')}</button>
          </div>
          : null}
        {turn.error ? <p role="alert" className="steward-hint">{turn.error}</p> : null}
      </> : null}
      {error && sessionId ? <p role="alert" className="steward-hint">{error}</p> : null}
      <div ref={scrollAnchor} />
    </div>
    <form className="steward-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <textarea
        value={draft}
        rows={2}
        placeholder={t('loopx.stewardAsk')}
        disabled={Boolean(turn) || messages === null}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
        }}
      />
      <button type="submit" disabled={!draft.trim() || Boolean(turn) || messages === null}>{t('common.send')}</button>
    </form>
  </section>;
}

function managerErrorText(cause: unknown): string {
  if (cause instanceof ManagerError) {
    if (cause.status === 502 || cause.body.error === 'loopx_core_unavailable') return t('loopx.coreDown');
    if (cause.code === 'session_queue_full') return t('loopx.queueFull');
    return cause.message;
  }
  return cause instanceof Error ? cause.message : String(cause);
}
