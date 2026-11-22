import type { SignalMaterial } from './api';
import { t } from './i18n';

export const SIGNAL_MATERIAL_MAX_CHARS = 32_000;

/** Limit parsing and layout work even if an older hub does not bound its response. */
export function boundedSignalMaterial(content: string | null): { text: string; clipped: boolean } {
  const text = content ?? '';
  if (text.length <= SIGNAL_MATERIAL_MAX_CHARS) return { text, clipped: false };
  return { text: text.slice(0, SIGNAL_MATERIAL_MAX_CHARS).replace(/[\uD800-\uDBFF]$/, ''), clipped: true };
}

/** A row can report metadata without fetching or constructing the material body. */
export function signalMaterialLine(material: SignalMaterial | null | undefined, bodyRead: boolean): { text: string; warn: boolean } {
  switch (material?.state) {
    case 'not-ready': return { text: t('desk.material.not-ready'), warn: true };
    case 'failed': return { text: t('desk.material.failed'), warn: true };
    case 'empty': return { text: t(material.partial ? 'desk.material.partialEmptyShort' : 'desk.material.empty'), warn: false };
    case 'ready': return { text: t(bodyRead ? 'desk.material.ready' : 'desk.material.available'), warn: false };
    default: return { text: t('desk.material.unread'), warn: false };
  }
}

/** Only a complete ready read is final; the other states offer an explicit fresh GET. */
export function canRefreshSignalMaterial(material: SignalMaterial | null | undefined): boolean {
  return material?.state !== 'ready' || material.partial === true;
}

/** Source links are separate from reading, and cannot execute a script or open a local file. */
export function signalSourceUrl(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null;
  } catch {
    return null;
  }
}
