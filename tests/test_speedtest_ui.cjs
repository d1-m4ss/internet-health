// Speedtest tab: service choice, schedule, notification and public-IP controls against a mocked API.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const ui = process.env.NETPROBE_UI || path.join(root, 'src/index.html');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  try {
    const now = Math.floor(Date.now() / 1000);
    const api = {
      prefs: {speedtest_service: '', speedtest_schedule: 'container', speedtest_interval_h: 6, speedtest_times: ['03:00'], speedtest_notify: false, show_public_ip: false}, publicIp: {enabled: false}, publicIpDelay: 0, delays: {}, statusSeq: [], status: {running: false, success: null, error: null, result: null}, runStatus: 202, prefsFail: false, prefsReadFail: false, speedRun: null, calls: [],
      latest: {ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server: {id: '1', name: 'Example City', country: 'EX'}, backend: 'python'},
      history: [{ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server_id: '1', server_name: 'Example City', backend: 'python'}],
    };
    async function open(style = 'netprobe', lang = 'en') {
      const page = await browser.newPage({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(([s, l]) => { try { localStorage.setItem('netprobe.style', s); localStorage.setItem('netprobe.lang', l); } catch (e) {} }, [style, lang]);
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(ui, 'utf8')});
        const body = request.method() === 'POST' ? request.postDataJSON() : undefined;
        api.calls.push({path: url.pathname, query: url.search, method: request.method(), headers: request.headers(), body});
        if (api.delays[url.pathname]) await new Promise(r => setTimeout(r, api.delays[url.pathname]));
        switch (url.pathname) {
          case '/api/config': return route.fulfill({json: {probe_interval: 30, ping_count: 4, sites: ['edge.example'], speedtest_backend: 'python', speedtest_secure: true, speedtest_enabled: true, speedtest_interval: 14400,
            speedtest_backends_available: {python: true, ookla: false, fast: true}, speedtest_ookla_accept_license: false}});
          case '/api/prefs':
            if (api.prefsReadFail && request.method() === 'GET') return route.fulfill({status: 503, json: {error: 'Persistent preferences cannot be read'}});
            if (request.method() === 'POST') {
              if (api.prefsFail) return route.fulfill({status: 500, json: {error: 'disk full'}});
              Object.assign(api.prefs, body);
              if ('show_public_ip' in body) api.publicIp = body.show_public_ip ? {enabled: true, ip: '198.51.100.7', isp: 'Example Net', source: 'ipwho.is', checked_at: now, error: null} : {enabled: false};
            }
            return route.fulfill({json: {prefs: api.prefs, schedule: {next_ts: api.prefs.speedtest_schedule === 'daily' ? now + 7200 : null, timezone: 'Europe/Bucharest'}}});
          case '/api/public-ip': if (api.publicIpDelay) await new Promise(r => setTimeout(r, api.publicIpDelay)); return route.fulfill({json: api.publicIp});
          case '/api/speedtest/latest': return route.fulfill({json: {result: api.latest}});
          case '/api/speedtest/history': return route.fulfill({json: {tests: api.history}});
          case '/api/speedtest/run': return route.fulfill(api.runStatus === 202 ? {status: 202, json: {success: true, started: true}} : {status: api.runStatus, json: {success: false, error: 'A Speedtest is already running. Wait for it to finish and try again.'}});
          case '/api/speedtest/status': return route.fulfill({json: api.statusSeq.length ? api.statusSeq.shift() : api.status});
          case '/api/settings': return route.fulfill({json: {settings: {enabled: true, loss_pct: 10, latency_ms: 150, jitter_ms: 80, dns_ms: 250, confirm_samples: 2, recovery_samples: 2, turbo: false}, storage: {bytes: 1, retention_days: null}, started_at: now - 60}});
          case '/api/score/latest': return route.fulfill({json: {data: {ts: now, score: 90, avg_loss_pct: 0, avg_latency_ms: 20, avg_jitter_ms: 2, avg_dns_latency_ms: 10}}});
          case '/api/loss': return route.fulfill({json: {truncated: false, data: {targets: [], episodes: [], outages: [], summary: {}, window: {from: now - 3600, to: now}}}});
          case '/api/logs/live': return route.fulfill({json: {lines: [], next_seq: 0}});
          default: return route.fulfill({json: {data: []}});
        }
      });
      await page.goto('http://netprobe.test/');
      await page.waitForFunction(() => document.getElementById('speedBtn').textContent.trim() !== '…');
      return {page, errors};
    }
    const service = page => page.locator('#speedBackend');

    // --- the service list: three real services, each with an explanation; unavailable ones are disabled ---
    {
      const {page, errors} = await open();
      assert.deepEqual(await service(page).locator('option').evaluateAll(o => o.map(x => x.value)), ['', 'python', 'ookla', 'fast']);
      assert.match(await service(page).locator('option').nth(0).textContent(), /As set in the container: Speedtest\.net/);
      assert.match(await service(page).locator('option[value="fast"]').textContent(), /Fast\.com \(Netflix\)/);
      assert.match(await service(page).locator('option[value="python"]').textContent(), /Speedtest\.net.*Ookla servers/);
      assert.equal(await service(page).locator('option[value="ookla"]').evaluate(o => o.disabled), true, 'the official CLI is not installed');
      assert.equal(await service(page).locator('option[value="fast"]').evaluate(o => o.disabled), false);
      assert.equal(await service(page).inputValue(), '', 'the saved preference (follow the container) is shown');
      assert.match(await page.locator('#serviceNote').textContent(), /Speedtest\.net servers/);
      assert.match(await page.locator('#speedServer').textContent(), /Speedtest\.net · Example City/, 'history shows a service name, not a library name');

      // choosing fast.com saves immediately, with the same-origin header, and adapts the form
      await service(page).selectOption('fast');
      await page.waitForFunction(() => /Service saved/.test(document.getElementById('speedMessage').textContent));
      const post = api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST').pop();
      assert.deepEqual(post.body, {speedtest_service: 'fast'});
      assert.equal(post.headers['x-netprobe-request'], 'settings');
      assert.match(await page.locator('#serviceNote').textContent(), /Netflix servers/);
      assert.equal(await page.locator('#serverId').isDisabled(), true, 'a server id does not apply to fast.com');
      assert.equal(await page.locator('#secure').isDisabled(), true);
      assert.equal(await page.locator('#forceAuto').isDisabled(), true);
      assert.equal(await page.locator('#speedBtn').isDisabled(), false);

      // a manual run starts in the background (the page must not wait for it) and uses fast.com;
      // an unmeasured upload is shown as a dash, never as zero
      const result = {timestamp: now, ping_ms: 11.5, download_mbps: 321.5, upload_mbps: null, server: {name: 'Netflix, Brasov', country: 'RO'}, backend: 'fast'};
      api.statusSeq = [{running: true, success: null}, {running: true, success: null}];
      api.status = {running: false, success: true, error: null, result};
      api.latest = {ts: now, ...result};  // what the server returns after the run
      await page.locator('#speedBtn').click();
      await page.waitForFunction(() => document.getElementById('speedBtn').textContent.trim() === 'Running…' || document.getElementById('speedBtn').disabled);
      assert.equal(await page.locator('#speedBtn').isDisabled(), true, 'the button is locked while the test runs');
      await page.waitForFunction(() => document.getElementById('download').textContent.startsWith('321.50'), null, {timeout: 15000});
      const run = api.calls.filter(c => c.path === '/api/speedtest/run').pop();
      assert.equal(run.body.async, true, 'asks the server to run it in the background');
      assert.equal(run.body.backend, 'fast');
      assert.equal(run.body.server_id, null);
      assert(api.calls.filter(c => c.path === '/api/speedtest/status').length >= 3, 'the page polls the status');
      assert.match(await page.locator('#upload').textContent(), /^—/, 'unknown upload is a dash');
      assert.match(await page.locator('#speedServer').textContent(), /Fast\.com · Netflix, Brasov · RO/);
      await page.waitForFunction(() => !document.getElementById('speedBtn').disabled);

      // a failed test: the reason is shown and the button is usable again
      api.status = {running: false, success: false, error: 'fast.com is not reachable: timed out', result: null};
      await page.locator('#speedBtn').click();
      await page.waitForFunction(() => /fast\.com is not reachable: timed out/.test(document.getElementById('speedMessage').textContent), null, {timeout: 10000});
      await page.waitForFunction(() => !document.getElementById('speedBtn').disabled);

      // the server refuses a second test: the message says so, nothing keeps polling
      api.runStatus = 409;
      await page.locator('#speedBtn').click();
      await page.waitForFunction(() => /already running/.test(document.getElementById('speedMessage').textContent));
      await page.waitForFunction(() => !document.getElementById('speedBtn').disabled);
      api.runStatus = 202;
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- the saved choice is shown after a reload; a failed save is reverted and explained ---
    {
      const {page, errors} = await open('unifi', 'ru');
      await page.locator('#unifiTabs [data-tab="speedtest"]').click();
      assert.equal(await service(page).inputValue(), 'fast', 'preference survives a reload');
      assert.match(await page.locator('#serviceNote').textContent(), /серверов Netflix/);
      api.prefsFail = true;
      await service(page).selectOption('python');
      await page.waitForFunction(() => /Не удалось сохранить сервис/.test(document.getElementById('speedMessage').textContent));
      assert.equal(await service(page).inputValue(), 'fast', 'the control returns to what is really saved');
      assert.match(await page.locator('#speedMessage').textContent(), /disk full/);
      api.prefsFail = false;
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- without the preferences API (extension not loaded) the manual choice still works for one run ---
    {
      const page = await browser.newPage({viewport: {width: 1280, height: 900}});
      await page.addInitScript(() => { try { localStorage.setItem('netprobe.lang', 'en'); } catch (e) {} });
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(ui, 'utf8')});
        if (url.pathname === '/api/config') return route.fulfill({json: {speedtest_backend: 'python', speedtest_backends_available: {python: true, ookla: false}}});
        if (url.pathname === '/api/speedtest/latest') return route.fulfill({json: {result: null}});
        return route.fulfill({status: 404, json: {error: 'not found'}});
      });
      await page.goto('http://netprobe.test/');
      await page.waitForFunction(() => document.getElementById('speedBtn').textContent.trim() !== '…');
      assert.equal(await page.locator('#speedBackend option[value="fast"]').evaluate(o => o.disabled), true, 'fast.com needs the extension');
      assert.equal(await page.locator('#speedBackend').inputValue(), '');
      await page.close();
    }
    // --- schedule and notifications ---
    {
      const {page, errors} = await open();
      const posts = () => api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST');
      await page.locator('#schedBox summary').click();
      assert.equal(await page.locator('#schedMode').inputValue(), 'container');
      assert.match(await page.locator('#schedMode option').nth(0).textContent(), /As set in the container: every 4 h/);
      assert.match(await page.locator('#schedNote').textContent(), /SPEEDTEST_ENABLED and SPEEDTEST_INTERVAL \(every 4 h\)/);
      assert.equal(await page.locator('#schedIntervalWrap').isVisible(), false);
      assert.equal(await page.locator('#schedTimesWrap').isVisible(), false);

      await page.locator('#schedMode').selectOption('daily');
      assert.equal(await page.locator('#schedTimesWrap').isVisible(), true, 'daily mode asks for times');
      assert.equal(await page.locator('#schedIntervalWrap').isVisible(), false);
      assert.match(await page.locator('#schedNote').textContent(), /Save to apply/);
      const before = posts().length;
      for (const bad of ['25:00', '', '03:00, 03:00 , 4:5', '01:00,02:00,03:00,04:00,05:00']) {
        await page.locator('#schedTimes').fill(bad);
        await page.locator('#schedForm button[type="submit"]').click();
        assert.match(await page.locator('#schedMessage').textContent(), /Times are 1 to 4 different HH:MM/, 'rejects ' + JSON.stringify(bad));
      }
      assert.equal(posts().length, before, 'invalid times are never sent');

      await page.locator('#schedTimes').fill('15:30, 3:00;15:30');
      await page.locator('#schedNotify').check();
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Schedule saved/.test(document.getElementById('schedMessage').textContent));
      const sent = posts().pop();
      assert.deepEqual(sent.body, {speedtest_schedule: 'daily', speedtest_notify: true, speedtest_times: ['03:00', '15:30']});
      assert.equal(sent.headers['x-netprobe-request'], 'settings');
      assert.equal(await page.locator('#schedTimes').inputValue(), '03:00, 15:30');
      assert.match(await page.locator('#schedNote').textContent(), /Next test: .+ · time zone Europe\/Bucharest/);

      await page.locator('#schedMode').selectOption('interval');
      const count = posts().length;
      for (const bad of ['0', '169', '1.5', '']) {
        await page.locator('#schedInterval').fill(bad);
        await page.locator('#schedForm button[type="submit"]').click();
        const refused = (await page.locator('#schedInterval').evaluate(e => !e.validity.valid)) || /whole number of hours from 1 to 168/.test(await page.locator('#schedMessage').textContent());
        assert(refused, 'rejects ' + JSON.stringify(bad));
      }
      assert.equal(posts().length, count, 'invalid intervals are never sent');
      await page.locator('#schedInterval').fill('12');
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Schedule saved/.test(document.getElementById('schedMessage').textContent));
      assert.deepEqual(posts().pop().body, {speedtest_schedule: 'interval', speedtest_notify: true, speedtest_interval_h: 12});

      await page.locator('#schedMode').selectOption('off');
      assert.match(await page.locator('#schedNote').textContent(), /Automatic tests are off/);
      api.prefsFail = true;
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Not saved: .*disk full/.test(document.getElementById('schedMessage').textContent));
      assert.equal(await page.locator('#schedFields').evaluate(e => e.disabled), false, 'the form is usable again after a failed save');
      api.prefsFail = false;
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Schedule saved/.test(document.getElementById('schedMessage').textContent));
      assert.deepEqual(posts().pop().body, {speedtest_schedule: 'off', speedtest_notify: true});
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- the saved schedule is what a reload shows (and Russian texts exist) ---
    {
      const {page, errors} = await open('netprobe', 'ru');
      assert.equal(await page.locator('#schedMode').inputValue(), 'off');
      assert.equal(await page.locator('#schedNotify').isChecked(), true);
      assert.equal(await page.locator('#schedInterval').inputValue(), '12');
      assert.match(await page.locator('#schedNote').textContent(), /Автоматические тесты выключены/);
      assert.match(await page.locator('#schedMode option').nth(0).textContent(), /Как задано в контейнере: каждые 4 ч/);
      assert.deepEqual(errors, []);
      api.prefs.speedtest_schedule = 'container';
      await page.close();
    }

    // --- external IP: opt-in checkbox and the WAN / ISP card (both styles) ---
    {
      const {page, errors} = await open('unifi', 'en');
      const card = page.locator('#wanCard'), box = page.locator('#show_public_ip');
      await page.locator('#unifiTabs [data-tab="settings"]').click();
      assert.equal(await card.isVisible(), false, 'no card while the option is off');
      assert.equal(await box.isChecked(), false);
      assert.equal(await box.isEnabled(), true);
      assert.match(await page.locator('.pref-row').textContent(), /ipwho\.is.*nothing is sent anywhere/);
      await box.check();
      await page.waitForFunction(() => document.getElementById('wanIp').textContent === '198.51.100.7');
      assert.deepEqual(api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST').pop().body, {show_public_ip: true});
      assert.equal(await page.locator('#wanIsp').textContent(), 'Example Net');
      assert.match(await page.locator('#wanMeta').textContent(), /Checked .+ · source ipwho\.is · refreshed every 5 minutes/);
      assert.equal(await page.locator('#wanCard strong').textContent(), 'WAN / ISP');
      assert.equal(await card.isVisible(), true);
      // the card sits between the status banner and the path card, full width of the content column
      const [status, wan, path] = await Promise.all(['.main-status', '#wanCard', '#pathCard'].map(sel => page.locator(sel).boundingBox()));
      assert(status.y + status.height <= wan.y + 1 && wan.y + wan.height <= path.y + 1, 'order: status, WAN card, path');
      assert(Math.abs(wan.x - status.x) < 2 && Math.abs(wan.width - status.width) < 2, 'same width as the status banner');
      // unknown provider is said, not invented
      api.publicIp = {enabled: true, ip: '198.51.100.7', isp: null, source: 'api.ipify.org', checked_at: now, error: null};
      await page.locator('#refreshBtn').click();
      await page.waitForFunction(() => document.getElementById('wanIsp').textContent === 'not reported');
      // the values are data, not markup
      api.publicIp = {enabled: true, ip: '<img src=x onerror=window.__xss=1>', isp: '<b onmouseover=window.__xss=2>ISP</b>', source: 'ipwho.is', checked_at: now, error: null};
      await page.locator('#refreshBtn').click();
      await page.waitForFunction(() => document.getElementById('wanIp').textContent.includes('<img'));
      assert.equal(await page.locator('#wanCard img, #wanCard b').count(), 0);
      assert.equal(await page.evaluate(() => window.__xss), undefined);
      // lookup failure is stated plainly
      api.publicIp = {enabled: true, ip: null, isp: null, source: null, checked_at: now, error: 'ipwho.is: timed out'};
      await page.locator('#refreshBtn').click();
      await page.waitForFunction(() => /could not be determined: ipwho\.is: timed out/.test(document.getElementById('wanMeta').textContent));
      assert.equal(await page.locator('#wanIp').textContent(), '—');
      // failed save: the box returns to what is stored
      api.prefsFail = true;
      await box.uncheck();
      await page.waitForFunction(() => /Could not save: .*disk full/.test(document.getElementById('ipMessage').textContent));
      assert.equal(await box.isChecked(), true);
      api.prefsFail = false;
      await box.uncheck();
      await page.waitForFunction(() => document.getElementById('wanCard').hidden);
      assert.deepEqual(api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST').pop().body, {show_public_ip: false});
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const [style, lang] of [['netprobe', 'ru'], ['unifi', 'ru']]) {
      api.prefs.show_public_ip = true; api.publicIp = {enabled: true, ip: '198.51.100.7', isp: 'Example Net', source: 'ipwho.is', checked_at: now, error: null};
      const {page, errors} = await open(style, lang);
      await page.waitForFunction(() => document.getElementById('wanIp').textContent === '198.51.100.7');
      assert.equal(await page.locator('#show_public_ip').isChecked(), true, 'the saved choice survives a reload');
      assert.equal(await page.locator('#wanCard strong').textContent(), 'WAN / провайдер');
      assert.deepEqual(await page.locator('.wan-label').allTextContents(), ['Внешний IP', 'Провайдер']);
      assert.match(await page.locator('#wanMeta').textContent(), /Проверено .+ · источник ipwho\.is/);
      assert.match(await page.locator('.pref-row').textContent(), /Пока галка снята, наружу ничего не отправляется/);
      const box = await page.locator('#wanCard').boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= 1280, 'the card fits the screen');
      assert.deepEqual(errors, []);
      api.prefs.show_public_ip = false; api.publicIp = {enabled: false};
      await page.close();
    }
    {
      // phone width: the two values stack and nothing overflows
      api.prefs.show_public_ip = true; api.publicIp = {enabled: true, ip: '2606:4700:4700:0000:0000:0000:0000:1111', isp: 'A Very Long Internet Provider Name Incorporated', source: 'ipwho.is', checked_at: now, error: null};
      const {page} = await open('unifi', 'en');
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForFunction(() => document.getElementById('wanIp').textContent.startsWith('2606'));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'no horizontal overflow with a long IPv6 address');
      const [ip, isp] = await Promise.all(['#wanIp', '#wanIsp'].map(sel => page.locator(sel).boundingBox()));
      assert(isp.y > ip.y, 'stacked on a phone');
      api.prefs.show_public_ip = false; api.publicIp = {enabled: false};
      await page.close();
    }

    // --- a test already running when the page opens (for example a scheduled one) is shown and followed ---
    {
      api.status = {running: true, success: null, error: null, result: null};
      const {page, errors} = await open();
      await page.waitForFunction(() => document.getElementById('speedBtn').disabled && /Running/i.test(document.getElementById('speedBtn').textContent));
      const result = {timestamp: now, ping_ms: 9, download_mbps: 250, upload_mbps: 40, server: {name: 'Example City'}, backend: 'python'};
      api.status = {running: false, success: true, error: null, result};
      await page.waitForFunction(() => document.getElementById('download').textContent.startsWith('250.00'), null, {timeout: 10000});
      await page.waitForFunction(() => !document.getElementById('speedBtn').disabled);
      assert.deepEqual(errors, []);
      api.status = {running: false, success: null, error: null, result: null};
      await page.close();
    }

    // --- one request per address, and data appears as it arrives ---
    {
      api.delays['/api/loss'] = 2500;
      api.calls.length = 0;
      const started = Date.now();
      const {page, errors} = await open();
      await page.waitForFunction(() => document.getElementById('scoreValue').textContent === '90.0');
      assert(Date.now() - started < 2200, 'the score is drawn without waiting for the slowest request (' + (Date.now() - started) + ' ms)');
      assert.equal(await page.locator('#refreshBtn').isDisabled(), true, 'the refresh is still in progress while the slow one is pending');
      await page.waitForFunction(() => !document.getElementById('refreshBtn').disabled, null, {timeout: 10000});
      const first = api.calls.filter(c => c.method === 'GET' && c.path === '/api/history' && c.query.includes('range=3600'));
      assert.equal(first.length, 1, 'the summary history is requested once per refresh, not twice (' + first.length + ')');
      api.delays['/api/loss'] = 0;
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- a slow lookup service never delays the dashboard ---
    {
      api.prefs.show_public_ip = true; api.publicIp = {enabled: true, ip: '198.51.100.7', isp: 'Example Net', source: 'ipwho.is', checked_at: now, error: null}; api.publicIpDelay = 4000;
      const started = Date.now();
      const {page, errors} = await open();
      await page.waitForFunction(() => document.getElementById('scoreValue').textContent === '90.0' && !document.getElementById('refreshBtn').disabled);
      assert(Date.now() - started < 3000, 'data is on screen and refresh is usable long before the 4 s lookup answers (' + (Date.now() - started) + ' ms)');
      await page.waitForFunction(() => document.getElementById('wanIp').textContent === '198.51.100.7', null, {timeout: 8000});
      assert.deepEqual(errors, []);
      api.prefs.show_public_ip = false; api.publicIp = {enabled: false}; api.publicIpDelay = 0;
      await page.close();
    }

    // --- unreadable preferences never turn the whole dashboard OFFLINE ---
    {
      api.prefsReadFail = true;
      const {page, errors} = await open();
      await page.waitForFunction(() => /LIVE/.test(document.getElementById('sourceTag').textContent), null, {timeout: 5000}).catch(() => {});
      assert.match(await page.locator('#sourceTag').textContent(), /^(LIVE|STALE)/, 'optional preferences are not part of the live/offline verdict');
      assert.equal(await page.locator('#speedBtn').isDisabled(), false, 'a manual test is still possible');
      api.prefsReadFail = false;
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: background speedtest (start, poll, failure, busy, resumed), one request per address, progressive drawing, external IP checkbox and WAN / ISP card in both styles (provider, unknown provider, escaped, failure, failed save, RU, phone width), schedule form (container/off/N hours/daily times, validation, notify switch, next test, saved state after reload, RU texts), speedtest service list (Speedtest.net / Ookla CLI / Fast.com) with explanations, saved choice, form adapts to fast.com, unknown upload shown as a dash, failed save reverted, works without the preferences API');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
