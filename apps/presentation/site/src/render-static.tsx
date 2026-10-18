import { renderToString } from "react-dom/server";
import { App } from "./App";
import { LhtbBrief } from "./LhtbBrief";
import { SweMarathonBrief } from "./SweMarathonBrief";
import TimeMachine from "./time-machine/TimeMachine";
import DelegationLab from "./delegation-lab/DelegationLab";
import { pageMetadata, siteUrl, type PublicPage } from "./page-metadata";

export { pageMetadata, siteUrl };

export function render(page: PublicPage) {
  return renderToString(page === "delegationLab" ? <DelegationLab /> : page === "timeMachine" ? <TimeMachine /> : page === "home" ? <App /> : page === "lhtb" ? <LhtbBrief /> : <SweMarathonBrief />);
}
