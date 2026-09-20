# LoopX Presentation Apps

Browser applications that present LoopX state live here.

- `dashboard/`: the React/Vite operator dashboard, frontstage routes, and
  developer cockpit.
- `site/`: the static
  [public homepage](https://loopx-project.github.io/loopx/) published at the
  GitHub Pages root.

Apps in this directory consume public-safe LoopX projections and fixtures. They
do not own control-plane state or write directly to LoopX registries.
