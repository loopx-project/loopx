// Generate the same public React content at build time; no live state or browser.
import { build } from "vite";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const root = fileURLToPath(new URL("../", import.meta.url));
const { values } = parseArgs({ options: {
  base: { type: "string", default: process.env.LOOPX_SITE_BASE ?? "/" },
  outDir: { type: "string", default: "dist" },
} });
const outDir = resolve(root, values.outDir);
const options = { root, base: values.base };
await build({ ...options, build: { outDir, emptyOutDir: true } });
// Keep temporary SSR dependencies resolvable, but outside the published output.
const cache = resolve(root, "node_modules/.cache");
await mkdir(cache, { recursive: true });
const serverDir = await mkdtemp(resolve(cache, "public-site-ssr-"));
const escape = (value) => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
try {
  await build({ ...options, publicDir: false, build: {
    ssr: resolve(root, "src/render-static.tsx"), outDir: serverDir, emptyOutDir: true,
  } });
  const { render, pageMetadata, siteUrl } = await import(pathToFileURL(resolve(serverDir, "render-static.js")));
  const template = await readFile(resolve(outDir, "index.html"), "utf8");
  for (const [key, page] of Object.entries(pageMetadata)) {
    const canonical = new URL(page.path, siteUrl).href;
    const { title, description } = page.en;
    const head = `
    <title>${escape(title)}</title>
    <meta name="description" content="${escape(description)}">
    <link rel="canonical" href="${canonical}">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="LoopX">
    <meta property="og:title" content="${escape(title)}">
    <meta property="og:description" content="${escape(description)}">
    <meta property="og:url" content="${canonical}">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="${escape(title)}">
    <meta name="twitter:description" content="${escape(description)}">
    ${key === "home" ? `<script type="application/ld+json">${JSON.stringify({
      "@context": "https://schema.org", "@type": "SoftwareSourceCode", name: "LoopX",
      description, url: siteUrl, codeRepository: "https://github.com/loopx-project/loopx",
      license: "https://www.apache.org/licenses/LICENSE-2.0",
    })}</script>` : ""}
`;
    const html = template
      .replace(/<title>[\s\S]*?<\/title>\s*/g, "")
      .replace(/<meta\s+(?:name="description"|property="og:[^"]+")[\s\S]*?>\s*/g, "")
      .replace("</head>", `${head}</head>`)
      .replace('<div id="root"></div>', () => `<div id="root">${render(key)}</div>`);
    const target = resolve(outDir, page.path, "index.html");
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, html);
  }
  // Discover static editorial pages from the built site, including both locales.
  // Do not include operator shells, redirects or URLs absent from this build.
  const urls = new Set();
  async function collect(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = resolve(directory, entry.name);
      if (entry.isDirectory()) await collect(file);
      else if (entry.name.endsWith(".html")) {
        const html = await readFile(file, "utf8");
        const canonical = html.match(/<link\s+rel="canonical"\s+href="([^"]+)"/i)?.[1];
        if (canonical?.startsWith(siteUrl) && !/<meta[^>]+(?:noindex|http-equiv="refresh")/i.test(html)) urls.add(canonical);
      }
    }
  }
  await collect(outDir);
  await writeFile(resolve(outDir, "sitemap-pages.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${[...urls].sort().map((url) => `  <url><loc>${escape(url)}</loc></url>`).join("\n")}\n</urlset>\n`);
  // MkDocs supplies the three documentation sitemaps in the Pages workflow.
  const maps = ["sitemap-pages.xml", "docs/sitemap.xml", "docs/book/sitemap.xml", "docs/book/en/sitemap.xml"];
  await writeFile(resolve(outDir, "sitemap.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${maps.map((path) => `  <sitemap><loc>${siteUrl}${path}</loc></sitemap>`).join("\n")}\n</sitemapindex>\n`);
} finally {
  await rm(serverDir, { recursive: true, force: true });
}
