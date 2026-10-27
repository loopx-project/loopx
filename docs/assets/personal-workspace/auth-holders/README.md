# Native account login holders

Public-safe synthetic process inventory on the built settings page. The page
uses the production Chat HTTP account service and a disposable native policy
store; native process IO is synthetic. No credentials, model, account switch,
process termination or live workload is used.

The original settings component and stylesheet at
`beede96e0ade200353ee0e4646a0b52d81576c37` produce `before-desktop.png`.
The remaining captures show the changed component with the same 21-holder
fixture: 18 helpers from one App, one renamed service and two held external
processes. App helpers share one row, with every PID preserved.

The default view keeps account status visible. Expanding shows held owners
first; the 390px view retains full names, PID lists and reasons. Failed and
incomplete reads clear the old list and show unknown. Empty and sparse reads,
retry, keyboard expansion, English and the absence of process scans on normal
status ticks are also exercised. These captures do not certify VM restart or
new-auth adoption.

Reproduce from the repository root after building the Chat bundle:

```sh
node apps/presentation/dashboard/smoke/codex-account-clients-smoke.mjs
```

Use the source test environment and the browser setup from the normal dashboard
smoke guide. The fixture has no dependency on a real installed slot manager.
