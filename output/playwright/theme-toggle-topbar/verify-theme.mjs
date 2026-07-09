import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const outDir = 'C:/sidekick/sidekick/output/playwright/theme-toggle-topbar';
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--disable-gpu'],
});
const page = await browser.newPage({ viewport: { width: 1900, height: 1100 } });
const url = 'http://127.0.0.1:9119/session/5d8a8db4cc02?cachebust=1783477607091';
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(800);

const data = await page.evaluate(() => {
  const reboot = document.getElementById('btnRebootSidekick');
  const theme = document.getElementById('titlebarThemeToggle');
  const shutdown = document.getElementById('btnShutdownSidekick');
  const railTheme = document.getElementById('railThemeToggle');
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  };
  return {
    hasTitlebarTheme: !!theme,
    hasRailTheme: !!railTheme,
    ariaPressed: theme?.getAttribute('aria-pressed') ?? null,
    tooltip: theme?.getAttribute('data-tooltip') ?? null,
    darkBefore: document.documentElement.classList.contains('dark'),
    rebootRect: rect(reboot),
    themeRect: rect(theme),
    shutdownRect: rect(shutdown),
  };
});

if (!data.hasTitlebarTheme) throw new Error('titlebarThemeToggle missing');
if (data.hasRailTheme) throw new Error('railThemeToggle still present');
if (!data.rebootRect || !data.themeRect || !data.shutdownRect) throw new Error('missing topbar rects');
if (!(data.rebootRect.x < data.themeRect.x && data.themeRect.x < data.shutdownRect.x)) throw new Error(`unexpected topbar order: ${JSON.stringify({ reboot: data.rebootRect, theme: data.themeRect, shutdown: data.shutdownRect })}`);
if (Math.abs(data.rebootRect.y - data.themeRect.y) > 6) throw new Error(`theme toggle not aligned with reboot: ${JSON.stringify({ reboot: data.rebootRect, theme: data.themeRect })}`);

await page.click('#titlebarThemeToggle');
await page.waitForTimeout(300);
const darkAfter = await page.evaluate(() => document.documentElement.classList.contains('dark'));
await page.click('#titlebarThemeToggle');
await page.waitForTimeout(300);
const darkRestored = await page.evaluate(() => document.documentElement.classList.contains('dark'));
await page.screenshot({ path: path.join(outDir, 'desktop-topbar-theme-toggle.png'), fullPage: false });

console.log(JSON.stringify({ ...data, darkAfter, darkRestored }, null, 2));
await browser.close();
