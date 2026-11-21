export interface PreviewPick {
  target: { selector: string; text: string; html?: string; rect: { x: number; y: number; width: number; height: number } };
  context: { title: string; url: string; theme: string; variant: string; revision: string; viewport: { width: number; height: number } };
}
export interface PreviewNote extends PreviewPick {
  id: string;
  page: string;
  at: string;
  comment: string;
  receipt: { turnId: string } | { queued: true; id: string } | null;
}
/** A development server stays on its own origin, just like file previews. */
export function externalPreview(path: string, appOrigin: string): string | null {
  if (!/^https?:\/\//i.test(path)) return null;
  const url = new URL(path);
  if (url.username || url.password || url.origin === appOrigin) throw new Error('Preview must use a separate origin without credentials');
  return url.href;
}
export function isPreviewPick(value: unknown): value is PreviewPick {
  const v = value as PreviewPick | null;
  if (!v || typeof v.target?.selector !== 'string' || v.target.selector.length > 1500 || typeof v.target.text !== 'string' || v.target.text.length > 300 || typeof v.target.html !== 'string' || v.target.html.length > 6000 || !v.context) return false;
  return ['title', 'url', 'theme', 'variant', 'revision'].every(key => typeof v.context[key as keyof PreviewPick['context']] === 'string') && [v.target.rect?.x, v.target.rect?.y, v.target.rect?.width, v.target.rect?.height, v.context.viewport?.width, v.context.viewport?.height].every(n => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 100000);
}
