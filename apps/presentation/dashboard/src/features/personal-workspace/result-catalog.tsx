import {useEffect, useRef} from "react";
import {ArrowLeft, FileText} from "lucide-react";
import "./result-catalog.css";

/** Presentation only: every owner supplies its native identity and status. */
export function ResultCatalogRow({resultKey, title, source, status, time, zh, selected, disabled, label, onSelect}: {
  resultKey: string; title: string; source: string; status: string; time?: string | null; zh: boolean;
  selected?: boolean; disabled?: boolean; label?: string; onSelect: () => void;
}) {
  const date = time && Number.isFinite(Date.parse(time))
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(time))
    : time;
  return <button type="button" className="result-catalog-row" data-result-key={resultKey}
    disabled={disabled} aria-pressed={selected} aria-label={label} onClick={event => {
      event.currentTarget.focus({preventScroll: true}); onSelect();
    }}>
    <FileText size={18} aria-hidden="true"/><span><strong>{title}</strong>
      <small>{source}</small><span className="result-catalog-meta"><span>{status}</span>
        {time ? <time dateTime={time} title={time}>{date}</time> : null}</span>
    </span>
  </button>;
}

export function ResultCatalogReturn({zh, onReturn}: {zh: boolean; onReturn: () => void}) {
  return <button type="button" className="result-catalog-return" onClick={onReturn}>
    <ArrowLeft size={16} aria-hidden="true"/>{zh ? "返回成果目录" : "Return to result catalog"}
  </button>;
}

/** Keep focus local while each result owner retains its own read/cancel lifecycle. */
export function useResultCatalogFocus() {
  const root = useRef<HTMLElement>(null);
  const frame = useRef<number | null>(null);
  const cancel = () => {
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    frame.current = null;
  };
  useEffect(() => cancel, []);
  function focus(reader: boolean, key?: string | null) {
    cancel();
    frame.current = window.requestAnimationFrame(() => {
      frame.current = null;
      if (!root.current?.isConnected) return;
      const row = key ? [...root.current.querySelectorAll<HTMLButtonElement>("[data-result-key]")]
        .find(button => button.dataset.resultKey === key && !button.disabled) : null;
      const target = reader ? root.current.querySelector<HTMLElement>("[data-result-reader]")
        : row ?? root.current.querySelector<HTMLElement>("[data-result-catalog]") ?? root.current;
      if (!target?.getClientRects().length) return;
      target.focus({preventScroll: true});
      target.scrollIntoView({block: "nearest"});
    });
  }
  return {root, cancel, reader: () => focus(true), row: (key?: string | null) => focus(false, key)};
}
