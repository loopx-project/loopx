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

The first-run CTA opens one setup dialog with a recommended Agent path and a
manual Shell path. The Agent option copies the localized, public-safe setup
contract; the Shell option copies the commands shown in the terminal section.
The `See in action` CTA scrolls to the public evidence showcase and restarts the
Issue Fix replay.

The homepage control-plane diagrams are synthetic UI. Finite, tabbed terminal
replays summarize two public README trajectories; they are curated projections,
not raw session logs. The full-screen viewer bundles only the two explicit
`docs/assets/long-running-loop-*-trajectory.png` files. The site must not consume
live LoopX state, local status feeds, private registries, raw logs, or write
APIs.
