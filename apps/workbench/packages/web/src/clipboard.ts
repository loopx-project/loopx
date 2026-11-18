/**
 * Copies text. The hub is usually reached over plain http on a VPN address, which is not a
 * "secure context", so navigator.clipboard is missing there. The fallback selects a hidden
 * textarea and uses execCommand, which still works on such pages when called from a click.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
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
