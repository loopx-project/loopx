# Conversation history source order

Synthetic local fixtures only; no private conversation, account or credentials.
The desktop captures use 1512 × 982; the narrow capture uses 390 × 844.

- [Before](before-desktop.png): opaque message-ID sorting puts the answer before its same-second question.
- [After](after-desktop.png): the Session append order keeps the question before its answer.
- [Narrow](after-narrow.png): the same source order remains readable at 390 px.

The existing conversation-history-recovery scenario also exercises failed older
reads, draft retention, current-session send blocking, projection rehydration,
retired observers and zero replay. The chat-route smoke checks complete long
bodies, repeated text, scoped identity and deterministic cross-session ties.
This qualifies source/display order and synthetic recovery, not complete
pagination, reply threading, native input or real App/Lark media delivery.
