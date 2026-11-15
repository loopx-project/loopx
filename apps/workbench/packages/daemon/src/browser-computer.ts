import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium, type Browser, type Page } from 'playwright-core';

export function browserExecutable(): string | undefined {
  if (process.env.CALLI_BROWSER_EXECUTABLE) return existsSync(process.env.CALLI_BROWSER_EXECUTABLE) ? process.env.CALLI_BROWSER_EXECUTABLE : undefined;
  return [chromium.executablePath(), '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(path => existsSync(path));
}

export const computerActionSchema = {
  type: 'object', additionalProperties: false, required: ['action'],
  properties: {
    action: { type: 'string', enum: ['navigate', 'screenshot', 'click', 'type', 'key', 'scroll', 'wait', 'finish'] },
    url: { type: 'string', description: 'HTTP(S) URL, for navigate.' },
    x: { type: 'integer', description: 'Screenshot pixel coordinate, for click.' },
    y: { type: 'integer', description: 'Screenshot pixel coordinate, for click.' },
    text: { type: 'string', description: 'Text to insert in the focused field, for type.' },
    key: { type: 'string', description: 'Enter, Tab, Escape, Backspace, Delete, ArrowUp/Down/Left/Right, Home, End, PageUp/Down or ControlOrMeta+A.' },
    deltaY: { type: 'integer', description: 'Vertical scroll in pixels, between -1600 and 1600.' },
    milliseconds: { type: 'integer', description: 'For wait, between 100 and 2000.' },
  },
};

export interface ComputerResult {
  image: Buffer;
  evidence: { runtime: 'calli-browser'; scope: 'browser'; action: string; url: string; actions: number; screenshot?: string };
}

/** A fresh browser per delegated task. Never attaches to a person's browser or profile. */
export class BrowserComputer {
  private browser?: Browser;
  private page?: Page;
  private opening?: Promise<Page>;
  private closed = false;
  private finished = false;
  private attempts = 0;
  private actions = 0;
  private tail: Promise<unknown> = Promise.resolve();
  private timer?: ReturnType<typeof setTimeout>;

  constructor(private readonly cwd: string, private readonly executable = browserExecutable()) {}

  execute(raw: unknown): Promise<ComputerResult> {
    const result = this.tail.then(() => this.perform(raw));
    this.tail = result.catch(() => undefined);
    return result;
  }

  private async open(): Promise<Page> {
    if (this.closed) throw new Error('Browser task is closed.');
    if (this.page) return this.page;
    if (!this.opening) this.opening = (async () => {
      if (!this.executable) throw new Error('No Chrome/Chromium executable. Set CALLI_BROWSER_EXECUTABLE on the daemon.');
      this.timer = setTimeout(() => { void this.close(); }, 300_000);
      this.timer.unref();
      const browser = await chromium.launch({ executablePath: this.executable, headless: true, chromiumSandbox: true, timeout: 20_000 });
      this.browser = browser;
      if (this.closed) { await browser.close(); throw new Error('Browser task was cancelled during startup.'); }
      const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, acceptDownloads: false, serviceWorkers: 'block' });
      // Scheme restriction is not a network sandbox: HTTP(S) can include local development sites.
      await context.route('**/*', route => /^https?:/.test(route.request().url()) ? route.continue() : route.abort());
      const page = await context.newPage();
      page.setDefaultTimeout(10_000);
      page.on('dialog', dialog => { void dialog.dismiss().catch(() => undefined); });
      page.on('download', download => { void download.cancel().catch(() => undefined); });
      // A task has one visible viewport; hidden popup windows must not become another control surface.
      context.on('page', popup => { void popup.close().catch(() => undefined); });
      this.page = page;
      return page;
    })();
    return this.opening;
  }

  private async perform(raw: unknown): Promise<ComputerResult> {
    if (this.closed || this.finished) throw new Error('Browser task is closed.');
    if (++this.attempts > 60) { await this.close(); throw new Error('Browser task exceeded 60 actions.'); }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Expected an action object.');
    const input = raw as Record<string, unknown>;
    const fields: Record<string, string[]> = { navigate: ['url'], screenshot: [], click: ['x', 'y'], type: ['text'], key: ['key'], scroll: ['deltaY'], wait: ['milliseconds'], finish: [] };
    const action = typeof input.action === 'string' ? input.action : '';
    if (!Object.hasOwn(fields, action) || Object.keys(input).some(k => k !== 'action' && !fields[action].includes(k))) throw new Error('Unknown action or action arguments.');
    const integer = (key: string, min: number, max: number): number => {
      const n = input[key];
      if (typeof n !== 'number' || !Number.isInteger(n) || n < min || n > max) throw new Error(`${key} must be an integer between ${min} and ${max}.`);
      return n;
    };
    // Validate before launching or touching the browser.
    let url: URL | undefined;
    if (action === 'navigate') {
      try { url = new URL(String(input.url)); } catch { throw new Error('navigate requires an HTTP(S) URL.'); }
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.href.length > 4000) throw new Error('navigate requires HTTP(S) without embedded credentials.');
    }
    if (action === 'click') { integer('x', 0, 1279); integer('y', 0, 799); }
    if (action === 'type' && (typeof input.text !== 'string' || !input.text.length || input.text.length > 4000)) throw new Error('type requires 1–4000 characters.');
    if (action === 'key' && (typeof input.key !== 'string' || !/^(Enter|Tab|Escape|Backspace|Delete|Arrow(Up|Down|Left|Right)|Home|End|Page(Up|Down)|ControlOrMeta\+A)$/.test(input.key))) throw new Error('Unsupported key.');
    if (action === 'scroll') integer('deltaY', -1600, 1600);
    if (action === 'wait') integer('milliseconds', 100, 2000);
    const page = await this.open();
    if (this.closed) throw new Error('Browser task is closed.');
    if (action === 'navigate') await page.goto(url!.href, { waitUntil: 'domcontentloaded', timeout: 20_000 });
    if (!/^https?:/.test(page.url())) throw new Error('Navigate to an HTTP(S) page first.');
    if (action === 'click') await page.mouse.click(input.x as number, input.y as number);
    if (action === 'type') await page.keyboard.insertText(input.text as string);
    if (action === 'key') await page.keyboard.press(input.key as string);
    if (action === 'scroll') await page.mouse.wheel(0, input.deltaY as number);
    if (action === 'wait') await page.waitForTimeout(input.milliseconds as number);
    else if (['click', 'type', 'key', 'scroll'].includes(action)) await page.waitForTimeout(150);
    if (!/^https?:/.test(page.url())) throw new Error('The page left the supported HTTP(S) browser surface.');
    const image = await page.screenshot({ type: 'png', timeout: 10_000 });
    if (this.closed) throw new Error('Browser task is closed.');
    this.actions++;
    const evidence: ComputerResult['evidence'] = { runtime: 'calli-browser', scope: 'browser', action, url: page.url(), actions: this.actions };
    if (action === 'finish') {
      evidence.screenshot = join(this.cwd, 'computer-final.png');
      writeFileSync(evidence.screenshot, image, { mode: 0o600, flag: 'wx' });
      this.finished = true;
      await this.close();
    }
    return { image, evidence };
  }

  async close(): Promise<void> {
    this.closed = true;
    clearTimeout(this.timer);
    // Startup may still be awaiting launch; it observes closed and closes its own process.
    await this.browser?.close().catch(() => undefined);
    await this.opening?.catch(() => undefined);
    await this.browser?.close().catch(() => undefined);
  }
}
