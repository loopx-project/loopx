import { useEffect, useRef, useState } from 'react';
import { Api, ApiError, AuthError } from '../api';
import { DialogSurface } from './Dialog';
import { t } from '../i18n';

interface Props {
  /** Why it opened, under the title: what was being done, or why the old token stopped working. */
  reason?: string;
  /** Called with a token the hub has just accepted. */
  onConnected: (token: string) => void;
  onCancel: () => void;
}

/** Asks for the hub's token and tries it before keeping it, so a wrong one is said here and not later. */
export function ConnectDialog({ reason, onConnected, onCancel }: Props) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);

  const connect = async () => {
    const token = value.trim();
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      await new Api(token).agents();
      onConnected(token);
    } catch (e) {
      setError(e instanceof AuthError ? t('connect.badToken') : e instanceof ApiError ? e.message : t('connect.unreachable'));
      setBusy(false);
      input.current?.focus();
    }
  };

  return (
    <DialogSurface labelledBy="connect-title" onClose={onCancel} busy={busy} className="connect-dialog">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void connect();
        }}
      >
        <h2 id="connect-title" className="dialog-title">
          {t('connect.title')}
        </h2>
        {reason && <p className="connect-reason">{reason}</p>}
        <p className="dialog-message">{t('connect.message')}</p>
        <label className="dialog-field connect-field">
          <span className="sr-only">{t('connect.token')}</span>
          <input
            ref={input}
            type="password"
            autoComplete="current-password"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError(null);
            }}
            placeholder="CALLI_TOKEN"
            aria-label={t('connect.token')}
            aria-invalid={error ? true : undefined}
            disabled={busy}
          />
        </label>
        {error ? (
          <p className="field-hint bad" role="alert">
            {error}
          </p>
        ) : (
          <p className="field-hint">{t('connect.tokenHint')}</p>
        )}
        <div className="dialog-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="primary" disabled={busy || !value.trim()}>
            {busy ? <span className="connect-spinner" aria-label={t('connect.connecting')} /> : t('connect.connect')}
          </button>
        </div>
      </form>
    </DialogSurface>
  );
}

/** The line that says the page shows made-up data, with the way to connect. */
export function DemoBar({ compact, onConnect }: { compact?: boolean; onConnect: () => void }) {
  return (
    <div className="demo-bar" role="note">
      <span className="demo-bar-text">
        <strong>{t('connect.demo')}</strong>
        {!compact && <span className="demo-bar-more">{t('connect.demoMore')}</span>}
      </span>
      <button type="button" className="primary demo-bar-btn" onClick={onConnect}>
        {t('connect.connectHub')}
      </button>
    </div>
  );
}
