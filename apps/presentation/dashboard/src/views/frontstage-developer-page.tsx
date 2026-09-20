import {
  BadgeCheck,
  Braces,
  ClipboardCheck,
  Code2,
  ExternalLink,
  FileJson,
  GitCompareArrows,
  LayoutDashboard,
  ListChecks,
  ShieldCheck,
  TestTube2,
} from "lucide-react";

import { Badge } from "../components/ui/badge";

type CockpitPanelProps = {
  children: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
};

const contractFields = [
  {
    label: "status payload",
    source: "apps/presentation/dashboard/src/data/status.ts",
    detail: "status_contract, attention_queue, local_dashboard_api, run_history",
  },
  {
    label: "channel projection",
    source: "apps/presentation/dashboard/src/data/goal-channel-frontstage.ts",
    detail: "decision_frame, todos, gates, leases, artifacts, truth_contract",
  },
  {
    label: "public showcase catalog",
    source: "docs/showcases/showcase-catalog.json",
    detail: "case metadata, visual hints, evidence boundaries, story beats",
  },
  {
    label: "route smoke",
    source: "apps/presentation/dashboard/smoke/frontstage-route-smoke.ts",
    detail: "static route contract, source guards, component expectations",
  },
];

const projectionDiffRows = [
  {
    axis: "schema",
    current: "goal_channel_projection_v0",
    proposed: "new optional projection field",
    gate: "parser default plus route smoke assertion",
  },
  {
    axis: "truth",
    current: "event ledger and active state remain source of truth",
    proposed: "derived UI state only",
    gate: "truth_contract must stay read-only",
  },
  {
    axis: "privacy",
    current: "compact source refs and warnings",
    proposed: "public-safe fixture field",
    gate: "loopx check and browser fake-private fixture",
  },
  {
    axis: "interaction",
    current: "render, filter, select, inspect",
    proposed: "no browser write by default",
    gate: "write affordance requires explicit loopback capability",
  },
];

const fixtureRules = [
  {
    title: "Fixture sources",
    body: "Use examples/status.example.json, browser-smoke fixtures, and docs/showcases/showcase-catalog.json.",
  },
  {
    title: "Required proof",
    body: "Every projection addition needs parser coverage, route smoke assertions, and one browser or bundle check when UI output changes.",
  },
  {
    title: "Never include",
    body: "Raw task text, trajectories, transcripts, local paths, private registry state, credentials, or internal document links.",
  },
];

const smokeChecklist = [
  "npm --prefix apps/presentation/dashboard run smoke:frontstage-route",
  "npm --prefix apps/presentation/dashboard run smoke:frontstage-browser",
  "npm --prefix apps/presentation/dashboard run smoke:frontstage-share-bundle",
  "npm --prefix apps/presentation/dashboard run build",
  "loopx check --scan-path apps/presentation/dashboard --scan-path docs/status-data-contract.md",
];

const componentExamples = [
  {
    name: "Projection lane",
    badges: ["read-only", "schema v0"],
    body: "Summarizes one compact projection lane without mutating the underlying LoopX state.",
  },
  {
    name: "Capability badge",
    badges: ["loopback", "dry-run"],
    body: "Shows an advertised local capability only after the status feed declares it.",
  },
  {
    name: "Boundary warning",
    badges: ["public-safe", "omitted"],
    body: "Names omitted private material and points contributors back to compact source references.",
  },
];

function CockpitPanel({ children, icon: Icon, title }: CockpitPanelProps) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
          <Icon className="h-4 w-4 text-slate-500" />
          {title}
        </h2>
      </div>
      {children}
    </section>
  );
}

function ContractExplorer() {
  return (
    <CockpitPanel icon={Braces} title="Status Contract Explorer">
      <div className="grid gap-3 p-4 lg:grid-cols-2" data-testid="developer-contract-explorer">
        {contractFields.map((item) => (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3" key={item.label}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="info">{item.label}</Badge>
              <Badge variant="neutral">public contract</Badge>
            </div>
            <div className="mt-2 break-words font-mono text-xs text-slate-700">{item.source}</div>
            <p className="mt-2 text-sm leading-6 text-slate-600">{item.detail}</p>
          </div>
        ))}
      </div>
    </CockpitPanel>
  );
}

