const Rules = require((process.env.REPO || '/home/claude/repo') + '/streamdeck-plugin/com.nascarcheckin.sd-api-request-multi.sdPlugin/rules.js');
let fail = 0;
function eq(name, got, want) { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? 'ok  ' : 'FAIL', name, ok ? '' : `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); }
const rules = [
  {value:'green', image:'G.png'}, {value:'yellow', image:'Y.png'}, {value:'red', image:'R.png'},
  {value:'hot', image:'H.png'},
];
for (const [st, want] of [['green','G.png'],['yellow','Y.png'],['red','R.png'],['hot','H.png'],['offline','OFF.png'],['GREEN','G.png'],[' Yellow ','Y.png']])
  eq('state '+st, Rules.choose(rules,'OFF.png',st,false,'').path, want);
eq('missing field -> default', Rules.choose(rules,'OFF.png',undefined,false,'').path, 'OFF.png');
eq('no default, no match', Rules.choose(rules,undefined,'zzz',false,'').path, undefined);
eq('numeric flag match', Rules.choose([{value:'1,4',image:'G.png'},{value:'2',image:'Y.png'}],'OFF',1,false,'').path, 'G.png');
eq('numeric alt 4', Rules.choose([{value:'1,4',image:'G.png'}],'OFF',4,false,'').path, 'G.png');
eq('boolean true vs "true"', Rules.choose([{value:'true',image:'ON'}],'OFF',true,false,'').path, 'ON');
eq('boolean false vs "true"', Rules.choose([{value:'true',image:'ON'}],'OFF',false,false,'').path, 'OFF');
eq('blank value = truthy (true)', Rules.choose([{value:'',image:'ON'}],'OFF',true,false,'').path, 'ON');
eq('blank value = truthy (false)', Rules.choose([{value:'',image:'ON'}],'OFF',false,false,'').path, 'OFF');
eq('blank value = truthy ("0")', Rules.choose([{value:'',image:'ON'}],'OFF','0',false,'').path, 'OFF');
eq('body search', Rules.choose([{value:'LIVE',image:'ON'}],'OFF',undefined,true,'status: live now').path, 'ON');
eq('body search miss', Rules.choose([{value:'LIVE',image:'ON'}],'OFF',undefined,true,'IDLE').path, 'OFF');
eq('rule without image skipped', Rules.choose([{value:'green',image:''},{value:'green',image:'G2'}],'OFF','green',false,'').path, 'G2');
eq('first match wins', Rules.choose([{value:'green',image:'A'},{value:'green',image:'B'}],'OFF','green',false,'').path, 'A');
eq('index reported', Rules.choose(rules,'OFF','red',false,'').index, 2);
eq('default index', Rules.choose(rules,'OFF','x',false,'').index, -1);
process.exit(fail ? 1 : 0);
