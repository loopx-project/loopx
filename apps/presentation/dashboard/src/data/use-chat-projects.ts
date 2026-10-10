import { useQuery } from "@tanstack/react-query";
import { fetchChatProjects, type ChatProject } from "./chat";

/** Workspaces the host currently grants to ordinary conversations. The
 * session re-checks its grant before every Turn; this list only offers scopes. */
export function useChatProjects(readOnly: boolean): { projects: ChatProject[] | null; readFailed: boolean } {
  const query = useQuery({
    queryKey: ["chat-projects"],
    queryFn: ({ signal }) => fetchChatProjects(AbortSignal.any([signal, AbortSignal.timeout(10_000)])),
    enabled: !readOnly,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  if (readOnly) return { projects: [], readFailed: false };
  return { projects: query.data?.projects ?? null, readFailed: query.isError };
}
