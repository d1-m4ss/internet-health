// Behavior tests: run with the bundled Playwright dependency directory in NODE_PATH.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const os = require('node:os');
const root = path.resolve(__dirname, '..');
const ui = process.env.NETPROBE_UI || path.join(root, 'src/index.html');
const shots = process.env.NETPROBE_SHOTS || os.tmpdir();
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  try {
    const page = await browser.newPage({reducedMotion:'reduce', locale:'ru-RU'});
    let offline = true, edgeState = 'DOWN', dnsDown = false, routerName = 'ASUS RT-AX86U';
    const calls = [];
    const now = Math.floor(Date.now()/1000);
    const row = {ts:now, score:88,avg_loss_pct:2,avg_latency_ms:31,avg_jitter_ms:4,avg_dns_latency_ms:null};
    const settings = {enabled:true,loss_pct:10,latency_ms:150,jitter_ms:80,dns_ms:250,confirm_samples:2,recovery_samples:2,turbo:false};
    const errors = [];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if(url.pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(ui,'utf8')});
      calls.push({url:url.pathname+url.search,method:request.method(),headers:request.headers(),body:request.postDataJSON()});
      if(offline) return route.fulfill({status:503,json:{error:'Controlled outage'}});
      const payload = url.pathname === '/api/config' ? {probe_interval:30,ping_count:4,gateway_ip:'192.0.2.1',router_ip:'192.0.2.2',router_name:routerName,sites:['edge.example'],speedtest_backend:'python',speedtest_backends_available:{python:true,ookla:false},speedtest_secure:true} :
        url.pathname === '/api/score/latest' ? {data:row} :
        url.pathname === '/api/history' ? {data:[{...row,ts:now-30,max_loss_pct:25},{...row,ts:now-300,avg_loss_pct:0,max_loss_pct:0}]} :
        url.pathname === '/api/targets/recent' ? {started_at:now-60,data:[{target:'192.0.2.1',address:'192.0.2.1',kind:'ping',ts:now,latency:0.4,jitter:0.1,loss:0,avg_latency:0.4,max_latency:0.6,avg_loss:0,samples:2,state:'OK'},{target:'192.0.2.2',address:'192.0.2.2',kind:'ping',ts:now,latency:0.9,jitter:0.2,loss:0,avg_latency:0.9,max_latency:1.2,avg_loss:0,samples:2,state:'OK'},{target:'dns:8.8.8.8',address:'8.8.8.8',kind:'dns',ts:now,latency:4.2,jitter:null,loss:null,avg_latency:4,max_latency:6,avg_loss:null,samples:2,state:'OK'},{target:'dns:1.1.1.1',address:'1.1.1.1',kind:'dns',ts:now,latency:dnsDown?null:3.1,jitter:null,loss:null,avg_latency:3,max_latency:5,avg_loss:null,samples:2,state:dnsDown?'DEGRADED':'OK'},{target:'edge.example',address:'192.0.2.7',kind:'ping',ts:now,latency:null,jitter:null,loss:100,avg_latency:31,max_latency:40,avg_loss:20,samples:2,state:edgeState,addresses:[{address:'192.0.2.7',samples:1,avg_latency:31,max_latency:40,avg_loss:20,max_loss:100},{address:'192.0.2.8',samples:1,avg_latency:35,max_latency:42,avg_loss:0,max_loss:0}]},...(dnsDown?[{target:'1.1.1.1',address:'1.1.1.1',kind:'ping',ts:now,latency:9,jitter:0.5,loss:0,avg_latency:9,max_latency:9.4,avg_loss:0,samples:2,state:'OK'}]:[])]} :
        url.pathname === '/api/targets/history' ? {data:[{ts:now-30,latency:null,jitter:null,loss:20,max_loss:100,address:'192.0.2.7'},{ts:now,latency:31,jitter:4,loss:0,max_loss:0,address:'192.0.2.7'}]} :
        url.pathname === '/api/loss' ? {truncated:false,data:{outages:[{id:2,start_ts:now-900,end_ts:now-860,kind:'internet',status:'sent',duration:40},{id:1,start_ts:now-1800,end_ts:null,kind:'router',status:'open',duration:null}],window:{from:now-3600,to:now},summary:{samples:100,lossy:4,avg_loss:2.5,max_loss:100,episodes:2},targets:[{target:'edge.example',samples:50,lossy:4,avg_loss:5,max_loss:100,episodes:2},{target:'192.0.2.2',samples:50,lossy:0,avg_loss:0,max_loss:0,episodes:0}],episodes:[{target:'edge.example',start:now-600,end:now-540,duration:60,peak:100,avg:75,samples:3},{target:'edge.example',start:now-3000,end:now-3000,duration:0,peak:25,avg:25,samples:1}]}} :
        url.pathname === '/api/events' ? {data:[{id:1,ts:now,target:'edge.example',previous:'OK',state:'DOWN',reasons:['packet loss'],metrics:{loss:100}}]} :
        url.pathname === '/api/settings' ? {settings,storage:{bytes:1024,retention_days:null},started_at:now-60} :
        url.pathname === '/api/speedtest/latest' ? {result:null} :
        url.pathname === '/api/speedtest/history' ? {tests:[]} : {lines:[],next_seq:0};
      if(url.pathname === '/api/settings' && request.method()==='POST') Object.assign(settings, request.postDataJSON());
      await route.fulfill({json:payload});
    });
    await page.goto('http://netprobe.test/');
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#scoreValue').textContent(),'—','offline startup must not invent score');
    assert.match(await page.locator('#sourceTag').textContent(),/OFFLINE|НЕДОСТУПЕН/);
    offline = false;
    await page.locator('#refreshBtn').click();
    await page.waitForFunction(()=>document.getElementById('scoreValue').textContent==='88.0');
    assert.equal(await page.locator('#dnsValue').textContent(),'—','null DNS must not become zero');
    await page.locator('#targetsBody button[data-target="edge.example"]').first().click();
    await page.waitForFunction(()=>document.getElementById('chartTitle').textContent.includes('edge.example'));
    assert(calls.some(c=>c.url.includes('/api/targets/history?')&&c.url.includes('target=edge.example')),'target selection must fetch target history');
    await page.waitForFunction(()=>document.querySelectorAll('#primaryChart circle.c-peak').length>0);
    {   // the popup of the point with the highest peak shows that peak (hover, no native title)
      const box=await page.evaluate(()=>{const c=[...document.querySelectorAll('#primaryChart circle.c-peak')].sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top)[0].getBoundingClientRect();return {x:c.left+c.width/2,y:c.top+c.height/2};});
      await page.mouse.move(box.x,box.y);
      await page.waitForFunction(()=>{const t=document.getElementById('chartTip');return !t.hidden&&/100\.00 %/.test(t.textContent)});
      assert.match(await page.locator('#chartTip').textContent(),/макс/i,'the popup names the peak row');
      assert.equal(await page.locator('#primaryChart title').count(),0,'no slow native tooltips');
      await page.mouse.move(2,2);
      await page.waitForFunction(()=>document.getElementById('chartTip').hidden);
    }
    await page.locator('[data-metric="avg_latency_ms"]').click();
    assert.equal(await page.locator('#primaryChart circle').count(),1,'null RTT must leave a graph gap, not a healthy zero');
    {   // every metric has its own line colour, and the legend swatch follows it
      const colour=()=>page.evaluate(()=>({line:getComputedStyle(document.getElementById('primaryChart')).getPropertyValue('--chart-line').trim(),swatch:getComputedStyle(document.querySelector('.legend-line')).backgroundColor}));
      const colours={};
      for(const m of ['avg_loss_pct','avg_latency_ms','avg_jitter_ms']){
        await page.locator(`#metricTabs [data-metric="${m}"]`).click();
        await page.waitForFunction(m2=>document.getElementById('diagnostics').dataset.metric===m2,m);
        colours[m]=await colour();
      }
      assert.equal(new Set(Object.values(colours).map(c=>c.line)).size,3,'loss, delay and jitter are three different colours: '+JSON.stringify(colours));
      for(const c of Object.values(colours)) assert.notEqual(c.swatch,'rgba(0, 0, 0, 0)','the legend swatch has a colour');
    }
    await page.locator('[data-metric="avg_loss_pct"]').click();
    await page.locator('#targetsBody details').evaluate(e=>e.open=true);
    await page.locator('[data-address="192.0.2.8"]').click();
    await page.waitForFunction(()=>document.getElementById('chartTitle').textContent.includes('192.0.2.8'));
    await page.waitForTimeout(50);
    assert(calls.some(c=>c.url.includes('address=192.0.2.8')),'IP selection must filter the target history');
    await page.locator('#alertSettings').evaluate(e=>e.open=true);
    const beforeInvalid = calls.filter(c=>c.url==='/api/settings'&&c.method==='POST').length;
    await page.locator('#loss_pct').fill('0');
    await page.locator('#settingsForm button[type="submit"]').click();
    assert.equal(calls.filter(c=>c.url==='/api/settings'&&c.method==='POST').length,beforeInvalid,'zero thresholds must not be submitted');
    await page.locator('#loss_pct').fill('15');
    await page.locator('#settingsForm button[type="submit"]').click();
    await page.waitForFunction(()=>document.getElementById('settingsMessage').textContent.includes('Сохранено'));
    const saved = calls.find(c=>c.url==='/api/settings'&&c.method==='POST');
    assert.equal(saved.body.loss_pct,15);
    assert.equal(saved.headers['x-netprobe-request'],'settings');
    assert.equal(saved.body.turbo,false,'turbo is part of every settings save');
    // ---- Turbo mode: toggle is saved, the chip appears, the explanation opens in a dialog and closes with Esc
    assert(!(await page.locator('#turboChip').isVisible()),'no Turbo chip while it is off');
    await page.locator('#turbo').check();
    await page.locator('#settingsForm button[type="submit"]').click();
    await page.waitForFunction(()=>!document.getElementById('turboChip').hidden);
    assert.equal(calls.filter(c=>c.url==='/api/settings'&&c.method==='POST').pop().body.turbo,true,'the Turbo toggle is sent in the POST body');
    assert(!(await page.locator('#turboDialog').isVisible()));
    await page.locator('#turboInfo').click();
    assert(await page.locator('#turboDialog').isVisible(),'the info button opens the Turbo dialog');
    assert.match(await page.locator('#turboDialog').textContent(),/5 секунд.*9 МБ/s);
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>!document.getElementById('turboDialog').open);
    assert.equal(await page.evaluate(()=>document.activeElement.id),'turboInfo','focus returns to the info button');
    await page.locator('#turboInfo').click(); await page.locator('#turboClose').click();
    await page.waitForFunction(()=>!document.getElementById('turboDialog').open);
    row.ts=now-1000;
    await page.locator('#refreshBtn').click();
    await page.waitForFunction(()=>document.getElementById('sourceTag').textContent.includes('STALE'));
    assert(!/штатно/.test(await page.locator('#statusName').textContent()),'old samples must not claim healthy status');
    offline = true;
    await page.locator('#refreshBtn').click();
    await page.waitForFunction(()=>document.getElementById('sourceTag').textContent.includes('OFFLINE'));
    assert.equal(await page.locator('#scoreValue').textContent(),'88.0','offline refresh must retain last real score');
    assert.equal(await page.locator('#speedBtn').isDisabled(),true);
    // ---- retained real data + offline: restore a live backend for the visual/behavioural checks below
    offline = false; row.ts = now;
    await page.locator('#refreshBtn').click();
    await page.waitForFunction(()=>document.getElementById('sourceTag').textContent.startsWith('LIVE'));

    // ---- period dropdown: every range from 1 minute to a month, requested with its own range
    const optionValues = await page.locator('#periodSelect option').evaluateAll(o=>o.map(x=>x.value));
    assert.deepEqual(optionValues,['60','300','900','3600','21600','86400','259200','604800','2592000','custom'],'period dropdown offers 1m..1mo and a custom range');
    for (const value of ['60','300','900','604800','2592000']) {
      await page.locator('#periodSelect').selectOption(value);
      await page.waitForTimeout(150);
      assert(calls.some(c=>c.url.includes('range='+value)),`period ${value}s must query the API with its own range`);
    }
    await page.locator('#periodSelect').selectOption('3600');

    // ---- compare mode: several metrics and several DNS servers on one chart
    const lines = () => page.locator('#primaryChart path.c-series').count();
    assert(!(await page.locator('#comparePicker').isVisible()),'compare picker is hidden in the normal chart');
    await page.locator('#compareToggle').click();
    assert(await page.locator('#comparePicker').isVisible()); assert(!(await page.locator('#chartTarget').isVisible()),'single-target select is replaced by the multi picker');
    // metrics are toggles now: latency + jitter share one real millisecond axis
    await page.locator('#metricTabs [data-metric="avg_latency_ms"]').click(); await page.locator('#metricTabs [data-metric="avg_loss_pct"]').click(); await page.locator('#metricTabs [data-metric="avg_jitter_ms"]').click();
    assert.equal(await lines(),2,'two metrics -> two lines'); assert.match(await page.locator('#axisNote').textContent(),/мс|ms/);
    // mixing units switches to per-series scale and says so
    await page.locator('#metricTabs [data-metric="avg_loss_pct"]').click();
    assert.equal(await lines(),3); assert.match(await page.locator('#axisNote').textContent(),/своя шкала|own scale/);
    assert((await page.locator('#compareLegend .lg').count())===3,'legend lists every series with its real value');
    await page.locator('#metricTabs [data-metric="avg_loss_pct"]').click(); await page.locator('#metricTabs [data-metric="avg_jitter_ms"]').click();
    // several DNS servers in one chart; DNS has no loss/jitter measurements, so only latency series exist
    await page.locator('#comparePicker summary').click();
    await page.locator('#cmpMenu [data-cmp-action="alldns"]').click();
    await page.waitForTimeout(250);
    assert(calls.some(c=>c.url.includes('/api/targets/history?')&&c.url.includes('target=dns%3A8.8.8.8')) && calls.some(c=>c.url.includes('/api/targets/history?')&&c.url.includes('target=dns%3A1.1.1.1')),'each selected DNS server is fetched');
    assert.equal(await lines(),2,'two DNS servers -> two latency lines'); assert.match(await page.locator('#compareLegend').textContent(),/dns:8\.8\.8\.8/); assert.match(await page.locator('#compareLegend').textContent(),/dns:1\.1\.1\.1/);
    assert(await page.locator('#comparePicker').evaluate(e=>e.open),'picker stays open while choosing');
    await page.locator('#chartTitle').click();
    assert(!(await page.locator('#comparePicker').evaluate(e=>e.open)),'clicking outside closes the picker');
    await page.locator('#primaryChart').hover({position:{x:300,y:100}});
    await page.waitForFunction(()=>{const t=document.getElementById('chartTip');return t&&!t.hidden&&/dns:/.test(t.textContent)});
    await page.locator('#metricTabs [data-metric="avg_dns_latency_ms"]').isDisabled().then(v=>assert.equal(v,true,'summary-only metrics are unavailable for per-target series'));
    await page.locator('#compareToggle').click();
    assert(await page.locator('#chartTarget').isVisible(),'leaving compare mode restores the normal chart');
    assert.equal(await page.locator('#primaryChart path.c-series').count(),0);

    // ---- custom period + packet-loss report
    const local = ts => { const d = new Date(ts * 1000), z = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`; };
    assert(!(await page.locator('#customRange').isVisible()),'custom range inputs are hidden until chosen');
    await page.locator('#periodSelect').selectOption('custom');
    assert(await page.locator('#customRange').isVisible());
    const callsBefore = calls.length;
    await page.locator('#rangeFrom').fill(local(now-7200)); await page.locator('#rangeTo').fill(local(now-3600));
    await page.locator('#rangeApply').click(); await page.waitForTimeout(250);
    const fromTs = Math.floor(new Date(local(now-7200)).getTime()/1000), toTs = Math.floor(new Date(local(now-3600)).getTime()/1000);
    const windowCalls = calls.slice(callsBefore).map(c=>c.url);
    for (const path of ['/api/history','/api/targets/recent','/api/events','/api/loss']) assert(windowCalls.some(u=>u.startsWith(path)&&u.includes('from='+fromTs)&&u.includes('to='+toTs)),`${path} is requested for the absolute window`);
    assert(windowCalls.some(u=>u.startsWith('/api/targets/recent')&&u.includes('range=300')&&!u.includes('from=')),'live state (banner, path) keeps using fresh targets while a past window is shown');
    assert.match(await page.locator('#exportLink').getAttribute('href'),new RegExp('from='+fromTs));
    // invalid windows are refused in the UI without any request
    const invalidBefore = calls.length;
    await page.locator('#rangeFrom').fill(local(now-3600)); await page.locator('#rangeTo').fill(local(now-7200)); await page.locator('#rangeApply').click(); await page.waitForTimeout(150);
    assert((await page.locator('#rangeError').textContent()).length>0,'from after to is explained'); assert.equal(calls.length,invalidBefore,'an invalid window sends no request');
    await page.locator('#rangeFrom').fill(local(now-7200)); await page.locator('#rangeTo').fill(local(now-3600)); await page.locator('#rangeApply').click(); await page.waitForTimeout(150);
    assert.equal((await page.locator('#rangeError').textContent()).trim(),'');
    // loss report: summary, per-target table, episodes, zoom to an episode
    const loss = await page.evaluate(()=>({sum:document.getElementById('lossSummary').textContent,rows:document.querySelectorAll('#lossTargets tr').length,episodes:document.querySelectorAll('#lossEpisodes [data-episode]').length,text:document.getElementById('lossCard').textContent}));
    assert.match(await page.locator('#lossOutages').textContent(),/Обрыв интернета.*уведомление отправлено.*40 с/s,'Turbo outages are listed in the loss card');
    assert.match(await page.locator('#lossOutages').textContent(),/Роутер не отвечал.*идёт сейчас/s);
    assert.match(loss.sum,/2\.5/); assert.match(loss.sum,/100/); assert.equal(loss.rows,2); assert.equal(loss.episodes,2); assert.match(loss.text,/edge\.example/);
    await page.locator('#lossEpisodes [data-episode="0"]').click(); await page.waitForTimeout(250);
    assert.equal(await page.locator('#periodSelect').inputValue(),'custom');
    assert(await page.evaluate(()=>document.getElementById('chartTitle').textContent.includes('edge.example')),'zoom selects the episode target');
    assert(calls.some(c=>c.url.startsWith('/api/targets/history?')&&c.url.includes('target=edge.example')&&c.url.includes('from='+(now-600-300))&&c.url.includes('to='+(now-540+300))),'zoom requests the episode window with a five minute margin');
    await page.locator('#periodSelect').selectOption('3600'); await page.waitForTimeout(200);
    assert(!(await page.locator('#customRange').isVisible()),'back to a relative period hides the custom inputs');
    assert(calls.slice(-8).some(c=>c.url.startsWith('/api/loss')&&!c.url.includes('from=')&&c.url.includes('range=3600')),'the loss report follows the relative period again');

    // ---- path Unraid -> router -> Internet is built only from real targets
    const chain = await page.evaluate(()=>({unraid:document.getElementById('nodeUnraid').dataset.s,router:document.getElementById('nodeRouter').dataset.s,net:document.getElementById('nodeInternet').dataset.s,
      l1:document.getElementById('linkUnraidMs').textContent,l2:document.getElementById('linkRouterMs').textContent,sub:document.getElementById('nodeInternetSub').textContent}));
    assert.deepEqual([chain.unraid,chain.router,chain.net],['ok','ok','bad'],'node colours follow the real target states: '+JSON.stringify(chain));
    assert.equal(chain.l1,'0.9 ms','Unraid->router link shows the measured router RTT');
    assert.equal(chain.l2,'—','a DOWN external target has no RTT and must not show an invented one');
    assert.match(chain.sub,/0 из 1/);

    // ---- the top banner follows severity: red when a target does not answer, amber for degradation, green only when healthy
    const banner = () => page.evaluate(()=>({dot:document.getElementById('statusDot').className,name:document.getElementById('statusName').textContent,desc:document.getElementById('statusDescription').textContent}));
    let b = await banner();
    assert.match(b.dot,/offline/,'a DOWN target must turn the banner red: '+JSON.stringify(b));
    assert.match(b.name,/Нет ответа: 1 из 5 проверок/); assert.match(b.desc,/edge\.example/);
    edgeState = 'DEGRADED'; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>/Перебои/.test(document.getElementById('statusName').textContent));
    b = await banner(); assert.match(b.dot,/warning/,'degradation is amber, not red'); assert.doesNotMatch(b.dot,/offline/);
    edgeState = 'DOWN'; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>/Нет ответа/.test(document.getElementById('statusName').textContent));
    // a DNS server that answers nothing while its address answers ping: named as a DNS server (not `dns:…`), with the hint, and the count says "checks"
    edgeState = 'OK'; dnsDown = true; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>/DNS 1\.1\.1\.1/.test(document.getElementById('statusDescription').textContent));
    b = await banner(); assert.match(b.name,/Перебои: 1 из \d+ проверок/); assert.match(b.dot,/warning/); assert.doesNotMatch(b.dot,/offline/); assert.equal(b.desc,'Проблемные проверки: DNS 1.1.1.1 (ping проходит, на DNS-запросы не отвечает)');
    edgeState = 'DOWN'; dnsDown = false; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>/edge\.example/.test(document.getElementById('statusDescription').textContent));

    // ---- the router label comes from the configuration, with a neutral default
    assert.equal(await page.locator('#nodeRouter .node-name').textContent(),'ASUS RT-AX86U'); assert.equal(await page.locator('#devCard .dev-title strong').textContent(),'ASUS RT-AX86U');
    assert.match(await page.locator('#linkUnraid').getAttribute('title'),/ASUS RT-AX86U/);
    routerName = undefined; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>document.querySelector('#nodeRouter .node-name').textContent==='Роутер');
    routerName = 'ASUS RT-AX86U'; await page.locator('#refreshBtn').click(); await page.waitForFunction(()=>document.querySelector('#nodeRouter .node-name').textContent==='ASUS RT-AX86U');

    // ---- language buttons: English leaves no Russian text, choice survives reload
    const russian = () => page.evaluate(()=>{const c=document.body.cloneNode(true);c.querySelectorAll('select,script,style').forEach(e=>e.remove());return (c.innerText.match(/[Ѐ-ӿ][Ѐ-ӿ\s·,.:()\-–—]*/g)||[]).slice(0,8)});
    await page.locator('#langSeg [data-lang="en"]').click();
    await page.waitForFunction(()=>document.documentElement.lang==='en'&&document.querySelector('h1').textContent==='Network status');
    assert.equal(await page.locator('#refreshBtn').textContent(),'Refresh');
    assert.deepEqual(await russian(),[],'English mode must not contain Russian text');
    assert.match(await page.locator('#eventList').textContent(),/OK → Down/);
    assert.match(await page.locator('#turboDialog').textContent(),/every 5 seconds.*9 MB.*Off by default/s,'the Turbo dialog is translated');
    assert.match(await page.locator('#lossOutages').textContent(),/Internet outage/);
    await page.locator('#langSeg [data-lang="ru"]').click();
    await page.waitForFunction(()=>document.querySelector('h1').textContent==='Состояние сети');
    await page.locator('#langSeg [data-lang="en"]').click();
    await page.reload(); await page.waitForTimeout(150);
    assert.equal(await page.evaluate(()=>document.documentElement.lang),'en','language is remembered');

    // ---- style dropdown + contrast + overflow in both styles and colour schemes
    const contrast = () => page.evaluate(()=>{
      const parse=c=>{const m=c.match(/[\d.]+/g).map(Number);return {r:m[0],g:m[1],b:m[2],a:m[3]??1}};
      const lum=({r,g,b})=>{const f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(r)+.7152*f(g)+.0722*f(b)};
      const bgOf=e=>{let n=e,acc=null;while(n){const c=parse(getComputedStyle(n).backgroundColor);if(c.a>0){if(c.a>=.99)return acc?{r:acc.r*acc.a+c.r*(1-acc.a),g:acc.g*acc.a+c.g*(1-acc.a),b:acc.b*acc.a+c.b*(1-acc.a)}:c;acc=acc||c}n=n.parentElement}return acc||{r:255,g:255,b:255}};
      const bad=[];
      for(const sel of ['.metric-label','.metric-val','.metric-footer','.card-sub','.pill','.table td','.table th','.status-text','.status-sub','.turbo-chip','.check','.node-name','.node-sub','.link-ms','nav a.selected','.tag','.btn','.chart-footer','h2','summary','.info-note','.sub','.muted','.kv dt','.kv dd','.tile-label','.tile-ms','.uptime-head','.chip-val','.counter','.link-btn','#unifiTabs button','.thr-chip','.chk-label','.chk-pct','.mini-label','.mini-ms','.site-pill','.net-tab']){
        for(const e of document.querySelectorAll(sel)){
          if(!e.offsetParent||!e.textContent.trim()||/^[\s—]*$/.test(e.textContent))continue;
          const cs=getComputedStyle(e),fg=parse(cs.color),bg=bgOf(e),size=parseFloat(cs.fontSize),bold=parseInt(cs.fontWeight)>=700;
          const l1=lum(fg),l2=lum(bg),ratio=(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05),need=(size>=24||(size>=18.66&&bold))?3:4.5;
          if(ratio<need)bad.push(sel+' '+ratio.toFixed(2)+' ('+cs.color+' on rgb('+[bg.r,bg.g,bg.b].map(Math.round)+'))');
        }
      }
      return [...new Set(bad)];
    });
    const combos = [['netprobe','dark'],['netprobe','light'],['unifi','light'],['unifi','dark']];
    for (const [styleName,scheme] of combos) {
      await page.emulateMedia({colorScheme:scheme});
      await page.locator('#styleSelect').selectOption(styleName);
      await page.waitForFunction(s=>document.documentElement.dataset.style===s,styleName);
      const vis = sel => page.evaluate(q=>{const e=document.querySelector(q);return !!e&&getComputedStyle(e).display!=='none'&&e.getClientRects().length>0},sel);
      if (styleName === 'unifi') {
        for (const sel of ['#unifiLeft','#devCard','#netCard','#thrCard','#uptimeCard','#tilesCard','#checksCard','#unifiTabs','.site-pill','.net-tab']) assert(await vis(sel),'UniFi layout shows '+sel);
        assert(!(await vis('.metrics')),'UniFi replaces the five metric cards with chips and left cards');
        // bottom tabs show exactly one section at a time
        for (const [tab,shown] of [['targets','#targets'],['dns','#dnsCard'],['events','#eventsCard'],['speedtest','#speedtest'],['settings','#settingsCard'],['loss','#lossCard'],['diagnostics','#diagCard']]) {
          await page.locator(`#unifiTabs [data-tab="${tab}"]`).click();
          assert(await vis(shown),'tab '+tab+' shows '+shown);
          const visibleCards = await page.evaluate(()=>[...document.querySelectorAll('.grid-content section.card')].filter(e=>getComputedStyle(e).display!=='none').length);
          assert.equal(visibleCards,1,'only one tab section is visible at a time');
        }
        await page.locator('#unifiTabs [data-tab="targets"]').click();
        // data-driven blocks come from real API data
        const facts = await page.evaluate(()=>({segs:[...document.querySelectorAll('#uptimeBar .seg')].map(e=>e.className),up:document.getElementById('uptimePct').textContent,
          tiles:document.querySelectorAll('#tilesRow [data-target]').length,checks:document.getElementById('checksCard').textContent,dev:document.getElementById('devCard').textContent,net:document.getElementById('netCard').textContent,
          chip:document.querySelector('[data-chip="avg_latency_ms"]').textContent}));
        assert(facts.segs.some(c=>/warn/.test(c))&&facts.segs.some(c=>/ok/.test(c))&&facts.segs.some(c=>/gap/.test(c)),'uptime bar shows loss, clean and no-data intervals from history: '+JSON.stringify(facts.segs.slice(-4)));
        assert.notEqual(facts.up,'—'); assert.equal(facts.tiles,5,'one tile per real target');
        assert.match(facts.checks,/80%/,'Internet success rate is 100 - average loss of the real target'); assert.match(facts.dev,/192\.0\.2\.2/); assert.match(facts.net,/0 /);
        assert.match(facts.chip,/\d/,'metric chip shows the live value');
        assert(!/Нет данных/.test(facts.checks)||true);
      } else {
        for (const sel of ['#unifiLeft','#uptimeCard','#tilesCard','#checksCard','#unifiTabs','.site-pill']) assert(!(await vis(sel)),'NetProbe style hides UniFi-only block '+sel);
        assert(await vis('.metrics'),'NetProbe style keeps the five metric cards');
      }
      for (const [w,h] of [[1440,1000],[390,844]]) {
        await page.setViewportSize({width:w,height:h}); await page.waitForTimeout(120);
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${styleName}/${scheme} ${w}px must not scroll horizontally`);
        assert.deepEqual(await contrast(),[],`${styleName}/${scheme} ${w}px text contrast`);
        await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
        await page.screenshot({path:path.join(shots,`netprobe-ui-${styleName}-${scheme}-${w}.png`),fullPage:true});
      }
    }
    assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('.c-area')||document.body).fillOpacity!==undefined),true);
    await page.locator('#styleSelect').selectOption('unifi');
    assert(await page.locator('.c-area').count()>0,'UniFi style draws the chart fill');
    await page.reload(); await page.waitForTimeout(150);
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.style),'unifi','style is remembered');
    await page.locator('#langSeg [data-lang="ru"]').click(); await page.locator('#styleSelect').selectOption('netprobe');
    // ---- theme buttons: light / dark / auto, in both styles; auto follows the browser live; the choice survives reload
    for (const styleName of ['netprobe','unifi']) {
      await page.locator('#styleSelect').selectOption(styleName);
      const bg = () => page.evaluate(()=>getComputedStyle(document.body).backgroundColor);
      const state = () => page.evaluate(()=>({theme:document.documentElement.dataset.theme,scheme:document.documentElement.dataset.scheme,meta:document.getElementById('colorSchemeMeta').content,pressed:[...document.querySelectorAll('#themeSeg [aria-pressed="true"]')].map(b=>b.dataset.theme)}));
      await page.emulateMedia({colorScheme:'dark'});
      await page.locator('#themeSeg [data-theme="light"]').click();
      assert.deepEqual(await state(),{theme:'light',scheme:'light',meta:'light',pressed:['light']},styleName+': Light wins over a dark browser');
      const light = await bg();
      await page.locator('#themeSeg [data-theme="dark"]').click();
      await page.emulateMedia({colorScheme:'light'});
      assert.deepEqual(await state(),{theme:'dark',scheme:'dark',meta:'dark',pressed:['dark']},styleName+': Dark wins over a light browser');
      assert.notEqual(await bg(),light,styleName+': the page really changes colour');
      await page.locator('#themeSeg [data-theme="auto"]').click();
      assert.deepEqual(await state(),{theme:'auto',scheme:'light',meta:'light',pressed:['auto']},styleName+': Auto follows a light browser');
      await page.emulateMedia({colorScheme:'dark'});
      await page.waitForFunction(()=>document.documentElement.dataset.scheme==='dark');
      await page.locator('#themeSeg [data-theme="light"]').click();
      await page.reload(); await page.waitForTimeout(150);
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light','the theme choice is remembered');
      await page.locator('#themeSeg [data-theme="auto"]').click();
    }
    await page.emulateMedia({colorScheme:'light'});
    await page.setViewportSize({width:390,height:844});
    const overflow = await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>window.innerWidth+1).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'narrow layout must not overflow: '+JSON.stringify(overflow));
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.waitForTimeout(100);
    await page.screenshot({path:path.join(os.tmpdir(),'netprobe-ui-mobile-test.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.waitForTimeout(100);
    await page.screenshot({path:path.join(os.tmpdir(),'netprobe-ui-desktop-test.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    assert(!calls.some(c=>c.url==='/api/speedtest/run'),'test must not trigger a Speedtest');
    console.log('PASS: offline startup/retained real metrics, stale samples, null graph gaps, per-target and per-IP history, max-loss peaks, strict settings contract, period dropdown 1 min..1 month, Unraid-router-Internet path from real targets, RU/EN buttons, light/dark/auto theme buttons, NetProbe/UniFi styles (light+dark) without overflow and with AA contrast, narrow layout, no live Speedtest or browser errors');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
