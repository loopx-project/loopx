import {useEffect, useId, useRef, useState, type ReactNode} from "react";
import {Info} from "lucide-react";
import "./context-help.css";

/** Optional, non-interactive explanation. Outcomes and action consequences stay
 * in the caller; this disclosure never performs the caller's operation. */
export function ContextHelp({label, children}: {label: string; children: ReactNode}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pinned = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const clearTimer = () => clearTimeout(timer.current);
  const place = () => {
    const button = trigger.current, bubble = panel.current;
    if (!button || !bubble) return;
    const anchor = button.getBoundingClientRect(), bounds = bubble.getBoundingClientRect();
    bubble.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12))}px`;
    bubble.style.top = `${Math.max(12, anchor.bottom + bounds.height + 8 <= window.innerHeight
      ? anchor.bottom + 8 : anchor.top - bounds.height - 8)}px`;
  };
  const show = () => {clearTimer(); panel.current?.showPopover(); place();};
  const dismiss = () => {clearTimer(); pinned.current = false; panel.current?.hidePopover();};
  const leave = () => {
    clearTimer();
    timer.current = setTimeout(() => {
      if (!pinned.current && document.activeElement !== trigger.current) dismiss();
    }, 180);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && panel.current?.matches(":popover-open")) {
        event.preventDefault(); event.stopPropagation(); dismiss();
      }
    };
    window.addEventListener("keydown", escape, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("keydown", escape, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);
  return <div className="context-help">
    <button ref={trigger} type="button" className="context-help-trigger" aria-label={label} aria-expanded={open}
      aria-controls={id} aria-describedby={open ? id : undefined}
      onPointerEnter={event => {if (event.pointerType === "mouse") show();}} onPointerLeave={leave}
      onFocus={show}
      onBlur={() => {if (!panel.current?.matches(":hover")) dismiss();}}
      onClick={() => {if (pinned.current) dismiss(); else {pinned.current = true; show();}}}>
      <Info size={15} aria-hidden="true"/>
    </button>
    <div ref={panel} id={id} className="context-help-popover" role="tooltip" popover="auto"
      onToggle={event => {const visible = event.currentTarget.matches(":popover-open"); setOpen(visible); if (!visible) pinned.current = false;}}
      onPointerEnter={clearTimer} onPointerLeave={leave}>
      <strong>{label}</strong>{children}
    </div>
  </div>;
}
