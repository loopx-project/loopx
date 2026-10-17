# LoopX Public Website

This directory owns the React/Vite, public-safe
[LoopX homepage](https://loopx-project.github.io/loopx/) published at the root
of the GitHub Pages site. The frontstage exporter builds this application into
the Pages root, publishes contributor tools at `/developers/projections/`, and
redirects legacy `/frontstage/` links to the public case directory.

Vite's base path is supplied by the exporter so links and assets work both at
the repository Pages base (`/loopx/`) and in root-base local previews.

The language switch keeps English as the default and provides a public-safe
Chinese locale. `?lang=zh` is the shareable Chinese entry.

The Blog is a static editorial section under `public/blog/`. `/blog/` and its
articles default to English; `/blog/zh/` contains the paired Chinese editions.
Each language has a direct URL, a matching language switch, canonical and
alternate-language metadata, and the complete article in HTML. Reading and
navigation work without JavaScript. Vite copies these pages into both the local
build and the existing Pages export; no separate hosting or content service is
needed. Relative navigation supports both root and repository base paths.
Both locale indexes list every article. The focused bilingual Blog smoke and
the frontstage share-bundle smoke discover article directories rather than
relying on a hand-maintained allowlist. They fail when a translation, index
entry, language alternate, counterpart link, or paired article section is
missing.

The Chinese DeepSWE × Sol research brief is a static page at
`public/benchmarks/deepswe-sol/`, linked from the homepage research collection.
It uses the shared editorial tokens and ships through the same public-directory
copy as the Blog. Its historical results and mechanism explanations cite the
immutable v1 archive; editing the brief must not rewrite that archive or restore
withdrawn scores. The article works without JavaScript and has stable section
anchors for other articles to cite.

The Chinese application-scenarios article at `public/blog/zh/application-scenarios/`
links the three research briefs with comparable summaries. Its full text and
initial PR-state example are static HTML; `presentation.js` progressively adds
presentation typography, section navigation, and the synthetic state selector.
These controls stay hidden when JavaScript is unavailable. No real repository
state or write APIs are involved.

Edit the paired HTML editions together, including their index summaries and
metadata. Preserve matching section anchors and public source attribution.
Keep source-document exports, private references, and unreviewed media outside
the public tree. Follow `docs/development/design.md` and obtain first-screen
review before changing the Blog or its homepage entry.

### Editorial responsibilities and reading routes

Keep the chronological catalog, and offer question-based routes alongside it.
The route is a reading aid, not another content store. Each article owns one
explanation; new introductory pieces should link to that owner rather than
repeat its full mechanisms, measurements, or history.

| Reader question | Article owner | Follow-up |
| --- | --- | --- |
| How does agent-assisted coding become sustained development? | `semantic-software-development` — short introduction, public engineering case, and agentic-software framing | Scenarios or core architecture |
| Which kinds of work fit? | `application-scenarios` — complex delivery, exploration, ongoing responsibilities | Handbook for use; research for measured evidence |
| How does the semantic control plane work? | `from-one-shot-agents-to-long-horizon-control` — core architecture, effects and recovery | Kanban for collaboration objects |
| How do agents coordinate work? | `agent-facing-kanban` — claims, leases, gates, evidence and transitions | Core architecture for system boundaries |
| What has evaluation established? | `edgebench-feedback-and-memory` — scoped findings and counterevidence | Scenario-specific research briefs |
| Where has reliability failed? | `five-freshness-incidents` — freshness failures and corrective contracts | Core architecture; retain unresolved limits |
| How do improvements enter subsequent work? | `long-horizon-rsi` — bootstrapping history and self-improvement thesis | Development introduction; adoption evidence |

Both editions preserve the same role, section anchors, and source boundaries.
Related-reading links should state the next article's purpose, not just list
titles. Historical publication dates and experimental settings stay with their
original articles. The Developer Book owns current operating instructions;
essays do not become a competing setup manual. A personal source document and
its adapted Blog article remain separate editions, not automatic mirrors.

Sharing sessions can combine an engineering case, the core mechanism, a
collaboration walkthrough, and selected research or postmortem evidence. Do
not expand the introductory essay into a second architecture reference merely
to fit every speaking route into one document.

The header and primary homepage CTA download the complete Apple Silicon Mac App
preview from the highest complete stable `vX.Y.Z` release at build time. Pages
opts in with `LOOPX_SITE_RESOLVE_DESKTOP_RELEASE=1`. The resolver in
`scripts/desktop-download.ts` follows the complete-set, manifest-last
contract owned by `scripts/desktop_release_artifacts.py`: all four desktop
artifacts must be uploaded, nonempty and versioned in the official repository,
and `DESKTOP-SHA256SUMS` must match their GitHub SHA-256 digests exactly. Draft,
prerelease, plugin and mutable updater tags are excluded. An empty or incomplete
new release retains the previous complete version; API/download failure or no
eligible release fails the build and preserves the existing deployment.

The homepage shows the selected version, states the Python 3.11+ prerequisite
and links to the first-launch guide. A successful public desktop-artifact
publication triggers Pages to rebuild trusted current `main`; the existing
six-hour schedule also refreshes inventory. `GH_TOKEN` optionally authenticates
only build-time GitHub API requests and is never sent to asset redirects or
included in the browser bundle. No visitor API calls are needed. Ordinary local
builds and Vite dev use the known v1.4.0 preview without network access. Use
`LOOPX_SITE_RESOLVE_DESKTOP_RELEASE=1 npm run build` to validate live selection
locally; removing this build-time flag restores the offline preview.
This selection does not change the App's signed update channel or its runtime.
The adjacent first-run CTA opens one setup dialog with an Agent path and a
manual Shell path. The Agent option copies the localized, public-safe setup
contract; the Shell option copies the commands shown in the terminal section.
The `See in action` link scrolls to the public evidence showcase and restarts
the Issue Fix replay.

The homepage control-plane diagrams are synthetic UI. Finite, tabbed terminal
replays summarize two public README trajectories; they are curated projections,
not raw session logs. The full-screen viewer bundles only the two explicit
`docs/assets/long-running-loop-*-trajectory.png` files. The site must not consume
live LoopX state, local status feeds, private registries, raw logs, or write
APIs.

## Time Machine

`/lab/time-machine/` is a read-only, decision-first public experience. Choose
for an Agent, see the actual recorded outcome, then optionally inspect the
original finding or browse each capture. The existing homepage research
collection discovers it. English is the default; `?lang=zh` selects Chinese,
and `?case=graph` or `?case=ad` opens another episode. Back/forward navigation
restores the episode. Choices live only in page memory; reloading resets them.
Both choices reveal the same historical outcome. They trigger no model,
installation, restore, live Goal operation or simulated counterfactual.

The three scenes distinguish a Portfolio local/native reversal, a scoped Graph
finding with unproven native benefit, and an Ad screening/fresh-sample reversal.
These are solver-authored diagnostic observations, not independent replications,
countable benchmark results or evidence of a LoopX performance gain. The original
status and text remain visible, including uncertainty. The record slider reads
one original event in append order; it is not a historical-state projection.
There are no inferred dependencies or causal links.

The 76/134/105 events are byte-identical public fixtures from
[the fixed research-history source](https://github.com/loopx-project/loopx/tree/44c7af3db271ab1991428d3ff1991ca922434eff/apps/presentation/dashboard/src/data/fixtures),
selected against the
[immutable public archive](https://huggingface.co/datasets/huangrt01/loopx-edgebench-research-checkpoints-20261015/tree/c858f51021dc1c1cf08263f9c04b692a5e29b40b).
The browser verifies the capture SHA-256 before enabling a choice. A missing,
changed or malformed capture clears the displayed records and offers retry;
old content is never relabeled as another case. No raw native session is bundled.
The existing Explore v0 owner defines record semantics; the local TS reader
only checks the pinned artifact shape and renders its text. No shared vocabulary,
capability or provider is introduced. The route loads separately from the home
page and the static build includes the English question and archive link.

Run `node --test scripts/time-machine.test.mjs` for capture integrity, negative
cases and verbatim order checks, then `npm run build` and the ordinary SEO smoke.
The packaged browser journey must also cover choices/results/original text,
locale/case/history/reload, keyboard and narrow screens, plus a missing or
changed capture and successful retry. Build at both `/` and `/loopx/` to qualify
asset and navigation bases. Removing the route and its research entry reverts
the experience without touching Explore state or live Agents.

## Search publication

`npm run build` compiles the browser app and prerenders the homepage and the
SWE-Marathon/LHTB briefs from the same React components. Each route ships its
own body, title, description, canonical URL and social metadata before any
JavaScript executes. The shared metadata module also updates translated titles
and descriptions when readers switch language. The English URL is canonical;
`?lang=zh` remains a client-rendered translation, while Blog translations have
separate static URLs and reciprocal language alternates.

The build discovers canonical editorial URLs for `sitemap-pages.xml` and writes
`sitemap.xml`, which also references the three MkDocs sitemaps. The complete
index is ready only after the Pages workflow builds the documentation. Submit
`https://loopx-project.github.io/loopx/sitemap.xml` in the verified Search Console
property after deployment. The Google-issued verification file
`public/google5a493c86e5bde9bc.html` is copied unchanged to the site root; keep it
published to retain ownership verification. This file does not load analytics
or collect visitor data, and verification is independent of GA4 consent.
A project-level `/loopx/robots.txt` would not control
crawling: robots rules belong at the origin root, outside this Pages artifact.

Public outbound links send at most the origin to other HTTPS sites through
`strict-origin-when-cross-origin`; new tabs retain `noopener`. This preserves a
referral signal without sending query strings or adding tracking identifiers to outbound links. Referrals do not reveal the search query or establish
conversion attribution. Search Console access and indexing are external steps;
a successful build does not establish ranking or traffic improvement.

Run `node --test scripts/desktop-download.test.mjs`, `npm run smoke:seo` and
`node scripts/analytics-smoke.mjs` after a local build (the browser smoke uses
the dashboard Playwright dependency). The Pages workflow also runs it on
the complete artifact with `--with-docs`. It checks static content, route-specific
metadata, sitemap targets and public asset paths. Preview with `npm run preview`.

Run `npm run smoke:seo:bases` to build and check both the default root preview
and `/loopx/` publication, including rejection of a guide link under the wrong
base or a missing guide file.

## Google Analytics 4

Set the GitHub repository variable `LOOPX_GA_MEASUREMENT_ID` to the website's
real GA4 web stream ID (`G-…`). The Pages workflow injects measurement into
canonical public pages after all builds. An unset variable disables analytics;
PR artifacts and localhost previews do not send production events. Remove the
variable and redeploy to disable it. No ID or GA API secret is hard-coded.

In the GA4 web stream, disable **Enhanced measurement**: this integration sends
explicit pageviews and conversion-intent events, including during MkDocs instant
navigation. Leaving automatic history, form or outbound collection on can create
duplicate events and collect fields outside this site's explicit event schema.
A bilingual notice explains the use of Google Analytics cookies and links to
Google's data-use information. No Google tag, measurement cookie or event is
initialized before the visitor chooses **Allow analytics**. **Reject** keeps
measurement off. Both choices are remembered in local storage and can be changed
through **Analytics preferences** in the page footer. Withdrawal immediately
disables collection, clears this stream's GA cookies and reloads the page to
unload the tag and its timers. It does not erase data already sent to Google.
When storage is unavailable, the choice applies to the current page only.

The loader honors Do Not Track and Global Privacy Control even after a previous
grant, and disables Google signals and advertising consent. The browser smoke
covers an ordinary visitor before choosing, rejection, grant and withdrawal,
including remembered choices and cookie cleanup. Google requests are intercepted
in tests; production collection still needs a real post-deployment check.

| Event | Meaning |
| --- | --- |
| `page_view` | A canonical public page was opened; hash and locale-only changes do not add views. |
| `setup_open` | Opened the homepage setup dialog. |
| `setup_copy` | Successfully copied an Agent prompt or Shell setup command (`setup_method`). |
| `desktop_download` | Clicked an official Mac App download link (`platform`, `artifact`); this records intent, not a completed download. |
| `showcase_open` | Followed the homepage demonstration CTA. |
| `github_click` | Clicked a repository or DSH plugin link (`destination`). |
| `docs_open` | Followed a link into documentation. |

These events measure interest, not completed installs or active LoopX projects.
Use GA4 Realtime to verify one real visit and setup action after publication,
then mark `setup_copy` as a key event if it matches the reporting goal. Compare
organic landing-page visits and setup-copy/GitHub-click rates over consistent
windows. Search Console supplies website query/impression data; GitHub Traffic
supplies repository referrals. Neither dataset alone identifies the full path.

Page locations use canonical paths and referrers use only their origin. The
integration does not send raw query strings, fragments, task text, clipboard
contents, local workspace state or operator dashboard interactions. The current
implementation does not attribute UTM campaigns separately.
