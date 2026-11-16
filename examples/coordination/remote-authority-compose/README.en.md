# Self-hosted LoopX coordination

Extract a built bundle on a machine with Docker Engine and Compose, then run the commands below. The first start uses container registries and npm. Select a free loopback port with `LOOPX_PORT`, which defaults to 8443.

```sh
./start.sh
./client.sh a read
./client.sh b read
./client.sh reader read
```

Two trusted writers have distinct credentials and can hand off the same starter Todo. An additional `reader` has its own credential and can read only the allowed starter task. The first start provisions PostgreSQL authority state, a private CA and clients. Later starts reuse the task, store identity and policies from the volumes. The database runs on an internal network.

Use `./client.sh a descriptor` to get the tenant, Goal, actor and store identity. Pass the request on standard input to `./client.sh a request`. When a reply is lost, keep the original request and `request_id` and pass them to `./client.sh a recover`.

A remote client can forward the server's loopback port over SSH and use the same revision of `remote-authority-client.ts` with `--ca`. Export the CA with `./client.sh a ca` and deliver the client's `credential` and `request.json` privately from its volume. The CLI reads the credential from `LOOPX_AUTHORITY_CREDENTIAL`. [Gateway requests and recovery](https://github.com/loopx-project/loopx/blob/main/docs/reference/remote-authority-task-service-v0.md)

Both TLS gateways publish loopback ports: trusted writers use `LOOPX_PORT` (8443 by default), while the scoped reader uses `LOOPX_READ_PORT` (8444 by default). Its private policy binds the exact Goal lifetime, Todo and original creation/binding receipt. Unlisted resources are denied. Snapshots return only allowed state, without notes, lease keys or a global cursor. Todo access does not authorize historical operation receipts.

The trusted writer endpoint retains v0 scope/operation grants; a/b can read other tasks in the same scope. The v1 resource ACL supports read/snapshot, not resource-scoped writes. Give observers only the reader credential and read port. Both listeners reuse the existing service and restricted database role. Reader isolation does not qualify resource isolation for writers. TLS covers client-to-gateway connections; database connections use the isolated backend network.

## Build and qualify

Run this from the reviewed template checkout. Supply the complete Git commit to use for the gateway build.

```sh
python3 examples/coordination/remote-authority-compose/build_bundle.py \
  --source-revision "$(git rev-parse HEAD)" --output /tmp/loopx-authority.tar.gz
```

The same source commit and template contents produce the same archive. The bundle contains `LICENSE`, `NOTICE`, the source digest and pinned image manifests. Docker verifies the digest before unpacking the source. Keep generated bundles, credentials, data and backups in their respective release or runtime storage.

The source revision must include scoped v1 reads (#8861). Qualify from an unpacked bundle on a disposable Docker host with a unique project name and two free loopback ports. On amd64, verify the architecture of the host and running services. Time cold and cached starts through image pull, build, readiness and client reads. Check that PostgreSQL has no published port, clients have no route to the backend network and they have no database credential.

Run the rehearsal from the repository. It creates a temporary project, exercises writer claim, expiry, takeover and receipt recovery after restart, plus reader filtering, write denial, persistent revocation and missing/malformed policy refusal, then removes its own containers and volumes.

```sh
node examples/coordination/remote-authority-compose/rehearse.mjs /path/to/unpacked-bundle
```

Compose limits CPU, memory, processes and retained logs. Check the Docker data disk and current load before a shared-host trial. Use dedicated projects, ports, networks and volumes. Tests with two local clients qualify the protocol. Qualifying two physical hosts requires a client on the second host.

## Back up, restore and rotate

```sh
./state.sh backup /absolute/path/to/new-backup
./start.sh
COMPOSE_PROJECT_NAME=loopx-restored ./state.sh restore /absolute/path/to/new-backup
COMPOSE_PROJECT_NAME=loopx-restored LOOPX_PORT=8445 LOOPX_READ_PORT=8446 ./start.sh
```

Backup stops the project and captures its database and credential volumes together. Keep backups encrypted and access-controlled. Restore verifies the digests and bundle version, and requires a new project with no existing target volumes. Read back tasks and original operation receipts before switching clients, and stop admitting requests on the old project first.

- Revoke access by atomically replacing `policy.json` in the relevant gateway volume: `reader-gateway` for scoped reads and `gateway` for trusted writes. Removing a resource entry or credential rejects the next request. Replacing a running v1 listener's policy with v0 cannot downgrade its permissions. Rotate high-entropy credentials together with their private policy digest and client file. Earlier backups still contain earlier credentials.
- The gateway certificate lasts 397 days. Reissue it with the CA material in `pg-admin` and the same SANs before expiry. Replace `cert.pem` and `key.pem` in the gateway volume, restart the gateway and check both clients. Distribute trust before rotating a CA.
- Back up before changing database credentials or versions. Rehearse migration and rollback in an isolated project. Update database roles and credential files together. Follow PostgreSQL's supported migration procedure across major versions.

[简体中文](README.md)
