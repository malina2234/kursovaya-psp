const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const sandbox={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/charts.js'),'utf8'),sandbox);
const build=(stats,period)=>JSON.parse(JSON.stringify(sandbox.window.ChatCharts.buildDays(stats,period)));

test('Календарные дни без сообщений заполняются нулями',()=>{
  const days=build({timeline_end:'2026-10-16',timeline:[{day:'2026-10-14',label:'positive',n:3}]},7);
  assert.equal(days.length,7);assert.equal(days[0].day,'2026-10-10');assert.equal(days[6].day,'2026-10-16');
  assert.deepEqual(days.map(d=>d.total),[0,0,0,0,3,0,0]);
});
test('Классы суммируются только в пределах выбранного периода',()=>{
  const days=build({timeline_end:'2026-10-16',timeline:[{day:'2026-10-16',label:'positive',n:3},{day:'2026-10-16',label:'negative',n:2},{day:'2026-10-16',label:'neutral',n:4},{day:'2026-09-01',label:'positive',n:100}]},7);
  assert.equal(days.at(-1).total,9);assert.equal(days.reduce((s,d)=>s+d.total,0),9);
});
test('Периоды корректно переходят через год и високосный февраль',()=>{
  const year=build({timeline_end:'2027-01-03'},7);assert.equal(year[0].day,'2026-12-28');
  const leap=build({timeline_end:'2028-03-02'},7);assert.ok(leap.some(d=>d.day==='2028-02-29'));
});
test('Некорректные значения не дают отрицательные числа или NaN',()=>{
  const days=build({timeline_end:'2026-10-16',timeline:[{day:'2026-10-16',label:'positive',n:-10},{day:'2026-10-16',label:'negative',n:'bad'},{day:'2026-10-16',label:'unknown',n:100}]},30);
  assert.equal(days.length,30);assert.equal(days.reduce((s,d)=>s+d.total,0),0);
});
test('Смена периода сохраняет общие дни и их значения',()=>{
  const stats={timeline_end:'2026-10-16',timeline:[{day:'2026-10-16',label:'neutral',n:12},{day:'2026-10-01',label:'positive',n:5}]};
  assert.equal(build(stats,7).reduce((s,d)=>s+d.total,0),12);
  assert.equal(build(stats,30).reduce((s,d)=>s+d.total,0),17);
});
