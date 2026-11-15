import {useState} from "react";
import {createRoot} from "react-dom/client";
import {GoalLoopXMode} from "../src/features/personal-workspace/goal-loopx-mode";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n";

function OriginalTaskProbe() {
  const [observations, setObservations] = useState(0);
  const forbidden = () => {throw new Error("Managed Task entered the coordinator owner");};
  return <main>
    <GoalLoopXMode onPrepare={async () => {forbidden(); return "";}} onExecute={forbidden} onChange={forbidden}
      managed={{target: {goalId: "cart-repair", agentId: "codex", sessionId: "original-session"},
        onObserve: async () => {setObservations(value => value + 1);}}}/>
    <output aria-label="History observations">{observations}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<WorkspaceI18nProvider><OriginalTaskProbe/></WorkspaceI18nProvider>);
