import { useEffect, useLayoutEffect, useState } from "react";

import { pageMetadata, type PublicPage } from "./page-metadata";

type Language = "en" | "zh";
const readLanguage = (): Language =>
  new URLSearchParams(typeof window === "undefined" ? "" : window.location.search).get("lang") === "zh" ? "zh" : "en";

// Public pages share URL language and fragment navigation. Client rendering or
// translation can move section positions after the initial static fragment lookup.
export function usePublicPageNavigation(page: PublicPage = "home") {
  const [language, setLanguage] = useState<Language>(readLanguage);

  useEffect(() => {
    const { title, description } = pageMetadata[page][language];
    document.title = title;
    for (const selector of ['meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]']) {
      document.querySelector(selector)?.setAttribute("content", description);
    }
    for (const selector of ['meta[property="og:title"]', 'meta[name="twitter:title"]']) {
      document.querySelector(selector)?.setAttribute("content", title);
    }
  }, [language, page]);

  useEffect(() => {
    const restoreLanguage = () => setLanguage(readLanguage());
    window.addEventListener("popstate", restoreLanguage);
    return () => window.removeEventListener("popstate", restoreLanguage);
  }, []);

  useLayoutEffect(() => {
    const url = new URL(window.location.href);
    if (language === "zh") url.searchParams.set("lang", "zh");
    else url.searchParams.delete("lang");
    window.history.replaceState(window.history.state, "", url);
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";

    let id: string;
    try {
      id = decodeURIComponent(url.hash.slice(1));
    } catch {
      return; // Malformed fragments leave normal page entry intact.
    }
    const target = document.getElementById(id);
    if (!target) return;
    // :target can remain unresolved after the client replaces static content. Reveal the
    // destination before scrolling, including when translated content reflows.
    target.closest(".reveal-block")?.setAttribute("data-anchor-entry", "");
    const align = () => target.scrollIntoView({ behavior: "instant" });
    align();

    // Web fonts can change all preceding section heights after the first layout.
    // Correct that one late reflow, but never pull a reader back after input or
    // another navigation. Cleanup also cancels stale locale/unmount callbacks.
    let cancelled = false;
    const cancel = () => { cancelled = true; };
    const interrupts = ["wheel", "touchstart", "pointerdown", "keydown", "hashchange", "popstate"] as const;
    for (const event of interrupts) window.addEventListener(event, cancel, { passive: true });
    void document.fonts.ready.then(() => {
      if (!cancelled && window.location.hash === url.hash) align();
    });
    return () => {
      cancel();
      for (const event of interrupts) window.removeEventListener(event, cancel);
    };
  }, [language]);

  return [language, setLanguage] as const;
}
