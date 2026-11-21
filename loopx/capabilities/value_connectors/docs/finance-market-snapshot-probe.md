# Finance Market Snapshot Migration Packet

Status: retired connector evidence and upgrade migration path.

`finance_market_snapshot` was a public-safe, no-credential probe profile. It
never became a live connector or trading adapter. Finance value discovery now
lives in the independently packaged `loopx-finance-value-discovery` extension,
without a `finance-value-discovery` capability.

Legacy callers may still inspect the migration contract:

```bash
loopx value-connectors source-map \
  --connector finance_market_snapshot \
  --format json

loopx value-connectors install-check \
  --connector finance_market_snapshot \
  --format json

loopx value-connectors plan \
  --connector-id finance_market_snapshot \
  --format json
```

All three commands return `value_connector_extension_migration_v0`. They perform no
external read, install, registration, or Finance execution. The packet gives an
agent this bounded sequence:

1. inspect `loopx extension list --format json`;
2. when working from a LoopX source checkout and local environment writes are
   authorized, install `./packages/loopx-finance-value-discovery`;
3. register its manifest with `loopx extension install --execute`;
4. invoke it with `loopx extension run ... --execute`.

The current sample is a co-located source package, not an artifact distributed
inside the LoopX wheel. If the provider source or package is unavailable, the
agent must report `provider source required`. It must not recreate the old
connector, invent a Finance capability, fetch credentials, access an account
or private portfolio, provide investment advice, trade, or start a continuous
watch.

Historical source and freshness findings remain lineage only under the
`finance_market_snapshot_probe_packet_v0` evidence id.
