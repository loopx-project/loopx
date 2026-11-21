    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { App } from '/src/App.tsx';
    import { DemoApi } from '/src/demo/DemoApi.ts';
    import { BUILTIN_MODULES, BUILTIN_MANIFESTS } from '/src/extensions/catalog.ts';
    import '/src/styles.css';
    const options = window.testOptions;
    const api = new DemoApi();
    Object.defineProperty(api, 'demo', { value: Boolean(options.demo) });
    const manifest = structuredClone(BUILTIN_MANIFESTS[0]);
    const initial = { manifest, source: 'builtin', installed: true, enabled: true, granted: manifest.permissions, installedAt: null, updatedAt: null };
    let states = [initial];
    const pending = [];
    let defer = false, failThreads = false, reads = 0, notify, armed = false;
    const readThreads = api.threads.bind(api);
    api.threads = async () => { reads++; if (failThreads) throw new Error('snapshot unavailable'); return readThreads(); };
    api.extensions = () => defer ? new Promise(resolve => pending.push(resolve)) : Promise.resolve(structuredClone(states));
    api.setExtensionEnabled = async (id, enabled) => { states = [{ ...states[0], enabled }]; return structuredClone(states[0]); };
    api.stream = async (path, cb, signal, after, onOpen) => {
      notify = cb;
      if (!options.demo) onOpen?.();
      await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
    };
    if (options.badModule) BUILTIN_MODULES.board = async () => ({ default: { manifest } });
    else if (options.throwing) BUILTIN_MODULES.board = async () => ({ default: { manifest, views: { board: ({ host }) => {
      React.useEffect(() => host.threads.subscribe(() => { if (armed) throw new Error('broken view subscription'); }), [host]);
      return React.createElement('p', { id: 'probe-view' }, 'Probe view');
    } } } });
    window.probe = {
      get pending() { return pending.length; }, get reads() { return reads; },
      fail(value) { failThreads = value; },
      emit(type = 'thread.updated') { notify({ type }); },
      defer() { defer = true; },
      release(index, enabled) { pending[index]([{ ...initial, enabled }]); },
      async changeThreads() { armed = true; const original = readThreads; api.threads = async () => (await original()).slice(0, 1); notify({ type: 'thread.updated' }); },
    };
    const noop = () => null;
    createRoot(document.getElementById('root')).render(React.createElement(App, {
      token: '', onToken: noop,
      showcase: { api, theme: 'light', onInstall: noop, notice: noop, intro: noop, composer: noop },
    }));
