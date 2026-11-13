import {ArrowDown, ArrowUp, Search, X} from "lucide-react";
import {useLayoutEffect, useRef, useState} from "react";
import type {WorkspaceMessage} from "./personal-workspace-model";
import "./conversation-find.css";

export function conversationMessageKey(message: WorkspaceMessage): string {
  return JSON.stringify([message.sourceSessionId ?? null, message.sourceMessageId ?? message.id]);
}

/** Search only the original text already read by the conversation owner. */
export function ConversationFind({items, incomplete, zh, onSelect, onNavigate}: {
  items: WorkspaceMessage[];
  incomplete: boolean;
  zh: boolean;
  onSelect: (id: string | null) => void;
  onNavigate: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const needle = query.trim().toLocaleLowerCase();
  const matches = open && needle ? items.filter(message => message.text.toLocaleLowerCase().includes(needle)) : [];
  // Retain the selected original message when an older page or live reply arrives.
  const index = Math.max(0, matches.findIndex(item => conversationMessageKey(item) === selected));
  const activeId = matches[index] ? conversationMessageKey(matches[index]) : null;
  useLayoutEffect(() => {setSelected(activeId); onSelect(activeId);}, [activeId, selected, onSelect]);
  function close() {
    setOpen(false); setQuery(""); setSelected(null); onSelect(null);
    trigger.current?.focus();
  }
  function move(offset: number) {
    if (!matches.length) return;
    setSelected(conversationMessageKey(matches[(index + offset + matches.length) % matches.length]));
    onNavigate();
  }
  return <div className="personal-conversation-find">
    <button ref={trigger} type="button" aria-expanded={open} onClick={() => open ? close() : setOpen(true)}>
      <Search size={16} aria-hidden/>{zh ? "查找对话" : "Find in conversation"}
    </button>
    {open ? <form role="search" onSubmit={event => {event.preventDefault(); move(1);}}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); close();}
        else if (event.key === "Enter" && event.shiftKey) {event.preventDefault(); move(-1);}
      }}>
      <input autoFocus type="search" value={query} maxLength={256}
        aria-label={zh ? "查找已加载消息" : "Find in loaded messages"}
        placeholder={zh ? "查找已加载消息" : "Find in loaded messages"}
        onChange={event => {setQuery(event.target.value); setSelected(null);}}/>
      <span role="status" aria-live="polite">{needle ? matches.length
        ? `${index + 1} / ${matches.length}` : (zh ? "没有匹配消息" : "No matching messages")
        : (zh ? "仅查找已加载文本" : "Loaded text only")}</span>
      <button type="button" disabled={!matches.length} aria-label={zh ? "上一条匹配消息" : "Previous matching message"} onClick={() => move(-1)}><ArrowUp size={16} aria-hidden/></button>
      <button type="submit" disabled={!matches.length} aria-label={zh ? "下一条匹配消息" : "Next matching message"}><ArrowDown size={16} aria-hidden/></button>
      <button type="button" aria-label={zh ? "关闭查找" : "Close find"} onClick={close}><X size={16} aria-hidden/></button>
      {incomplete ? <small>{zh ? "对话记录尚未完整读取，结果仅包含已加载消息。" : "History is incomplete; results include loaded messages only."}</small> : null}
    </form> : null}
  </div>;
}
