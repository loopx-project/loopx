# RFC: Configuration Assembly Provider (v0)

- **RFC status:** Draft
- **Delivery maturity:** Partial
- **Authors / owners:** LoopX maintainers
- **Created:** 2026-09-14
- **Last normative revision:** 2026-09-14
- **Implementation baseline:** issue #3800
- **Related contracts:** [Host Integration Surface v0](../../reference/protocols/host-integration-surface-v0.md), [TypeScript control-plane migration v0](typescript-control-plane-migration-v0.md)
- **Language mirror:** [中文版](configuration-assembly-provider-v0.zh-CN.md)

## 1. Decision summary

LoopX may call an independently installed configuration-assembly provider across a versioned JSON process boundary. Version 0 is default-off and read-only: only `probe` and `plan` exist. LoopX remains authoritative for goals, Todo, gates, quota, recovery, and accepted writeback. A provider plan, status, or zero exit code never validates task completion or grants launch, confirmation, or write authority.

## 2. Placement

The provider is an optional extension boundary, not a new outcome capability and not a host runtime. Host adapters remain thin under Host Integration Surface v0. The process contract follows the TypeScript migration rule: coarse versioned JSON calls, never in-process imports or copied databases.

## 3. Protocol

Requests contain `schema_version`, `operation`, `operation_id`, and a public-safe `request`. Responses must echo the schema, operation, and operation id. `plan` additionally returns `plan_id`, canonical SHA-256 `plan_digest`, and a JSON plan. Status is exactly `ready`, `unknown`, or `incomplete`; LoopX preserves `unknown` and `incomplete` rather than promoting them.

Only allowlisted response fields enter LoopX readback. Provider stderr and undeclared response fields are discarded. Output is bounded to 64 KiB and execution is timed out. Missing executables, malformed or truncated JSON, oversized output, version drift, identity mismatch, digest mismatch, timeout, and nonzero exit all fail closed as public-safe read failures.

## 4. Feature-off parity

Absent, disabled, incompatible, or failed providers return unavailable/unknown readback and do not alter the existing lifecycle. No result from this protocol sets completion, gate, validation, launch, or writeback state.

## 5. CLI fallback

The stable fallback is a direct JSON request to the configured executable over stdin and one JSON response over stdout. Hosts may wrap the same call but gain no additional authority.

## 6. Non-goals

Version 0 does not approve `apply`, `observe`, `recover`, client launch, Skills/workflow-kit integration, provider-managed confirmation, or provider-owned LoopX state.

## 7. Validation

Focused tests cover feature-off parity; public-field filtering; preservation of incomplete; missing executable; malformed JSON; schema, operation, identity, and digest fencing; timeout; and the absence of completion/validation authority.

## 8. Smallest delivered slice

`loopx.configuration_assembly_provider.invoke_configuration_provider` supplies the provider-neutral read boundary. A synthetic executable fixture exercises the same stdin/stdout contract without coupling the protocol to any particular provider.
