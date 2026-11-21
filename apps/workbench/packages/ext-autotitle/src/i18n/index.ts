import type { Locale } from '@calli/extension-api/web';
import { zh } from './zh';
import { en } from './en';
import { ja } from './ja';

type Messages = typeof zh;
type Arguments<K extends keyof Messages> = Messages[K] extends (...args: infer A) => string ? A : [];
const locales: Record<Locale, Messages> = { zh, en, ja };

export function t<K extends keyof Messages>(locale: Locale, key: K, ...args: Arguments<K>): string;
export function t(locale: Locale, key: keyof Messages, ...args: unknown[]): string {
  const message = locales[locale][key];
  return typeof message === 'function' ? (message as (...values: unknown[]) => string)(...args) : message;
}
