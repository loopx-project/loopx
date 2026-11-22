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
