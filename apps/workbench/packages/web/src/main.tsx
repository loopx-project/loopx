import './storage-migration';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { App, type AppStart } from './App';
import { loadToken, saveToken } from './api';
import { loadHost, saveHost } from './hosts';
import { localeTag } from './i18n';
import './styles.css';

// The language was settled when the i18n module loaded; the page says so for fonts, screen readers and spell check.
document.documentElement.lang = localeTag();

/**
 * Which machine the page shows, and whether it is connected at all. Each machine is a hub of its
 * own, and the demo is a workspace of its own, so switching either starts the app over.
 */
function Root() {
  const [at, setAt] = useState<{ hostId: string | null; start?: AppStart }>(() => ({ hostId: loadHost() }));
  const [token, setToken] = useState(loadToken);
  // Set when the app starts over on the demo with the connect dialog open, and why.
  const [connect, setConnect] = useState<{ reason?: string } | undefined>();
  // Without a token there is only the demo, which is no machine.
  const hostId = token ? at.hostId : null;
  return (
    <App
      key={`${token ? 'hub' : 'demo'}:${hostId ?? ''}`}
      token={token}
      hostId={hostId}
      start={at.start}
      connect={connect}
      onHost={(hostId, start) => {
        saveHost(hostId);
        setAt({ hostId, start });
      }}
      onToken={(next, reason) => {
        saveToken(next);
        setToken(next);
        setConnect(!next && reason ? { reason } : undefined);
        if (next) setAt({ hostId: loadHost() });
      }}
    />
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
