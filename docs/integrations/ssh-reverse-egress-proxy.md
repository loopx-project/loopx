# Reliable SSH Reverse Egress Proxy

A remote agent runtime may have a healthy SSH control channel while lacking a
reliable outbound route to its model API. These are separate data paths. A
desktop application that starts a remote runtime over SSH does not necessarily
forward the desktop's HTTPS connectivity to that runtime.

This guide provides a public-safe reference for running a loopback-only HTTPS
CONNECT proxy on the desktop side and exposing it to one remote runtime through
an SSH reverse forward. It transfers network traffic only. It does not copy
credentials, session databases, rollout files, or runtime state.

The implementation is the parameterized
[`examples/ssh_reverse_proxy_supervisor.py`](../../examples/ssh_reverse_proxy_supervisor.py)
script. It is an optional integration example, not a LoopX control-plane
capability or a replacement for a host application's native SSH connection.

## Data Path And Ownership

```text
remote agent process
  -> remote 127.0.0.1:<remote-port>
  -> managed SSH reverse-forward channel
  -> desktop 127.0.0.1:<local-port>
  -> loopback HTTPS CONNECT proxy
  -> public model endpoint:443
```

The desktop owns both the local proxy and the SSH client that creates the
reverse forward. The remote side receives only a loopback listener. Keeping
both listeners on `127.0.0.1` prevents other machines from using the proxy.

Use a dedicated port for this bridge. Do not reuse the native control channel's
port, a project status port, or a shared proxy port.

## Failure Modes Worth Preserving

### A stale remote listener is not necessarily a timer

When a route, VPN, or network interface changes abruptly, the desktop SSH
client can disappear before the remote `sshd` child notices. That child may
temporarily retain the reverse-forward listener. A process manager with
`KeepAlive` or an equivalent restart policy then launches a new SSH client,
which fails with `remote port forwarding failed` because the old listener still
owns the port.

The restart policy creates repeated attempts; it does not create the stale
listener. Treat the remote `sshd` child and the local supervisor as two
different lifecycle owners when diagnosing this loop.

### Out-of-band SSH health probes can amplify an outage

A periodic check that opens a second SSH connection may time out during a slow
handshake even while the managed tunnel is still usable. If that check kills
the tunnel, a transient route slowdown becomes a deterministic restart loop.

Prefer the managed SSH process's `ServerAliveInterval` and
`ServerAliveCountMax`. Run remote inspection only after that process exits.
This keeps the failure detector on the same connection it manages.

### Globally scoped IPv6 can still be unreachable

DNS may return IPv6 before IPv4 while the active route has no working IPv6
egress. A sequential connector can spend the caller's entire timeout on the
first IPv6 address. The example filters non-public targets, prefers IPv4, and
retains IPv6 as a fallback.

## Run The Supervisor

Configure an ordinary SSH alias for the remote runtime, then run:

```bash
python3 examples/ssh_reverse_proxy_supervisor.py \
  --ssh-host example-runtime \
  --local-port 18080 \
  --remote-port 18080
```

The local proxy remains alive while its managed SSH child reconnects. The SSH
command uses batch mode, disables connection sharing, enables server-alive
checks, and requires the reverse forward to bind successfully.

On the remote runtime, point HTTPS clients at the remote loopback listener:

```bash
export HTTPS_PROXY=http://127.0.0.1:18080
```

Keep credentials in the remote runtime's existing credential source. Proxy
configuration should contain only the loopback URL.

## Optional Stale-Listener Cleanup

Automatic cleanup is intentionally opt-in:

```bash
python3 examples/ssh_reverse_proxy_supervisor.py \
  --ssh-host example-runtime \
  --local-port 18080 \
  --remote-port 18080 \
  --cleanup-stale-listener
```

Cleanup runs only after the managed tunnel exits. It refuses to terminate a
process unless all of these conditions hold:

- the process listens on the exact configured remote loopback port;
- the process belongs to the current remote user;
- its executable name is `sshd` or `sshd-session`.

The remote account must be able to inspect that listener. If `lsof` requires
privilege, allow only non-interactive inspection of the dedicated loopback
port. Do not grant a broad process-management rule. Terminating a same-user
`sshd` child should not require elevated privilege.

If cleanup is disabled or refused, the local proxy stays up and the supervisor
continues retrying. Operators can inspect the exact listener before deciding
whether to remove it.

## End-To-End Validation

First validate the local proxy without depending on SSH:

```bash
curl --proxy http://127.0.0.1:18080 \
  --noproxy '' \
  --connect-timeout 10 \
  https://example.com/ \
  --output /dev/null \
  --write-out 'status=%{http_code} total=%{time_total}\n'
```

Then validate the reverse-forward path from the remote runtime. The synthetic
health host is answered locally by the example and does not use public DNS:

```bash
curl --proxy http://127.0.0.1:18080 \
  --noproxy '' \
  --max-time 15 \
  --output /dev/null \
  --write-out 'status=%{http_code} total=%{time_total}\n' \
  http://reverse-proxy-health.invalid/
```

Expected status is `204`. Finally, request the intended public HTTPS endpoint.
Any real HTTP response, including an authentication or method error, proves
that DNS resolution, reverse forwarding, CONNECT, TLS, and HTTP reached the
service. A successful health endpoint with a failed HTTPS request narrows the
problem to desktop DNS or public egress rather than the reverse forward.

## Controlled Recovery Test

Before relying on a process manager, terminate only the supervisor's managed
SSH child. Do not terminate the host application's native SSH control process.
The expected result is:

1. the supervisor process stays alive;
2. the old SSH child exits;
3. an exact stale listener is cleaned only when cleanup is enabled;
4. a new SSH child appears;
5. the remote health endpoint returns `204` again.

When using `launchd`, `systemd`, or another process manager, verify that its
restart counter does not increase during this test. A stable supervisor with a
replaced child proves that reconnect ownership is inside the supervisor rather
than delegated to a crash loop.

## Security Boundary

The example deliberately applies a narrow policy:

- local and remote listeners bind only to IPv4 loopback;
- only HTTPS `CONNECT` requests to port `443` are accepted;
- resolved targets must be globally routable addresses;

- the SSH host, ports, retry policy, and cleanup behavior are explicit CLI
  configuration;
- logs contain lifecycle events and error classes, not request headers or
  response bodies.

Do not publish real SSH aliases, hostnames, private addresses, local absolute
paths, process listings, route tables, credentials, or incident logs when
sharing a diagnosis. Preserve the lifecycle pattern and validation method,
not the original environment.

## Repository Validation

The smoke is offline: it mocks address resolution and opens only an ephemeral
loopback server. It never contacts an SSH host or a public endpoint.

```bash
python3 examples/ssh-reverse-proxy-supervisor-smoke.py
python3 -m py_compile examples/ssh_reverse_proxy_supervisor.py
```
