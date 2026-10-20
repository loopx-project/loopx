import { useState } from "react";
import { createRoot } from "react-dom/client";
import { currentChannelSession, useConversationHistory } from "../src/data/use-conversation-history";

function HistoryReadProbe() {
  const [goal, setGoal] = useState("a");
  const [lastRefreshTurn, setLastRefreshTurn] = useState<string | null>(null);
  const history = useConversationHistory({ agentId: "codex", currentAgentId: "codex", channelId: `goal.${goal}`, goalId: goal, enabled: true });
  return <main>
    <button onClick={history.retry}>Retry missing history</button>
    <button onClick={() => void history.refresh().then(loaded => setLastRefreshTurn(currentChannelSession(loaded, "codex")?.active_turn_id ?? null))}>Refresh history</button>
    <button onClick={() => setGoal(current => current === "a" ? "b" : "a")}>Switch Goal</button>
    <output aria-label="History state">{JSON.stringify({ goal, phase: history.phase, reading: history.reading,
      sessionId: history.currentSession?.session_id ?? null,
      status: history.currentSession?.status, turnId: history.currentSession?.active_turn_id,
      texts: history.history?.messages.map(message => message.text), lastRefreshTurn })}</output>
  </main>;
}

createRoot(document.getElementById("root")!).render(<HistoryReadProbe />);
