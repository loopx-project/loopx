# Calli Workbench

The Calli web UI (React 18 / Vite 5), ported from the Calli repository
(`packages/web`) into LoopX as a separate presentation
surface, then stripped to the part M1 needs: the thread sidebar, the thread view,
the launch bar and the desk. It is milestone M1 of the Calli workbench and
session fabric RFC. No change to `loopx/`.

## Data layer

`App.tsx` builds a `LoopxApi` (`src/loopx/LoopxApi.ts`), a read-only subclass of
`Api`. Reads come from the LoopX adapter (`src/loopx/`): zod contracts for
`/status.json` and `/api/chat`, mappers to the app's own types, a schema-version
gate, and an SSE-to-polling stream. `Api.send()` is closed, so every write
rejects with `ReadOnlyError`; `Api.readOnly` is what the UI reads to disable
composing and write buttons. The base `Api` has no transport of its own, and
`fetch` appears only under `src/loopx/`.

`vite.config.ts` proxies `/status.json` (8766) and `/api/chat`, `/ssh-hosts`
(8767), with the same-origin `Origin` rewrite the official dashboard uses.

`?fixture=1` answers from `src/loopx/fixtures/status.demo.json` (a public-safe
copy of the status example plus synthetic sessions) and makes no network
request. An unsupported or malformed contract shows "update the local LoopX
service" with a retry button instead of a blank page.

`src/demo/` (`DemoApi`) is no longer used by the app; only `test/demo.test.ts`
reaches it.

## Checks

```sh
npm run smoke:projection      # fixture (zero fetch) and, if 8766 is up, live counts
npm run check:no-second-state # src/ keeps only display-preference storage, no fetch outside src/loopx/
```

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
  workspace is `LoopxApi`.
- The Composer accepts pictures only; mentions, videos and files, notes and
  takeover are gone.
- The merged handover history is replaced by a plain `ThreadView`.

## Provenance & license

Derived from Calli (`packages/web`) and relicensed under Apache-2.0 with the consent of its contributors; see `NOTICE`.
