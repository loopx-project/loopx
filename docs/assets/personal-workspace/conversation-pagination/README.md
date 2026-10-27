# Original history pagination: failure and recovery

These are packaged browser captures of the production Chat HTTP/store with a
disposable synthetic runtime. The workspace navigation is synthetic; no personal
conversation, credentials, live Goal, provider or model is used.

- `before-desktop.png` (1512×982): the second page is deliberately unavailable.
  The current report remains visible, the incomplete-history notice offers retry,
  and the draft stays in the composer. This is a fault state, not a baseline-version screenshot.
- `after-desktop.png` (1512×982): the same owner recovered all 451 original rows
  through offsets 0, 200 and 400; the viewport shows the tail and retained draft.
- `after-narrow.png` (390×844): the recovered tail and draft remain usable without
  horizontal overflow. The original long body is checked in the DOM, not inferred
  from this tail screenshot.

Reproduce after `npm run build:chat` in `apps/presentation/dashboard`:

```sh
node apps/presentation/dashboard/smoke/conversation-return-browser-smoke.mjs --pagination-only
```

The scoped scenario checks unchanged stored bytes and zero Turns after observation.
The default full return scenario also checks later results/revisions and quiet reads;
its background-read wait times out on both the unchanged baseline production code
(`2de490ed6c632e0d641453fd2f10ed3dde69fc48`) and this branch. That retained gap
is not qualified by the pagination-only result. Cross-page original
commitment/return metadata is checked independently against the real projection
in `tests/test_chat_conversation_bindings.py`. Source and packaged recovery
scenarios retain full text and drafts without replay.

All pixels and PNG chunk metadata were inspected; no textual metadata is present.
These captures belong to the code in this PR. The fixture's unrelated delegation
read is unavailable; these images do not qualify delegation completion, live Lark
transport, actual native App input, reply threading or installed media delivery.
