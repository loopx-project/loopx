import {
  activeStatusSourceForUrl,
  addSshTunnelStatusSource,
  bindConfiguredSshHostAliases,
  defaultLocalStatusSourceUrl,
  emptyStatusSourceCatalog,
  loadStatusSourceCatalog,
  localStatusSource,
  projectedStatusSourceForUrl,
  removeStatusSource,
  runtimeStatusSourceCatalog,
  saveStatusSourceCatalog,
  statusSourceCatalogStorageKey,
  statusSourceForUrl,
} from "../src/data/status-source-catalog";
import {
  configuredSshTunnelDraft,
  defaultConfiguredSshHostsUrl,
  parseConfiguredSshHostCatalog,
} from "../src/data/ssh-host-catalog";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string) {
  assert(Object.is(actual, expected), `${message}: expected ${String(expected)}, received ${String(actual)}`);
}

function deepEqual(actual: unknown, expected: unknown, message: string) {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

class MemoryStorage {
  values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

const baseHref = "http://127.0.0.1:5173/";
const initial = emptyStatusSourceCatalog();
deepEqual(initial.sources, [localStatusSource], "the default catalog contains only the local source");
equal(defaultLocalStatusSourceUrl, "/status.json", "the built-in source stays on the Dashboard origin");
equal(initial.sources[0].readOnly, false, "the built-in local control plane stays interactive");
equal(statusSourceForUrl(initial, "/status.json", baseHref)?.id, "local", "the Vite-proxied default resolves to the local source");
equal(projectedStatusSourceForUrl(initial, "/api/status.json", baseHref).readOnly, false, "a same-origin relative proxy stays local");
equal(projectedStatusSourceForUrl(initial, "http://127.0.0.1:9999/status.json", baseHref).readOnly, true, "an unregistered loopback port does not inherit local authority");

const added = addSshTunnelStatusSource(initial, {
  hostAlias: "remote-lab",
  label: "Remote lab",
  statusUrl: "http://localhost:8876/status.json",
}, baseHref);
assert("catalog" in added, "a valid tunnel source is accepted");
equal(added.source.kind, "ssh_tunnel", "the source keeps its typed provider kind");
equal(added.source.readOnly, true, "a tunneled source cannot inherit local write authority");
equal(added.source.statusUrl, "http://localhost:8876/status.json", "the tunnel URL is canonicalized");
equal(added.source.hostAlias, "remote-lab", "a configured source retains its remote write routing identity");
equal(statusSourceForUrl(added.catalog, added.source.statusUrl, baseHref)?.id, added.source.id, "URL lookup preserves source identity");

const publicSource = addSshTunnelStatusSource(initial, {
  label: "Unsafe public endpoint",
  statusUrl: "https://example.com/status.json",
}, baseHref);
assert("error" in publicSource, "public endpoints fail closed instead of entering the local control plane catalog");

const duplicate = addSshTunnelStatusSource(added.catalog, {
  label: "Duplicate",
  statusUrl: added.source.statusUrl,
}, baseHref);
assert("error" in duplicate, "one tunnel URL has one stable source identity");

const secondTunnel = addSshTunnelStatusSource(added.catalog, {
  label: "Remote build host",
  statusUrl: "http://127.0.0.1:8976/status.json",
}, baseHref);
assert("catalog" in secondTunnel, "a catalog accepts more than one named SSH tunnel");
equal(secondTunnel.catalog.sources.length, 3, "local and multiple SSH sources coexist");
assert(secondTunnel.source.id !== added.source.id, "each tunnel keeps an independent stable identity");

const storage = new MemoryStorage();
saveStatusSourceCatalog(storage, secondTunnel.catalog);
const stored = JSON.parse(storage.getItem(statusSourceCatalogStorageKey) ?? "{}");
stored.sources[0].readOnly = false;
storage.setItem(statusSourceCatalogStorageKey, JSON.stringify(stored));
const restored = loadStatusSourceCatalog(storage, baseHref);
equal(restored.sources[1].readOnly, true, "persisted input cannot downgrade a tunnel to writable");
equal(restored.sources[1].hostAlias, "remote-lab", "configured routing identity survives catalog reload");

const legacyStorage = new MemoryStorage();
legacyStorage.setItem(statusSourceCatalogStorageKey, JSON.stringify({
  schemaVersion: 1,
  sources: [{ kind: "ssh_tunnel", label: "remote-lab", statusUrl: "http://127.0.0.1:9076/status.json" }],
}));
const migrated = bindConfiguredSshHostAliases(loadStatusSourceCatalog(legacyStorage, baseHref), ["remote-lab"]);
equal(migrated.sources[1].hostAlias, "remote-lab", "an exact configured alias upgrades an older read-only source for remote Goal lifecycle");
const unbound = bindConfiguredSshHostAliases(loadStatusSourceCatalog(legacyStorage, baseHref), ["other-host"]);
equal(unbound.sources[1].hostAlias, undefined, "a display label never gains remote write authority without an exact configured alias");

const withoutTunnel = removeStatusSource(restored, restored.sources[1].id);
equal(withoutTunnel.sources.length, 2, "removing one tunnel preserves the other named source");
assert(withoutTunnel.sources.some((source) => source.label === "Remote build host"), "the second SSH source remains selectable");
deepEqual(removeStatusSource(withoutTunnel, localStatusSource.id).sources, withoutTunnel.sources, "the built-in local source cannot be removed");

equal(activeStatusSourceForUrl(initial, null, localStatusSource.statusUrl, baseHref).id, "local", "idle defaults to the local source");
equal(activeStatusSourceForUrl(added.catalog, added.source.statusUrl, localStatusSource.statusUrl, baseHref).id, added.source.id, "an in-flight selection wins over the previously loaded local source");
equal(activeStatusSourceForUrl(added.catalog, null, added.source.statusUrl, baseHref).id, added.source.id, "with no in-flight request the loaded source is active");

const configuredHosts = parseConfiguredSshHostCatalog({
  ok: true,
  schema_version: "ssh_host_catalog_v0",
  hosts: [{ alias: "remote-lab" }, { alias: "*.example" }, { alias: "remote-lab" }, { alias: "jump_box" }],
});
equal(defaultConfiguredSshHostsUrl, "/ssh-hosts", "configured Host discovery stays on the active local Dashboard origin");
deepEqual(configuredHosts.hosts, [{ alias: "remote-lab" }, { alias: "jump_box" }], "only explicit safe SSH aliases enter the browser catalog");
const configuredDraft = configuredSshTunnelDraft("remote-lab", "8876");
assert("command" in configuredDraft, "a configured Host and valid local port produce a tunnel draft");
equal(configuredDraft.command, "ssh -N -L 8876:127.0.0.1:8766 remote-lab", "the UI generates one exact OpenSSH command");
equal(configuredDraft.statusUrl, "http://127.0.0.1:8876/status.json", "the selected Host maps to the loopback-only status source");
assert("error" in configuredSshTunnelDraft("remote-lab", "22"), "privileged local ports fail closed");

const runtimeUrl = "http://127.0.0.1:19801/status.json";
const runtimeEntries = [
  {id: "retained", label: "Retained", status_url: runtimeUrl},
  {id: "fresh", label: "Fresh", status_url: "http://127.0.0.1:19802/status.json"},
];
const runtimes = runtimeStatusSourceCatalog(runtimeEntries, runtimeUrl, baseHref);
assert(runtimes, "an owner-prepared catalog recognizes its current source");
assert(runtimes.sources.every((s) => s.readOnly && s.kind === "runtime" && !s.hostAlias), "runtime readers never acquire SSH or execution authority");
equal(statusSourceForUrl(runtimes, "/status.json", "http://127.0.0.1:19801/chat/")?.id, "retained", "a relative entry on the prepared runtime stays scoped and read-only");
equal(activeStatusSourceForUrl(runtimes, runtimeEntries[1].status_url, runtimeUrl, baseHref).id, "fresh", "a failed or pending same-Goal source selection does not bounce to the loaded runtime");
equal(runtimeStatusSourceCatalog(undefined, runtimeUrl, baseHref), null, "existing services need no runtime catalog");
equal(runtimeStatusSourceCatalog(runtimeEntries, "http://127.0.0.1:19803/status.json", baseHref), null, "an unrelated service cannot replace source choices");
equal(runtimeStatusSourceCatalog([...runtimeEntries, runtimeEntries[0]], runtimeUrl, baseHref), null, "duplicate runtime ids or URLs fail closed");
equal(runtimeStatusSourceCatalog([{...runtimeEntries[0], status_url: "https://external.example/status.json"}], runtimeUrl, baseHref), null, "runtime readers remain loopback-only");
const runtimeStorage = new MemoryStorage();
saveStatusSourceCatalog(runtimeStorage, runtimes);
equal(loadStatusSourceCatalog(runtimeStorage, baseHref).sources.length, 1, "ephemeral runtime choices are not persisted as execution or SSH configuration");

console.log("status source catalog smoke: ok");
