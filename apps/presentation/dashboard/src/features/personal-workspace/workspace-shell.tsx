import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceTheme } from "./workspace-theme";

export function useDrawerOverlay(ref: RefObject<HTMLElement | null>, enabled: boolean) {
  const [overlay, setOverlay] = useState(false);
  useLayoutEffect(() => {
    if (!enabled) {
      setOverlay(false);
      return;
    }
    const element = ref.current?.closest<HTMLElement>("[data-context-drawer]") ?? ref.current;
    if (!element) return;
    const update = () => {
      const next = getComputedStyle(element).position === "fixed";
      setOverlay(current => current === next ? current : next);
    };
    const observer = new ResizeObserver(update);
    observer.observe(element);
    window.addEventListener("resize", update);
    update();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [enabled, ref]);
  return enabled && overlay;
}

export function WorkspaceShell({
  overlay,
  drawer,
  drawerMode = "panel",
  drawerOpen,
  main,
  mobileSidebarOpen = false,
  notice,
  onCloseMobileSidebar,
  sidebar,
  theme = "loopx",
}: {
  overlay?: ReactNode;
  drawer?: ReactNode;
  drawerMode?: "inspector" | "inspector-full" | "panel";
  drawerOpen: boolean;
  main: ReactNode;
  mobileSidebarOpen?: boolean;
  // A service notice belongs to the main column: rendered outside the shell
  // it pushed the full-height workspace below the viewport.
  notice?: ReactNode;
  onCloseMobileSidebar?: () => void;
  sidebar: ReactNode;
  theme?: WorkspaceTheme;
}) {
  const { t } = useWorkspaceI18n();
  const drawerRef = useRef<HTMLElement>(null);
  const drawerOverlay = useDrawerOverlay(drawerRef, drawerOpen && drawerMode.startsWith("inspector"));
  const sidebarRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!drawerOverlay) return;
    const root = document.documentElement;
    const body = document.body;
    const previousRootOverflow = root.style.overflow;
    const previousBodyOverflow = body.style.overflow;
    root.style.overflow = "hidden";
    body.style.overflow = "hidden";
    return () => {
      root.style.overflow = previousRootOverflow;
      body.style.overflow = previousBodyOverflow;
    };
  }, [drawerOverlay]);

  useEffect(() => {
    if (!mobileSidebarOpen) return;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const sidebar = sidebarRef.current;
    const focusable = sidebar?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], select:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    focusable?.[0]?.focus();

    function trapSidebarFocus(event: KeyboardEvent) {
      if (event.key !== "Tab" || !focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", trapSidebarFocus);
    return () => {
      document.removeEventListener("keydown", trapSidebarFocus);
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [mobileSidebarOpen]);

  return (
    <section className={`personal-workspace-shell${drawerOpen ? " has-drawer" : ""}${drawerOpen && drawerMode.startsWith("inspector") ? " has-task-inspector" : ""}${drawerMode === "inspector-full" ? " is-task-inspector-full" : ""}${mobileSidebarOpen ? " mobile-sidebar-open" : ""}`} data-pw-theme={theme}>
      {overlay}
      {mobileSidebarOpen ? <button aria-hidden className="personal-sidebar-backdrop" onClick={onCloseMobileSidebar} tabIndex={-1} type="button" /> : null}
      <aside
        aria-label={mobileSidebarOpen ? t("header.goalNavigation") : undefined}
        aria-hidden={drawerOverlay || undefined}
        aria-modal={mobileSidebarOpen ? true : undefined}
        className="personal-workspace-sidebar"
        data-workspace-sidebar
        inert={drawerOverlay || undefined}
        ref={sidebarRef}
        role={mobileSidebarOpen ? "dialog" : undefined}
      >
        <div className="personal-workspace-sidebar-inner">
          {mobileSidebarOpen ? <button className="personal-sr-only" onClick={onCloseMobileSidebar} type="button">{t("common.close")} {t("header.goalNavigation")}</button> : null}
          {sidebar}
        </div>
      </aside>
      <main aria-hidden={mobileSidebarOpen || drawerOverlay || undefined} className={`personal-workspace-main${notice ? " has-notice" : ""}`} inert={mobileSidebarOpen || drawerOverlay || undefined}>{notice}{main}</main>
      {drawerOpen ? <aside className="personal-workspace-drawer" data-context-drawer data-drawer-mode={drawerMode} ref={drawerRef}>{drawer}</aside> : null}
    </section>
  );
}
