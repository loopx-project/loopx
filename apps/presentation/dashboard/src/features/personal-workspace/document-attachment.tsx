import {useState} from "react";

export const isTextDocument = (mime: string) => mime === "text/plain" || mime === "text/markdown";
export const isDocumentAttachment = (mime: string) => isTextDocument(mime) || mime === "application/octet-stream";

/** Render source as inert text; downloading preserves the original UTF-8 bytes. */
export function DocumentAttachment({dataUrl, name, size, zh}: {dataUrl: string; name: string; size: number; zh: boolean}) {
  const [open, setOpen] = useState(false);
  // Only the original-message API can supply returned file bytes. This does
  // not add a MIME type or remote URL to the input attachment allowlist.
  if (/^\/api\/chat\/sessions\/[A-Za-z0-9._-]{1,160}\/messages\/handoff\.[a-f0-9]{64}\/files\/[a-f0-9]{64}\.[0-3]$/.test(dataUrl)) {
    return <div className="personal-document-attachment"><a href={dataUrl} download={name}>
      {zh ? "下载文件：" : "Download file: "}{name} · {size.toLocaleString()} B
    </a></div>;
  }
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
