# Workbench installation review evidence

Product revision: `458a7afbe0c8c0ac8f609d904b32de31a88fca24`.

Captured by `apps/workbench/packages/e2e/test/pwa-install.spec.mjs` using the packaged UI and isolated real Workbench daemon in Linux Chromium. All conversations and the expiring-credential notice are synthetic. Browser install offers, consent outcomes, and installation receipts are simulated; native macOS and Windows installation is not certified. The served manifest and browser installability check report no errors.

Before images show the same revision without an install offer; after images show the eligible state. Desktop: 1440 × 1000. Narrow: 390 × 844. Both light and dark themes were inspected, including a long conversation list and coexisting expiry notice. Recovery images show a failed browser prompt and menu guidance.

These images are revision-pinned PR attachments, kept outside the product source branch.
