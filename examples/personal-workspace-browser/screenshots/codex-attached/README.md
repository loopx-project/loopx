# Original Codex App conversation UI

Public synthetic visual evidence from the packaged browser regression. All Goals,
Agent messages and native owner acknowledgements are disposable fixtures. These
images do not depict a real account, installation or native live send.

Reproduce from the repository root after building the Chat frontend:

```sh
LOOPX_PERSONAL_WORKSPACE_PACKAGED=1 \
LOOPX_PERSONAL_WORKSPACE_SCENARIO=attached-host-follow-up \
node examples/personal-workspace-browser-smoke.mjs
```

The scenario writes current screenshots under `output/playwright/personal-workspace`.
The checked-in images document the proposed entry, desktop conversation, narrow
conversation, explicit open-failure state and unconfirmed delivery; they are not
golden pixel oracles. Confirmed delivery keeps an empty composer available;
unconfirmed delivery exposes receipt recovery and never automatically resends.
