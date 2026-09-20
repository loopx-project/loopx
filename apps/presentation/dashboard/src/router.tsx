import {
  Navigate,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { z } from "zod";

import { DashboardPage } from "./views/dashboard-page";
import { FrontstageDeveloperPage } from "./views/frontstage-developer-page";
import { useEffect } from "react";
import { resolveLocalStatusUrl } from "./data/local-status-query";
import { BenchmarkStudyPage } from "./views/benchmark-study-page";

const searchSchema = z.object({
  goalId: z.string().optional().default(""),
  statusUrl: z.string().optional().default(""),
});

const frontstageSearchSchema = z.object({
  goalId: z.string().optional().default(""),
  mode: z.enum(["showcase", "developer", "ops"]).optional().default("showcase"),
  statusUrl: z.string().optional().default(""),
  todoLane: z.enum(["all", "user", "agent"]).optional().default("all"),
  todoQuery: z.string().optional().default(""),
});

const deprecatedFrontstageOpsSearchSchema = frontstageSearchSchema.omit({ mode: true });

const benchmarkStudySearchSchema = z.object({
  dashboardUrl: z.string().optional().default(""),
  view: z.enum(["campaign", "arms", "cases", "runs"]).optional().default("campaign"),
  runId: z.string().optional().default(""),
});

// Bookmarks remain valid, but the retired boards no longer ship a second UI.
function PublicCasesRedirect() {
  const target = "https://loopx-project.github.io/loopx/docs/showcases/index.en.html";
  useEffect(() => { window.location.replace(target); }, []);
  return <a href={target}>Open LoopX cases / 浏览案例</a>;
}

function WorkspaceRedirect({ goalId, statusUrl }: { goalId: string; statusUrl: string }) {
  const resolved = statusUrl ? resolveLocalStatusUrl(statusUrl, window.location.href) : null;
  if (resolved?.error) return <main role="alert">{resolved.error}</main>;
  return <Navigate replace to="/" search={{ goalId, statusUrl }} />;
}

function FrontstageRoutePage() {
  const search = frontstageRoute.useSearch();
  if (search.mode === "ops") return <WorkspaceRedirect {...search} />;
  if (search.mode === "developer") return <Navigate replace to="/developers/projections" />;
  return <PublicCasesRedirect />;
}

function DeprecatedFrontstageOpsRoutePage() {
  return <WorkspaceRedirect {...deprecatedFrontstageOpsRoute.useSearch()} />;
}

export const rootRoute = createRootRoute({
  component: () => <Outlet />,
  errorComponent: () => <main role="alert" className="p-8">
    <h1>页面暂时无法显示 / Page unavailable</h1>
    <p>请重新加载页面；这不会执行任务。 / Reloading does not execute tasks.</p>
    <button type="button" onClick={() => window.location.reload()}>重新加载 / Reload</button>
  </main>,
});

export const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: (search) => searchSchema.parse(search),
  component: DashboardPage,
});

export const frontstageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/frontstage",
  validateSearch: (search) => frontstageSearchSchema.parse(search),
  component: FrontstageRoutePage,
});

export const deprecatedFrontstageOpsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/deprecated/frontstage/ops",
  validateSearch: (search) => deprecatedFrontstageOpsSearchSchema.parse(search),
  component: DeprecatedFrontstageOpsRoutePage,
});

export const frontstageDeveloperRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/frontstage/developer",
  component: () => <Navigate replace to="/developers/projections" />,
});

export const projectionDeveloperRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developers/projections",
  component: FrontstageDeveloperPage,
});

export const benchmarkStudyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/benchmarks/study",
  validateSearch: (search) => benchmarkStudySearchSchema.parse(search),
  component: BenchmarkStudyPage,
});

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  frontstageRoute,
  deprecatedFrontstageOpsRoute,
  frontstageDeveloperRoute,
  projectionDeveloperRoute,
  benchmarkStudyRoute,
]);

function routerBasepathFromViteBase(baseUrl: string) {
  if (!baseUrl || baseUrl === "/" || baseUrl === "./") {
    return "/";
  }
  const withLeadingSlash = baseUrl.startsWith("/") ? baseUrl : `/${baseUrl}`;
  return withLeadingSlash.replace(/\/+$/, "") || "/";
}

export const router = createRouter({
  routeTree,
  basepath: routerBasepathFromViteBase(import.meta.env.BASE_URL),
  trailingSlash: "preserve",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
