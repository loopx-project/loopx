# Calli Workbench

The Calli web UI (React 18 / Vite 5), ported from the Calli repository
(`packages/web`) into LoopX as a separate presentation
surface, then stripped to the part M1 needs: the thread sidebar, the thread view,
the launch bar and the desk. No change to `loopx/`.

## Data layer: pending replacement

The app currently runs on the fixture-backed `DemoApi` (`src/demo/`), which
answers from made-up data and refuses every write. It reaches no service.

`src/api.ts` is still the Calli daemon client and its method signatures are the
interface the M1 adapter has to implement (`LoopxApi`, read-only). Until that
lands, `npm run dev` shows the demo workspace. `vite.config.ts` already proxies
`/status.json` (8766) and `/api/chat`, `/ssh-hosts` (8767) for the adapter,
with the same-origin `Origin` rewrite the official dashboard uses.

## Layout

- `src/`, `public/`, `test/`: Calli web, with the removals listed below.
- `src/daemon-shared/`: `icons.ts` and `todo-priority.ts`, copied from the Calli
  daemon (the web app imported them across packages).
- `src/attention.ts`: the `Attention` and `ProgressReport` types that the web app
  took from Calli's extension API package.
- `src/thread-feed.ts`: the thread list feed, moved out of Calli's extensions.

## Build

```sh
npm install
npm run build       # tsc --noEmit && vite build -> dist/
npm test            # unit tests (tsx --test)
npm run dev         # 127.0.0.1:5174
```

## Removed from upstream

Voice, writing mode, the pet, quota and cost boards, skills and MCP pages,
agent management and login, daemon settings, Calli's own todos/schedules/teams,
handover and the merged handover history, terminal/desktop/files/preview panels,
observability and activity pages, the extension system and its packages,
import/archive panels, side chat, selection notes, and the local caches and
persisted drafts. Their tests went with them. `styles.css` is untouched, so it
still carries the rules for those features.

## Other deviations from upstream

- Dev port 5174 / preview 4174 (upstream 5173), and the LoopX proxy above
  instead of the Calli daemon proxy.
- `App.tsx` and `main.tsx` no longer take a token or connect dialog; the
  workspace is the demo until the adapter lands.
- The Composer accepts pictures only; mentions, videos and files, notes and
  takeover are gone.
- The merged handover history is replaced by a plain `ThreadView`.

## Provenance & license

Derived from Calli (`packages/web`) and relicensed under Apache-2.0 with the consent of its contributors; see `NOTICE`.
