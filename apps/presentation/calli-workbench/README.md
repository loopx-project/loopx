# Calli Workbench

A separate presentation app for working with several agent sessions at once:
machines on top, sessions on the left, the selected session in the middle, and
what needs the user's attention in view. It consumes LoopX's public projections
and owns no control-plane state.

This directory currently holds only the toolchain. The UI and the LoopX data
source follow in their own changes.

## Run

```sh
cd apps/presentation/calli-workbench
npm ci
npm run build       # tsc --noEmit && vite build
npm run dev         # http://127.0.0.1:5174
```

The dev server proxies the local LoopX services with the same rules as the
official dashboard (`apps/presentation/dashboard/vite.config.ts`), including
the same-origin `Origin` rewrite:

| Path | Target |
| --- | --- |
| `/status.json` | `127.0.0.1:8766` |
| `/api/chat`, `/ssh-hosts` | `127.0.0.1:8767` |

The workbench only reads through them.

## Relationship to the dashboard

Both apps read the same projections and neither writes LoopX state directly.
They share no code; each has its own `package.json` and lockfile.

## License

Apache-2.0.
