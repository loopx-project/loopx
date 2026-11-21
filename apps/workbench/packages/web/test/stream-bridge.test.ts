import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StreamBridgeOption } from '../src/components/StreamBridgeOption.js';
import { t, setLocale } from '../src/i18n/index.js';

const capability = { platform: 'Linux x64', temporaryMegabytes: 106 };
test('stream bridge consent has an accessible separate switch, platform and fallback hint in every locale', () => {
  for (const locale of ['zh', 'en', 'ja'] as const) {
    setLocale(locale);
    try {
      const html = renderToStaticMarkup(createElement(StreamBridgeOption, { capability, value: false, onChange: () => undefined }));
      assert.ok(html.includes('role="switch"')); assert.ok(html.includes('aria-checked="false"'));
      assert.ok(html.includes('aria-describedby='));
      assert.ok(html.includes('Linux x64')); assert.ok(html.includes('106 MB'));
      assert.ok(t('streamBridge.incompatible', 'reason-sample').includes('reason-sample'), 'the fallback note interpolates the reason');
      const disabled = renderToStaticMarkup(createElement(StreamBridgeOption, { capability, value: true, onChange: () => undefined, disabled: true, compact: true }));
      assert.ok(disabled.includes('aria-checked="true"')); assert.ok(disabled.includes('disabled=""'));
      assert.equal(disabled.includes('106 MB'), false, 'the compact discovery row keeps the detailed resource note in settings');
    } finally { setLocale('zh'); }
  }
});
