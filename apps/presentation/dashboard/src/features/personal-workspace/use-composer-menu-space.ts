import { useEffect, type RefObject } from "react";

/** Menus above the whole input must fit the space left by a growing draft. */
export function useComposerMenuSpace(root: RefObject<HTMLDetailsElement | null>) {
  useEffect(() => {
    const menu = root.current;
    const composer = menu?.closest<HTMLElement>(".personal-channel-composer");
    if (!menu || !composer) return;
    let stop: (() => void) | undefined;
    const toggle = () => {
      stop?.();
      stop = undefined;
      if (!menu.open) return;
      const place = () => {
        const top = window.visualViewport?.offsetTop ?? 0;
        // Reserve the panel gap and a margin at the visible viewport's top.
        menu.style.setProperty("--composer-menu-space", `${Math.max(0, composer.getBoundingClientRect().top - top - 24)}px`);
      };
      const observer = new ResizeObserver(place);
      observer.observe(composer);
      window.addEventListener("resize", place);
      window.addEventListener("scroll", place, true);
      window.visualViewport?.addEventListener("resize", place);
      window.visualViewport?.addEventListener("scroll", place);
      place();
      stop = () => {
        observer.disconnect();
        window.removeEventListener("resize", place);
        window.removeEventListener("scroll", place, true);
        window.visualViewport?.removeEventListener("resize", place);
        window.visualViewport?.removeEventListener("scroll", place);
      };
    };
    menu.addEventListener("toggle", toggle);
    toggle();
    return () => { stop?.(); menu.removeEventListener("toggle", toggle); };
  }, [root]);
}
