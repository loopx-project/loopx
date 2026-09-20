// Execute the real settlement identity builder against given inputs.
//
// This is a witness, not a reimplementation: it imports the shipped module and
// reports what the builder actually returns, including the rejection. The probe
// inputs arrive on stdin so the registry cannot select an arbitrary callable --
// the module and the function are fixed here, in tracked code.
import {settlementIdentity, settlementIdentityPayload} from "../loopx/control_plane/effect_program.ts";

type Probe = {
  todo_id?: string | null;
  replan_obligation_id?: string | null;
};

const request = JSON.parse(await new Response(process.stdin).text()) as {probes: Probe[]};
const results = request.probes.map(probe => {
  const input = {
    goal_id: "g",
    agent_id: "a",
    turn_instance_id: "t",
    todo_id: probe.todo_id ?? null,
    replan_obligation_id: probe.replan_obligation_id ?? null,
  };
  try {
    const identity = settlementIdentity(input);
    const payload = settlementIdentityPayload(input);
    return {
      ok: true,
      binding_kind: identity.binding_kind,
      binding_id: identity.binding_id,
      effect_id: identity.effect_id,
      // The wire payload does not always carry binding_kind; reporting the key
      // presence keeps a later change to that shape visible.
      payload_has_binding_kind: Object.hasOwn(payload, "binding_kind"),
      payload_schema_version: payload.schema_version,
    };
  } catch (error) {
    return {ok: false, error: error instanceof Error ? error.message : String(error)};
  }
});
process.stdout.write(JSON.stringify(results));
