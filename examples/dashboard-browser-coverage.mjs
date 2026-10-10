// Record the existing Chromium interaction smoke against Vite's original sources.
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { relative, resolve, sep } from "node:path";

const require = createRequire(import.meta.url);

export async function writeDashboardBrowserCoverage(entries, { repoRoot, dashboardDir, outputDir }) {
  const v8ToIstanbul = require("v8-to-istanbul");
  const { createCoverageMap } = require("istanbul-lib-coverage");
  const { createContext } = require("istanbul-lib-report");
  const reports = require("istanbul-reports");
  const coverage = createCoverageMap({});
  for (const entry of entries) {
    const pathname = new URL(entry.url).pathname;
    if (!pathname.startsWith("/src/") || !entry.source) continue;
    const sourcePath = resolve(dashboardDir, `.${pathname}`);
    const converter = v8ToIstanbul(sourcePath, 0, { source: entry.source });
    await converter.load();
    converter.applyCoverage(entry.functions);
    const mapped = converter.toIstanbul();
    for (const [path, fileCoverage] of Object.entries(mapped)) {
      const localPath = relative(repoRoot, path).split(sep).join("/");
      if (localPath.startsWith("apps/presentation/dashboard/src/")) {
        coverage.addFileCoverage({ ...fileCoverage, path: localPath });
      }
    }
  }
  if (coverage.files().length === 0) throw new Error("Browser smoke produced no mapped dashboard coverage");
  await mkdir(outputDir, {recursive: true});
  await writeFile(resolve(outputDir, "browser-coverage.json"), JSON.stringify(coverage.toJSON()), "utf8");
  reports.create("lcovonly", { file: "browser-lcov.info" }).execute(createContext({ dir: outputDir, coverageMap: coverage }));
}
