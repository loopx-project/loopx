import {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {GoalAcceptanceContractSection} from "../src/features/personal-workspace/goal-acceptance-contract";
import {goalAcceptanceContractCopy} from "../src/features/personal-workspace/delivery-review-copy";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n";

const queryKey = ["goal-acceptance-readback", "release-demo"];
const client = new QueryClient({defaultOptions: {queries: {retry: false}}});

function WriteAuthorityProbe() {
  const [phase, setPhase] = useState("arming");
  const resume = useRef<(() => void) | null>(null);
  useEffect(() => {
    const cancel = client.cancelQueries.bind(client);
    let deferNext = true;
    client.cancelQueries = async (...args) => {
      await cancel(...args);
      if (!deferNext) return;
      deferNext = false;
      await new Promise<void>(resolve => {resume.current = resolve; setPhase("waiting");});
      setPhase("released");
    };
    setPhase("ready");
    return () => {resume.current?.(); client.cancelQueries = cancel;};
  }, []);
  return <main>
    <output aria-label="Cancellation state">{phase}</output>
    <button type="button" onClick={() => void client.refetchQueries({queryKey, exact: true})}>Refresh native read</button>
    <button type="button" disabled={phase !== "waiting"} onClick={() => {
      const release = resume.current; resume.current = null; release?.();
    }}>Release query cancellation</button>
    {/* The source and canManage stay fixed while only the native read changes. */}
    <GoalAcceptanceContractSection goalId="release-demo" contract={{}} copy={goalAcceptanceContractCopy.en}
      current canManage localSource management/>
  </main>;
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}><WorkspaceI18nProvider><WriteAuthorityProbe/></WorkspaceI18nProvider></QueryClientProvider>,
);
