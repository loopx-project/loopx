# Returned file readback / 回传文件读回

An original bound conversation can already have a delivered result with file
snapshots while its saved Chat message has no attachments. Reading that message
must expose those published files without rerunning the recipient or sending
the result again. Both the conversation timeline and full-answer reader use the
same original-message download route.

原会话的结果可能已经携带文件送达，但保存的 Chat 消息没有附件。读取这条消息时，
应能下载已经发布的原文件，无需重新执行接收者或再次发送结果。对话页与完整答复页
复用同一个原消息下载入口。

## Source and byte checks / 来源与字节校验

- History joins the original Session, Turn and return route, then reuses
  `inspect_result_identity` and the TypeScript result-publication owner. The
  projection changes neither the message journal nor publication receipts.
- A download repeats those checks and uses `read_result_file` to verify the
  bounded, no-follow immutable snapshot's size and SHA-256. A mutable workspace
  file is never the download source. An unavailable snapshot returns HTTP 503;
  a mismatched original message or source returns 404.
- File identity includes its position within the published result, preserving
  distinct names even when two files contain identical bytes. The response is
  an attachment with an encoded original filename and `nosniff`.
- Returned file URLs are output only. The existing input MIME and data-URL
  admission remains unchanged; arbitrary URLs cannot become returned files.

- 历史读取核对原 Session、Turn 和回传路由，复用 `inspect_result_identity` 及
  TypeScript 结果发布规则，不改写消息日志或发布凭证。
- 下载时重新校验来源，并由 `read_result_file` 检查不可变快照的大小、SHA-256、
  普通文件类型与无符号链接限制，不读取工作区中的可变原文件。快照不可用返回
  HTTP 503，原消息或来源不匹配返回 404。
- 文件标识保留其在原结果中的位置，相同字节的不同文件仍保留各自名称。响应以
  原文件名下载并设置 `nosniff`。
- 文件 URL 仅用于结果输出；输入附件的 MIME 和 data-URL 准入保持原样，任意 URL
  不能变成回传文件。

## Reproduction / 复现

Build the packaged Chat bundle, then run the existing bound-conversation smoke:

构建实际 Chat 包后，运行已有原会话场景：

```sh
npm --prefix apps/presentation/dashboard run build:chat
node apps/presentation/dashboard/smoke/conversation-reply-browser-smoke.mjs --returned-files
```

Only provider/model identity is synthetic; the fixture uses real publication,
Chat storage, HTTP and packaged UI. It checks timeline and full-reader downloads,
Chinese names, BOM/CRLF and binary bytes, keyboard access, narrow layout and
English labels. History reads must leave receipts unchanged, with zero model
Turns and exactly the original one provider send. Screenshots remain in ignored
output; this fixture reads no personal conversations or credentials.

仅 provider/model 身份为合成数据；场景使用真实发布、Chat 存储、HTTP 和打包页面，
验证两处下载、中文名称、BOM/CRLF、二进制字节、键盘、窄屏及英文。浏览后凭证必须
保持不变，模型 Turn 为零，provider 发送次数仍为原来的一次。截图留在忽略目录，
场景不读取个人会话或凭据。
