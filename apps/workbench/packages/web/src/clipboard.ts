/**
 * Copies text. The hub is usually reached over plain http on a VPN address, which is not a
 * "secure context", so navigator.clipboard is missing there. The fallback selects a hidden
 * textarea and uses execCommand, which still works on such pages when called from a click.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      forgetCopiedQuote();
      return true;
    }
  } catch {
    // Permission refused or unsupported: try the fallback.
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  // On screen but invisible: iOS will not copy from an element that is display:none or off-screen.
  Object.assign(area.style, { position: 'fixed', top: '0', left: '0', opacity: '0', fontSize: '16px' });
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  area.remove();
  if (ok) forgetCopiedQuote();
  return ok;
}

/** A passage as a Markdown block quote, so a plain-text box (and the agent) still sees it as quoted. */
export function quoteMarkdown(text: string): string {
  return text
    .trim()
    .split('\n')
    .map(line => `> ${line}`.trimEnd())
    .join('\n');
}

/** A readable, portable pointer: the hub id is not an agent's native session id. */
export function conversationPointer(thread: { id: string; title: string }, turnId?: string, machineId?: string): string {
  const query = [turnId ? `turn=${encodeURIComponent(turnId)}` : '', machineId ? `machine=${encodeURIComponent(machineId)}` : ''].filter(Boolean).join('&');
  return `LoopX: ${thread.title.replace(/\s+/g, ' ').trim()}\nloopx://thread/${encodeURIComponent(thread.id)}${query ? `?${query}` : ''}`;
}

export interface PastedPointer { threadId: string; title: string; machineId?: string; turnId?: string; quote?: string }

/** The title is display-only; the receiving hub resolves the id and supplies canonical metadata. */
export function pastedPointer(pasted: string): PastedPointer | null {
  const match = /^(?:LoopX|Calli): ([^\n]*)\n(?:loopx|calli):\/\/thread\/([^\s?#]+)(?:\?([^\s#]+))?(?:\n\n([\s\S]+))?$/.exec(pasted.replace(/\r\n?/g, '\n').trim());
  if (!match) return null;
  try {
    const valid = (value: string) => Boolean(value && value.length <= 500 && !/[\s\x00-\x1f]/.test(value));
    const threadId = decodeURIComponent(match[2]);
    if (!valid(threadId)) return null;
    const query = new Map<string, string>();
    for (const part of match[3]?.split('&') ?? []) {
      const [key, encoded, extra] = part.split('=');
      if (!['turn', 'machine'].includes(key) || encoded === undefined || extra !== undefined || query.has(key)) return null;
      const value = decodeURIComponent(encoded);
      if (!valid(value)) return null;
      query.set(key, value);
    }
    const turnId = query.get('turn');
    const machineId = query.get('machine');
    const lines = match[4]?.split('\n');
    if (lines && (!turnId || !lines.every(line => line.startsWith('>')))) return null;
    return { threadId, title: match[1], ...(turnId ? { turnId } : {}), ...(machineId ? { machineId } : {}),
      ...(lines ? { quote: lines.map(line => line.replace(/^>\s?/, '')).join('\n').trim() } : {}) };
  } catch { return null; }
}

export function copiedQuoteText(quote: CopiedQuote, title: string): string {
  return quote.threadId && quote.turnId ? `${conversationPointer({ id: quote.threadId, title }, quote.turnId, quote.machineId)}\n\n${quoteMarkdown(quote.text)}` : quoteMarkdown(quote.text);
}

const COPIED_QUOTE = 'calli.copiedQuote';

/** An ordinary copy must not inherit provenance from a previous copy-as-quote action. */
function forgetCopiedQuote(): void {
  try { localStorage.removeItem(COPIED_QUOTE); } catch { /* storage unavailable */ }
}

/** A message copied as a quote, remembered so pasting it into a box brings back a quote, not plain lines. */
export interface CopiedQuote { text: string; threadId?: string; turnId?: string; host?: string | null; machineId?: string }

export function rememberCopiedQuote(quote: CopiedQuote): void {
  try { localStorage.setItem(COPIED_QUOTE, JSON.stringify(quote)); } catch { /* storage off: paste stays plain */ }
}

/**
 * What a paste quotes, when it is a quote: the message this page last copied as one (with where it
 * came from), or any text made only of Markdown `> ` lines, such as a quote copied from another app.
 */
export function pastedQuote(pasted: string): CopiedQuote | null {
  const plain = pasted.replace(/\r\n?/g, '\n').trim();
  if (!plain) return null;
  try {
    const copied = JSON.parse(localStorage.getItem(COPIED_QUOTE) ?? 'null') as CopiedQuote | null;
    if (copied && typeof copied.text === 'string' && (plain === quoteMarkdown(copied.text) || plain === copied.text.trim())) return copied;
  } catch { /* unreadable: fall through to the plain form */ }
  const lines = plain.split('\n');
  if (!lines.every(line => line.startsWith('>'))) return null;
  const text = lines.map(line => line.replace(/^>\s?/, '')).join('\n').trim();
  return text ? { text } : null;
}

/**
 * Copies text with an HTML twin, so a rich editor (Lark, Slack, Notion, mail) pastes the formatted
 * version and a plain one gets the text. The copy event works on plain http and keeps the click's
 * permission on iOS; the async API is the second try, plain text the last.
 */
export async function copyRich(text: string, html: string): Promise<boolean> {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  Object.assign(area.style, { position: 'fixed', top: '0', left: '0', opacity: '0', fontSize: '16px' });
  let written = false;
  const onCopy = (event: ClipboardEvent) => {
    if (!event.clipboardData) return;
    event.clipboardData.setData('text/plain', text);
    event.clipboardData.setData('text/html', html);
    event.preventDefault();
    written = true;
  };
  document.addEventListener('copy', onCopy, true);
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  try {
    written = document.execCommand('copy') && written;
  } catch {
    written = false;
  }
  document.removeEventListener('copy', onCopy, true);
  area.remove();
  if (written) return true;
  try {
    if (window.isSecureContext && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      await navigator.clipboard.write([new ClipboardItem({
        'text/plain': new Blob([text], { type: 'text/plain' }),
        'text/html': new Blob([html], { type: 'text/html' }),
      })]);
      return true;
    }
  } catch {
    // Refused or unsupported: plain text below.
  }
  return copyText(text);
}
