import { useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import { SettingsSection } from './PageNavigation';
import {
  createLoginLink, fetchAuthKind, listLoginLinks, loginLinkUrl, revokeLoginLink,
  AuthKindError, type AuthKind, type IssuedLink,
} from '../link-auth';

/** What the daemon said about the login links, or why it said nothing. */
type LinksState = { links: IssuedLink[]; error: string | null; busy: boolean };

const day = (iso: string): string => {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString() : iso;
};

/**
 * Settings > Access: what this device logged in with, and the keys to this Workbench. A link token
 * arrives through a link and runs out; the permanent token is the one that does not. Filling it in
 * here replaces the link on this device at once. Links are made and revoked here too — the daemon
 * lets only the permanent token do either.
 */
export function AccessSettings({ token, auth, onReplaced }: {
  token: string;
  auth: AuthKind | null;
  /** Called with a token the daemon just accepted as the permanent one. */
  onReplaced: (token: string) => void;
}) {
  const [value, setValue] = useState('');
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [links, setLinks] = useState<LinksState>({ links: [], error: null, busy: false });
  const [issued, setIssued] = useState<{ url: string; record: IssuedLink } | null>(null);
  const [copied, setCopied] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const reload = () => {
    // Only the permanent token may read this list; asking with a link would be answered 403, and
    // that is not an error to show — the link section says why it stays empty instead.
    if (auth?.kind !== 'permanent') return;
    setLinks(state => ({ ...state, busy: true }));
    void listLoginLinks(token)
      .then(list => setLinks({ links: list, error: null, busy: false }))
      .catch(cause => setLinks({ links: [], error: cause instanceof Error ? cause.message : String(cause), busy: false }));
  };
  useEffect(reload, [token, auth?.kind]);

  const check = async () => {
    const candidate = value.trim();
    if (!candidate || checking) return;
    setChecking(true); setError(null); setOk(false);
    try {
      // The daemon's own word for what this token is; only the permanent one passes this door.
      const kind = await fetchAuthKind(candidate);
      if (kind.kind !== 'permanent') throw new Error(t('access.notPermanent'));
      onReplaced(candidate);
      setOk(true); setValue('');
      reload();
    } catch (cause) {
      setError(cause instanceof AuthKindError
        ? cause.reason === 'unauthorized' ? t('access.badToken') : t('access.unreachable')
        : cause instanceof Error ? cause.message : String(cause));
    } finally {
      setChecking(false);
      input.current?.focus();
    }
  };

  const make = async () => {
    if (links.busy) return;
    setLinks(state => ({ ...state, busy: true }));
    try {
      const made = await createLoginLink(token);
      setIssued({ url: loginLinkUrl(made.token), record: made.record });
      setCopied(false);
      reload();
    } catch (cause) {
      setLinks({ links: [], error: cause instanceof Error ? cause.message : String(cause), busy: false });
    }
  };

  const revoke = async (id: string) => {
    if (links.busy) return;
    setLinks(state => ({ ...state, busy: true }));
    try {
      await revokeLoginLink(token, id);
      reload();
    } catch (cause) {
      setLinks({ links: [], error: cause instanceof Error ? cause.message : String(cause), busy: false });
    }
  };

  const copy = async () => {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.url);
      setCopied(true);
    } catch {
      // Selection still works; the field below can be copied by hand.
    }
  };

  return <SettingsSection title={t('access.title')} hint={t('access.hint')} className="access-settings" busy={links.busy}>
    <div className="access-block">
      <h3>{t('access.current')}</h3>
      {auth === null ? <p className="access-quiet">{t('access.currentUnknown')}</p> : auth.kind === 'permanent'
        ? <p className="access-quiet">{t('access.currentPermanent')}</p>
        : <p className="access-quiet">{t('access.currentLink', day(auth.expiresAt))}</p>}
    </div>

    {auth?.kind === 'link' ? <div className="access-block">
      <h3>{t('access.fillPermanent')}</h3>
      <form className="access-token-form" onSubmit={event => { event.preventDefault(); void check(); }}>
        <input ref={input} type="password" autoComplete="current-password" placeholder={t('access.permanentPlaceholder')}
          value={value} aria-label={t('access.fillPermanent')} onChange={e => { setValue(e.target.value); setError(null); setOk(false); }} />
        <button type="submit" className="primary" disabled={!value.trim() || checking}>{t('access.replace')}</button>
      </form>
      {error ? <p role="alert" className="access-error">{error}</p> : null}
      {ok ? <p role="status" className="access-ok">{t('access.replaced')}</p> : null}
    </div> : null}

    <div className="access-block">
      <h3>{t('access.links')}</h3>
      {auth?.kind === 'link' ? <p className="access-quiet">{t('access.linksNeedPermanent')}</p> : <>
        <button type="button" className="ghost" onClick={() => void make()}>{t('access.newLink')}</button>
        {issued ? <div className="access-issued">
          <input readOnly value={issued.url} aria-label={t('access.linkAddress')} onFocus={event => event.currentTarget.select()} />
          <button type="button" onClick={() => void copy()}>{copied ? t('access.copied') : t('access.copy')}</button>
          <p className="access-quiet">{t('access.issuedHint', day(issued.record.expiresAt))}</p>
        </div> : null}
        {links.error ? <p role="alert" className="access-error">{links.error}</p> : null}
        {links.links.length ? <ul className="access-links">
          {links.links.map(link => <li key={link.id} className={link.revokedAt ? 'revoked' : link.expiresAt && Date.parse(link.expiresAt) <= Date.now() ? 'expired' : ''}>
            <span>{t('access.linkRow', day(link.createdAt), day(link.expiresAt))}</span>
            {link.lastUsedAt ? <span className="access-quiet">{t('access.lastUsed', day(link.lastUsedAt))}</span> : null}
            {link.revokedAt ? <span className="access-quiet">{t('access.revoked')}</span>
              : <button type="button" className="ghost danger" disabled={links.busy} onClick={() => void revoke(link.id)}>{t('access.revoke')}</button>}
          </li>)}
        </ul> : !links.error ? <p className="access-quiet">{t('access.noLinks')}</p> : null}
      </>}
    </div>

    <div className="access-block">
      <h3>{t('access.wherePermanent')}</h3>
      <p className="access-quiet">{t('access.wherePermanentHint')}</p>
    </div>
  </SettingsSection>;
}
