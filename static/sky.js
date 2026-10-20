'use strict';
(() => {
  const canvas=document.getElementById('star-sky'),ctx=canvas.getContext('2d');
  if(!ctx)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const palette=['#fff5ff','#f2d0ed','#d8c5ff','#ffffff'];
  let width=0,height=0,stars=[],frame=0,last=0;
  function resize(){
    width=innerWidth;height=innerHeight;const ratio=Math.min(devicePixelRatio||1,1.5);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    ctx.setTransform(ratio,0,0,ratio,0,0);
    stars=Array.from({length:Math.min(200,Math.max(65,Math.round(width*height/6500)))},(_,i)=>({
      x:Math.random(),y:Math.random(),size:.5+Math.random()*1.25,
      phase:Math.random()*Math.PI*2,speed:.3+Math.random()*.7,
      color:palette[i%palette.length],shine:i%17===0
    }));
    draw(performance.now());
  }
  function draw(time){
    ctx.clearRect(0,0,width,height);const seconds=reduced.matches?0:time/1000;
    for(const star of stars){
      const x=(star.x*width+seconds*star.speed*1.5)%width;
      const y=((star.y*height-seconds*star.speed*.65)%height+height)%height;
      ctx.globalAlpha=reduced.matches?.7:.4+.55*(Math.sin(seconds*.55+star.phase)+1)/2;
      ctx.fillStyle=star.color;ctx.beginPath();ctx.arc(x,y,star.size,0,Math.PI*2);ctx.fill();
      if(star.shine){
        ctx.globalAlpha*=.45;ctx.strokeStyle=star.color;ctx.lineWidth=.65;
        const radius=star.size*3.4;ctx.beginPath();ctx.moveTo(x-radius,y);ctx.lineTo(x+radius,y);
        ctx.moveTo(x,y-radius);ctx.lineTo(x,y+radius);ctx.stroke();
      }
    }
    ctx.globalAlpha=1;
  }
  function animate(time){
    if(time-last>=33){draw(time);last=time;}
    frame=requestAnimationFrame(animate);
  }
  function resume(){
    cancelAnimationFrame(frame);frame=0;
    if(!document.hidden&&!reduced.matches)frame=requestAnimationFrame(animate);
    else draw(performance.now());
  }
  addEventListener('resize',resize,{passive:true});
  document.addEventListener('visibilitychange',resume);
  reduced.addEventListener('change',resume);
  resize();resume();
})();
