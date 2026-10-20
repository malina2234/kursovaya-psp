'use strict';
(() => {
  const classes=['positive','neutral','negative'];
  const titles={positive:'Позитивные',neutral:'Нейтральные',negative:'Негативные'};
  const colors={positive:'#9562c0',neutral:'#b0a8c9',negative:'#d17d9f'};
  const ns='http://www.w3.org/2000/svg';
  const element=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const svgElement=(tag,attrs={},text)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));if(text!==undefined)e.textContent=text;return e;};
  const count=value=>Number.isFinite(Number(value))?Math.max(0,Math.floor(Number(value))):0;
  const shortDay=day=>`${day.slice(8,10)}.${day.slice(5,7)}`;
  const percentage=(n,total)=>total?`${(n/total*100).toLocaleString('ru-RU',{maximumFractionDigits:1})}%`:'0%';

  function buildDays(stats,period=7){
    period=[7,14,30].includes(Number(period))?Number(period):7;
    let end=new Date(`${stats.timeline_end}T00:00:00Z`);
    if(!Number.isFinite(end.getTime()))end=new Date(new Date().toISOString().slice(0,10)+'T00:00:00Z');
    const data=new Map();
    for(const row of stats.timeline||[]){
      if(!classes.includes(row.label))continue;
      if(!data.has(row.day))data.set(row.day,{positive:0,neutral:0,negative:0});
      data.get(row.day)[row.label]+=count(row.n);
    }
    return Array.from({length:period},(_,i)=>{
      const date=new Date(end);date.setUTCDate(date.getUTCDate()-period+1+i);
      const day=date.toISOString().slice(0,10),values=data.get(day)||{positive:0,neutral:0,negative:0};
      return {day,...values,total:classes.reduce((sum,key)=>sum+values[key],0)};
    });
  }

  function renderDonut(container,counts,options={}){
    container.replaceChildren();const values=Object.fromEntries(classes.map(k=>[k,count(counts?.[k])])),total=classes.reduce((s,k)=>s+values[k],0);
    const noun=options.noun||'сообщений',unit=options.unit||'сообщ.';
    const wrap=element('div','donut-layout'),svg=svgElement('svg',{viewBox:'0 0 240 240',class:'donut-svg',role:'img','aria-label':`Распределение тональностей. Всего ${noun}: ${total}.`});
    svg.append(svgElement('title',{},options.title||'Тональность сообщений за всё время'));
    const radius=82,length=2*Math.PI*radius;
    svg.append(svgElement('circle',{cx:120,cy:120,r:radius,fill:'none',stroke:'#f1eaf5','stroke-width':26}));
    let offset=0;
    for(const key of classes){
      if(!values[key])continue;
      const size=values[key]/total*length;
      const segment=svgElement('circle',{cx:120,cy:120,r:radius,fill:'none',stroke:colors[key],'stroke-width':26,'stroke-dasharray':`${size} ${length}`,'stroke-dashoffset':-offset,transform:'rotate(-90 120 120)'});
      segment.append(svgElement('title',{},`${titles[key]}: ${values[key]} (${percentage(values[key],total)})`));svg.append(segment);offset+=size;
    }
    svg.append(svgElement('text',{x:120,y:118,'text-anchor':'middle',class:'donut-total'},total.toLocaleString('ru-RU')),
      svgElement('text',{x:120,y:142,'text-anchor':'middle',class:'donut-caption'},noun));
    const legend=element('div','chart-legend');
    for(const key of classes){
      const row=element('div','chart-legend-row'),name=element('span','chart-legend-name'),dot=element('i','chart-dot');dot.style.background=colors[key];name.append(dot,element('span','',titles[key]));
      const number=element('span','chart-legend-value');number.append(element('b','',percentage(values[key],total)),element('small','',`${values[key].toLocaleString('ru-RU')} ${unit}`));row.append(name,number);legend.append(row);
    }
    wrap.append(svg,legend);container.append(wrap);
    if(!total)container.append(element('p','chart-empty-note',options.emptyText||'Диаграмма заполнится после первых сообщений.'));
  }

  function renderActivity(container,stats,period=7){
    const days=buildDays(stats,period),selectedDay=container.dataset.selectedDay;
    container.replaceChildren();
    const width=Math.max(280,container.clientWidth||640),height=270,left=38,right=18,top=20,bottom=37;
    const plotWidth=width-left-right,plotHeight=height-top-bottom,max=Math.max(1,...days.map(d=>d.total));
    const base=10**Math.floor(Math.log10(max/4)),step=[1,2,5,10].map(n=>n*base).find(n=>n>=max/4),tick=Math.max(1,step),ceiling=tick*4;
    const x=i=>left+i*plotWidth/(days.length-1),y=n=>top+plotHeight*(1-n/ceiling);
    const svg=svgElement('svg',{viewBox:`0 0 ${width} ${height}`,class:'activity-svg',role:'img',tabindex:0,'aria-label':`Число сообщений за ${days.length} дней. Для выбора дня используйте стрелки влево и вправо.`});
    svg.append(svgElement('title',{},`Активность с ${days[0].day} по ${days.at(-1).day}`));
    for(let i=0;i<=4;i++){
      const value=i*tick;svg.append(svgElement('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),stroke:'#eee6f3','stroke-dasharray':i?'3 5':'none'}),
        svgElement('text',{x:left-9,y:y(value)+4,'text-anchor':'end',class:'chart-axis'},value.toLocaleString('ru-RU')));
    }
    const points=days.map((d,i)=>`${x(i)},${y(d.total)}`),gradientId='activity-gradient';
    const defs=svgElement('defs'),gradient=svgElement('linearGradient',{id:gradientId,x1:0,y1:0,x2:0,y2:1});
    gradient.append(svgElement('stop',{offset:'0%','stop-color':'#a779ca','stop-opacity':.25}),svgElement('stop',{offset:'100%','stop-color':'#a779ca','stop-opacity':.02}));defs.append(gradient);svg.append(defs);
    svg.append(svgElement('path',{d:`M${x(0)},${y(0)} L${points.join(' L')} L${x(days.length-1)},${y(0)} Z`,fill:`url(#${gradientId})`}),
      svgElement('polyline',{points:points.join(' '),fill:'none',stroke:'#9562c0','stroke-width':2.5,'stroke-linejoin':'round','stroke-linecap':'round'}));
    days.forEach((d,i)=>svg.append(svgElement('circle',{cx:x(i),cy:y(d.total),r:days.length>14?2:3,fill:'#9562c0'})));
    const labelCount=Math.max(2,Math.min(7,Math.floor(plotWidth/65))),indices=new Set(Array.from({length:labelCount},(_,i)=>Math.round(i*(days.length-1)/(labelCount-1))));
    indices.forEach(i=>svg.append(svgElement('text',{x:x(i),y:height-10,'text-anchor':i===0?'start':i===days.length-1?'end':'middle',class:'chart-axis'},shortDay(days[i].day))));
    const guide=svgElement('line',{stroke:'#c4a0dc','stroke-dasharray':'4 4',y1:top,y2:y(0)}),point=svgElement('circle',{r:5,fill:'#9562c0',stroke:'#fff','stroke-width':2});svg.append(guide,point);
    const detail=element('div','activity-detail');detail.setAttribute('aria-live','polite');let selected=-1;
    function select(index){
      index=Math.max(0,Math.min(days.length-1,index));if(index===selected)return;selected=index;
      const day=days[index];container.dataset.selectedDay=day.day;guide.setAttribute('x1',x(index));guide.setAttribute('x2',x(index));point.setAttribute('cx',x(index));point.setAttribute('cy',y(day.total));
      detail.replaceChildren(element('b','',`${shortDay(day.day)} · ${day.total} сообщ.`));
      const values=element('div','activity-breakdown');classes.forEach(key=>{const item=element('span'),dot=element('i','chart-dot');dot.style.background=colors[key];item.append(dot,element('span','',`${titles[key]}: ${day[key]}`));values.append(item);});detail.append(values);
    }
    function choose(event){const rect=svg.getBoundingClientRect(),local=(event.clientX-rect.left)*width/rect.width;select(Math.round((local-left)/plotWidth*(days.length-1)));}
    svg.addEventListener('pointermove',event=>{if(event.pointerType==='mouse')choose(event);});
    svg.addEventListener('pointerdown',choose);
    svg.addEventListener('keydown',event=>{const actions={ArrowLeft:()=>selected-1,ArrowRight:()=>selected+1,Home:()=>0,End:()=>days.length-1};if(actions[event.key]){event.preventDefault();select(actions[event.key]());}});
    const previous=days.findIndex(d=>d.day===selectedDay);
    container.append(svg,detail);select(previous>=0?previous:days.length-1);
    if(!days.some(d=>d.total))container.append(element('p','chart-empty-note','За этот период сообщений пока нет.'));
    return days;
  }
  window.ChatCharts={buildDays,renderDonut,renderActivity};
})();
