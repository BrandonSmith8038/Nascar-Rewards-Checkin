const { JSDOM } = require('jsdom'); const fs = require('fs'); const base = process.argv[2];
(async () => {
  const dom = new JSDOM(fs.readFileSync((process.env.REPO || '/home/claude/repo') + '/dashboard.html','utf8'), { runScripts:'dangerously', pretendToBeVisual:true, url: base+'/',
    beforeParse(w){ w.fetch=(u,o)=>fetch(new URL(u,base),o); w.Element.prototype.scrollIntoView=()=>{}; } });
  const d=dom.window.document; const wait=(ms)=>new Promise(r=>setTimeout(r,ms)); await wait(1200);
  console.log('login overlay visible when password set:', d.getElementById('loginOverlay').hidden===false);
  console.log('page body hides data until login:', /Loading|…/.test(d.getElementById('racesBody').textContent));
  process.exit(0);
})();
