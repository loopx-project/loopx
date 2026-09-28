# Canonical Todo presentation checkpoint (2026-09-12)

Moved without content change from [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md) (former section "Canonical Todo presentation checkpoint (2026-09-12)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

The authority boundary now treats presentation as a first-class projection
contract rather than naming it `legacy_projection`. A shared TS presentation
normalizer maps the v0 wire shape's `source_section`/`index` to
`display_section`/`display_order`, while native records derive their display
section from domain role/archive state and never receive a fake persisted
index. The normalized presentation contract is shared; the wire coordinate is
not a second Todo state machine.

Todo creation, terminal successor materialization, projection validation,
standing-decision ordering, and archive ordering all use the same presentation
owner. The canonical domain validator is shared by both wire shapes, and the
v0 record is produced by an adapter from a validated domain record. This
unifies the semantic owner without rewriting v0 heads or receipts.

Python read callers now import the semantic owner directly; the compatibility
facade is no longer an internal dependency. Python presentation sorting keeps
source `index` order when it is present and uses completion/update time plus
Todo identity for native records, so the compatibility shape cannot leak into
business eligibility or lifecycle decisions.

The next migration may persist an optional canonical `presentation` object, but
only after proving whether an imported section is provenance or current display
intent and after qualifying a stable display-order policy. Until then, native
display positions remain derived at the renderer boundary and must not affect
authority lifecycle decisions.
