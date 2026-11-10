#!/usr/bin/env node
// Fast source-level contract test for the bilingual static Blog catalog.

import { existsSync } from "node:fs";
import { cp, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deepStrictEqual, rejects } from "node:assert/strict";
import { tmpdir } from "node:os";
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
  const anchors = html.matchAll(/<a(?=[\s/>])((?:[^>"']|"[^"]*"|'[^']*')*)>/gi);
  for (const [, attributes] of anchors) {
    const parsedAttributes = attributes.matchAll(
      /\s+([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g,
    );
    let linkHref = null;
    for (const [, name, doubleQuoted, singleQuoted, unquoted] of parsedAttributes) {
      if (name.toLowerCase() === "href") {
        linkHref = doubleQuoted ?? singleQuoted ?? unquoted ?? null;
        break;
      }
    }
    if (linkHref === null) continue;
    try {
      if (new URL(linkHref, sourceHref).href === expectedUrl) return true;
    } catch {
      // Invalid URLs cannot satisfy the paired-edition navigation requirement.
    }
  }
  return false;
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
  const auditPath = resolve(blogDir, "data/long-horizon-audit.json");
  if (existsSync(auditPath)) {
    const audit = JSON.parse(await readFile(auditPath, "utf8"));
    const tasks = audit.context.tasks;
    const sum = key => tasks.reduce((total, task) => total + task[key], 0);
    deepStrictEqual([tasks.length, sum("wakes"), sum("baseline_wakes"), sum("delta_wakes")],
      [8, 213, 101, 112], "Publication keeps its two wake cohorts separate");
    deepStrictEqual([sum("full_bytes"), sum("progressive_bytes"), sum("delta_bytes")],
      [22408690, 7285951, 4488335], "Totals are independently reconciled against published per-task rows");
    for (const row of audit.stride.transitions) {
      deepStrictEqual(row.improved + row.flat + row.regressed + row.unreconciled, row.total,
        "Missing transition outcomes cannot silently become flat or regressed");
    }
    deepStrictEqual(audit.stride.latest.scored_transitions, 17);
    for (const locale of ["", "zh/"]) {
      const article = await readFile(resolve(blogDir, `${locale}context-delivery/index.html`), "utf8");
      const table = article.split('<h3 id="multitask-context-byte-ratios">')[1]?.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1];
      const rows = [...(table ?? "").matchAll(/<tr>([\s\S]*?)<\/tr>/g)];
      deepStrictEqual(rows.length, tasks.length + 1, "Published table keeps all task rows and its total");
      for (const [index, task] of tasks.entries()) {
        const cells = [...rows[index][1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)]
          .map(cell => cell[1].replace(/<[^>]*>/g, ""));
        const firstNumber = cell => Number(cell.match(/[\d,]+/)?.[0].replaceAll(",", ""));
        deepStrictEqual(cells.slice(2, 5).map(firstNumber),
          [task.full_bytes, task.progressive_bytes, task.delta_bytes],
          "Each published task row agrees with the source cells");
      }
      assertIncludes(article, `data-audit-progressive-bytes="${sum("progressive_bytes")}"`,
        "Both published editions must use the recomputed total");
      assertIncludes(article, "32.5%", "The weighted share matches the public rows");
      if (/7,479,469|33\.4%|69\.9 KB|57\.1ms/.test(article))
        throw new Error("Unreconciled totals or unsupported C1 attribution reappeared");
    }
  }
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

  await validateEventClockLabels(blogDir);
  await validateEmpiricalEvidenceClaims(blogDir);
  return { articleSlugs: englishSlugs };
}

// A primary chart annotation must name the same event clock the article
// declares. These long-horizon scores are aligned to each submission, so a
// figure must not assert a feedback-return latency the prose never claims, and
// the measured numbers stay in both the chart and the article so a label edit
// cannot silently drop the measurement.
const EVENT_CLOCK_LABELS = [
  {
    slug: "edgebench-long-horizon-repairs",
    directory: "",
    figure: "images/edgebench-long-horizon-repairs/score-time-en.svg",
    annotation: "First submission: native 13.6 min, LoopX 48.2 min",
    rejected: "First feedback",
  },
  {
    slug: "edgebench-long-horizon-repairs",
    directory: "zh",
    figure: "images/edgebench-long-horizon-repairs/score-time.svg",
    annotation: "首次提交：原生 13.6 分钟，LoopX 48.2 分钟",
    rejected: "首次反馈",
  },
];
const EVENT_CLOCK_VALUES = ["13.6", "48.2"];

async function validateEventClockLabels(blogDir) {
  for (const contract of EVENT_CLOCK_LABELS) {
    const figure = await readFile(resolve(blogDir, contract.figure), "utf8");
    assertIncludes(
      figure,
      contract.annotation,
      `Primary chart must name the declared event clock: ${contract.figure}`,
    );
    if (figure.includes(contract.rejected)) {
      throw new Error(
        `Primary chart must not assert a feedback clock: ${contract.figure}`,
      );
    }
    const article = await readFile(
      resolve(blogDir, contract.directory, contract.slug, "index.html"),
      "utf8",
    );
    for (const value of EVENT_CLOCK_VALUES) {
      assertIncludes(
        figure,
        value,
        `Primary chart must keep its measured value: ${contract.figure} ${value}`,
      );
      assertIncludes(
        article,
        value,
        `Article must state the same measured value as its chart: ${contract.slug} ${value}`,
      );
    }
  }
}

const EMPIRICAL_CLAIM_CONTRACTS = [
  {
    article: "edgebench-feedback-and-memory/index.html",
    required: [
      "44 + 36 + 24 = 104",
      "27 taxonomy vs 5 + 11 + 15 = 31",
      "rather than a matched repeated C2 comparison",
      "0.74329",
      "conditional estimate for that fixed component vector",
    ],
    rejected: "tracing 86 replan events",
  },
  {
    article: "zh/edgebench-feedback-and-memory/index.html",
    required: [
      "44 + 36 + 24 = 104",
      "分类表 27 次，分臂合计 5 + 11 + 15 = 31 次",
      "不是经过匹配重复的 C2 比较",
      "0.74329",
      "固定分量向量的条件估算",
    ],
    rejected: "对 86 次重规划事件进行了逐次因果回溯",
  },
];

async function validateEmpiricalEvidenceClaims(blogDir) {
  const fixedComponentEstimate = 0.4 * 0.7088 + 0.3 * 0.7251 + 0.2 * 0.7112 + 0.1;
  if (Math.abs(fixedComponentEstimate - 0.74329) > 1e-6) {
    throw new Error("Graph fixed-component arithmetic drifted");
  }
  for (const contract of EMPIRICAL_CLAIM_CONTRACTS) {
    const article = await readFile(resolve(blogDir, contract.article), "utf8");
    for (const value of contract.required) {
      assertIncludes(article, value, `Empirical article must retain reconciled claim: ${contract.article} ${value}`);
    }
    if (article.includes(contract.rejected)) {
      throw new Error(`Empirical article restored an unreconciled total: ${contract.article}`);
    }
  }
  for (const figure of [
    "images/context-delivery/context-delivery-ablation-en.svg",
    "images/context-delivery/context-delivery-ablation-zh.svg",
  ]) {
    const svg = await readFile(resolve(blogDir, figure), "utf8");
    const ticks = new Map(
      [...svg.matchAll(/<text[^>]*x="([\d.]+)" y="585\.694"[^>]*>(-?\d+)<\/text>/g)]
        .map((match) => [Number(match[2]), Number(match[1])]),
    );
    const zero = ticks.get(0), tenth = ticks.get(300);
    if (zero === undefined || tenth === undefined) {
      throw new Error(`Ablation chart must expose a linear 0–300 percent axis: ${figure}`);
    }
    const patch = svg.match(/<g id="patch_23">([\s\S]*?)<\/g>/)?.[1];
    const points = patch ? [...patch.matchAll(/[ML] ([\d.]+) /g)].map((match) => Number(match[1])) : [];
    if (points.length < 2) throw new Error(`Ablation chart is missing its 304.7% bar: ${figure}`);
    const expected = zero + 304.7 * ((tenth - zero) / 300);
    if (Math.abs(points[1] - expected) > 0.11) {
      throw new Error(`Ablation chart must plot +304.7% at linear scale: ${figure}`);
    }
    assertIncludes(svg, "+304.7%", `Ablation chart must label the corrected bar: ${figure}`);
  }
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

async function assertPairedLinkRejected(blogDir, slug, anchor, message) {
  const temporaryRoot = await mkdtemp(join(tmpdir(), "loopx-blog-paired-link-"));
  const fixtureBlog = join(temporaryRoot, "blog");
  try {
    await cp(blogDir, fixtureBlog, { recursive: true });
    const articlePath = resolve(fixtureBlog, slug, "index.html");
    const html = await readFile(articlePath, "utf8");
    const pairedAnchor = `<a href="../zh/${slug}/" lang="zh-CN">`;
    if (!html.includes(pairedAnchor)) {
      throw new Error(`Could not locate the paired anchor in ${slug}`);
    }
    await writeFile(articlePath, html.replace(pairedAnchor, anchor));
    await rejects(
      () => validateBilingualBlog(fixtureBlog),
      /Blog article must link its paired edition/,
      message,
    );
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

async function assertClockLabelRejected(blogDir, { figure, declared, regressed }, message) {
  const temporaryRoot = await mkdtemp(join(tmpdir(), "loopx-blog-event-clock-"));
  const fixtureBlog = join(temporaryRoot, "blog");
  try {
    await cp(blogDir, fixtureBlog, { recursive: true });
    const figurePath = resolve(fixtureBlog, figure);
    const source = await readFile(figurePath, "utf8");
    if (!source.includes(declared)) {
      throw new Error(`Could not locate the declared event clock in ${figure}`);
    }
    await writeFile(figurePath, source.replace(declared, regressed));
    await rejects(
      () => validateBilingualBlog(fixtureBlog),
      /Primary chart (must name the declared event clock|must not assert a feedback clock)/,
      message,
    );
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

const modulePath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === modulePath) {
  const articleUrl = "https://loopx-project.github.io/loopx/blog/example/";
  const counterpartUrl = "https://loopx-project.github.io/loopx/blog/zh/example/";
  for (const href of ["../zh/example/", "../../blog/zh/example/", counterpartUrl]) {
    deepStrictEqual(hasAnchorToHref(`<a href="${href}">中文</a>`, counterpartUrl, articleUrl), true);
  }
  for (const href of ["../zh/other/", "https://example.invalid/loopx/blog/zh/example/", "http://["]) {
    deepStrictEqual(hasAnchorToHref(`<a href="${href}">中文</a>`, counterpartUrl, articleUrl), false);
  }
  deepStrictEqual(hasAnchorToHref(`<link href="${counterpartUrl}" rel="alternate">`, counterpartUrl, articleUrl), false);
  deepStrictEqual(hasAnchorToHref(`<a data-href="${counterpartUrl}">中文</a>`, counterpartUrl, articleUrl), false);
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
  const pairedSlug = "edgebench-feedback-and-memory";
  await assertPairedLinkRejected(
    blogDir,
    pairedSlug,
    `<a data-href="../zh/${pairedSlug}/" lang="zh-CN">中文</a>`,
    "A data-href attribute must not substitute for a navigable paired link",
  );
  await assertPairedLinkRejected(
    blogDir,
    pairedSlug,
    `<a href="../zh/another/" data-href="../zh/${pairedSlug}/" lang="zh-CN">中文</a>`,
    "A correct data-href must not hide an incorrect navigable href",
  );
  await assertClockLabelRejected(
    blogDir,
    {
      figure: "images/edgebench-long-horizon-repairs/score-time-en.svg",
      declared: "First submission: native 13.6 min, LoopX 48.2 min",
      regressed: "First feedback: native 13.6 min, LoopX 48.2 min",
    },
    "An English chart that restores the feedback clock must fail the paired label check",
  );
  await assertClockLabelRejected(
    blogDir,
    {
      figure: "images/edgebench-long-horizon-repairs/score-time.svg",
      declared: "首次提交：原生 13.6 分钟，LoopX 48.2 分钟",
      regressed: "首次反馈：原生 13.6 分钟，LoopX 48.2 分钟",
    },
    "中文主图恢复反馈口径也必须被配对标签检查拦下",
  );
  console.log(`blog-bilingual-index-smoke: ok (${articleSlugs.length} paired articles)`);
}
