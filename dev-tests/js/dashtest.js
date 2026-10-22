const { JSDOM } = require('jsdom');
const fs = require('fs');
const base = process.argv[2];
(async () => {
  const html = fs.readFileSync((process.env.REPO || '/home/claude/repo') + '/dashboard.html', 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: base + '/',
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, base), o);        // node's fetch against the real server
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window, d = w.document, $ = (s) => d.querySelector(s), $$ = (s) => [...d.querySelectorAll(s)];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let fails = 0; const check = (n, c, x) => { console.log((c ? 'ok   ' : 'FAIL ') + n, c ? '' : (x || '')); if (!c) fails++; };
  await wait(1500);
  check('summary pill rendered', /caught up|to do/.test($('#summary').textContent), $('#summary').textContent);
  check('deck state shown', /GREEN|YELLOW|RED|HOT|OFFLINE/.test($('#deckBody').textContent), $('#deckBody').textContent.slice(0, 80));
  check('5 state chips', $$('#deckBody .chip').length === 5);
  check('exactly one active chip', $$('#deckBody .chip.active').length === 1);
  const rows = $$('#racesBody .row'); check('race rows', rows.length === 3, rows.length);
  const names = rows.map((r) => r.querySelector('.title').childNodes[0].textContent);
  check('race names escaped as text', names.includes('Started OReilly Race'), names);
  const btns = $$('#racesBody .row .btn');
  check('un-started race button disabled', btns.some((b) => b.disabled && /Not started/.test(b.textContent)));
  const startedRow = rows.find((r) => /Started OReilly Race/.test(r.textContent)); const markBtn = startedRow.querySelector('.btn:not(:disabled)');
  check('started race has active button', !!markBtn);
  check('daily row for configured game', $$('#dailyBody .row').length === 1 && /Trivia/.test($('#dailyBody').textContent), $('#dailyBody').textContent);
  const dd = (await (await fetch(base + '/api/dashboard')).json()).daily[0];
  const dbtn = $('#dailyBody .row .btn');
  if (dd.state === 'today') check('daily today: muted Mark done (.todo), enabled', dbtn.classList.contains('todo') && !dbtn.disabled && /Mark done/.test(dbtn.textContent), dbtn.className);
  else if (dd.state === 'past') check('daily missed: disabled red Missed (.missed)', dbtn.classList.contains('missed') && dbtn.disabled && /Missed/.test(dbtn.textContent), dbtn.className);
  else check('daily upcoming: plain disabled Not today', !dbtn.classList.contains('missed') && !dbtn.classList.contains('todo') && dbtn.disabled && /Not today/.test(dbtn.textContent), dbtn.className);
  check('notifications list', $$('#notesBody .note').length >= 1 || /Nothing sent/.test($('#notesBody').textContent));
  const pending0 = $('#summary').textContent;
  markBtn.click(); await wait(1500);
  check('clicking marks done + disables', $$('#racesBody .btn.done').length === 1, $$('#racesBody .btn').map((b) => b.textContent));
  check('summary updated after click', $('#summary').textContent !== pending0 || /caught up/.test($('#summary').textContent), $('#summary').textContent);
  startedRow.querySelector('.linkbtn') ? 0 : 0; $$('#racesBody .linkbtn')[0].click(); await wait(1200);
  check('undo works', $$('#racesBody .btn.done').length === 0);
  // settings
  const groups = $$('#settingsBody details.group'); check('settings groups', groups.length >= 8, groups.length);
  const edit = !$('#settingsBody .banner'); console.log('editable form?', edit);
  check('secret fields are password inputs', $$('#settingsBody input[type=password]').length >= 2);
  if (edit) {
    check('save button starts disabled', $('#saveBtn').disabled === true);
    const nud = $$('#settingsBody input[type=number]').find((i) => i.closest('.field').textContent.includes('Follow-ups'));
    nud.value = '2'; nud.dispatchEvent(new w.Event('input', { bubbles: true }));
    check('editing enables save + shows unsaved', $('#saveBtn').disabled === false && $('#dirtyMsg').hidden === false);
    nud.value = '99'; $('#saveBtn').click(); await wait(1200);
    const errs = $$('#settingsBody .err').filter((e) => !e.hidden);
    check('server validation error shown inline', errs.length === 1 && /at most/.test(errs[0].textContent), errs.map((e) => e.textContent));
  }
  console.log(fails ? '\nFAILURES: ' + fails : '\nALL OK'); process.exit(fails ? 1 : 0);
})();