function ProjectionDiffing() {
  return (
    <CockpitPanel icon={GitCompareArrows} title="Projection Diffing">
      <div className="overflow-x-auto" data-testid="developer-projection-diffing">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs uppercase tracking-normal text-slate-500">
              <th className="border-b border-slate-200 px-4 py-3 font-semibold">Axis</th>
              <th className="border-b border-slate-200 px-4 py-3 font-semibold">Current</th>
              <th className="border-b border-slate-200 px-4 py-3 font-semibold">Proposed</th>
              <th className="border-b border-slate-200 px-4 py-3 font-semibold">Gate</th>
            </tr>
          </thead>
          <tbody>
            {projectionDiffRows.map((row) => (
              <tr className="align-top" key={row.axis}>
                <td className="border-b border-slate-100 px-4 py-3 font-semibold text-slate-950">{row.axis}</td>
                <td className="border-b border-slate-100 px-4 py-3 text-slate-600">{row.current}</td>
                <td className="border-b border-slate-100 px-4 py-3 text-slate-600">{row.proposed}</td>
                <td className="border-b border-slate-100 px-4 py-3 text-slate-600">{row.gate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CockpitPanel>
  );
}

function FixtureGeneration() {
  return (
    <CockpitPanel icon={FileJson} title="Fixture Generation">
      <div className="grid gap-3 p-4 md:grid-cols-3" data-testid="developer-fixture-generation">
        {fixtureRules.map((rule) => (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3" key={rule.title}>
            <div className="text-sm font-semibold text-slate-950">{rule.title}</div>
            <p className="mt-2 text-sm leading-6 text-slate-600">{rule.body}</p>
          </div>
        ))}
      </div>
    </CockpitPanel>
  );
}

function SmokeChecklist() {
  return (
    <CockpitPanel icon={ClipboardCheck} title="Smoke Checklist">
      <div className="space-y-2 p-4" data-testid="developer-smoke-checklist">
        {smokeChecklist.map((command) => (
          <div className="flex items-start gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2" key={command}>
            <BadgeCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
            <code className="break-all text-xs font-semibold leading-5 text-slate-700">{command}</code>
          </div>
        ))}
      </div>
    </CockpitPanel>
  );
}

function ComponentExamples() {
  return (
    <CockpitPanel icon={Code2} title="Component Examples">
      <div className="grid gap-3 p-4 lg:grid-cols-3" data-testid="developer-component-examples">
        {componentExamples.map((example) => (
          <article className="rounded-md border border-slate-200 bg-slate-50 p-3" key={example.name}>
            <div className="flex flex-wrap gap-2">
              {example.badges.map((badge) => (
                <Badge key={badge} variant={badge === "read-only" || badge === "public-safe" ? "success" : "neutral"}>
                  {badge}
                </Badge>
              ))}
            </div>
            <h3 className="mt-3 text-sm font-semibold leading-6 text-slate-950">{example.name}</h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">{example.body}</p>
          </article>
        ))}
      </div>
    </CockpitPanel>
  );
}

export function FrontstageDeveloperPage() {
  return (
    <main className="min-h-screen bg-[#f7f7f4] px-4 py-4 text-slate-950 sm:px-5" data-testid="frontstage-developer-cockpit">
      <div className="mx-auto grid max-w-[1500px] gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm xl:sticky xl:top-4 xl:self-start">
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-slate-950 text-white">
              <TestTube2 className="h-4 w-4" />
            </div>
            <div>
              <div className="text-sm font-semibold">Developer Cockpit</div>
              <div className="text-xs text-slate-500">Projection extension</div>
            </div>
          </div>
          <div className="mt-4 grid gap-2">
            <a className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm font-medium" href="https://loopx-project.github.io/loopx/">
              <LayoutDashboard className="h-4 w-4" />
              LoopX home
            </a>
            <a className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm font-medium" href="https://loopx-project.github.io/loopx/docs/showcases/index.en.html">
              <ExternalLink className="h-4 w-4" />
              Public cases
            </a>
            <a className="flex items-center gap-2 rounded-md bg-slate-950 px-3 py-2 text-sm font-medium text-white" href={`${import.meta.env.BASE_URL}developers/projections/`}>
              <Code2 className="h-4 w-4" />
              Developer cockpit
            </a>
          </div>
          <div className="mt-5 space-y-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-950">
            <div className="flex flex-wrap gap-2">
              <Badge variant="success">read-only</Badge>
              <Badge variant="neutral">public fixtures</Badge>
            </div>
            <p>
              This route uses static public contracts only; live status feeds, registry files, and browser write APIs stay outside the cockpit.
            </p>
          </div>
        </aside>

        <section className="space-y-4">
          <div className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="info">developers/projections</Badge>
                  <Badge variant="success">public-safe</Badge>
                  <Badge variant="neutral">no browser writes</Badge>
                </div>
                <h1 className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
                  LoopX Projection Developer Cockpit
                </h1>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                  A read-only contributor workbench for adding dashboard/frontstage projections without reverse-engineering the large operator page.
                </p>
              </div>
              <div className="grid min-w-[260px] gap-2 text-sm">
                <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className="text-[11px] font-semibold uppercase tracking-normal text-slate-500">Source of truth</div>
                  <div className="mt-1 font-semibold text-slate-950">status contract + compact fixtures</div>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className="text-[11px] font-semibold uppercase tracking-normal text-slate-500">Boundary</div>
                  <div className="mt-1 font-semibold text-slate-950">read-only extension surface</div>
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ContractExplorer />
            <ProjectionDiffing />
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <FixtureGeneration />
            <SmokeChecklist />
          </div>
          <ComponentExamples />

          <CockpitPanel icon={ShieldCheck} title="Extension Boundary">
            <div className="grid gap-3 p-4 md:grid-cols-3" data-testid="developer-extension-boundary">
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-3">
                <div className="text-sm font-semibold text-slate-950">Allowed</div>
                <p className="mt-2 text-sm leading-6 text-slate-600">Versioned parser defaults, public fixtures, read-only route panels, and focused smoke assertions.</p>
              </div>
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-3">
                <div className="text-sm font-semibold text-slate-950">Review</div>
                <p className="mt-2 text-sm leading-6 text-slate-600">New dashboard dependencies, status contract fields, loopback capability display, and browser-visible workflows.</p>
              </div>
              <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-3">
                <div className="text-sm font-semibold text-slate-950">Stop</div>
                <p className="mt-2 text-sm leading-6 text-slate-600">Credentials, private registry material, raw logs, transcripts, production actions, or default browser write authority.</p>
              </div>
            </div>
          </CockpitPanel>
        </section>
      </div>
    </main>
  );
}
