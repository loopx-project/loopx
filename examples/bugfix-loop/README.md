# Bug-fixing loop practice project

[English walkthrough](../../docs/guides/first-bugfix-loop.md) ·
[中文实操教程](../../docs/guides/first-bugfix-loop.zh-CN.md)

This is an intentionally broken, standard-library-only Python exercise. Copy
[cart.py](cart.py) and [checks.py](checks.py) into a fresh directory outside the LoopX checkout.
Use Python 3.11 or later. Run:

```bash
python3 checks.py
```

Expected starting result: **six checks, two failures, exit status 1**. The
failures concern an empty cart and the free-shipping threshold. After the
repair, the same six checks must pass with exit status 0.

The independent requirements are:

- Inputs are nonnegative integer prices in cents.
- An empty cart costs zero, including shipping.
- A subtotal of at least 10,000 cents ships free.
- A smaller, nonempty cart adds 500 cents for shipping.
- Negative prices raise `ValueError`; the input list stays unchanged.

Do not delete or weaken checks to obtain a green result. The intentionally
failing file is named `checks.py` so ordinary repository test discovery does
not collect this exercise as a failing product test.

The walkthrough shows how to give this task to LoopX, confirm host activation,
inspect the patch, and make a local signed-off commit. It also includes a
separate recurring bug-hunt prompt and the real-project PR workflow. No remote
repository, network service, paid API, or model call is needed to run the six
checks. An agent-driven repair uses the agent host's own account and usage.

This fixture demonstrates a repair exercise. Passing its checks does not
qualify unattended host continuation, real-project bug discovery, or a learning
benefit.
