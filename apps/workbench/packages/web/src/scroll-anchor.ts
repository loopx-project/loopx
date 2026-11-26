// Reading position belongs to the live message DOM node; switching threads or handover segments resets it.
export interface ReadingAnchor {
  element: HTMLElement;
  offset: number;
  height: number;
}

export function captureReadingAnchor(scroller: HTMLElement): ReadingAnchor | null {
  const box = scroller.getBoundingClientRect();
  // Content scrolls beneath the glass header; anchor the paragraph below the header even inside long replies.
  const top = box.top + Math.min(80, scroller.clientHeight / 4);
  let fallback: HTMLElement | null = null;
  for (const row of scroller.querySelectorAll<HTMLElement>('[data-message-key]')) {
    const rect = row.getBoundingClientRect();
    if (rect.height === 0 || rect.bottom <= top || rect.top >= box.bottom) continue;
    let element = row;
    const blocks = row.querySelectorAll<HTMLElement>('.markdown > *');
    let visibleBlock = false;
    for (const block of blocks) {
      const bounds = block.getBoundingClientRect();
      // Image placeholders grow after loading; prefer visible text blocks below them.
      if (bounds.height > 0 && bounds.bottom > top && bounds.top < box.bottom && !block.classList.contains('file-images')) { element = block; visibleBlock = true; break; }
    }
    if (blocks.length && !visibleBlock) { fallback ??= row; continue; }
    return { element, offset: element.getBoundingClientRect().top - box.top, height: scroller.scrollHeight };
  }
  return fallback ? { element: fallback, offset: fallback.getBoundingClientRect().top - box.top, height: scroller.scrollHeight } : null;
}

// Returns the scrollTop delta applied, or null when the anchored node was replaced.
export function restoreReadingAnchor(scroller: HTMLElement, anchor: ReadingAnchor | null): number | null {
  if (!anchor || !scroller.contains(anchor.element)) return null;
  const delta = anchor.element.getBoundingClientRect().top - scroller.getBoundingClientRect().top - anchor.offset;
  const previous = scroller.scrollTop;
  if (Math.abs(delta) > 0.5) scroller.scrollTop += delta;
  return scroller.scrollTop - previous;
}
