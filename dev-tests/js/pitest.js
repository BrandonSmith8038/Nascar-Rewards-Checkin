const { JSDOM } = require('jsdom');
const fs = require('fs'), path = require('path');
const dir = (process.env.REPO || '/home/claude/repo') + '/streamdeck-plugin/com.nascarcheckin.sd-api-request-multi.sdPlugin/propertyinspector/';
const html = fs.readFileSync(dir + 'index.html', 'utf8').replace(/<script src="[^"]+"><\/script>/g, '');
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const saved = [];
const handlers = {};
w.console = console;
// stubs for Stream Deck PI globals
w.addDynamicStyles = () => {};
w.$SD = { on: (n, f) => { handlers[n] = f; }, uuid: 'uuid1', connection: { readyState: 1, send() {} },
  api: { getSettings() {}, setSettings: (u, s) => saved.push(JSON.parse(JSON.stringify(s))) }, applicationInfo: { colors: {} } };
w.Utils = { debounce: (f) => f, isUndefined: v => v === undefined };
w.eval(fs.readFileSync(dir + 'index_pi.js', 'utf8'));
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));

// 1. settings arrive with 2 saved rules
handlers['didReceiveSettings']({ payload: { settings: {
  response_parse: true, response_parse_field: 'state', poll_status: true, poll_status_url: 'http://x/status',
  rules: [{ value: 'green', image: 'C:/img/g.png' }, { value: 'yellow', image: '' }], image_default: 'C:/img/off.png' } } });
const $ = s => w.document.querySelector(s), $$ = s => [...w.document.querySelectorAll(s)];
console.log('rules rendered:', $$('#rules_container .rule').length, '| rule1 value:', $$('input[data-kind=value]')[0].value,
  '| rule1 file label:', $$('.rule .sdpi-file-info')[0].textContent, '| rule2 label:', $$('.rule .sdpi-file-info')[1].textContent,
  '| default label:', $('.sdpi-file-info[for=image_default]').textContent);
console.log('settings panel visible:', $('#response_parse_settings').style.display === '', '| poll panel visible:', $('#poll_status_settings').style.display === '');

// 2. click "Add NASCAR track-state rules"
$('#add_nascar_btn').click();
console.log('after NASCAR button, values:', $$('input[data-kind=value]').map(i => i.value).join(','));

// 3. type into a rule value
const v = $$('input[data-kind=value]')[2]; v.value = 'red,3'; v.dispatchEvent(new w.Event('input', { bubbles: true }));
// 4. choose a file for rule 3 (simulate Stream Deck's fakepath value)
const f = $('#rule_image_2');
Object.defineProperty(f, 'value', { value: 'C:\\fakepath\\C%3A%2FUsers%2Fme%2Fred.png', configurable: true });
f.dispatchEvent(new w.Event('change', { bubbles: true }));
console.log('rule3 label after pick:', $$('.rule .sdpi-file-info')[2].textContent);

// 5. move rule 4 up, remove rule 1
$$('button[data-kind=up]')[2].click();   // third "up" button belongs to rule index 3
$$('button[data-kind=remove]')[0].click();
const last = saved[saved.length - 1];
console.log('saved rules:', JSON.stringify(last.rules));
console.log('saved field:', last.response_parse_field, '| default image kept:', last.image_default);

// 6. re-sending identical settings must not rebuild/clobber
const before = $$('#rules_container .rule').length;
handlers['didReceiveSettings']({ payload: { settings: JSON.parse(JSON.stringify(last)) } });
console.log('rules after echo:', $$('#rules_container .rule').length, '(was', before + ')');
