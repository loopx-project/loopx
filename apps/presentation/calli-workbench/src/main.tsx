import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { App, type AppStart } from './App';
import { loadHost, saveHost } from './hosts';
import { localeTag } from './i18n';
import './styles.css';

// The language was settled when the i18n module loaded; the page says so for fonts, screen readers and spell check.
document.documentElement.lang = localeTag();

/** Which machine the page shows. Each machine is a service of its own, so switching starts the app over. */
function Root() {
  const [at, setAt] = useState<{ hostId: string | null; start?: AppStart }>(() => ({ hostId: loadHost() }));
  return (
    <App
      key={at.hostId ?? ''}
      hostId={at.hostId}
      start={at.start}
      onHost={(hostId, start) => {
        saveHost(hostId);
        setAt({ hostId, start });
      }}
    />
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
