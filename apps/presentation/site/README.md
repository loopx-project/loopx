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

The header and primary homepage CTA download the latest Apple Silicon Mac App
preview through the release's stable `LoopX.app.zip` asset name. The homepage
states the Python 3.11+ prerequisite and links to the desktop first-launch guide.
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

Run `npm run smoke:seo` and `node scripts/analytics-smoke.mjs` after a local build (the browser smoke uses the dashboard Playwright dependency). The Pages workflow also runs it on
the complete artifact with `--with-docs`. It checks static content, route-specific
metadata, sitemap targets and public asset paths. Preview with `npm run preview`.

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
