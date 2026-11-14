import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

// Entry point only; the workbench UI arrives in the next change.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main />
  </StrictMode>,
);
