import {useCallback, useEffect, useRef, useState} from "react";
import {fetchLarkApps, type LarkApp} from "./chat";

/** One settings mount owns one read, shared by its connection/setup panels.
 * Observed readiness is presentation only; operations revalidate at Core.
 */
export function useLarkApps() {
  const [apps, setApps] = useState<LarkApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const current = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    setApps([]); setError(null); setLoading(true);
    const active = () => !controller.signal.aborted;
    try {
      const profiles = await fetchLarkApps({verify: false,
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(7000)])});
      if (!active()) return;
      setApps(profiles);
      let next = 0;

      const worker = async () => {
        while (active() && next < profiles.length) {
          const profile = profiles[next++];
          let observed: LarkApp;
          try {
            const rows = await fetchLarkApps({verify: true, appRef: profile.app_ref,
              signal: AbortSignal.any([controller.signal, AbortSignal.timeout(7000)])});
            if (rows.length !== 1 || rows[0].app_ref !== profile.app_ref) throw new Error("Lark profile changed");
            observed = rows[0].health_error_code === "lark_app_unverified"
              ? {...rows[0], health_error_code: "lark_app_verification_failed"} : rows[0];
          } catch {
            observed = {...profile, ready: false, reply_ready: false, health_error_code: "lark_app_verification_failed"};
          }
          if (active()) setApps(items => items.map(item => item.app_ref === profile.app_ref ? observed : item));
        }
      };
      void Promise.all(Array.from({length: Math.min(4, profiles.length)}, worker));
    } catch (cause) {if (active()) setError(cause);}
    finally {if (active()) setLoading(false);}
  }, []);
  useEffect(() => {void refresh(); return () => current.current?.abort();}, [refresh]);
  return {apps, loading, error, refresh};
}
