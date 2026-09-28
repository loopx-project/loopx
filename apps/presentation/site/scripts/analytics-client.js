// Public website measurement only. Never loaded into the local workspace app.
(() => {
  const id = document.currentScript?.dataset.measurementId;
  if (!/^G-[A-Z0-9]+$/.test(id ?? "") || location.hostname !== "loopx-project.github.io" ||
      navigator.doNotTrack === "1" || navigator.globalPrivacyControl || window.__loopxAnalyticsActive) return;
  window.__loopxAnalyticsActive = true;
  const storageKey = "loopx.analytics-consent.v1";
  const readChoice = () => {
    try {
      const choice = JSON.parse(localStorage.getItem(storageKey))?.choice;
      return ["granted", "denied"].includes(choice) ? choice : null;
    } catch { return null; }
  };
  let choice = readChoice();
  let started = false;
  const panel = document.createElement("section");
  panel.id = "loopx-analytics-consent";
  panel.setAttribute("aria-label", "Analytics preferences");
  panel.innerHTML = `<p></p><a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener"></a><div><button type="button" data-consent="granted"></button><button type="button" data-consent="denied"></button></div>`;
  panel.hidden = choice !== null;
  document.body.append(panel);
  const settings = document.createElement("button");
  settings.type = "button";
  settings.id = "loopx-analytics-settings";
  settings.onclick = () => { panel.hidden = false; panel.querySelector("button").focus(); };
  const syncUi = () => {
    const zh = new URLSearchParams(location.search).get("lang") === "zh" || document.documentElement.lang.startsWith("zh");
    const labels = zh ? [
      "经你允许，我们会使用 Google Analytics Cookie 统计访问和使用情况，以改进网站。可随时在页脚修改选择。",
      "Google 如何使用数据", "允许统计", "拒绝", "统计偏好",
    ] : [
      "With your permission, we use Google Analytics cookies to understand visits and usage and improve this site. You can change your choice in the footer.",
      "How Google uses data", "Allow analytics", "Reject", "Analytics preferences",
    ];
    [panel.querySelector("p"), panel.querySelector("a"), ...panel.querySelectorAll("button"), settings].forEach((node, i) => {
      if (node.textContent !== labels[i]) node.textContent = labels[i];
    });
    if (panel.getAttribute("aria-label") !== labels[4]) panel.setAttribute("aria-label", labels[4]);
    // React startup and Material instant navigation can replace the site footer.
    const footer = document.querySelector(".site-footer, .bm-footer, .md-footer") || [...document.querySelectorAll("footer")].at(-1) || document.body;
    if (settings.parentNode !== footer) footer.append(settings);
  };
  const choose = (next) => {
    choice = next;
    try { localStorage.setItem(storageKey, JSON.stringify({ choice, at: new Date().toISOString() })); } catch { /* The choice still applies to this page when storage is unavailable. */ }
    panel.hidden = true;
    if (choice === "granted") start();
    else if (started) {
      // Disable collection immediately, remove measurement cookies, then unload
      // the tag and its automatic timers. No denied-state pings are needed.
      window[`ga-disable-${id}`] = true;
      for (const cookie of document.cookie.split(";")) {
        const name = cookie.split("=")[0].trim();
        if (name !== "_ga" && name !== `_ga_${id.slice(2)}`) continue;
        for (const path of ["/", "/loopx", "/loopx/"]) {
          for (const domain of ["", `; domain=${location.hostname}`]) {
            document.cookie = `${name}=; Max-Age=0; path=${path}${domain}`;
          }
        }
      }
      location.reload();
    }
    if (panel.contains(document.activeElement)) settings.focus({ preventScroll: true });
  };
  panel.querySelectorAll("button").forEach((button) => { button.onclick = () => choose(button.dataset.consent); });
  window.addEventListener("storage", (e) => {
    if (e.key === storageKey) {
      const next = readChoice();
      if (next !== choice) {
        if (started) window[`ga-disable-${id}`] = true;
        location.reload();
      }
    }
  });
  syncUi();
  new MutationObserver(syncUi).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-language"] });
  if (choice === "granted") start();

  function start() {
    if (started || choice !== "granted") return;
    started = true;
    window.dataLayer = window.dataLayer || [];
    function gtag() { window.dataLayer.push(arguments); }
    const originOnly = (url) => { try { return new URL(url).origin; } catch { return ""; } };
    let previous = "";
    const pageFields = () => {
      const canonical = document.querySelector('link[rel="canonical"]')?.href;
      if (!canonical) return null;
      const url = new URL(canonical);
      if (url.origin !== "https://loopx-project.github.io" || !url.pathname.startsWith("/loopx/")) return null;
      return { page_location: url.origin + url.pathname, page_referrer: originOnly(document.referrer),
        page_title: document.title, language: new URLSearchParams(location.search).get("lang") === "zh" ? "zh-CN" : document.documentElement.lang };
    };
    const initial = pageFields();
    if (!initial) return;
    gtag("consent", "default", { analytics_storage: "denied", ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied" });
    gtag("consent", "update", { analytics_storage: "granted" });
    gtag("js", new Date());
    gtag("set", initial);
    gtag("config", id, { ...initial, send_page_view: false,
      allow_google_signals: false, allow_ad_personalization_signals: false });
    const pageView = () => {
      const fields = pageFields();
      if (choice !== "granted" || !fields || fields.page_location === previous) return;
      previous = fields.page_location;
      gtag("set", fields);
      gtag("event", "page_view", fields);
    };
    pageView();
    const tag = document.createElement("script");
    tag.async = true;
    tag.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    document.head.append(tag);
    // Material's instant navigation updates the canonical element. Locale and
    // fragment changes on a React page must not inflate pageview counts.
    new MutationObserver(pageView).observe(document.head, { subtree: true, childList: true, attributes: true, attributeFilter: ["href"] });
    const event = (name, fields = {}) => {
      const page = pageFields();
      if (page && choice === "granted") gtag("event", name, { ...page, ...fields });
    };
    document.addEventListener("click", (e) => {
      const target = e.target instanceof Element ? e.target : null;
      const marked = target?.closest("[data-analytics-event]")?.dataset.analyticsEvent;
      if (["setup_open", "showcase_open"].includes(marked)) event(marked);
      const anchor = target?.closest("a[href]");
      if (!anchor) return;
      const url = new URL(anchor.href);
      if (marked === "desktop_download" && url.hostname === "github.com" &&
          url.pathname === "/loopx-project/loopx/releases/latest/download/LoopX.app.zip") {
        event("desktop_download", { platform: "macos", artifact: "app_zip" });
        return;
      }
      if (url.hostname === "github.com" && /^\/(?:loopx-project|huangruiteng)\/loopx(?:\/|$)/.test(url.pathname)) {
        event("github_click", { destination: url.pathname.includes("packages/dsh-loopx-plugin") ? "dsh_plugin" : "repository" });
      } else if (url.origin === location.origin && url.pathname.startsWith("/loopx/docs/") && url.pathname !== location.pathname) {
        event("docs_open");
      }
    });
    window.addEventListener("loopx:setup-copy", (e) => {
      if (e.detail === "agent" || e.detail === "shell") event("setup_copy", { setup_method: e.detail });
    });
  }
})();
