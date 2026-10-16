import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LhtbBrief } from "./LhtbBrief";
import { SweMarathonBrief } from "./SweMarathonBrief";
import "./styles.css";
import "./swe-marathon-brief.css";
import "./lhtb-brief.css";
import "./time-machine/time-machine.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("LoopX homepage root is missing");

const pathSegments = window.location.pathname.split("/").filter(Boolean);
if (pathSegments.at(-1) === "index.html") pathSegments.pop();
const isSweMarathonBrief = pathSegments.slice(-2).join("/") === "benchmarks/swe-marathon";
const isLhtbBrief = pathSegments.slice(-2).join("/") === "benchmarks/lhtb";
const isTimeMachine = pathSegments.slice(-2).join("/") === "lab/time-machine";
const TimeMachine = lazy(() => import("./time-machine/TimeMachine"));

createRoot(rootElement).render(
  <StrictMode>
    {isTimeMachine ? <Suspense fallback={<p role="status">Loading / 正在读取…</p>}><TimeMachine /></Suspense>
      : isSweMarathonBrief ? <SweMarathonBrief /> : isLhtbBrief ? <LhtbBrief /> : <App />}
  </StrictMode>,
);
