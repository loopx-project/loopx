import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { detectLocale, formatList, locale, LOCALES, parseLocaleChoice, resolveLocale, setLocale, t } from '../src/i18n/index.js';
import { en } from '../src/i18n/locales/en.js';
import { zh } from '../src/i18n/locales/zh.js';
import { timeAgo } from '../src/sessions.js';
import { runSummary, type Step } from '../src/timeline.js';

/** Runs `fn` in another language and puts the source language back, so tests stay independent. */
function inLocale<T>(id: 'zh' | 'en', fn: () => T): T {
  setLocale(id);
  try {
    return fn();
  } finally {
    setLocale('zh');
  }
}

test('outside a browser the source language is used', () => {
  assert.equal(locale(), 'zh');
});

test('the first language the hub speaks wins, by its base tag', () => {
  assert.equal(detectLocale(['ja-JP', 'en-US']), 'en');
  assert.equal(detectLocale(['fr-FR', 'zh-TW', 'en']), 'zh');
  assert.equal(detectLocale(['en-GB']), 'en');
  assert.equal(detectLocale(['zh_CN']), 'zh');
  // Nothing it speaks: English, the widest-read.
  assert.equal(detectLocale(['fr-FR', 'de']), 'en');
  assert.equal(detectLocale([]), 'en');
});

test('a saved choice is read back; anything else follows the system', () => {
  assert.equal(parseLocaleChoice('ja'), 'system');
  assert.equal(parseLocaleChoice('en'), 'en');
  assert.equal(parseLocaleChoice(null), 'system');
  assert.equal(parseLocaleChoice('fr'), 'system');
  assert.equal(resolveLocale('system', ['ja']), 'en');
  assert.equal(resolveLocale('en', ['ja']), 'en');
});

test('every language has every message, of the same kind, and none is left blank by mistake', () => {
  const keys = Object.keys(zh).sort();
  for (const [name, messages] of Object.entries({ en })) {
    assert.deepEqual(Object.keys(messages).sort(), keys, `${name} has the same keys as zh`);
    for (const key of keys) {
      const source = zh[key as keyof typeof zh];
      const translated = messages[key as keyof typeof messages];
      assert.equal(typeof translated, typeof source, `${name} ${key} is a ${typeof source}`);
      if (typeof source === 'function') assert.equal((translated as (...a: never[]) => string).length, source.length, `${name} ${key} takes the same arguments`);
      // English leaves a word out where its grammar puts it elsewhere; the source never does.
      if (typeof translated === 'string' && translated === '') assert.ok(name === 'en', `${name} ${key} is empty`);
    }
  }
});

test('each language is offered under its own name', () => {
  assert.deepEqual(LOCALES.map((l) => l.id), ['zh', 'en']);
  assert.deepEqual(LOCALES.map((l) => l.name), ['简体中文', 'English']);
});

test('messages take their values, and English counts in the plural', () => {
  assert.equal(t('time.minutesAgo', 3), '3 分钟前');
  inLocale('en', () => {
    assert.equal(t('time.minutesAgo', 1), '1 minute ago');
    assert.equal(t('time.minutesAgo', 3), '3 minutes ago');
  });
});

test('helpers that build sentences follow the language on screen', () => {
  const now = Date.parse('2026-10-17T12:00:00Z');
  const twoHoursAgo = new Date(now - 2 * 3_600_000).toISOString();
  assert.equal(timeAgo(twoHoursAgo, now), '2 小时前');
  assert.equal(inLocale('en', () => timeAgo(twoHoursAgo, now)), '2 hours ago');

  const step = (id: string, kind: string): Step => ({ id, kind, title: id, status: 'completed', diffs: [] }) as unknown as Step;
  const steps = [step('a', 'read'), step('b', 'read'), step('c', 'search')];
  assert.equal(runSummary(steps).text, '已浏览 2 个文件，搜索 1 次');
  assert.equal(inLocale('en', () => runSummary(steps).text), 'Explored 2 files, searched 1 time');
});

test('lists are joined with the language\'s own separator', () => {
  assert.equal(formatList(['a', 'b', 'c']), 'a、b、c');
  assert.equal(inLocale('en', () => formatList(['a', 'b', 'c'])), 'a, b, c');
});

test('no Chinese is written into the interface outside the dictionaries', () => {
  // What may stay: the daemon's own wording that is parsed back, slash-menu search aliases, the
  // writing mode's marks the agent writes into bodies, and the dictionaries and their language
  // names. Anything else on screen goes through t().
  const allowed = [
    /^i18n\//,
    /^demo\/data\.ts$/,
  ];
  const parsed = /来自 |状态：|注释|评论：|文件：|（接手）|〔补〕|【待补|'失败'|'结果'|keywords: \[|^const (?:NEXT_STEP_HEADING|PERSONAL_CONFIRMATION_TEXT) = /;
  const offenders: string[] = [];
  const walk = (dir: string, rel = ''): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      const name = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(path, name);
      else if (/\.tsx?$/.test(entry.name) && !allowed.some((a) => a.test(name))) {
        readFileSync(path, 'utf8').split('\n').forEach((line, i) => {
          if (/[一-鿿]/.test(line) && !parsed.test(line)) offenders.push(`${name}:${i + 1}: ${line.trim()}`);
        });
      }
    }
  };
  walk(new URL('../src', import.meta.url).pathname);
  assert.deepEqual(offenders, []);
});
