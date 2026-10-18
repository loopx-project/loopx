import {
  Navigate,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { z } from "zod";

import { DashboardPage } from "./views/dashboard-page";
import { Suspense, lazy, useEffect } from "react";
import { resolveLocalStatusUrl } from "./data/local-status-query";

// Secondary routes load on demand. The workspace is what every visit opens;
// contributor projections, benchmark studies and answer reports arrive as
// their own chunks only when such a route is entered.
const FrontstageDeveloperPage = lazy(() => import("./views/frontstage-developer-page").then((module) => ({ default: module.FrontstageDeveloperPage })));
const BenchmarkStudyPage = lazy(() => import("./views/benchmark-study-page").then((module) => ({ default: module.BenchmarkStudyPage })));
const AnswerReportPage = lazy(() => import("./features/personal-workspace/answer-report-page").then((module) => ({ default: module.AnswerReportPage })));

function RouteLoading() {
  return <main className="p-8" role="status">加载中… / Loading…</main>;
}

const searchSchema = z.object({
  goalId: z.string().optional().default(""),
  // Absence alone opens all Goals. Preserve present decoded values, including
  // empty strings/arrays, so malformed scope cannot become an unscoped visit.
  // Numeric opaque IDs still survive the router's JSON decoding.
  rootGoalId: z.unknown().transform(value => typeof value === "string" && value
    ? value : JSON.stringify(value)).optional(),
  // Preserve the existing history/analysis owners' query state. They validate
  // it from the URL themselves; router decoding can produce arrays or numbers.
  research_lens: z.unknown().optional(),
  research_goal: z.unknown().optional(),
  research_node: z.unknown().optional(),
  research_group: z.unknown().optional(),
  research_pack: z.unknown().optional(),
  research_analysis: z.unknown().optional(),
  research_analysis_goal: z.unknown().optional(),
  // Stable Goal/Agent working conversation; empty string opens the directory.
  workAgent: z.string().regex(/^[A-Za-z0-9._:-]{0,200}$/).optional().catch(undefined),
  todo: z.string().regex(/^[A-Za-z0-9._:-]{1,200}$/).optional().catch(undefined),
  // Scopes the steward conversation to a host-granted workspace; selecting a Goal clears it.
  workspace: z.string().regex(/^[A-Za-z0-9]{1,80}$/).optional().catch(undefined),
  statusUrl: z.string().optional().default(""),
  view: z.enum(["conversation", "overview", "tasks", "files"]).optional(),
  reportSessionId: z.string().regex(/^[A-Za-z0-9._-]{1,160}$/).optional(),
  reportMessageId: z.string().regex(/^[A-Za-z0-9._-]{1,160}$/).optional(),
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
  caseId: z.string().optional().default(""),
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
  component: () => {
    const search = dashboardRoute.useSearch();
    return search.reportSessionId && search.reportMessageId
      ? <Suspense fallback={<RouteLoading />}><AnswerReportPage sessionId={search.reportSessionId} messageId={search.reportMessageId} statusUrl={search.statusUrl}/></Suspense>
      : <DashboardPage/>;
  },
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
  component: () => <Suspense fallback={<RouteLoading />}><FrontstageDeveloperPage /></Suspense>,
});

export const benchmarkStudyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/benchmarks/study",
  validateSearch: (search) => benchmarkStudySearchSchema.parse(search),
  component: () => <Suspense fallback={<RouteLoading />}><BenchmarkStudyPage /></Suspense>,
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
