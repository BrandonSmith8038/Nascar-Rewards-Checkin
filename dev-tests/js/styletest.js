const { JSDOM } = require('jsdom'); const fs = require('fs'); const base = process.argv[2];
(async () => {
  const lr = await fetch(base + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'nc-dashboard' }, body: JSON.stringify({ password: 'pw' }) });
  const cookie = lr.headers.get('set-cookie').split(';')[0];
  const mk = async (withCookie) => {
    const dom = new JSDOM(fs.readFileSync((process.env.REPO || '/home/claude/repo') + '/dashboard.html', 'utf8'), { runScripts: 'dangerously', pretendToBeVisual: true, url: base + '/',
      beforeParse(w) { w.fetch = (u, o = {}) => fetch(new URL(u, base), { ...o, headers: { ...(o.headers || {}), ...(withCookie ? { Cookie: cookie } : {}) } }); w.Element.prototype.scrollIntoView = () => {}; } });
    await new Promise((r) => setTimeout(r, 1500)); return dom.window;
  };
  let fails = 0; const check = (n, c, x) => { console.log((c ? 'ok   ' : 'FAIL ') + n, c ? '' : (x || '')); if (!c) fails++; };
  const w = await mk(true); const d = w.document, $ = (s) => d.querySelector(s), $$ = (s) => [...d.querySelectorAll(s)];
  check('banner img present', !!$('.hero img') && $('.hero img').getAttribute('src') === '/banner.jpg');
  check('hero wrap visible', $('#heroWrap').hidden === false);
  check('six fact rows, label/value structure', $$('#deckBody .fact').length === 6 && $$('#deckBody .fact .k').length === 6 && $$('#deckBody .fact .v').length === 6, $$('#deckBody .fact').length);
  check('series row keeps all three on one line (inline group)', $('#deckBody .series') && $$('#deckBody .series .s').length === 3);
  check('card accent variable set from state', /--accent-c/.test($('#cardDeck').getAttribute('style') || ''), $('#cardDeck').getAttribute('style'));
  // show / hide toggle on secret fields
  const secretField = $$('#settingsBody .field').find((f) => f.querySelector('.lbl').textContent.startsWith('ntfy topic'));
  const inp = secretField.querySelector('input'), btn = secretField.querySelector('button');
  check('secret starts hidden, button says Show', inp.type === 'password' && btn.textContent === 'Show');
  btn.click(); check('after click: revealed, button says Hide', inp.type === 'text' && btn.textContent === 'Hide', inp.type + '/' + btn.textContent);
  btn.click(); check('click again: hidden, button says Show', inp.type === 'password' && btn.textContent === 'Show');
  // icon manager
  check('icon manager has 5 cells', $$('#iconMgr .icell').length === 5);
  check('upload buttons enabled when logged in', $$('#iconMgr .mini-btn').every((b) => !b.disabled));
  check('default tiles use the built-in svg flag', $$('#iconMgr .ithumb svg').length === 5);
  // custom icon for the current (offline) state -> tile shows an <img>
  const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
  const up = await fetch(base + '/api/icon/offline', { method: 'POST', headers: { 'X-Requested-With': 'nc-dashboard', Cookie: cookie, 'Content-Type': 'image/png' }, body: PNG });
  check('uploaded a custom offline icon', up.ok);
  const w2 = await mk(true); const t = w2.document.querySelector('#deckBody .tile img');
  check('tile now shows the custom image', !!t && /^\/icons\/offline\?v=\d+$/.test(t.getAttribute('src')), t && t.getAttribute('src'));
  check('manager shows Replace + Reset for that icon', /Replace/.test(w2.document.querySelector('#iconMgr').textContent) && /Reset/.test(w2.document.querySelector('#iconMgr').textContent));
  await fetch(base + '/api/icon/offline/reset', { method: 'POST', headers: { 'X-Requested-With': 'nc-dashboard', Cookie: cookie, 'Content-Type': 'application/json' }, body: '{}' });
  const w3 = await mk(false); // password set, not logged in -> login overlay, no data
  check('logged-out: login overlay shown', w3.document.getElementById('loginOverlay').hidden === false);
  console.log(fails ? '\nFAILURES: ' + fails : '\nALL OK'); process.exit(fails ? 1 : 0);
})();
