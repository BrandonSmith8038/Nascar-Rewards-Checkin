const fs = require('fs'), vm = require('vm');
const dir = (process.env.REPO || '/home/claude/repo') + '/streamdeck-plugin/com.nascarcheckin.sd-api-request-multi.sdPlugin/';
const events = [];
const handlers = {};
const ctx = {
  console: { log: () => {} }, setInterval, clearInterval, fetch, Date, JSON, Promise, Boolean, Number, String, Array, Error,
  window: { clearInterval },
  debugLog: () => {},
  Utils: {
    getProperty: (o, p) => p.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o),
    loadImage: (path, cb) => cb('data:' + path),
  },
  $SD: { on: (n, f) => { handlers[n] = f; }, api: {
    setImage: (c, img) => events.push([Date.now(), 'image', img]),
    showAlert: () => events.push([Date.now(), 'ALERT']), showOk: () => {} } },
};
ctx.window.setInterval = setInterval;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(dir + 'rules.js', 'utf8'), ctx);
vm.runInContext(fs.readFileSync(dir + 'app.js', 'utf8').replace(/^const PLUGIN_UUID/m, 'var PLUGIN_UUID'), ctx);
const settings = {
  response_parse: true, response_parse_field: 'state', poll_status: true, poll_status_url: process.argv[2],
  poll_status_frequency: 1,
  rules: [ {value:'green',image:'GREEN'}, {value:'yellow',image:'YELLOW'}, {value:'red',image:'RED'}, {value:'hot',image:'HOT'} ],
  image_default: 'OFFLINE',
};
vm.runInContext('connected({})', ctx);
handlers['com.nascarcheckin.sd-api-request-multi.willAppear']({ context: 'ctx1', payload: { settings } });
setTimeout(() => {
  const seq = events.map(e => e[2] || e[1]).map(x => x.replace('data:', ''));
  console.log('image sequence:', seq.join(' -> '));
  process.exit(0);
}, Number(process.argv[3]) * 1000);
