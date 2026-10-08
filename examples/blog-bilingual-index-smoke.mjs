#!/usr/bin/env node
// Fast source-level contract test for the bilingual static Blog catalog.

import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deepStrictEqual } from "node:assert/strict";
import {
  comparePublicationDates,
  sortBlogIndex,
} from "../apps/presentation/site/public/blog/blog-index.js";

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

function hasAnchorToHref(html, href, sourceHref) {
  const expectedUrl = new URL(href, sourceHref).href;
  return [...html.matchAll(/<a\b[^>]*\bhref="([^"]+)"[^>]*>/g)].some(([, linkHref]) => {
    try {
      return new URL(linkHref, sourceHref).href === expectedUrl;
    } catch {
      return false;
    }
  });
}

function sectionIds(html) {
  return [...html.matchAll(/<section\s+id="([^"]+)"/g)].map((match) => match[1]);
}

function listingDates(html, language, articleSlugs) {
  const dates = new Map();
  for (const [, listing] of html.matchAll(/<article class="post-listing">([\s\S]*?)<\/article>/g)) {
    const slug = listing.match(/<h2><a href="([^"/]+)\/"/)?.[1];
    const date = listing.match(/<p class="listing-date"><time datetime="([^"]+)"/)?.[1];
    if (!slug || dates.has(slug) || !date || !/^\d{4}-\d{2}(?:-\d{2})?$/.test(date)) {
      throw new Error(`Blog listing must have one slug and an ISO publication date: ${language}/${slug}`);
    }
    const completeDate = date.length === 7 ? `${date}-01` : date;
    const parsed = new Date(`${completeDate}T00:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== completeDate) {
      throw new Error(`Blog listing has an invalid publication date: ${language}/${slug}: ${date}`);
    }
    dates.set(slug, date);
  }
  deepStrictEqual([...dates.keys()].sort(), articleSlugs, `Every ${language} article must appear once in the catalog`);
  return dates;
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

  const datesByLocale = [];
  for (const locale of locales) {
    const indexHtml = await readFile(resolve(locale.directory, "index.html"), "utf8");
    assertIncludes(indexHtml, `<html lang="${locale.language}">`, `Blog index language drifted: ${locale.directory}`);
    const scriptHref = locale.language === "en" ? "../blog/blog-index.js" : "../../blog/blog-index.js";
    assertIncludes(indexHtml, `<script type="module" src="${scriptHref}"></script>`, `Blog index must load automatic sorting: ${locale.language}`);
    const listingDateBySlug = listingDates(indexHtml, locale.language, englishSlugs);
    datesByLocale.push(listingDateBySlug);
    // JavaScript only refines the catalog: the shipped HTML must already list
    // posts newest first, so the module never has to move a reader's node.
    const htmlOrder = [...listingDateBySlug.keys()];
    deepStrictEqual(
      htmlOrder,
      [...htmlOrder].sort((left, right) =>
        comparePublicationDates(listingDateBySlug.get(left), listingDateBySlug.get(right)),
      ),
      `Blog catalog must ship ${locale.language} posts newest first`,
    );
    for (const slug of englishSlugs) {
      assertIncludes(indexHtml, `href="${slug}/"`, `Blog index must link every ${locale.language} article: ${slug}`);
      const articleHtml = await readFile(resolve(locale.directory, slug, "index.html"), "utf8");
      assertIncludes(articleHtml, `<html lang="${locale.language}">`, `Blog article language drifted: ${slug}`);
      assertIncludes(articleHtml, "<h1>", `Blog article must contain a visible title: ${slug}`);
      assertIncludes(articleHtml, `rel="canonical" href="${locale.canonicalHref(slug)}"`, `Blog canonical URL drifted: ${slug}`);
      if (!hasAnchorToHref(articleHtml, locale.counterpartHref(slug), locale.canonicalHref(slug))) {
        throw new Error(`Blog article must link its paired edition: ${slug}`);
      }
      for (const hreflang of ["en", "zh-CN", "x-default"]) {
        assertIncludes(articleHtml, `hreflang="${hreflang}"`, `Blog article is missing ${hreflang}: ${slug}`);
      }
    }
  }

  for (const slug of englishSlugs) {
    deepStrictEqual(datesByLocale[0].get(slug), datesByLocale[1].get(slug), `Paired Blog publication dates must match: ${slug}`);
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

// Minimal element model for the sorting module. It keeps only the two browser
// facts that module depends on: children are read in live order, and detaching a
// card blurs whatever was focused inside it. The packaged page is checked in a
// real browser during release qualification.
function fakeBlogIndex(order, { focusedSlug = null } = {}) {
  const index = { children: [], moves: 0, activeElement: null };
  const note = { kind: "index-note" };
  const cards = new Map(
    order.map(([slug, date]) => {
      const link = {
        slug,
        focusOptions: null,
        focus(options) {
          link.focusOptions = options;
          index.activeElement = link;
        },
      };
      const time = { getAttribute: (name) => (name === "datetime" ? date : null) };
      return [
        slug,
        {
          slug,
          link,
          querySelector: (selector) =>
            selector === ".listing-date time[datetime]" ? time : null,
          contains: (candidate) => candidate === link,
        },
      ];
    }),
  );
  index.children = [...order.map(([slug]) => cards.get(slug)), note];
  index.activeElement = focusedSlug ? cards.get(focusedSlug).link : null;
  index.querySelectorAll = (selector) =>
    selector === ":scope > .post-listing"
      ? index.children.filter((child) => child !== note)
      : [];
  index.querySelector = (selector) =>
    selector === ":scope > .index-note" ? note : null;
  index.insertBefore = (node, reference) => {
    index.moves += 1;
    const previous = index.children.indexOf(node);
    if (previous >= 0) index.children.splice(previous, 1);
    index.children.splice(index.children.indexOf(reference), 0, node);
    if (node.contains(index.activeElement)) index.activeElement = null;
  };
  index.visibleOrder = () =>
    index.children.filter((child) => child !== note).map((child) => child.slug);
  return index;
}

const modulePath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === modulePath) {
  // Independent expectations for unsorted input, partial dates, ties and undated posts.
  const dates = ["2026-09", "", "2026-09-15", "2026-10-02", "2026-09-26"];
  deepStrictEqual(dates.sort(comparePublicationDates), ["2026-10-02", "2026-09-26", "2026-09-15", "2026-09", ""]);
  deepStrictEqual(comparePublicationDates("2026-10-02", "2026-10-02"), 0);
  const englishArticle = "https://loopx-project.github.io/loopx/blog/example/";
  const relativeChineseArticle = '<a href="../zh/example/" lang="zh-CN">中文</a>';
  deepStrictEqual(
    hasAnchorToHref(relativeChineseArticle, "../../blog/zh/example/", englishArticle),
    true,
    "Equivalent relative paths must resolve to the paired edition",
  );
  deepStrictEqual(
    hasAnchorToHref('<a href="../zh/another/">中文</a>', "../../blog/zh/example/", englishArticle),
    false,
    "A link to a different article must not satisfy the paired-edition check",
  );
  // A module response that arrives after keyboard navigation started must not
  // move an already ordered catalog or drop the focused article link.
  const ordered = fakeBlogIndex(
    [["newest", "2026-10-02"], ["older", "2026-09-26"], ["oldest", "2026-09"]],
    { focusedSlug: "newest" },
  );
  const orderedTarget = ordered.activeElement;
  sortBlogIndex(ordered, { activeElement: ordered.activeElement });
  deepStrictEqual(ordered.visibleOrder(), ["newest", "older", "oldest"]);
  deepStrictEqual(ordered.moves, 0, "An already ordered catalog must not move any card");
  deepStrictEqual([ordered.activeElement, orderedTarget.focusOptions], [orderedTarget, null]);
  // A real reorder rewrites the DOM but keeps the reader on the same link.
  const shuffled = fakeBlogIndex(
    [["oldest", "2026-09"], ["newest", "2026-10-02"], ["older", "2026-09-26"]],
    { focusedSlug: "oldest" },
  );
  const shuffledTarget = shuffled.activeElement;
  sortBlogIndex(shuffled, { activeElement: shuffled.activeElement });
  deepStrictEqual(shuffled.visibleOrder(), ["newest", "older", "oldest"]);
  deepStrictEqual([shuffled.activeElement, shuffledTarget.focusOptions], [shuffledTarget, { preventScroll: true }]);
  // Without a focused card the module still reorders the marked-up catalog.
  const unfocused = fakeBlogIndex([["older", "2026-09-26"], ["newest", "2026-10-02"]]);
  sortBlogIndex(unfocused);
  deepStrictEqual(unfocused.visibleOrder(), ["newest", "older"]);
  const repoRoot = resolve(dirname(modulePath), "..");
  const blogDir = resolve(repoRoot, "apps/presentation/site/public/blog");
  const { articleSlugs } = await validateBilingualBlog(blogDir);
  console.log(`blog-bilingual-index-smoke: ok (${articleSlugs.length} paired articles)`);
}
