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
      prefs: {speedtest_service: '', speedtest_schedule: 'container', speedtest_interval_h: 6, speedtest_times: ['03:00'], speedtest_notify: false, show_public_ip: false, wan_check_min: 15, wan_notify: false, history_retention_days: 0, notify_unraid: true}, history: {last: null, next_ts: null, db_bytes: 5320000, oldest_ts: now - 86400 * 40}, cleanupTotal: 1234, cleanupError: null, publicIp: {enabled: false}, targets: [], wanData: {sources: [], current: null}, publicIpDelay: 0, loss: null, historyByWan: null, series: [], backups: [], backupFail: null, tg: {enabled: false, configured: false, token_set: false, chat_set: false, source: null, last_ok_ts: null, last_error: null, last_error_ts: null}, tgFail: null, delays: {}, statusSeq: [], status: {running: false, success: null, error: null, result: null}, runStatus: 202, prefsFail: false, prefsReadFail: false, speedRun: null, calls: [],
      latest: {ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server: {id: '1', name: 'Example City', country: 'EX'}, backend: 'python'},
      history: [{ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server_id: '1', server_name: 'Example City', backend: 'python'}],
    };
    async function open(style = 'netprobe', lang = 'en') {
      const page = await browser.newPage({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
      const errors = [];
      page.on('pageerror', e => { errors.push(e.message); console.error('PAGE ERROR:', e.message); });
      await page.addInitScript(([s, l]) => { try { localStorage.setItem('netprobe.style', s); localStorage.setItem('netprobe.lang', l); } catch (e) {} }, [style, lang]);
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(ui, 'utf8')});
        const body = request.method() === 'POST' ? request.postDataJSON() : undefined;
        api.calls.push({path: url.pathname, query: url.search, method: request.method(), headers: request.headers(), body});
        if (api.delays[url.pathname]) await new Promise(r => setTimeout(r, api.delays[url.pathname]));
        if (/^\/api\/backups\/[^/]+\/delete$/.test(url.pathname)) { const name = url.pathname.split('/')[3]; api.backups = api.backups.filter(b => b.name !== name); return route.fulfill({json: {running: false, error: null, backups: api.backups, keep: 5, db_bytes: 5320000, free_bytes: 90e9}}); }
        switch (url.pathname) {
          case '/api/config': return route.fulfill({json: {probe_interval: 30, ping_count: 4, sites: ['edge.example'], speedtest_backend: 'python', speedtest_secure: true, speedtest_enabled: true, speedtest_interval: 14400,
            speedtest_backends_available: {python: true, ookla: false, fast: true}, speedtest_ookla_accept_license: false}});
          case '/api/prefs':
            if (api.prefsReadFail && request.method() === 'GET') return route.fulfill({status: 503, json: {error: 'Persistent preferences cannot be read'}});
            if (request.method() === 'POST') {
              if (api.prefsFail) return route.fulfill({status: 500, json: {error: 'disk full'}});
              Object.assign(api.prefs, body);
              if ('show_public_ip' in body) api.publicIp = body.show_public_ip ? {enabled: true, ip: '198.51.100.7', isp: 'Example Net', source: 'ipwho.is', checked_at: now, error: null, every_min: api.prefs.wan_check_min, refreshing: false} : {enabled: false};
              if ('wan_check_min' in body && api.publicIp.enabled) api.publicIp = {...api.publicIp, every_min: body.wan_check_min};
            }
            return route.fulfill({json: {prefs: api.prefs, schedule: {next_ts: api.prefs.speedtest_schedule === 'daily' ? now + 7200 : null, timezone: 'Europe/Berlin'}, history: {...api.history, next_ts: api.prefs.history_retention_days ? now + 36000 : null}}});
          case '/api/history/cleanup':
            if (api.cleanupError) return route.fulfill({status: 409, json: {error: api.cleanupError}});
            if (body.dry_run) return route.fulfill({json: {days: body.days, dry_run: true, total: api.cleanupTotal, deleted: {}, bytes_before: 5320000, bytes_after: 5320000, ts: now}});
            api.history.last = {ts: now, days: body.days, total: api.cleanupTotal, bytes_before: 5320000, bytes_after: 2100000};
            return route.fulfill({json: {days: body.days, dry_run: false, total: api.cleanupTotal, deleted: {}, bytes_before: 5320000, bytes_after: 2100000, ts: now}});
          case '/api/targets/recent': return route.fulfill({json: {data: api.targets, started_at: now - 3600}});
          case '/api/wan/sources': return route.fulfill({json: api.wanData});
          case '/api/wan/forget': api.forgotten = body; return route.fulfill({json: {forgotten: 42}});
          case '/api/telegram':
            if (request.method() === 'POST') {
              if (api.tgFail) return route.fulfill({status: 400, json: {error: api.tgFail}});
              if (body.clear) api.tg = {...api.tg, token_set: false, chat_set: false, configured: false};
              if (typeof body.token === 'string') api.tg.token_set = true;
              if (typeof body.chat_id === 'string' && body.chat_id) api.tg.chat_set = true;
              if (typeof body.enabled === 'boolean') api.tg.enabled = body.enabled;
              api.tg.configured = api.tg.token_set && api.tg.chat_set; api.tg.source = api.tg.configured ? 'ui' : null;
            }
            return route.fulfill({json: api.tg});
          case '/api/telegram/test':
            if (api.tgFail) return route.fulfill({status: 502, json: {error: api.tgFail}});
            api.tg = {...api.tg, last_ok_ts: now, last_error: null};
            return route.fulfill({json: {ok: true, ...api.tg}});
          case '/api/public-ip/refresh': {
            const finished = {...api.publicIp, ip: '198.51.100.99', isp: 'Refreshed Net', checked_at: now + 60, error: null, refreshing: false};
            api.publicIp = {...api.publicIp, refreshing: true};
            setTimeout(() => { api.publicIp = finished; }, 1800);
            return route.fulfill({status: 202, json: api.publicIp});
          }
          case '/api/public-ip': if (api.publicIpDelay) await new Promise(r => setTimeout(r, api.publicIpDelay)); return route.fulfill({json: api.publicIp});
          case '/api/speedtest/latest': return route.fulfill({json: {result: api.latest}});
          case '/api/backups':
            if (request.method() === 'POST') {
              if (api.backupFail) return route.fulfill({status: 507, json: {error: api.backupFail}});
              api.backups.unshift({name: 'netprobe-20261026-0' + (10 + api.backups.length) + '000.sqlite', ts: now, bytes: 5300000});
              api.backups = api.backups.slice(0, 5);
            }
            return route.fulfill({status: request.method() === 'POST' ? 202 : 200, json: {running: false, started_at: now, finished_at: now, error: null, name: api.backups[0]?.name ?? null, backups: api.backups, keep: 5, db_bytes: 5320000, free_bytes: 90e9}});
          case '/api/speedtest/series': { const all = url.searchParams.get('range') === 'all'; return route.fulfill({json: {tests: all ? api.series : api.series.filter(r => r.ts >= now - Number(url.searchParams.get('range'))), range: all ? 630720000 : Number(url.searchParams.get('range'))}}); }
          case '/api/speedtest/history': return route.fulfill({json: {tests: api.history}});
          case '/api/speedtest/run': return route.fulfill(api.runStatus === 202 ? {status: 202, json: {success: true, started: true}} : {status: api.runStatus, json: {success: false, error: 'A Speedtest is already running. Wait for it to finish and try again.'}});
          case '/api/speedtest/status': return route.fulfill({json: api.statusSeq.length ? api.statusSeq.shift() : api.status});
          case '/api/settings': return route.fulfill({json: {settings: {enabled: true, loss_pct: 10, latency_ms: 150, jitter_ms: 80, dns_ms: 250, confirm_samples: 2, recovery_samples: 2, turbo: false}, storage: {bytes: 1, retention_days: null}, started_at: now - 60}});
          case '/api/score/latest': return route.fulfill({json: {data: {ts: now, score: 90, avg_loss_pct: 0, avg_latency_ms: 20, avg_jitter_ms: 2, avg_dns_latency_ms: 10}}});
          case '/api/loss': return route.fulfill({json: api.loss || {truncated: false, data: {targets: [], episodes: [], outages: [], summary: {}, window: {from: now - 3600, to: now}}}});
          case '/api/history': return route.fulfill({json: {data: (api.historyByWan && api.historyByWan[url.searchParams.get('wan_ip') ?? '']) || []}});
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
      const result = {timestamp: now, ping_ms: 11.5, download_mbps: 321.5, upload_mbps: null, server: {name: 'Netflix, Example City', country: 'RO'}, backend: 'fast'};
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
      assert.match(await page.locator('#speedServer').textContent(), /Fast\.com · Netflix, Example City · RO/);
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
      assert.deepEqual(sent.body, {speedtest_schedule: 'daily', speedtest_notify: true, speedtest_notify_manual: false, speedtest_drop_pct: 0, speedtest_times: ['03:00', '15:30']});
      assert.equal(sent.headers['x-netprobe-request'], 'settings');
      assert.equal(await page.locator('#schedTimes').inputValue(), '03:00, 15:30');
      assert.match(await page.locator('#schedNote').textContent(), /Next test: .+ · time zone Europe\/Berlin/);

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
      assert.deepEqual(posts().pop().body, {speedtest_schedule: 'interval', speedtest_notify: true, speedtest_notify_manual: false, speedtest_drop_pct: 0, speedtest_interval_h: 12});

      await page.locator('#schedMode').selectOption('off');
      assert.match(await page.locator('#schedNote').textContent(), /Automatic tests are off/);
      api.prefsFail = true;
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Not saved: .*disk full/.test(document.getElementById('schedMessage').textContent));
      assert.equal(await page.locator('#schedFields').evaluate(e => e.disabled), false, 'the form is usable again after a failed save');
      api.prefsFail = false;
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Schedule saved/.test(document.getElementById('schedMessage').textContent));
      assert.deepEqual(posts().pop().body, {speedtest_schedule: 'off', speedtest_notify: true, speedtest_notify_manual: false, speedtest_drop_pct: 0});
      // manual-test messages and the drop warning
      assert.deepEqual(await page.locator('#schedDrop option').allTextContents(), ['Do not warn', '20 % or more below the usual', '30 % or more below the usual', '50 % or more below the usual']);
      assert.equal(await page.locator('#schedNotifyManual').isChecked(), false);
      assert.equal(await page.locator('#schedDrop').inputValue(), '0');
      assert.match(await page.locator('#schedBox .card-sub').nth(1).textContent(), /median of the same service over the last 7 days/);
      await page.locator('#schedNotifyManual').check();
      await page.locator('#schedDrop').selectOption('30');
      await page.locator('#schedForm button[type="submit"]').click();
      await page.waitForFunction(() => /Schedule saved/.test(document.getElementById('schedMessage').textContent));
      assert.deepEqual(posts().pop().body, {speedtest_schedule: 'off', speedtest_notify: true, speedtest_notify_manual: true, speedtest_drop_pct: 30});
      assert.equal(await page.locator('#schedDrop').inputValue(), '30');
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
      assert.equal(await page.locator('#schedNotifyManual').isChecked(), true, 'the saved choices come back after a reload');
      assert.equal(await page.locator('#schedDrop').inputValue(), '30');
      assert.equal(await page.locator('#schedDrop option:checked').textContent(), 'Ниже обычной на 30 % и больше');
      assert.match(await page.locator('#schedMode option').nth(0).textContent(), /Как задано в контейнере: каждые 4 ч/);
      assert.deepEqual(errors, []);
      api.prefs.speedtest_schedule = 'container';
      await page.close();
    }

    // --- Speedtest chart: speed, delay and loss panels together, a service, lines to switch off, hover popup, the table under it ---
    {
      const day = 86400;
      api.series = [
        {ts: now - 5 * day, ping_ms: 14, download_mbps: 410, upload_mbps: 88, backend: 'fast', server_name: 'Old City', jitter_ms: 1.2, loaded_down_ms: 40, loaded_up_ms: 90, packet_loss_pct: null},
        {ts: now - 3 * day, ping_ms: 15, download_mbps: 395, upload_mbps: null, backend: 'fast', server_name: 'Mid City', jitter_ms: null, loaded_down_ms: null, loaded_up_ms: null, packet_loss_pct: null},
        {ts: now - 2 * day, ping_ms: 13, download_mbps: 120, upload_mbps: 80, backend: 'python', server_name: 'Net City', jitter_ms: null, loaded_down_ms: null, loaded_up_ms: null, packet_loss_pct: null},
        {ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, backend: 'fast', server_name: 'Example City', jitter_ms: 2.5, loaded_down_ms: 55, loaded_up_ms: 110, packet_loss_pct: 0.5},
      ];
      api.latest = {ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server: {id: '1', name: 'Example City', country: 'EX'}, backend: 'fast'};
      api.history = [{ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, server_id: '1', server_name: 'Example City', backend: 'fast'}];
      const {page, errors} = await open('netprobe', 'en');
      const marks = () => page.locator('#speedChart circle, #speedChart rect, #speedChart polygon').count();
      const chips = () => page.locator('#speedLegend .sp-chip').allTextContents();
      const on = () => page.locator('#speedLegend .sp-chip[aria-pressed="true"]').allTextContents();
      const titles = async () => (await page.locator('#speedChart text').allTextContents()).filter(x => /, (Mbps|ms|%)$/.test(x));
      await page.waitForFunction(() => document.querySelectorAll('#speedChart circle').length > 0);
      assert.deepEqual(await chips(), ['Download ↓', 'Upload ↑', 'Idle ping', 'Ping while downloading', 'Ping while uploading', 'Jitter', 'Packet loss']);
      assert.deepEqual(await page.locator('#speedLegend .sp-gl').allTextContents(), ['Speed', 'Delay', 'Loss']);
      assert.deepEqual(await titles(), ['Speed, Mbps', 'Delay, ms', 'Loss, %'], 'speed, delay and loss are drawn together, one panel each');
      assert.equal(await page.locator('#spService').inputValue(), 'fast', 'the newest test decides: services are not mixed on one line');
      assert.deepEqual(await page.locator('#spService option').allTextContents(), ['All services', 'Fast.com', 'Speedtest.net']);
      assert.equal(await marks(), 3 + 2 + 3 + 2 + 2 + 2 + 1, 'Fast.com only, last 7 days: an unmeasured value is not a point');
      assert.equal(await page.locator('#speedChart path.sp-line').count(), 7);
      const strokes = await page.locator('#speedChart path.sp-line').evaluateAll(l => l.map(e => e.getAttribute('stroke')));
      assert.equal(new Set(strokes).size, 7, 'every line has its own colour');
      assert.equal(strokes[0], '#3b82f6', 'download is blue');
      assert.equal(strokes[1], '#ef4444', 'upload is red');
      const upPath = await page.locator('#speedChart path.sp-line').nth(1).getAttribute('d');
      assert.equal((upPath.match(/M/g) || []).length, 2, 'the unmeasured upload breaks the line: a gap, not a zero');
      assert.equal(await page.locator('#speedChart title').count(), 0, 'no slow native tooltips');
      assert.equal(await page.locator('#speedChartNote').textContent(), '');
      // the popup of a point lists every visible line at that moment, across the panels
      const pointOf = async index => { await page.locator('#speedChart').scrollIntoViewIfNeeded(); return page.locator('#speedChart circle').nth(index).evaluate(c => { const r = c.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; }); };
      let p = await pointOf(0);
      await page.mouse.move(p.x, p.y);
      await page.waitForFunction(() => !document.getElementById('speedTip').hidden);
      const tip = await page.locator('#speedTip').textContent();
      assert.match(tip, /Download ↓410\.0 Mbps/);
      assert.match(tip, /Upload ↑88\.0 Mbps/);
      assert.match(tip, /Idle ping14\.0 ms/);
      assert.match(tip, /Ping while downloading40\.0 ms/);
      assert.match(tip, /Fast\.com · Old City/);
      await page.mouse.move(2, 2);
      await page.waitForFunction(() => document.getElementById('speedTip').hidden);
      // lines can be switched off and on, and the choice is remembered; an empty panel disappears
      await page.locator('#speedLegend [data-sk="upload_mbps"]').click();
      assert.equal((await on()).includes('Upload ↑'), false);
      assert.equal(await marks(), 3 + 3 + 2 + 2 + 2 + 1);
      assert.deepEqual((await page.evaluate(() => JSON.parse(localStorage.getItem('netprobe.speedchart')))).hidden, ['upload_mbps']);
      await page.locator('#speedLegend [data-sk="download_mbps"]').click();
      assert.deepEqual(await titles(), ['Delay, ms', 'Loss, %'], 'no speed line: no speed panel');
      for (const k of ['ping_ms', 'loaded_down_ms', 'loaded_up_ms', 'jitter_ms', 'packet_loss_pct']) await page.locator(`#speedLegend [data-sk="${k}"]`).click();
      await page.waitForFunction(() => /Every line is hidden/.test(document.getElementById('speedChartNote').textContent));
      assert.equal(await marks(), 0);
      for (const k of ['download_mbps', 'upload_mbps', 'ping_ms', 'loaded_down_ms', 'loaded_up_ms', 'jitter_ms', 'packet_loss_pct']) await page.locator(`#speedLegend [data-sk="${k}"]`).click();
      assert.equal(await marks(), 15);
      // all services: the colour of a line says which line it is, the shade / dashes / marker say which service
      await page.locator('#spService').selectOption('all');
      assert.equal(await page.locator('#speedChart rect').count(), 3, 'Speedtest.net points are squares');
      assert.match(await page.locator('#speedLegend .sp-key').textContent(), /● Fast\.com\s*■ Speedtest\.net · dashed|■ Speedtest\.net · dashed/);
      const all = await page.locator('#speedChart path.sp-line').evaluateAll(l => l.map(e => [e.getAttribute('stroke'), e.getAttribute('stroke-dasharray')]));
      assert.equal(new Set(all.map(x => x[0])).size, all.length, 'two services never share a colour');
      assert.equal(all.filter(x => x[1]).length, 3, 'the dashed lines are Speedtest.net');
      p = await pointOf(0);
      await page.mouse.move(p.x, p.y);
      await page.waitForFunction(() => /· Fast\.com/.test(document.getElementById('speedTip').textContent));
      // long names wrap inside the popup instead of sticking out of it
      const fit = await page.locator('#speedTip').evaluate(t => { const b = t.getBoundingClientRect(); return [...t.querySelectorAll('.row, .row *')].every(e => { const r = e.getBoundingClientRect(); return r.right <= b.right + 0.5 && r.left >= b.left - 0.5; }); });
      assert.equal(fit, true, 'every row stays inside the popup');
      await page.mouse.move(2, 2);
      await page.locator('#spService').selectOption('fast');
      assert.match(await page.locator('#speedExtra').textContent(), /^Jitter 3 ms · delay under load: ↓ 55 ms \/ ↑ 110 ms · loss 0\.5 %$/);
      // another period is applied without asking the server again
      api.calls.length = 0;
      await page.locator('#spRange').selectOption('86400');
      await page.waitForFunction(() => document.querySelectorAll('#speedChart circle').length === 7);
      assert.equal(api.calls.some(c => c.path === '/api/speedtest/series'), false, 'the period filters what is already loaded');
      await page.locator('#spRange').selectOption('0');
      assert.equal(await marks(), 15, 'all time, this service');
      await page.locator('#spRange').selectOption('86400');
      // the table: newest first, the last 10 / the chart period / all
      const rowsText = () => page.locator('#speedHistory tr').allTextContents();
      await page.locator('#speedHistory').evaluate(e => { e.closest('details').open = true; });
      const rows = await rowsText();
      assert.equal(rows.length, 4, 'the last 10, but only 4 tests exist');
      assert.match(rows[0], /Example City/);
      assert.match(rows[3], /Old City/);
      await page.locator('#spHist').selectOption('range');
      assert.equal((await rowsText()).length, 1, 'the chart period is 24 hours');
      await page.locator('#spHist').selectOption('all');
      assert.equal((await rowsText()).length, 4);
      assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('netprobe.speedchart')))).hist, 'all');
      // the choices are remembered over a reload
      await page.reload();
      await page.waitForFunction(() => document.getElementById('spRange').value === '86400' && document.getElementById('spHist').value === 'all');
      // no tests at all: a note instead of an empty plot
      api.series = [];
      await page.reload();
      await page.waitForFunction(() => document.getElementById('speedChartNote').textContent === 'No tests in this period.');
      assert.equal(await marks(), 0);
      // Speedtest.net-like data: the delay lines that need Fast.com are greyed out, the note says why, no loss panel
      api.series = [{ts: now - 600, ping_ms: 20, download_mbps: 480, upload_mbps: 90, backend: 'python', server_name: 'Net City', jitter_ms: null, loaded_down_ms: null, loaded_up_ms: null, packet_loss_pct: null}];
      await page.reload();
      await page.waitForFunction(() => /measured by Fast\.com only/.test(document.getElementById('speedChartNote').textContent));
      assert.deepEqual(await page.locator('#speedLegend .sp-chip:not([disabled])').allTextContents(), ['Download ↓', 'Upload ↑', 'Idle ping']);
      assert.equal(await page.locator('#speedLegend .sp-chip[disabled]').count(), 3);
      assert.deepEqual(await page.locator('#speedLegend .sp-gl').allTextContents(), ['Speed', 'Delay'], 'nothing measured the loss: no loss group');
      // a placeholder ping (speedtest-cli stores 1 800 000 ms when it cannot measure) is not drawn and not shown
      api.series = [{ts: now - 600, ping_ms: 1800000, download_mbps: 480, upload_mbps: 90, backend: 'python', server_name: 'Net City', jitter_ms: null, loaded_down_ms: null, loaded_up_ms: null, packet_loss_pct: null}];
      api.latest = {ts: now - 600, ping_ms: 1800000, download_mbps: 480, upload_mbps: 90, server: {id: '1', name: 'Net City', country: 'EX'}, backend: 'python'};
      await page.reload();
      await page.waitForFunction(() => document.getElementById('speedPing').textContent.startsWith('—'));
      assert.deepEqual(await titles(), ['Speed, Mbps'], 'no delay panel: the only delay is a placeholder');
      assert.doesNotMatch(await page.locator('#speedChart').textContent(), /1800000|1800|1\.8k/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- database backups: make, list, download link, delete ---
    {
      api.backups = [];
      const {page, errors} = await open('netprobe', 'en');
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      await page.waitForFunction(() => document.getElementById('bkList').textContent.includes('No backups yet.'));
      assert.match(await page.locator('#bkMessage').textContent(), /^Free disk space: .+; database: 5\.07 MiB\.$/);
      assert.match(await page.locator('#bkBox .card-sub').first().textContent(), /the 5 newest stay/);
      await page.locator('#bkCreate').click();
      await page.waitForFunction(() => /^Backup created: netprobe-/.test(document.getElementById('bkMessage').textContent));
      assert.equal(await page.locator('#bkList tr').count(), 1);
      assert.equal(await page.locator('#bkList a').getAttribute('href'), '/api/backups/netprobe-20261026-010000.sqlite');
      assert.equal(await page.locator('#bkList a').getAttribute('download') !== null, true);
      assert.match(await page.locator('#bkList td').nth(1).textContent(), /5\.05 MiB/);
      // a refusal is said plainly
      api.backupFail = 'Not enough free space for a backup: 3 MiB free, about 14 MiB needed';
      await page.locator('#bkCreate').click();
      await page.waitForFunction(() => /The backup failed: Not enough free space/.test(document.getElementById('bkMessage').textContent));
      assert.equal(await page.locator('#bkList tr').count(), 1);
      api.backupFail = null;
      // delete asks first
      let asked = '';
      page.once('dialog', async d => { asked = d.message(); await d.dismiss(); });
      await page.locator('#bkList [data-bk-del]').click();
      await page.waitForTimeout(200);
      assert.match(asked, /Delete the backup netprobe-20261026-010000\.sqlite\?/);
      assert.equal(api.backups.length, 1);
      page.once('dialog', d => d.accept());
      await page.locator('#bkList [data-bk-del]').click();
      await page.waitForFunction(() => document.getElementById('bkList').textContent.includes('No backups yet.'));
      assert.deepEqual(errors, []);
      await page.close();
    }

    // --- Integrations: Unraid and Telegram switches, a draft with one Save, a write-only token ---
    {
      const {page, errors} = await open('netprobe', 'en');
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      const posts = path => api.calls.filter(c => c.path === path && c.method === 'POST');
      const tg = path => posts('/api/telegram' + path);
      const save = page.locator('#intSave'), msg = () => page.locator('#tgMessage').textContent();
      assert.equal(await page.locator('#tgBox strong').textContent(), 'Integrations');
      assert.equal(await page.locator('#int_unraid').isChecked(), true, 'Unraid is on by default');
      assert.equal(await page.locator('#tg_enabled').isChecked(), false);
      assert.equal(await page.locator('#tgFields').isHidden(), true, 'no Telegram fields until Telegram is ticked');
      assert.equal(await save.isDisabled(), true, 'nothing to save yet');
      assert.equal(await page.locator('#tg_token').getAttribute('type'), 'password');

      // Unraid off on its own: nothing is sent before Save, then the preference and an unchanged Telegram state go out
      await page.locator('#int_unraid').uncheck();
      assert.equal(posts('/api/prefs').filter(c => c.body && 'notify_unraid' in c.body).length, 0, 'ticking only edits a draft');
      await save.click();
      await page.waitForFunction(() => document.getElementById('tgMessage').textContent === 'Saved.');
      assert.deepEqual(posts('/api/prefs').pop().body, {notify_unraid: false});
      assert.equal(tg('').length, 0, 'Telegram was not touched');
      assert.equal(await save.isDisabled(), true);
      assert.equal(await page.locator('#int_unraid').isChecked(), false);

      // Telegram on: the fields appear; saving without a token is refused in the browser
      await page.locator('#tg_enabled').check();
      assert.equal(await page.locator('#tgFields').isVisible(), true);
      assert.equal(await page.locator('#tgForm').isVisible(), true);
      assert.equal(await page.locator('#tg_token').getAttribute('placeholder'), '123456789:AA…');
      assert.equal(await save.isEnabled(), true);
      const before = tg('').length;
      await save.click();
      assert.equal(await msg(), 'Enter the bot token and the chat ID.');
      assert.equal(tg('').length, before);
      // a server refusal keeps the draft
      api.tgFail = 'The bot token looks like 123456789:AAH… (from @BotFather)';
      await page.locator('#tg_token').fill('1111');
      await page.locator('#tg_chat').fill('-11111');
      await save.click();
      await page.waitForFunction(() => /Failed: The bot token looks like/.test(document.getElementById('tgMessage').textContent));
      assert.equal(await page.locator('#tg_token').inputValue(), '1111', 'the draft stays');
      // the test button sends the form values, saves nothing
      api.tgFail = null;
      const saves = tg('').length;
      await page.locator('#tg_token').fill('123456789:AAH-test_token_value_0123456789abc');
      await page.locator('#tgTest').click();
      await page.waitForFunction(() => document.getElementById('tgMessage').textContent === 'Test message sent.');
      assert.deepEqual(tg('/test').pop().body, {token: '123456789:AAH-test_token_value_0123456789abc', chat_id: '-11111'});
      assert.equal(tg('').length, saves, 'testing does not save');
      assert.match(await page.locator('#tgStatus').textContent(), /^Last delivery: /);
      api.tgFail = 'Telegram refused the message (400): Bad Request: chat not found';
      await page.locator('#tgTest').click();
      await page.waitForFunction(() => /Failed: Telegram refused the message \(400\): Bad Request: chat not found/.test(document.getElementById('tgMessage').textContent));
      api.tgFail = null;
      // Save sends the draft; the token field is emptied and only a hint remains
      await save.click();
      await page.waitForFunction(() => document.getElementById('tgMessage').textContent === 'Saved.');
      assert.deepEqual(tg('').pop().body, {enabled: true, chat_id: '-11111', token: '123456789:AAH-test_token_value_0123456789abc'});
      assert.equal(await page.locator('#tg_token').inputValue(), '');
      assert.equal(await page.locator('#tg_token').getAttribute('placeholder'), 'saved · leave empty to keep it');
      assert.equal(await page.locator('#tg_chat').inputValue(), '', 'the saved chat is not shown, like the token');
      assert.equal(await page.locator('#tg_chat').getAttribute('placeholder'), 'saved · leave empty to keep it');
      assert.equal(await page.locator('#tgClear').isEnabled(), true);
      assert.equal(await save.isEnabled(), false, 'nothing to save');
      // saving again without typing a token keeps the saved one (no token key is sent)
      await page.locator('#tg_chat').fill('-22222');
      await save.click();
      await page.waitForFunction(() => document.getElementById('tg_chat').value === '' && document.getElementById('tgMessage').textContent === 'Saved.');
      assert.deepEqual(tg('').pop().body, {enabled: true, chat_id: '-22222'});
      // Unraid back on together with Telegram off: one Save, both calls
      await page.locator('#int_unraid').check();
      await page.locator('#tg_enabled').uncheck();
      assert.equal(await page.locator('#tgFields').isHidden(), true, 'the fields go away with the switch');
      await save.click();
      await page.waitForFunction(() => document.getElementById('tgMessage').textContent === 'Saved.');
      assert.equal(tg('').pop().body.enabled, false);
      assert.deepEqual(posts('/api/prefs').pop().body, {notify_unraid: true});
      assert.equal(await page.locator('#tg_chat').getAttribute('placeholder'), 'saved · leave empty to keep it', 'the saved chat is kept while Telegram is off');
      assert.deepEqual(tg('').pop().body, {enabled: false}, 'an untouched chat field sends nothing');
      // forgetting asks first
      await page.locator('#tg_enabled').check();
      let asked = '';
      page.once('dialog', async d => { asked = d.message(); await d.dismiss(); });
      await page.locator('#tgClear').click();
      await page.waitForTimeout(200);
      assert.match(asked, /Delete the saved token and chat ID/);
      assert.equal(api.tg.token_set, true, 'refused: still saved');
      page.once('dialog', d => d.accept());
      await page.locator('#tgClear').click();
      await page.waitForFunction(() => document.getElementById('tg_chat').value === '' && !document.getElementById('tg_enabled').checked);
      assert.deepEqual(tg('').pop().body, {clear: true, enabled: false});
      assert.equal(await page.locator('#tgFields').isHidden(), true);

      // values that come from the container environment are not shown; only on / off is offered
      api.tg = {enabled: true, configured: true, token_set: true, chat_set: true, source: 'env', last_ok_ts: null, last_error: 'Telegram refused the message (403): Forbidden: bot was blocked by the user', last_error_ts: now};
      await page.reload();
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      await page.waitForFunction(() => document.getElementById('tg_enabled').checked);
      assert.equal(await page.locator('#tgForm').isHidden(), true, 'no token and chat fields in environment mode');
      assert.equal(await page.locator('#tg_chat').inputValue(), '', 'the environment chat is not copied into the page');
      assert.match(await page.locator('#tgEnvNote').textContent(), /come from the container environment/);
      assert.match(await page.locator('#tgStatus').textContent(), /Delivery error \(.+\): Telegram refused the message \(403\)/);
      await page.locator('#tg_enabled').uncheck();
      await page.locator('#intSave').click();
      await page.waitForFunction(() => document.getElementById('tgMessage').textContent === 'Saved.');
      assert.deepEqual(tg('').pop().body, {enabled: false}, 'only the switch is sent, never the environment values');
      // a delivery error is shown plainly, in both languages
      api.tg = {enabled: true, configured: true, token_set: true, chat_set: true, source: 'ui', last_ok_ts: null, last_error: 'Telegram refused the message (403): Forbidden: bot was blocked by the user', last_error_ts: now};
      await page.reload();
      await page.waitForFunction(() => /Delivery error \(.+\): Telegram refused the message \(403\)/.test(document.getElementById('tgStatus').textContent));
      await page.locator('#langSelect').selectOption('ru');
      assert.match(await page.locator('#tgStatus').textContent(), /Ошибка доставки \(/);
      assert.equal(await page.locator('#tgBox strong').textContent(), 'Интеграции');
      // messages waiting while the network is down: how many, since when, and when the next attempt is
      api.tg = {...api.tg, last_error: 'api.telegram.org is not reachable: network is unreachable', queued: 3, oldest_ts: now - 900, retry_ts: now + 120};
      await page.reload();
      await page.waitForFunction(() => /Waiting to be sent: 3 \(the oldest from .+\)\. Next attempt at /.test(document.getElementById('tgStatus').textContent));
      assert.match(await page.locator('#tgStatus').textContent(), /^Delivery error \(.+\): api\.telegram\.org is not reachable/);
      await page.locator('#langSelect').selectOption('ru');
      assert.match(await page.locator('#tgStatus').textContent(), /Ждут отправки: 3 \(самое старое — .+\)\. Следующая попытка — /);
      api.tg = {...api.tg, last_error: null, last_ok_ts: now, queued: 0, oldest_ts: null, retry_ts: null};
      await page.reload();
      await page.waitForFunction(() => /^Last delivery: .+\.$/.test(document.getElementById('tgStatus').textContent) && !/Waiting/.test(document.getElementById('tgStatus').textContent));
      assert.deepEqual(errors, []);
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
      assert.match(await page.locator('.pref-row').first().textContent(), /ipwho\.is.*nothing is sent anywhere/);
      const posts = () => api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST');
      const save = page.locator('#ipSave');
      assert.equal(await save.isDisabled(), true, 'nothing to save yet');
      const sent = posts().length;
      await box.check();
      assert.equal(await save.isEnabled(), true);
      assert.equal(posts().length, sent, 'ticking only edits a draft, nothing is sent');
      assert.equal(await card.isVisible(), false);
      await save.click();
      await page.waitForFunction(() => document.getElementById('wanIp').textContent === '198.51.100.7');
      assert.deepEqual(posts().pop().body, {show_public_ip: true, wan_check_min: 15, wan_notify: false});
      assert.equal(await page.locator('#ipMessage').textContent(), 'Saved.');
      assert.equal(await save.isDisabled(), true, 'saved: nothing left to save');
      assert.equal(await page.locator('#wanIsp').textContent(), 'Example Net');
      assert.match(await page.locator('#wanMeta').textContent(), /Checked .+ · source ipwho\.is · checked every 15 minutes/);
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
      // failed save: the draft is kept so it can be saved again, the card stays as stored
      api.prefsFail = true;
      await box.uncheck();
      await save.click();
      await page.waitForFunction(() => /Could not save: .*disk full/.test(document.getElementById('ipMessage').textContent));
      assert.equal(await box.isChecked(), false, 'the edit is not thrown away');
      assert.equal(await save.isEnabled(), true);
      assert.equal(await card.isVisible(), true, 'nothing changed on the server');
      api.prefsFail = false;
      await save.click();
      await page.waitForFunction(() => document.getElementById('wanCard').hidden);
      assert.deepEqual(posts().pop().body, {show_public_ip: false, wan_check_min: 15, wan_notify: false});
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
      assert.match(await page.locator('.pref-row').first().textContent(), /Пока галка снята, наружу ничего не отправляется/);
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
      api.latest = {ts: now, ...result};  // the server's latest result is the finished test
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

    // --- WAN source selectors: one choice shared by the chart and the targets table ---
    {
      const A = {wan_ip: '203.0.113.10', wan_isp: 'Alpha Net'}, B = {wan_ip: '198.51.100.20', wan_isp: 'Beta Cable'};
      const row = (loss, extra = {}) => ({target: 'google.com', address: '192.0.2.1', kind: 'ping', ts: now, latency: 20, jitter: 1, loss, avg_latency: 21, max_latency: 30, avg_loss: loss, max_loss: loss, samples: 70, state: 'OK', addresses: [], wan_sources: [], ...extra});
      api.prefs.show_public_ip = true; api.publicIp = {enabled: true, ip: A.wan_ip, isp: A.wan_isp, source: 'ipwho.is', checked_at: now, error: null};
      api.wanData = {sources: [{...A, samples: 120, first_ts: now - 3000, last_ts: now}, {...B, samples: 60, first_ts: now - 3500, last_ts: now - 2000}, {wan_ip: null, wan_isp: null, samples: 10, first_ts: now - 3600, last_ts: now - 3500}], current: A};
      api.targets = [row(0, {wan_sources: [{...A, samples: 40, avg_latency: 20, max_latency: 31, avg_loss: 0, max_loss: 0}, {...B, samples: 30, avg_latency: 60, max_latency: 90, avg_loss: 5, max_loss: 20}]})];
      const {page, errors} = await open('netprobe', 'en');
      const dialogs = [];
      let answer = false;
      page.on('dialog', async d => { dialogs.push(d.message()); await (answer ? d.accept() : d.dismiss()); });
      const sel = (id) => page.locator(id);
      await page.waitForFunction(() => !document.getElementById('wanPickChart').hidden);
      assert.equal(await page.locator('#wanPickTargets').isVisible(), true);
      const labels = await sel('#wanSelectChart option').allTextContents();
      assert.deepEqual(labels, ['All WAN sources', 'Current WAN: Alpha Net · 203.0.113.10 · 120 samples', 'Beta Cable · 198.51.100.20 · 60 samples', 'No WAN recorded (before it was switched on) · 10 samples']);
      assert.deepEqual(await sel('#wanSelectTargets option').allTextContents(), labels, 'both selectors offer the same choices');
      assert.equal(await sel('#wanSelectChart').inputValue(), 'all');
      // the table shows how each WAN carried the target when the period saw more than one
      assert.match(await page.locator('#targetsBody').textContent(), /Alpha Net · 203\.0\.113\.10: RTT 20\.0 \/ max 31\.0 ms.*Beta Cable · 198\.51\.100\.20: RTT 60\.0 \/ max 90\.0 ms/s);
      const calls = () => api.calls.filter(c => c.method === 'GET');
      assert.equal(calls().filter(c => c.query.includes('wan_ip')).length, 0, 'no filter, no parameter');

      // choosing a WAN in the chart selector filters the chart and the table, and the other selector follows
      api.calls.length = 0;
      await sel('#wanSelectChart').selectOption({label: 'Beta Cable · 198.51.100.20 · 60 samples'});
      await page.waitForFunction(() => document.getElementById('wanSelectTargets').selectedOptions[0].textContent.startsWith('Beta Cable'));
      await page.waitForTimeout(300);
      const filtered = calls().map(c => c.path + c.query);
      const expected = '&wan_ip=198.51.100.20&wan_isp=Beta%20Cable';
      assert(filtered.some(u => u.startsWith('/api/history?') && u.endsWith(expected)), 'summary history is filtered: ' + filtered.join(' '));
      assert(filtered.some(u => u.startsWith('/api/targets/recent?') && u.endsWith(expected)), 'the targets table is filtered');
      assert.equal(await page.locator('#metricTabs [data-metric="score"]').isDisabled(), true, 'no quality score for a single WAN');
      assert.match(await page.locator('#metricTabs [data-metric="score"]').getAttribute('title'), /whole network/);
      const summaryCalls = filtered.filter(u => u === '/api/history?range=3600' + expected);
      assert.equal(summaryCalls.length, 1, 'the filtered summary is requested once, not twice');

      // the other selector drives the same state; "no WAN recorded" asks for the empty address
      api.calls.length = 0;
      await sel('#wanSelectTargets').selectOption({index: 3});
      await page.waitForFunction(() => document.getElementById('wanSelectChart').selectedOptions[0].textContent.startsWith('No WAN recorded'));
      await page.waitForTimeout(300);
      assert(calls().some(c => c.path === '/api/targets/recent' && /&wan_ip=&wan_isp=$/.test(c.query)), 'empty wan_ip selects samples without a WAN');

      // back to everything: no parameter, the quality score is available again
      api.calls.length = 0;
      await sel('#wanSelectChart').selectOption('all');
      await page.waitForFunction(() => !document.querySelector('#metricTabs [data-metric="score"]').disabled);
      await page.waitForTimeout(300);
      assert.equal(calls().filter(c => c.query.includes('wan_ip')).length, 0);

      // the loss report: limited by the same WAN choice, with a table of how each source fared
      api.loss = {truncated: false, data: {targets: [], episodes: [], outages: [], summary: {samples: 12, lossy: 4, episodes: 2, avg_loss: 5, max_loss: 50}, window: {from: now - 3600, to: now}, rows_capped: false,
        wan_sources: [{wan_ip: A.wan_ip, wan_isp: A.wan_isp, samples: 6, lossy: 2, avg_loss: 8.3, max_loss: 50}, {wan_ip: B.wan_ip, wan_isp: B.wan_isp, samples: 4, lossy: 2, avg_loss: 5, max_loss: 10}, {wan_ip: null, wan_isp: '', samples: 2, lossy: 0, avg_loss: 0, max_loss: 0}]}};
      await page.locator('#refreshBtn').click();
      await page.waitForFunction(() => !document.getElementById('lossWansBox').hidden);
      assert.equal(await page.locator('#lossWans tr').count(), 3);
      assert.match(await page.locator('#lossWans tr').nth(0).textContent(), /Alpha Net · 203\.0\.113\.10\s*6\s*2\s*8\.3% \/ 50%/);
      assert.match(await page.locator('#lossWans tr').nth(2).textContent(), /No WAN recorded/);
      api.calls.length = 0;
      await page.locator('#lossWans [data-loss-wan]').nth(1).click();
      await page.waitForFunction(() => document.getElementById('wanSelectChart').selectedOptions[0].textContent.startsWith('Beta Cable'));
      await page.waitForTimeout(300);
      assert(calls().some(c => c.path === '/api/loss' && c.query.includes('wan_ip=198.51.100.20&wan_isp=Beta%20Cable')), 'the loss report is requested for that source');
      assert.equal(await page.locator('#lossWans tr.selected').count(), 1);
      await sel('#wanSelectChart').selectOption('all');
      await page.waitForTimeout(300);

      // "one line per WAN source" in the compare chart
      api.historyByWan = {[A.wan_ip]: [{ts: now - 1200, avg_loss_pct: 1}, {ts: now - 600, avg_loss_pct: 2}, {ts: now - 60, avg_loss_pct: 0}], [B.wan_ip]: [{ts: now - 900, avg_loss_pct: 6}, {ts: now - 300, avg_loss_pct: 9}]};
      await page.locator('#compareToggle').click();
      await page.locator('#comparePicker').evaluate(e => e.open = true);
      assert.equal(await page.locator('[data-cmp-bywan]').count(), 1, 'offered because the period has two sources');
      api.calls.length = 0;
      await page.locator('[data-cmp-bywan]').check();
      await page.waitForFunction(() => document.querySelectorAll('#compareLegend .lg').length === 2);
      const legend = await page.locator('#compareLegend .lg').allTextContents();
      assert(/Alpha Net/.test(legend[0]) && /Beta Cable/.test(legend[1]), legend.join(' | '));
      assert(calls().some(c => c.path === '/api/history' && c.query.includes('wan_ip=203.0.113.10')) && calls().some(c => c.path === '/api/history' && c.query.includes('wan_ip=198.51.100.20')), 'one request per source');
      assert.equal(await page.locator('#primaryChart path.c-series').count(), 2, 'two lines: one per source');
      await page.locator('[data-cmp-bywan]').uncheck();
      await page.waitForFunction(() => document.querySelectorAll('#compareLegend .lg').length === 0);
      await page.locator('#compareToggle').click();
      api.loss = null; api.historyByWan = null;

      // remembered filters: the period, the WAN source and the metric come back after a reload
      const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('netprobe.filters')));
      await sel('#periodSelect').selectOption('86400');
      await sel('#wanSelectChart').selectOption({label: 'Beta Cable · 198.51.100.20 · 60 samples'});
      await page.locator('#metricTabs [data-metric="avg_latency_ms"]').click();
      assert.deepEqual(await stored(), {range: 86400, wan: ['198.51.100.20', 'Beta Cable'], metric: 'avg_latency_ms'});
      api.calls.length = 0;
      await page.reload();
      await page.waitForFunction(() => document.getElementById('wanSelectChart').selectedOptions[0]?.textContent.startsWith('Beta Cable'));
      assert.equal(await sel('#periodSelect').inputValue(), '86400');
      assert.equal(await page.locator('#metricTabs button.active').getAttribute('data-metric'), 'avg_latency_ms');
      await page.waitForTimeout(400);
      const firstHistory = calls().find(c => c.path === '/api/history');
      assert(firstHistory && firstHistory.query.startsWith('?range=86400') && firstHistory.query.endsWith('&wan_ip=198.51.100.20&wan_isp=Beta%20Cable'), 'the very first request already carries the filters: ' + firstHistory?.query);
      // a provider that is no longer in the data, an unknown metric and a period that does not exist are dropped quietly
      await page.evaluate(() => localStorage.setItem('netprobe.filters', JSON.stringify({range: 12345, wan: ['192.0.2.77', 'Gone Net'], metric: 'nope'})));
      await page.reload();
      await page.waitForFunction(() => document.getElementById('wanSelectChart').options.length > 1);
      await page.waitForFunction(() => JSON.parse(localStorage.getItem('netprobe.filters')).wan === null);
      assert.equal(await sel('#wanSelectChart').inputValue(), 'all');
      assert.equal(await sel('#periodSelect').inputValue(), '3600');
      assert.equal(await page.locator('#metricTabs button.active').getAttribute('data-metric'), 'avg_loss_pct');
      // damaged storage never breaks the page
      for (const junk of ['{not json', 'null', '[1,2]', '"text"']) {
        await page.evaluate(v => localStorage.setItem('netprobe.filters', v), junk);
        await page.reload();
        await page.waitForFunction(() => document.getElementById('wanSelectChart').options.length > 1);
        assert.equal(await sel('#wanSelectChart').inputValue(), 'all', junk);
      }
      await page.evaluate(() => localStorage.removeItem('netprobe.filters'));

      // forgetting the recorded addresses: asks first, sends the explicit confirmation
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      await page.locator('#wanForget').click();
      await page.waitForFunction(() => !document.getElementById('wanForget').disabled);
      assert.match(dialogs.pop(), /Erase the recorded IP addresses and providers from the whole history\?/);
      assert.equal(api.forgotten, undefined, 'refused: nothing sent');
      answer = true;
      await page.locator('#wanForget').click();
      await page.waitForFunction(() => /Addresses erased from 42 records/.test(document.getElementById('retMsg').textContent));
      assert.deepEqual(api.forgotten, {confirm: true});

      // the notification switch: needs the option on, is part of the same draft, a failed save keeps the edit
      const notify = page.locator('#wan_notify'), save = page.locator('#ipSave');
      const posts = () => api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST');
      assert.equal(await notify.isEnabled(), true);
      assert.equal(await notify.isChecked(), false);
      const sent = posts().length;
      await notify.check();
      assert.equal(posts().length, sent, 'nothing is sent before Save');
      await save.click();
      await page.waitForFunction(() => document.getElementById('ipMessage').textContent === 'Saved.');
      assert.deepEqual(posts().pop().body, {show_public_ip: true, wan_check_min: 15, wan_notify: true});
      api.prefsFail = true;
      await notify.uncheck();
      await save.click();
      await page.waitForFunction(() => /Could not save/.test(document.getElementById('ipMessage').textContent));
      assert.equal(await notify.isChecked(), false, 'the edit stays');
      api.prefsFail = false;
      await save.click();
      await page.waitForFunction(() => document.getElementById('ipMessage').textContent === 'Saved.');
      assert.equal(posts().pop().body.wan_notify, false);
      assert.match(await page.locator('label:has(#wan_notify)').textContent(), /when the external IP or provider changes/);

      // how often the address is checked: 15 minutes by default, a minute, or only by the button in the card
      const every = page.locator('#wan_check'), refresh = page.locator('#wanRefresh');
      const prefPosts = () => api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST');
      assert.deepEqual(await every.locator('option').allTextContents(), ['Manually (button in the card)', 'Every 15 minutes', 'Every minute']);
      assert.equal(await every.inputValue(), '15');
      assert.equal(await every.isEnabled(), true);
      await every.selectOption('1');
      assert.equal(await save.isEnabled(), true);
      assert.match(await page.locator('#wanMeta').textContent(), /checked every 15 minutes/, 'the card shows what is saved until Save');
      await page.locator('#refreshBtn').click();           // a data refresh must not throw the draft away
      await page.waitForFunction(() => !document.getElementById('refreshBtn').disabled);
      assert.equal(await every.inputValue(), '1');
      await save.click();
      await page.waitForFunction(() => /checked every minute/.test(document.getElementById('wanMeta').textContent));
      assert.deepEqual(posts().pop().body, {show_public_ip: true, wan_check_min: 1, wan_notify: false});
      await every.selectOption('0');
      assert.equal(await notify.isDisabled(), true, 'a manual card cannot watch the link (shown while editing)');
      assert.match(await page.locator('#wanNotifyNote').textContent(), /not available in manual mode/);
      api.prefsFail = true;
      await save.click();
      await page.waitForFunction(() => /Could not save/.test(document.getElementById('ipMessage').textContent));
      assert.equal(await every.inputValue(), '0', 'a failed save keeps the edit');
      api.prefsFail = false;
      await save.click();
      await page.waitForFunction(() => /refreshed on demand/.test(document.getElementById('wanMeta').textContent));
      assert.equal(posts().pop().body.wan_check_min, 0);

      // the button inside the card asks the server once, shows progress and then the new answer
      const refreshCalls = () => api.calls.filter(c => c.path === '/api/public-ip/refresh');
      assert.equal(await refresh.isVisible(), true);
      assert.equal(await refresh.textContent(), 'Refresh');
      await refresh.click();
      await page.waitForFunction(() => document.getElementById('wanRefresh').disabled && /Refreshing/.test(document.getElementById('wanRefresh').textContent));
      await page.waitForFunction(() => document.getElementById('wanIp').textContent === '198.51.100.99', null, {timeout: 8000});
      assert.equal(await page.locator('#wanIsp').textContent(), 'Refreshed Net');
      assert.equal(await refresh.isEnabled(), true);
      assert.equal(await refresh.textContent(), 'Refresh');
      assert.equal(refreshCalls().length, 1, 'one press, one request');
      assert.equal(refreshCalls()[0].method, 'POST');
      assert.equal(refreshCalls()[0].headers['x-netprobe-request'], 'settings');
      await every.selectOption('15');
      await save.click();
      await page.waitForFunction(() => /checked every 15 minutes/.test(document.getElementById('wanMeta').textContent));
      assert.equal(await notify.isEnabled(), true);
      assert.equal(await page.locator('#wanNotifyNote').isHidden(), true);

      // manual mode without any answer yet: the card asks to press the button, nothing is requested by itself
      api.prefs.wan_check_min = 0;
      api.publicIp = {enabled: true, every_min: 0, pending: false, refreshing: false, ip: null, isp: null, source: null, checked_at: null, error: null};
      const manualPage = (await open('unifi', 'en')).page;
      await manualPage.locator('#unifiTabs [data-tab="settings"]').click();
      await manualPage.waitForFunction(() => /Press Refresh/.test(document.getElementById('wanMeta').textContent));
      assert.equal(await manualPage.locator('#wanIp').textContent(), '—');
      assert.equal(await manualPage.locator('#wan_check').inputValue(), '0');
      assert.equal(refreshCalls().length, 1, 'opening the page in manual mode sends no refresh');
      await manualPage.close();
      api.prefs.wan_check_min = 15;
      api.publicIp = {enabled: true, ip: '198.51.100.7', isp: 'Example Net', source: 'ipwho.is', checked_at: now, error: null, every_min: 15, refreshing: false};

      // switching the option off removes the selectors again
      api.prefs.show_public_ip = false; api.publicIp = {enabled: false};
      await page.locator('#show_public_ip').evaluate(e => { e.checked = false; e.dispatchEvent(new Event('change')); });
      assert.equal(await notify.isDisabled(), true, 'nothing to notify about while the option is off');
      await save.click();
      await page.waitForFunction(() => document.getElementById('wanPickChart').hidden);
      assert.equal(await page.locator('#retBox #storageInfo').count(), 1, 'the database size line sits in the History block');
      assert.equal(await page.locator('#ipBox #storageInfo').count(), 0);
      assert.equal(await page.locator('#wanPickTargets').isVisible(), false);
      assert.equal(await notify.isDisabled(), true, 'nothing to notify about while the option is off');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // a single WAN in the period: selectors are there, the table shows no per-source lines; unknown only: no selectors
      const A = {wan_ip: '203.0.113.10', wan_isp: 'Alpha Net'};
      api.prefs.show_public_ip = true; api.publicIp = {enabled: true, ip: A.wan_ip, isp: A.wan_isp, source: 'ipwho.is', checked_at: now, error: null};
      api.wanData = {sources: [{...A, samples: 70, first_ts: now - 3000, last_ts: now}], current: A};
      api.targets = [{target: 'google.com', address: '192.0.2.1', kind: 'ping', ts: now, latency: 20, jitter: 1, loss: 0, avg_latency: 21, max_latency: 30, avg_loss: 0, max_loss: 0, samples: 70, state: 'OK', addresses: [], wan_sources: [{...A, samples: 70, avg_latency: 21, max_latency: 30, avg_loss: 0, max_loss: 0}]}];
      const {page, errors} = await open('unifi', 'ru');
      await page.waitForFunction(() => !document.getElementById('wanPickChart').hidden);
      assert.deepEqual(await page.locator('#wanSelectChart option').allTextContents(), ['Все WAN-источники', 'Текущий WAN: Alpha Net · 203.0.113.10 · 70 замеров']);
      assert.equal(await page.locator('#targetsBody .wan-src').count(), 0, 'one source: no per-source lines');
      api.wanData = {sources: [{wan_ip: null, wan_isp: null, samples: 70, first_ts: now - 3000, last_ts: now}], current: null};
      await page.locator('#refreshBtn').click();
      await page.waitForFunction(() => document.getElementById('wanPickChart').hidden);
      assert.deepEqual(errors, []);
      api.prefs.show_public_ip = false; api.publicIp = {enabled: false}; api.targets = []; api.wanData = {sources: [], current: null};
      await page.close();
    }

    // --- history: retention period for the nightly cleanup, preview and confirmed deletion ---
    {
      const {page, errors} = await open('netprobe', 'en');
      const dialogs = [];
      let answer = false;
      page.on('dialog', async d => { dialogs.push(d.message()); await (answer ? d.accept() : d.dismiss()); });
      const cleanups = () => api.calls.filter(c => c.path === '/api/history/cleanup');
      const retentionPosts = () => api.calls.filter(c => c.path === '/api/prefs' && c.method === 'POST' && c.body && 'history_retention_days' in c.body);
      await page.locator('#alertSettings').evaluate(e => e.open = true);
      assert.equal(await page.locator('#retKeep').inputValue(), '0');
      assert.match(await page.locator('#retNote').textContent(), /History is not deleted now/);
      assert.match(await page.locator('#retLast').textContent(), /No cleanup has run yet/);
      assert.deepEqual(await page.locator('#retKeep option').allTextContents(), ['Forever (never delete)', '7 days', '30 days', '90 days', '180 days', '1 year']);

      // choosing a period asks first; refusing changes nothing
      await page.locator('#retKeep').selectOption('30');
      await page.waitForFunction(() => document.getElementById('retKeep').value === '0');
      assert.match(dialogs.pop(), /Keep history for 30 days\..*irreversibly/);
      assert.equal(retentionPosts().length, 0);
      answer = true;
      await page.locator('#retKeep').selectOption('30');
      await page.waitForFunction(() => /Retention saved/.test(document.getElementById('retMsg').textContent));
      assert.deepEqual(retentionPosts().pop().body, {history_retention_days: 30});
      assert.equal(await page.locator('#retKeep').inputValue(), '30');
      assert.match(await page.locator('#retNote').textContent(), /older than 30 days is deleted once a day at night\. Next cleanup: .+/);
      // going back to "forever" needs no scary question
      dialogs.length = 0;
      await page.locator('#retKeep').selectOption('0');
      await page.waitForFunction(() => /History is not deleted now/.test(document.getElementById('retNote').textContent));
      assert.deepEqual(dialogs, []);
      assert.deepEqual(retentionPosts().pop().body, {history_retention_days: 0});

      // preview: counts only
      await page.locator('#retDays').selectOption('180');
      await page.locator('#retPreview').click();
      await page.waitForFunction(() => /1,234 records would be deleted \(older than 180 days\)/.test(document.getElementById('retMsg').textContent));
      assert.deepEqual(cleanups().pop().body, {days: 180, dry_run: true});
      assert.equal(cleanups().filter(c => c.body.confirm).length, 0, 'a preview never deletes');

      // delete: the number is shown in the question, refusing deletes nothing, accepting sends the explicit confirmation
      answer = false; dialogs.length = 0;
      await page.locator('#retDelete').click();
      await page.waitForFunction(() => !document.getElementById('retDelete').disabled);
      assert.match(dialogs.pop(), /Delete 1,234 records older than 180 days\? This cannot be undone\./);
      assert.equal(cleanups().filter(c => c.body.confirm).length, 0, 'refused: nothing was deleted');
      answer = true;
      // a failing backup stops the deletion
      api.backupFail = 'Not enough free space for a backup: 3 MiB free, about 14 MiB needed';
      await page.locator('#retDelete').click();
      await page.waitForFunction(() => /Nothing was deleted: the backup could not be made \(Not enough free space/.test(document.getElementById('retMsg').textContent));
      assert.equal(cleanups().filter(c => c.body.confirm).length, 0, 'no copy, no deletion');
      api.backupFail = null;
      api.calls.length = 0;
      await page.locator('#retDelete').click();
      await page.waitForFunction(() => /1,234 records deleted; database 5\.07 MiB → 2\.00 MiB/.test(document.getElementById('retMsg').textContent));
      assert.deepEqual(cleanups().filter(c => c.body.confirm).pop().body, {days: 180, confirm: true});
      const order = api.calls.filter(c => c.method === 'POST').map(c => c.path);
      assert(order.indexOf('/api/backups') >= 0 && order.indexOf('/api/backups') < order.indexOf('/api/history/cleanup', order.indexOf('/api/backups')), 'the copy is made before the deletion: ' + order.join(' '));
      assert.equal(api.backups.length, 1);
      await page.waitForFunction(() => /Last cleanup: .+ — 1,234 records deleted; database 5\.07 MiB → 2\.00 MiB/.test(document.getElementById('retLast').textContent));

      // nothing older: no question at all; a busy server is reported
      api.cleanupTotal = 0; dialogs.length = 0;
      await page.locator('#retDelete').click();
      await page.waitForFunction(() => /Nothing to delete: no records are older than 180 days/.test(document.getElementById('retMsg').textContent));
      assert.deepEqual(dialogs, []);
      api.cleanupTotal = 5; api.cleanupError = 'A history cleanup is already running';
      await page.locator('#retPreview').click();
      await page.waitForFunction(() => /Failed: A history cleanup is already running/.test(document.getElementById('retMsg').textContent));
      api.cleanupError = null; api.cleanupTotal = 1234; api.history.last = null; api.prefs.history_retention_days = 0;
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page} = await open('unifi', 'ru');
      await page.locator('#unifiTabs [data-tab="settings"]').click();
      assert.deepEqual(await page.locator('#retKeep option').allTextContents(), ['Всегда (не удалять)', '7 дней', '30 дней', '90 дней', '180 дней', '1 год']);
      assert.match(await page.locator('#retNote').textContent(), /Сейчас история не удаляется/);
      assert.equal(await page.locator('#retDelete').textContent(), 'Удалить…');
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
    console.log('PASS: history retention and cleanup (choice, preview, confirmed deletion, busy), background speedtest (start, poll, failure, busy, resumed), one request per address, progressive drawing, external IP checkbox and WAN / ISP card in both styles (provider, unknown provider, escaped, failure, failed save, RU, phone width), schedule form (container/off/N hours/daily times, validation, notify switch, next test, saved state after reload, RU texts), speedtest service list (Speedtest.net / Ookla CLI / Fast.com) with explanations, saved choice, form adapts to fast.com, unknown upload shown as a dash, failed save reverted, works without the preferences API');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
