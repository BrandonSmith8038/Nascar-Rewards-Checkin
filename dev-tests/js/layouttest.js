const { JSDOM } = require('jsdom'); const fs = require('fs'); const base = process.argv[2]; const withLogin = process.argv[3] === 'login';
(async () => {
  let cookie = '';
  if (withLogin) { const lr = await fetch(base + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'nc-dashboard' }, body: JSON.stringify({ password: 'pw' }) }); cookie = lr.headers.get('set-cookie').split(';')[0]; }
  const dom = new JSDOM(fs.readFileSync((process.env.REPO || '/home/claude/repo') + '/dashboard.html', 'utf8'), { runScripts: 'dangerously', pretendToBeVisual: true, url: base + '/',
    beforeParse(w) { w.fetch = (u, o = {}) => fetch(new URL(u, base), { ...o, headers: { ...(o.headers || {}), ...(cookie ? { Cookie: cookie } : {}) } }); w.Element.prototype.scrollIntoView = () => {}; } });
  const w = dom.window, d = w.document, $ = (s) => d.querySelector(s), $$ = (s) => [...d.querySelectorAll(s)];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let fails = 0; const check = (n, c, x) => { console.log((c ? 'ok   ' : 'FAIL ') + n, c ? '' : (x || '')); if (!c) fails++; };
  await wait(1800);
  console.log('--- mode:', withLogin ? 'logged in (password set)' : 'no password');
  check('old header gone: no brand/sub/h1', !$('.brand') && !$('#sub') && !$('h1'));
  check('status bar sits after the banner', $('.hero-wrap').nextElementSibling.classList.contains('statusbar'));
  check('pill + updated live inside one centered row', !!$('.statusbar .statusrow #summary') && !!$('.statusbar .statusrow #updated'));
  check('updated text filled', /updated/.test($('#updated').textContent), $('#updated').textContent);
  const chips = $$('#racesBody .sr'); check('race chips use O\'Reilly + class Oreilly', chips.some((c) => c.textContent === "O'Reilly" && c.classList.contains('Oreilly')), chips.map((c) => c.className + ':' + c.textContent));
  check('no Xfinity text anywhere on the page', !/xfinity/i.test(d.body.textContent), d.body.textContent.match(/.{20}xfinity.{20}/i));
  check('series dots list O\'Reilly', /O'Reilly/.test($('#deckBody .series').textContent));
  const css = [...d.querySelectorAll('style')].map((s) => s.textContent).join('');
  check('series colors: Cup red, O\'Reilly green, Truck yellow', /--cup: #ff5d55/.test(css) && /--oreilly: #3fb950/.test(css) && /--truck: #f2c94c/.test(css));
  check('wider layout (1880px)', /\.hero-wrap \{ max-width: 1880px/.test(css) && /main \{ max-width: 1880px/.test(css));
  check('races/notifications span 2 columns on wide screens', /min-width: 1500px\) \{ #cardRaces, #cardNotes \{ grid-column: span 2/.test(css));
  // tests card
  check('Tests card present', !!$('#cardTests'));
  const tiles = $$('#testsBody .test'); check('11 test tiles', tiles.length === 11, tiles.length);
  check('missed-day preview tile present', !!$('#run_missed_day_test'));
  check('tile tags: 3 send, 8 read-only', $$('#testsBody .tag2.send').length === 3 && $$('#testsBody .tag2.read').length === 8, [$$('#testsBody .tag2.send').length, $$('#testsBody .tag2.read').length]);
  check('2 tiles marked contacts NASCAR', $$('#testsBody .tag2.net').length === 2);
  const sendBtns = $$('#testsBody .test.sends .btn'); check(withLogin ? 'send tests enabled when logged in' : 'send tests disabled without login/password', sendBtns.every((b) => b.disabled === !withLogin), sendBtns.map((b) => b.disabled));
  $('#run_races').click(); await wait(1500);
  const out = $('#testsBody .test:nth-child(8) .tout'); 
  check('running a read-only test shows output', $$('#testsBody .tout').length >= 1 && /upcoming|No upcoming/.test($('#testsBody').textContent), $('#testsBody').textContent.slice(0, 200));
  $('#runAllBtn').click(); await wait(8000);
  const outs = $$('#testsBody .tout').length; check('run-all produced output for the read-only checks (excl. icon test)', outs >= 6, outs);
  check('run-all did not run the missed-day preview', !$('#run_missed_day_test').closest('.test').querySelector('.tout'));
  check('run-all did not run send tests', !/Sent\./.test(d.querySelector('#testsBody .test.sends .tout') ? d.querySelector('#testsBody .test.sends .tout').textContent : ''));
  if (withLogin) { $('#run_notification').click(); await wait(1500); check('send test shows a result', /FAILED|Sent/.test($('#testsBody').textContent)); }
  console.log(fails ? '\nFAILURES: ' + fails : '\nALL OK'); process.exit(fails ? 1 : 0);
})();
