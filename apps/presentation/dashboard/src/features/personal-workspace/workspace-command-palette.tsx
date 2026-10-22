import { useEffect, useId, useRef, useState } from "react";
import { useWorkspaceI18n } from "./i18n";
import { filterWorkspaceCommands, isPaletteShortcut, type WorkspaceCommandEntry } from "./workspace-command-entries";
import "./workspace-command-palette.css";

export function WorkspaceCommandPalette({ entries, open, enabled, readOnly, unavailable, onOpen, onClose, onSelect }: {
  entries: WorkspaceCommandEntry[]; open: boolean; enabled: boolean; readOnly: boolean; unavailable: boolean;
  onOpen: () => void; onClose: () => void; onSelect: (entry: WorkspaceCommandEntry) => void;
}) {
  const { t } = useWorkspaceI18n();
  const id = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const matches = filterWorkspaceCommands(entries, query);
  const active = matches.find(entry => entry.id === activeId && !entry.disabled)
    ?? matches.find(entry => !entry.disabled);
  const activeIndex = matches.findIndex(entry => entry === active);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (!enabled || !isPaletteShortcut(event)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (open) onClose(); else onOpen();
    }
    document.addEventListener("keydown", shortcut, true);
    return () => document.removeEventListener("keydown", shortcut, true);
  }, [enabled, open, onOpen, onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    composing.current = false;
    setQuery("");
    setActiveId(null);
    dialog.showModal();
    inputRef.current?.focus();
    return () => { dialog.close(); returnFocus.current?.focus({ preventScroll: true }); };
  }, [open]);

  useEffect(() => {
    if (open && activeIndex >= 0) document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [open, id, activeIndex, query]);

  function choose(entry: WorkspaceCommandEntry) {
    // Resolve against this render, never a retained snapshot/search result.
    const current = entries.find(candidate => candidate.id === entry.id);
    if (!current || current.disabled) return;
    dialogRef.current?.close();
    returnFocus.current?.focus({ preventScroll: true });
    onClose();
    onSelect(current);
  }

  // Keep closed palette labels/results out of the workspace DOM.
  if (!open) return null;

  return <dialog ref={dialogRef} className="workspace-command-palette" aria-labelledby={`${id}-title`}
    aria-describedby={`${id}-scope`} onCancel={event => { event.preventDefault(); if (!composing.current) onClose(); }}
    onKeyDown={event => {
      // Do not let the underlying inspector consume keys from this modal.
      event.stopPropagation();
      if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key === "Tab") {
        const first = dialogRef.current?.querySelector<HTMLButtonElement>("button");
        const last = inputRef.current;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
    <header><h2 id={`${id}-title`}>{t("palette.title")}</h2>
      <button type="button" onClick={onClose} aria-label={t("common.close")}>Esc</button></header>
    <p id={`${id}-scope`}>{t(readOnly ? "palette.readOnly" : "palette.scope")}</p>
    <label htmlFor={`${id}-search`}>{t("palette.search")}</label>
    <input id={`${id}-search`} ref={inputRef} role="combobox" aria-expanded={open}
      aria-controls={`${id}-list`} aria-autocomplete="list" autoComplete="off"
      aria-activedescendant={active ? `${id}-option-${activeIndex}` : undefined}
      value={query} onChange={event => { setQuery(event.target.value); setActiveId(null); }}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => {
        if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const available = matches.filter(entry => !entry.disabled);
          const index = available.findIndex(entry => entry.id === active?.id);
          const next = available[(index + (event.key === "ArrowDown" ? 1 : -1) + available.length) % available.length];
          if (next) setActiveId(next.id);
        } else if (event.key === "Enter") {
          event.preventDefault();
          if (active) choose(active);
        }
      }} />
    <ul id={`${id}-list`} role="listbox" aria-label={t("palette.matches")}>
      {matches.map((entry, index) => <li id={`${id}-option-${index}`} key={entry.id} role="option"
        aria-selected={entry.id === active?.id} aria-disabled={Boolean(entry.disabled)}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(entry)}>
        <span>{entry.title}</span><small>{t(entry.disabled ? `palette.${entry.disabled}` : `palette.${entry.group}`)}</small>
      </li>)}
    </ul>
    {!matches.length ? <p role="status">{t(unavailable ? "palette.unavailable" : query.trim() ? "palette.noMatches" : "palette.empty")}</p> : null}
    <footer>{t("palette.keys")}</footer>
  </dialog>;
}
