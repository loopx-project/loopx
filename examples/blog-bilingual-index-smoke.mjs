#!/usr/bin/env node
// Fast source-level contract test for the bilingual static Blog catalog.

import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

async function collectArticleSlugs(localeDir, { exclude = [] } = {}) {
  const entries = await readdir(localeDir, { withFileTypes: true });
  return entries
    .filter(
      (entry) =>
        entry.isDirectory() &&
        !exclude.includes(entry.name) &&
        existsSync(resolve(localeDir, entry.name, "index.html")),
    )
    .map((entry) => entry.name)
    .sort();
}

function assertIncludes(html, value, message) {
  if (!html.includes(value)) throw new Error(message);
}

function sectionIds(html) {
  return [...html.matchAll(/<section\s+id="([^"]+)"/g)].map((match) => match[1]);
}

export async function validateBilingualBlog(blogDir) {
  const englishSlugs = await collectArticleSlugs(blogDir, { exclude: ["zh"] });
  const chineseSlugs = await collectArticleSlugs(resolve(blogDir, "zh"));
  if (JSON.stringify(englishSlugs) !== JSON.stringify(chineseSlugs)) {
    throw new Error(
      `Every Blog article must ship paired English and Chinese editions: en=${englishSlugs.join(",")} zh=${chineseSlugs.join(",")}`,
    );
  }

  const locales = [
    {
      directory: blogDir,
      language: "en",
      counterpartHref: (slug) => `../../blog/zh/${slug}/`,
      canonicalHref: (slug) => `https://loopx-project.github.io/loopx/blog/${slug}/`,
    },
    {
      directory: resolve(blogDir, "zh"),
      language: "zh-CN",
      counterpartHref: (slug) => `../../../blog/${slug}/`,
      canonicalHref: (slug) => `https://loopx-project.github.io/loopx/blog/zh/${slug}/`,
    },
  ];

  for (const locale of locales) {
    const indexHtml = await readFile(resolve(locale.directory, "index.html"), "utf8");
    assertIncludes(indexHtml, `<html lang="${locale.language}">`, `Blog index language drifted: ${locale.directory}`);
    for (const slug of englishSlugs) {
      assertIncludes(indexHtml, `href="${slug}/"`, `Blog index must link every ${locale.language} article: ${slug}`);
      const articleHtml = await readFile(resolve(locale.directory, slug, "index.html"), "utf8");
      assertIncludes(articleHtml, `<html lang="${locale.language}">`, `Blog article language drifted: ${slug}`);
      assertIncludes(articleHtml, "<h1>", `Blog article must contain a visible title: ${slug}`);
      assertIncludes(articleHtml, `rel="canonical" href="${locale.canonicalHref(slug)}"`, `Blog canonical URL drifted: ${slug}`);
      assertIncludes(articleHtml, `href="${locale.counterpartHref(slug)}"`, `Blog article must link its paired edition: ${slug}`);
      for (const hreflang of ["en", "zh-CN", "x-default"]) {
        assertIncludes(articleHtml, `hreflang="${hreflang}"`, `Blog article is missing ${hreflang}: ${slug}`);
      }
    }
  }

  for (const slug of englishSlugs) {
    const englishHtml = await readFile(resolve(blogDir, slug, "index.html"), "utf8");
    const chineseHtml = await readFile(resolve(blogDir, "zh", slug, "index.html"), "utf8");
    if (JSON.stringify(sectionIds(englishHtml)) !== JSON.stringify(sectionIds(chineseHtml))) {
      throw new Error(`Paired Blog article sections must match: ${slug}`);
    }
  }

  return { articleSlugs: englishSlugs };
}

const modulePath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === modulePath) {
  const repoRoot = resolve(dirname(modulePath), "..");
  const blogDir = resolve(repoRoot, "apps/presentation/site/public/blog");
  const { articleSlugs } = await validateBilingualBlog(blogDir);
  console.log(`blog-bilingual-index-smoke: ok (${articleSlugs.length} paired articles)`);
}
