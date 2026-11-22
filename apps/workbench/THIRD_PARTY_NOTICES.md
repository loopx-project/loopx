# Third-party notices

## persona

The machine token metering (`packages/daemon/src/machine-usage.ts`, `packages/daemon/src/adapters/transcript-usage.ts`) and the machine usage page (`packages/web/src/machine-usage.ts`, `packages/web/src/components/MachineUsagePage.tsx` — counting rules, chart drawing rules, the today card and the machine cards) follow nwverse/persona's design and adapt parts of its implementation (`server/internal/node/usage.go`, `web/src/lib/tokens.ts`, `web/src/views/machines/Machines.tsx`, `web/src/views/machines/UsageSection.tsx`), rewritten in TypeScript for this repository's daemon and web UI.

MIT License

Copyright (c) 2026 nwverse

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Calli

Antigravity account isolation and native OAuth login (`packages/daemon/src/adapters/agy-account.ts`,
`code-login.ts`, inline login UI and related account tests) are adapted from Calli
(`maxliux5/calli`, account flow at `d2a6d6f1dc524dd0ce36a73f6674bd74967c950c`,
inline/resumable login at `c9802662b5219395b14e65ba30dfff21230df91b`).

Quote-paste handling is adapted from Calli `b456a10716221f06baee5d62a02f87dd626df460`;
quoted side-chat model selection from `80eb16edbd3e2cc3e2a3c887a2560fe39b6ab209`,
including the current local SideChat preference/catalog refinements. Existing Loochat
message-draft protections and machine-scoped provenance are retained.

Antigravity model-family/effort selection and legacy preference compatibility are adapted from
Calli `d89d2656d707ba9b152178be4343f9112fd87c72`; the ACP conversation transport and
its protocol fixtures from `96f3dade4c603778d48a494f53753b888b5e1c26`. The port retains
Loochat's typed login recovery and prompt-submission lifecycle.

MIT License

Copyright (c) 2026 Calli contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
