import {useId, useLayoutEffect, useMemo, useRef, useState} from "react";
import {MarkdownText} from "./markdown";
import {markdownExcerpt} from "./markdown-blocks";
import "./message-reading.css";

/** A complete long return opens at its answer, without following or parsing prose. */
export function returnedAnswerStart(scroller: HTMLElement): {key: string; top: number} | null {
  // The timeline owns display order, including late history pages. Its typed
  // return marker avoids inferring origin from message text or ID prefixes.
  const row = Array.from(scroller.querySelectorAll<HTMLElement>(".personal-message")).at(-1);
  if (row?.dataset.returnedAnswer !== "true" || !row.dataset.messageSession || !row.dataset.messageId
      || row.getBoundingClientRect().height <= scroller.clientHeight) return null;
  return {key: JSON.stringify([row.dataset.messageSession, row.dataset.messageId]),
    top: Math.max(0, scroller.scrollTop + row.getBoundingClientRect().top - scroller.getBoundingClientRect().top)};
}

/** Folding is an explicit reading choice after the initial historical snapshot. */
export function HistoricalMessageText({text, initiallyCollapsed, focused, focusRequest, zh, onRead}: {
  text: string;
  initiallyCollapsed: boolean;
  focused: boolean;
  focusRequest?: number;
  zh: boolean;
  onRead?: () => void;
}) {
  const id = useId();
  const excerpt = useMemo(() => initiallyCollapsed ? markdownExcerpt(text) : null, [text, initiallyCollapsed]);
  const [expanded, setExpanded] = useState(!initiallyCollapsed || focused);
  const toggle = useRef<HTMLButtonElement>(null);
  const anchor = useRef<number | null>(null);
  useLayoutEffect(() => {if (focused) setExpanded(true);}, [focused, focusRequest]);
  useLayoutEffect(() => {
    if (anchor.current === null || !toggle.current) return;
    const scroller = toggle.current.closest<HTMLElement>(".personal-channel-scroll");
    const delta = toggle.current.getBoundingClientRect().top - anchor.current;
    if (scroller) scroller.scrollTop += delta;
    toggle.current.focus({preventScroll: true});
    anchor.current = null;
  }, [expanded]);
  if (excerpt === null) return <MarkdownText text={text}/>;
  return <div className="personal-message-reading">
    <div className="personal-message-reading-controls">
      <button ref={toggle} type="button" aria-expanded={expanded} aria-controls={id} onClick={() => {
        onRead?.();
        anchor.current = toggle.current?.getBoundingClientRect().top ?? null;
        setExpanded(value => !value);
      }}>{expanded ? (zh ? "收起消息" : "Collapse message") : (zh ? "展开完整消息" : "Read full message")}</button>
      {!expanded ? <span>{zh ? "原文节选" : "Original excerpt"}</span> : null}
    </div>
    <div id={id}><MarkdownText text={expanded ? text : excerpt}/></div>
  </div>;
}
