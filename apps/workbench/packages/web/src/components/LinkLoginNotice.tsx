import { useEffect, useState } from 'react';
import { t } from '../i18n';
import { fetchAuthKind, AuthKindError, type AuthKind } from '../link-auth';

/** What the daemon says this credential is: permanent, or a link with its expiry. A dead link is
 * an error (expired or revoked), so the app can say which — and fall back to the login page. */
export function useAuthKind(token: string | undefined) {
  const [read, setRead] = useState<{ auth: AuthKind | null; error: AuthKindError | null; loading: boolean }>(
    { auth: null, error: null, loading: Boolean(token) },
  );
  useEffect(() => {
    if (!token) { setRead({ auth: null, error: null, loading: false }); return; }
    let active = true;
    const controller = new AbortController();
    fetchAuthKind(token, controller.signal)
      .then(auth => { if (active) setRead({ auth, error: null, loading: false }); })
      .catch(cause => {
        if (!active || cause instanceof DOMException && cause.name === 'AbortError') return;
        setRead({ auth: null, error: cause instanceof AuthKindError ? cause : null, loading: false });
      });
    return () => { active = false; controller.abort(); };
  }, [token]);
  return read;
}

function expiryDate(expiresAt: string): string {
  const at = Date.parse(expiresAt);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString() : expiresAt;
}

/**
 * The standing word at the sidebar's foot while the login came through a link: when it runs out,
 * and where the permanent token goes. A day or less turns it warning-coloured — still quiet, just
 * no longer possible to miss. Clicking opens Settings > Access.
 */
export function LinkLoginNotice({ auth, onOpenAccess }: { auth: AuthKind; onOpenAccess: () => void }) {
  if (auth.kind !== 'link') return null;
  const msLeft = Date.parse(auth.expiresAt) - Date.now();
  const soon = Number.isFinite(msLeft) && msLeft <= 24 * 60 * 60 * 1000;
  return <button type="button" className={`link-login-notice${soon ? ' soon' : ''}`} onClick={onOpenAccess}>
    <span className="link-login-notice-text">
      {soon ? t('auth.linkNoticeSoon', expiryDate(auth.expiresAt)) : t('auth.linkNotice', expiryDate(auth.expiresAt))}
    </span>
  </button>;
}
