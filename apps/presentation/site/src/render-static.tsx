import { renderToString } from "react-dom/server";
import { App } from "./App";
import { LhtbBrief } from "./LhtbBrief";
import { SweMarathonBrief } from "./SweMarathonBrief";
import { pageMetadata, siteUrl, type PublicPage } from "./page-metadata";

export { pageMetadata, siteUrl };

export function render(page: PublicPage) {
  return renderToString(page === "home" ? <App /> : page === "lhtb" ? <LhtbBrief /> : <SweMarathonBrief />);
}
