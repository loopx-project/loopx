import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { typedActionProposalSchema } from "../src/data/chat.js";

// Input comes from ChatActionService over disposable registries, not a replica
// of the backend's source-basis builder.
const proposals = JSON.parse(readFileSync(0, "utf8")) as Record<string, unknown>[];
assert.ok(proposals.length > 0);
for (const proposal of proposals) {
  const decoded = typedActionProposalSchema.parse(proposal);
  assert.deepEqual(decoded.canonical_update_basis, proposal.canonical_update_basis);
  const basis = proposal.canonical_update_basis as Record<string, unknown>;
  for (const key of Object.keys(basis)) {
    const incomplete = { ...basis };
    delete incomplete[key];
    assert.equal(typedActionProposalSchema.safeParse({
      ...proposal, canonical_update_basis: incomplete,
    }).success, false, `missing ${key} must be rejected`);
  }
  assert.equal(typedActionProposalSchema.safeParse({
    ...proposal, canonical_update_basis: { ...basis, source_identity: "bad-digest" },
  }).success, false);
  assert.equal(typedActionProposalSchema.safeParse({
    ...proposal, canonical_update_basis: { ...basis, schema_version: "unknown" },
  }).success, false);
  if (basis.schema_version === "loopx_goal_deletion_source_basis_v1") {
    for (const patch of [{ source_content_sha256: "bad-digest" }, { route_mode: "unknown" }]) {
      assert.equal(typedActionProposalSchema.safeParse({
        ...proposal, canonical_update_basis: { ...basis, ...patch },
      }).success, false);
    }
  }
}

const template = proposals[0];
const digest = "a".repeat(64);
for (const schema_version of ["loopx_chat_canonical_update_basis_v0", "loopx_chat_canonical_terminal_basis_v0"]) {
  const proposal = {
    ...template, action_kind: "todo.update", normalized_parameters: {},
    canonical_update_basis: { schema_version, provider_revision: "revision-1", source_authority: "file_v0", registry_sha256: digest },
  };
  assert.ok(typedActionProposalSchema.safeParse(proposal).success);
  assert.equal(typedActionProposalSchema.safeParse({
    ...proposal, canonical_update_basis: { ...proposal.canonical_update_basis, provider_revision: "" },
  }).success, false);
}
console.log(`PASS ${proposals.length} real lifecycle proposals and Todo basis parity`);
