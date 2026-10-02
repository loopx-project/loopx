import { summarizePerformanceProfile } from "../../../../../../loopx/control_plane/capabilities/performance_profile";

// Raw captures stay in this browser. Parsing and aggregation never block the UI.
self.onmessage = async (event: MessageEvent<File>) => {
  try {
    if (event.data.size > 16 * 1024 * 1024) throw new Error("Profile exceeds 16 MiB; select a shorter capture.");
    const profile: unknown = JSON.parse(await event.data.text());
    self.postMessage({ result: summarizePerformanceProfile({ profile, top: 15 }) });
  } catch (cause) {
    self.postMessage({ error: cause instanceof Error ? cause.message : "Could not inspect profile." });
  }
};
