/**
 * The words on screen, in the language chosen on this device.
 *
 * Every string the hub shows lives in `locales/`, one file per language. `zh.ts` is the source:
 * the other files are typed against it, so a key that is missing, extra or takes the wrong
 * arguments fails the typecheck. A message with values in it is a function, which keeps the
 * arguments typed and lets each language put them where its grammar wants them, plurals included.
 *
 * The language is settled once, before the first render, and changing it reloads the page. That
 * keeps `t` a plain function: nothing has to subscribe, and strings built at import time are right.
 */
import { en } from './locales/en';
import { ja } from './locales/ja';
import { zh, type Messages } from './locales/zh';

export type Locale = 'zh' | 'en' | 'ja';
/** What the user picked: a language, or whatever the browser asks for. */
export type LocaleChoice = Locale | 'system';

/** The languages on offer, each named in itself so it can be found by someone who cannot read the others. */
export const LOCALES: ReadonlyArray<{ id: Locale; name: string; tag: string }> = [
  { id: 'zh', name: '简体中文', tag: 'zh-CN' },
  { id: 'en', name: 'English', tag: 'en-US' },
  { id: 'ja', name: '日本語', tag: 'ja-JP' },
];

const DICTIONARIES: Record<Locale, Messages> = { zh, en, ja };

/** Where the choice is kept on this device. */
export const LOCALE_KEY = 'calli.locale';

/** The first of the browser's languages that the hub speaks; English when it speaks none of them. */
export function detectLocale(languages: readonly string[]): Locale {
  for (const lang of languages) {
    const base = lang.toLowerCase().split(/[-_]/)[0];
    if (base === 'zh' || base === 'en' || base === 'ja') return base;
  }
  return 'en';
}

export function parseLocaleChoice(raw: string | null): LocaleChoice {
  return raw === 'zh' || raw === 'en' || raw === 'ja' ? raw : 'system';
}

export function resolveLocale(choice: LocaleChoice, languages: readonly string[]): Locale {
  return choice === 'system' ? detectLocale(languages) : choice;
}

function readChoice(): LocaleChoice {
  try {
    return parseLocaleChoice(localStorage.getItem(LOCALE_KEY));
  } catch {
    return 'system';
  }
}

function startingLocale(): Locale {
  // Outside a browser (the unit tests, build tooling) there is nobody to ask: the source language.
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return 'zh';
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language ?? ''];
  return resolveLocale(readChoice(), languages);
}

let current: Locale = startingLocale();

/** The language on screen now. */
export function locale(): Locale {
  return current;
}

/** The BCP 47 tag for the language on screen, for `Intl` and `<html lang>`. */
export function localeTag(): string {
  return LOCALES.find((l) => l.id === current)!.tag;
}

/** What was picked on this device, before "follow the system" is resolved. */
export function localeChoice(): LocaleChoice {
  return readChoice();
}

/** Switches the language for the rest of this process. For tests; the page uses `chooseLocale`. */
export function setLocale(next: Locale): void {
  current = next;
}

/** Keeps the choice on this device and starts the page over in it. */
export function chooseLocale(choice: LocaleChoice): void {
  try {
    if (choice === 'system') localStorage.removeItem(LOCALE_KEY);
    else localStorage.setItem(LOCALE_KEY, choice);
  } catch {
    // Storage unavailable: there is nowhere to keep the choice across the reload.
    return;
  }
  location.reload();
}

export type MessageKey = keyof Messages;
type Args<K extends MessageKey> = Messages[K] extends (...args: infer A) => string ? A : [];

/** The message for `key` in the language on screen, with its values filled in. */
export function t<K extends MessageKey>(key: K, ...args: Args<K>): string {
  const message: string | ((...a: never[]) => string) = DICTIONARIES[current][key] ?? zh[key];
  return typeof message === 'function' ? (message as (...a: unknown[]) => string)(...args) : message;
}

/** A date and time, in the language on screen. */
export function formatDateTime(at: Date | string | number, options?: Intl.DateTimeFormatOptions): string {
  return new Date(at).toLocaleString(localeTag(), options);
}

export function formatDate(at: Date | string | number, options?: Intl.DateTimeFormatOptions): string {
  return new Date(at).toLocaleDateString(localeTag(), options);
}

export function formatTime(at: Date | string | number, options?: Intl.DateTimeFormatOptions): string {
  return new Date(at).toLocaleTimeString(localeTag(), options);
}

export function formatNumber(n: number, options?: Intl.NumberFormatOptions): string {
  return n.toLocaleString(localeTag(), options);
}

/** A plain enumeration with the language's separator: "a, b, c", "a、b、c". */
export function formatList(items: readonly string[]): string {
  return new Intl.ListFormat(localeTag(), { style: 'narrow', type: 'conjunction' }).format(items);
}
