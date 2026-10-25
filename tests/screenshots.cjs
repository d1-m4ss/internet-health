// Regenerates docs/screenshots from invented data (tests/demo_server.py): English UI, no real network involved.
//   node tests/screenshots.cjs [output-dir]        PYTHON=.venv/bin/python CHROME_PATH=/path/to/chrome
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const out = path.resolve(process.argv[2] || path.join(root, 'docs/screenshots'));

function startDemo(port, extra = []) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.PYTHON || 'python3', [path.join(__dirname, 'demo_server.py'), '--port', String(port), ...extra], {stdio: ['ignore', 'pipe', 'ignore']});
    const timer = setTimeout(() => reject(new Error('demo server did not start')), 240000);
    child.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(timer); resolve(child); } });
    child.on('exit', code => reject(new Error('demo server exited: ' + code)));
  });
}

(async () => {
  const [healthy, failing] = await Promise.all([startDemo(8799), startDemo(8798, ['--failing'])]);
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  const errors = [];
  async function open(port, style, scheme, width, height) {
    const ctx = await browser.newContext({viewport: {width, height}, locale: 'en-US', colorScheme: scheme, reducedMotion: 'reduce'});
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(s => { try { localStorage.setItem('netprobe.style', s); localStorage.setItem('netprobe.lang', 'en'); } catch (e) {} }, style);
    await page.goto('http://127.0.0.1:' + port + '/');
    await page.waitForFunction(() => /^LIVE/.test(document.getElementById('sourceTag').textContent), null, {timeout: 30000});
    await page.waitForFunction(() => document.getElementById('wanCard').hidden || !/Looking/.test(document.getElementById('wanIp').textContent), null, {timeout: 30000});
    await page.waitForTimeout(1200);
    return {ctx, page};
  }
  const local = ts => { const d = new Date(ts * 1000), z = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`; };
  try {
    for (const [name, style, scheme] of [['overview-unifi-light', 'unifi', 'light'], ['overview-unifi-dark', 'unifi', 'dark'], ['overview-netprobe-dark', 'netprobe', 'dark']]) {
      const {ctx, page} = await open(8799, style, scheme, 1440, 1000);
      await page.screenshot({path: `${out}/${name}.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 390, 844);
      await page.screenshot({path: `${out}/mobile-unifi-light.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 390, 844);
      await page.evaluate(() => window.scrollTo(0, 1400));
      await page.waitForTimeout(300);
      await page.screenshot({path: `${out}/mobile-menu-pinned.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 1440, 1000);
      await page.locator('#unifiTabs [data-tab="speedtest"]').click();
      await page.locator('#schedBox summary').click();
      await page.waitForTimeout(500);
      await page.locator('#speedtest').screenshot({path: `${out}/speedtest-schedule.png`});
      await page.locator('#unifiTabs [data-tab="settings"]').click();
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      await page.waitForTimeout(500);
      await page.locator('#settingsCard').screenshot({path: `${out}/settings-history.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 1440, 1000);
      await page.locator('#periodSelect').selectOption('86400');
      await page.waitForTimeout(1500);
      await page.locator('#targets').screenshot({path: `${out}/wan-targets.png`});
      const backup = await page.locator('#wanSelectChart option').evaluateAll(o => o.find(x => x.textContent.includes('Sample Mobile Backup')).value);
      await page.locator('#wanSelectChart').selectOption(backup);
      await page.waitForTimeout(1800);
      await page.locator('#diagnostics').screenshot({path: `${out}/wan-chart.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8798, 'unifi', 'light', 1440, 1000);
      await page.screenshot({path: `${out}/simulated-outage.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 1440, 1000);
      await page.locator('#compareToggle').click();
      await page.locator('#metricTabs [data-metric="avg_loss_pct"]').click();
      await page.locator('#metricTabs [data-metric="avg_latency_ms"]').click();
      await page.locator('#comparePicker summary').click();
      await page.locator('#cmpMenu [data-cmp-action="alldns"]').click();
      await page.waitForTimeout(1500);
      await page.locator('#chartTitle').click();
      await page.locator('#primaryChart').hover({position: {x: 700, y: 120}});
      await page.waitForTimeout(300);
      await page.locator('#diagnostics').screenshot({path: `${out}/compare-dns.png`});
      await ctx.close();
    }
    {
      const {ctx, page} = await open(8799, 'unifi', 'light', 1440, 1000);
      const now = Math.floor(Date.now() / 1000);
      await page.locator('#periodSelect').selectOption('custom');
      await page.locator('#rangeFrom').fill(local(now - 24 * 3600));
      await page.locator('#rangeTo').fill(local(now));
      await page.locator('#rangeApply').click();
      await page.waitForTimeout(1500);
      await page.locator('#unifiTabs [data-tab="loss"]').click();
      await page.waitForTimeout(300);
      await page.locator('#lossCard').screenshot({path: `${out}/loss-report.png`});
      await page.locator('#lossEpisodes [data-episode="0"]').click();
      await page.waitForTimeout(1500);
      await page.locator('#diagnostics').screenshot({path: `${out}/loss-zoom.png`});
      await ctx.close();
    }
    if (errors.length) throw new Error('browser errors: ' + JSON.stringify(errors));
    console.log('screenshots written to', out);
  } finally {
    await browser.close();
    healthy.removeAllListeners('exit'); failing.removeAllListeners('exit');
    healthy.kill(); failing.kill();
  }
})().catch(e => { console.error(e); process.exit(1); });
