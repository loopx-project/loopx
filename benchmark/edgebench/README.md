# Native EdgeBench worker trials

This research adapter calls SForge's native `run_agent`. Task images, prompts,
submission cooldowns, host auto-evaluation, judge isolation and scoring remain
owned by the pinned EdgeBench checkout. Install SForge and Harbor in an isolated
runner environment. Run the controller on Linux with Docker and native SForge
iptables permissions; a macOS Docker socket alone does not provide that boundary.

The five worker profiles are `official`, `single`, `native-goal`,
`heartbeat-resume`, and `heartbeat-explore`. `official` uses SForge's Codex Stop
hook and outer recovery. `single` executes once with neither. `native-goal` uses
the shared native Codex Goal transport. The heartbeat profiles use the shared
LoopX scheduler and resume store. `heartbeat-explore` additionally enables the
existing Explore Graph and analysis-only Explore Harness gates with the
`adaptive-resilient` profile; this grants no child-agent execution authority.

All profiles use the same explicitly staged Codex binary and model/effort. This
permits an updated Codex version with the native scaffold; it is not a claim of
identical settings to a published leaderboard. Preserve the version receipt and
report hardware, emulation and every protocol deviation with results.

Use the offline runtime payloads documented in
[`benchmark/runtime/RUNTIME.md`](../runtime/RUNTIME.md). `CODEX_AUTH_JSON_PATH`
opts into a private local Codex login; it is copied into each disposable trial
and excluded from artifact collection. Host network isolation additionally
permits the OpenAI OAuth endpoint for refresh during long runs.

If the Linux host requires an HTTP proxy, start the restricted TLS relay with
`python -m benchmark.runtime.connect_proxy --host BRIDGE_IP --port 9090
--allow-host chatgpt.com --allow-host auth.openai.com`, with the host upstream
in `HTTPS_PROXY`. Pass `--api-proxy-url http://BRIDGE_IP:9090` to the trial.
The relay rejects other hosts and ports and never decrypts TLS. Check both an
allowed TLS handshake and a denied external destination before admission;
stop the relay process after the campaign. Do not expose an unrestricted host
proxy to isolated tasks. This transport does not grant general internet access.

When the product must run from main while the adapter is under development, set
`LOOPX_SRC_DIR` / `LOOPX_EXPECTED_COMMIT` to the clean product checkout and
`LOOPX_RUNNER_SRC_DIR` / `LOOPX_EXPECTED_RUNNER_COMMIT` to the clean runner checkout.
Only `benchmark/runtime` is overlaid from the runner archive; installed LoopX
code remains at the product pin. Both revisions are recorded. Without separate
runner settings, the existing single-checkout contract applies.

Start the native judge with the same task directory and log root, then run:

```sh
python -m benchmark.edgebench.run \
  --task TASK_ID --tasks-dir /data/tasks --log-dir /data/private-runs \
  --run-id UNIQUE_ATTEMPT --worker heartbeat-resume \
  --model MODEL --effort xhigh --judge-url http://HOST:8080
```

The default trial timeout is **64,800 seconds (18 hours)**, auto-evaluation is
every 300 seconds, and the submission cooldown is 120 seconds. `--timeout`,
`--eval-interval`, and `--submission-cooldown` support explicitly recorded
qualification runs. The shared Harbor defaults are unchanged. Native task
internet policy is retained. Each attempt requires a new output directory.

This entrypoint currently exposes native evaluator feedback. The blind-feedback
ablation requires a separately qualified provider boundary before admission.
Configuration receipts prove requested startup settings; actual session model,
resume continuity, capability use, evaluator completion and integrity need
runtime/post-run qualification before any score is countable. Raw trial outputs
and credentials belong outside the public repository.
