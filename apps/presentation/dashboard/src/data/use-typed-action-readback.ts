import { useQuery } from "@tanstack/react-query";
import { listTypedActions } from "./chat";

/** Visible workspace readback only: no confirmation, dispatch or effect owner.
 * Query keys fence scope changes; React Query serializes same-key requests,
 * cancels superseded reads and suspends background interval polling. */
export function useTypedActionReadback(readOnly: boolean, goalId: string | null | undefined) {
  return useQuery({
    queryKey: ["typed-action-readback", goalId ?? "manager"],
    queryFn: ({ signal }) => listTypedActions(goalId ? { goalId } : {},
      AbortSignal.any([signal, AbortSignal.timeout(10_000)])),
    enabled: !readOnly,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
}
