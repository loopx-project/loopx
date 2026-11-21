# Workbench installation review evidence

Product revision: `a5cc1fce756f80f9ecfd70d069318ae23268c27b`.

Captured by `apps/workbench/packages/e2e/test/pwa-install.spec.mjs` using the packaged UI and isolated real Workbench daemon in Linux Chromium. All conversations and the expiring-credential notice are synthetic. Browser install offers, consent outcomes, and installation receipts are simulated; native macOS and Windows installation is not certified. The served manifest and browser installability check report no errors.

Before images show the same revision without an install offer; after images show the eligible state. Desktop: 1440 × 1000. Narrow: 390 × 844. Both light and dark themes were inspected, including a long conversation list and coexisting expiry notice. Recovery images show a failed browser prompt and menu guidance.

These images are revision-pinned PR attachments, kept outside the product source branch.

The `workbench-*` captures come from `tests/test_workbench_cli.py` at the same product revision, entering via the real CLI-issued login link. Desktop is 1280 × 900 (light); narrow is 390 × 844 (dark). Conversations and accounts are synthetic; no live model execution.
