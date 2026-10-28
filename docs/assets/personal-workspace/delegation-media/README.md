# Original conversation reading / 原对话阅读

These are public synthetic screenshots of the packaged Chat reader backed by the
production SQLite, Inbox and HTTP owners. The refresh examples show the same
open original after a native Inbox return, at desktop and narrow widths. They
do not certify external delivery or model execution.

这些截图来自公开合成数据与真实 SQLite、Inbox、HTTP 读取链路。刷新示例展示
原生 Inbox 返回后仍展开的同一原对话，分别核验桌面和窄屏；不代表外部投递或
模型执行验收。

- `refresh-before.png`: the previous reader collapses the original on refresh.
- `refresh-return-desktop.png`, `refresh-return-narrow.png`: the open reader
  retains the native return, full body and loaded pages after refresh.
- The other images cover the original full-body/image reading and failure UI.

Run the targeted author/PR check after building the Chat bundle:

```sh
npm --prefix apps/presentation/dashboard run build:chat
LOOPX_PLAYWRIGHT_PACKAGE="$PWD/apps/presentation/dashboard/node_modules/playwright" \
  node apps/presentation/dashboard/smoke/delegation-media-browser-smoke.mjs
```

The smoke owns image reading, same-source refresh and reader cancellation. It
extends the existing fixture rather than adding a second consumer or test server.
It checks atomic multi-page refresh, focus and scroll, native return visibility,
foreign-session/read failures, snapshot drift, retry and late close/query changes.
Browser requests remain GET-only; only the disposable fixture explicitly drains
its synthetic queued return. `LOOPX_MEDIA_ASSETS` selects a prebuilt bundle for the
same semantic oracle against an earlier version.
