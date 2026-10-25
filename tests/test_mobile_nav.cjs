// The navigation bar must stay pinned to the top while a phone-sized page scrolls (both styles).
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const ui = process.env.NETPROBE_UI || path.join(root, 'src/index.html');
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  try {
    for (const style of ['netprobe', 'unifi']) {
      for (const [width, height] of [[390, 844], [768, 1024]]) {
        const page = await browser.newPage({viewport: {width, height}, reducedMotion: 'reduce', locale: 'en-US'});
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.addInitScript(s => { try { localStorage.setItem('netprobe.style', s); localStorage.setItem('netprobe.lang', 'en'); } catch (e) {} }, style);
        await page.route('**/*', route => {
          const url = new URL(route.request().url());
          if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(ui, 'utf8')});
          return route.fulfill({status: 503, json: {error: 'offline'}});
        });
        await page.goto('http://netprobe.test/');
        await page.waitForTimeout(150);
        const label = `${style} ${width}px`;
        assert.equal(await page.evaluate(() => document.documentElement.dataset.style), style, label + ': style applied');
        const total = await page.evaluate(() => document.documentElement.scrollHeight);
        assert(total > height * 1.5, `${label}: page must be long enough to scroll (${total}px)`);
        for (const y of [400, 900, total - height]) {
          await page.evaluate(v => window.scrollTo(0, v), y);
          await page.waitForTimeout(50);
          const box = await page.locator('aside').boundingBox();
          assert(box.y >= -0.5 && box.y <= 10, `${label}: menu must stay at the top after scrolling to ${y}px, but is at y=${box.y}`);
          const links = await page.locator('aside nav a').evaluateAll(list => list.map(a => { const r = a.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight && r.width > 0; }));
          assert(links.every(Boolean), `${label}: every menu link must be visible after scrolling to ${y}px`);
          const covered = await page.evaluate(() => { const r = document.querySelector('aside nav a').getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !hit || !hit.closest('aside'); });
          assert(!covered, `${label}: something covers the pinned menu`);
        }
        // the pinned bar must not hide the section a menu link jumps to
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.locator('aside nav a[href="#speedtest"]').click();
        await page.waitForTimeout(100);
        const gap = await page.evaluate(() => {
          const bar = document.querySelector('aside').getBoundingClientRect();
          const target = [...document.querySelectorAll('#speedtest')].find(e => e.offsetParent !== null) || document.querySelector('#speedtest');
          return target.getBoundingClientRect().top - bar.bottom;
        });
        assert(gap >= -1, `${label}: the target of a menu link is hidden under the pinned menu (gap ${gap}px)`);
        const bar = (await page.locator('aside').boundingBox()).height;
        assert(bar <= height * 0.25, `${label}: the pinned menu may take at most a quarter of the screen, it takes ${bar}px of ${height}px`);
        if (width === 390) {
          assert(bar <= 64, `${label}: on a phone the pinned menu is one row (at most 64px), it takes ${bar}px`);
          assert(await page.evaluate(() => { const nav = document.querySelector('aside nav'); return nav.scrollWidth <= nav.clientWidth + 1; }), `${label}: every menu link is visible without scrolling the menu`);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, label + ': no horizontal overflow');
        assert.deepEqual(errors, [], label + ': page errors');
        await page.close();
      }
    }
    // desktop layout keeps its fixed side bar
    const desktop = await browser.newPage({viewport: {width: 1400, height: 900}});
    await desktop.route('**/*', route => new URL(route.request().url()).pathname === '/' ? route.fulfill({contentType: 'text/html', body: fs.readFileSync(ui, 'utf8')}) : route.fulfill({status: 503, json: {}}));
    await desktop.goto('http://netprobe.test/');
    await desktop.evaluate(() => window.scrollTo(0, 800));
    const side = await desktop.locator('aside').boundingBox();
    assert(side.y >= -0.5 && side.y <= 1 && side.width > 150, 'desktop side bar stays pinned');
    console.log('PASS: menu stays pinned while scrolling (phone and tablet widths, both styles), does not cover jump targets, desktop sidebar unchanged');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
