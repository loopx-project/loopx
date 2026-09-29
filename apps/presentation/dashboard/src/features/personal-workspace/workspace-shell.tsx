import { useEffect, useRef, type ReactNode } from "react";

import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceTheme } from "./workspace-theme";

export function WorkspaceShell({
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
  const sidebarRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

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
      {mobileSidebarOpen ? <button aria-hidden className="personal-sidebar-backdrop" onClick={onCloseMobileSidebar} tabIndex={-1} type="button" /> : null}
      <aside
        aria-label={mobileSidebarOpen ? t("header.goalNavigation") : undefined}
        aria-modal={mobileSidebarOpen ? true : undefined}
        className="personal-workspace-sidebar"
        data-workspace-sidebar
        ref={sidebarRef}
        role={mobileSidebarOpen ? "dialog" : undefined}
      >
        <div className="personal-workspace-sidebar-inner">
          {mobileSidebarOpen ? <button className="personal-sr-only" onClick={onCloseMobileSidebar} type="button">{t("common.close")} {t("header.goalNavigation")}</button> : null}
          {sidebar}
        </div>
      </aside>
      <main aria-hidden={mobileSidebarOpen || undefined} className={`personal-workspace-main${notice ? " has-notice" : ""}`} inert={mobileSidebarOpen || undefined}>{notice}{main}</main>
      {drawerOpen ? <aside className="personal-workspace-drawer" data-context-drawer data-drawer-mode={drawerMode}>{drawer}</aside> : null}
    </section>
  );
}
