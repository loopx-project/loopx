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

## Original reply reader

The `reply-*` captures use the real bound-conversation SQLite/HTTP fixture and
packaged Chat. Only provider/model identity and conversation content are synthetic.
They are a separate scenario from the pagination captures above.

- `reply-default-desktop.png`: the original body and collapsed quote remain on the
  primary page; the model input envelope is absent from the user-facing body.
- `reply-expanded-narrow.png`: the exact quoted target and longer ordered thread
  excerpt remain readable at 390×844 without horizontal overflow.
- `reply-english-narrow.png`: the same source and collapsed reader in English.

Run `node apps/presentation/dashboard/smoke/conversation-reply-browser-smoke.mjs`
after building Chat. `LOOPX_CHAT_FIXTURE_BACKEND_ROOT` selects an exact baseline
checkout with its separately built assets. The same original-body oracle fails
on the pre-change baseline and passes here; `LOOPX_REPLY_BEFORE=1` captures the
old envelope for diagnosis. Source files, stored Turn/messages and correlation
receipts remain byte-identical during browsing, with zero model Turns. The
delegation-media smoke separately covers this shared reader in the original-request
drawer. None of these synthetic captures proves live provider delivery, new
bidirectional sending, complete threads/cards or installed native App adoption.
