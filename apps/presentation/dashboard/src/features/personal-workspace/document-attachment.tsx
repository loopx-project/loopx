import {useState} from "react";

export const isTextDocument = (mime: string) => mime === "text/plain" || mime === "text/markdown";

/** Render source as inert text; downloading preserves the original UTF-8 bytes. */
export function DocumentAttachment({dataUrl, name, size, zh}: {dataUrl: string; name: string; size: number; zh: boolean}) {
  const [open, setOpen] = useState(false);
  let text: string;
  try {
    if (!/^data:text\/(plain|markdown);base64,[A-Za-z0-9+/=]*$/.test(dataUrl)) throw new Error("Invalid source");
    text = new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(Uint8Array.from(atob(dataUrl.split(",")[1]), c => c.charCodeAt(0)));
  } catch {
    return <p role="alert">{zh ? "文件无法读取：" : "Document unavailable: "}{name}</p>;
  }
  return <div className="personal-document-attachment">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{name} · {size.toLocaleString()} B</button>
    <a href={dataUrl} download={name}>{zh ? "下载原文" : "Download source"}</a>
    {open ? <pre tabIndex={0} aria-label={name}>{text}</pre> : null}
  </div>;
}
