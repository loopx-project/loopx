// Run on the fully assembled Pages artifact, after MkDocs and case restoration.
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const directory = resolve(process.argv[2] ?? "output/frontstage-pages/site");
const id = process.env.LOOPX_GA_MEASUREMENT_ID?.trim() ?? "";
if (id && !/^G-[A-Z0-9]+$/.test(id)) throw new Error("LOOPX_GA_MEASUREMENT_ID must be a GA4 G- measurement ID");
const marker = /(?:<script\b[^>]*data-loopx-analytics[^>]*><\/script>|<link\b[^>]*data-loopx-analytics[^>]*>)\s*/g;
let count = 0;
async function visit(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const file = resolve(dir, entry.name);
    if (entry.isDirectory()) { await visit(file); continue; }
    if (!entry.name.endsWith(".html")) continue;
    const source = await readFile(file, "utf8");
    let html = source.replace(marker, "");
    const canonical = html.match(/<link\s+rel="canonical"\s+href="([^"]+)"/i)?.[1];
    if (id && canonical?.startsWith("https://loopx-project.github.io/loopx/") &&
        !/<meta[^>]+(?:noindex|http-equiv="refresh")/i.test(html)) {
      html = html.replace("</head>", `<link rel="stylesheet" data-loopx-analytics href="/loopx/site-assets/analytics-consent.css">\n<script defer data-loopx-analytics data-measurement-id="${id}" src="/loopx/site-assets/analytics.js"></script>\n</head>`);
      count++;
    }
    if (html !== source) await writeFile(file, html);
  }
}
await visit(directory);
for (const [source, name] of [["analytics-client.js", "analytics.js"], ["analytics-consent.css", "analytics-consent.css"]]) {
  const target = resolve(directory, "site-assets", name);
  if (count) {
    await mkdir(resolve(directory, "site-assets"), { recursive: true });
    await copyFile(new URL(source, import.meta.url), target);
  } else await rm(target, { force: true });
}
console.log(id ? `GA4 configured on ${count} canonical public pages` : "GA4 disabled: measurement ID is unset");
