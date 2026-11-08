/* 塊 B「流れ」の共通の殻。場面の index.html は、この後に
 *   FLOW.scene({agent:"sokuryo", build(F){…}, seek(F,t){…}})
 * を書くだけ。F.page が右の本文の板（1412×1024、左上が (0,0)）。時刻 t は場面の頭からの秒。
 * 撮るのは scripts/video/render_html.py（window.seek(t) と window.READY を見る）。
 */
"use strict";
(function(){
const $=(tag,cls,parent,html)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;if(parent)parent.appendChild(e);return e};
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const P=(t,a,d)=>clamp((t-a)/d);
const eo=x=>1-Math.pow(1-x,3);
const eio=x=>x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2;
const back=x=>{const c=1.6;return 1+(c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2)};
function css(e,o){for(const k in o)e.style[k]=o[k]}
function place(e,x,y,w,h){css(e,{left:x+"px",top:y+"px"});if(w!=null)e.style.width=w+"px";if(h!=null)e.style.height=h+"px";return e}
function rise(e,t,a,d=.38,dy=30){const p=eo(P(t,a,d));e.style.opacity=p;e.style.transform=`translateY(${(1-p)*dy}px)`}
function pop(e,t,a,d=.32){const x=P(t,a,d);e.style.opacity=clamp(x*3);e.style.transform=`scale(${x<=0?1.25:1+.25*(1-back(x))})`}
function draw(e,t,a,d){const p=eio(P(t,a,d));e.style.clipPath=`inset(0 ${(1-p)*100}% 0 0)`}
function drawDown(e,t,a,d){const p=eio(P(t,a,d));e.style.clipPath=`inset(0 0 ${(1-p)*100}% 0)`}
// 寄り（Ken Burns）：a から d 秒で s0→s1
function kb(img,t,a,d,s0=1,s1=1.1,origin="50% 50%"){img.style.transformOrigin=origin;img.style.transform=`scale(${s0+(s1-s0)*eio(P(t,a,d))})`}
// 注目の枠と暗転：rect={x,y,w,h}（親の中の px）。p の割合で周りを暗くする
function spot(parent){const e=$("div","spot",parent);e.style.opacity=0;return e}
function spotAt(e,t,a,rect,d=.4,dark=.55){const p=eo(P(t,a,d));place(e,rect.x,rect.y,rect.w,rect.h);e.style.opacity=p>0?1:0;
  e.style.boxShadow=`0 0 0 4000px rgba(14,16,21,${dark*p})`;e.style.borderColor=`rgba(0,49,216,${p})`}
// 数え上げ（小数の桁は to の文字列に合わせる）
function countTo(e,str,p,pre="",post=""){const s=String(str),n=Number(s.replace(/,/g,""));if(!isFinite(n)){e.textContent=pre+s+post;return}
  const dec=(s.split(".")[1]||"").length;const v=(n*p).toLocaleString("en-US",{minimumFractionDigits:dec,maximumFractionDigits:dec,useGrouping:s.includes(",")});e.textContent=pre+v+post}

const F={$,clamp,P,eo,eio,back,css,place,rise,pop,draw,drawDown,kb,spot,spotAt,countTo};
window.FLOW=F;

// 偽のカーソル：track=[[t,x,y],[t,x,y,"click"],…]（画面全体の px）。点の間は滑らかに動く
F.cursor=function(track){F._track=track;const c=document.getElementById("cursor");c.style.display="block"};
function seekCursor(t){
  const tr=F._track;if(!tr)return;const c=document.getElementById("cursor"),r=document.getElementById("ripple");
  let x=tr[0][1],y=tr[0][2];
  for(let i=0;i<tr.length-1;i++){const a=tr[i],b=tr[i+1];if(t>=a[0]&&t<=b[0]){const p=eio((t-a[0])/Math.max(.001,b[0]-a[0]));x=a[1]+(b[1]-a[1])*p;y=a[2]+(b[2]-a[2])*p}}
  if(t>tr[tr.length-1][0]){x=tr[tr.length-1][1];y=tr[tr.length-1][2]}
  c.style.transform=`translate(${x}px,${y}px)`;c.style.opacity=t<tr[0][0]?0:1;
  const clicks=tr.filter(p=>p[3]==="click");let shown=false;
  for(const k of clicks){const q=P(t,k[0],.5);if(q>0&&q<1){shown=true;const s=20+70*eo(q);css(r,{left:(k[1]-s/2)+"px",top:(k[2]-s/2)+"px",width:s+"px",height:s+"px",opacity:1-q})}}
  if(!shown)r.style.opacity=0;
  const pressed=clicks.some(k=>t>=k[0]-.05&&t<k[0]+.12);c.style.transform+=pressed?" scale(.88)":"";
};
// 見出し（前置き＋行）。lines の中の「[…]」は差し色
F.headline=function(pre,lines,x=80,y=60,size=110){
  const o={};if(pre){o.pre=$("div","el pre",F.page,pre);place(o.pre,x,y)}
  const y0=pre?y+64:y;
  o.lines=lines.map((L,i)=>{const e=$("div","el head",F.page,L.replace(/\[(.+?)\]/g,'<span class="a">$1</span>'));e.style.fontSize=size+"px";place(e,x,y0+i*size*1.14);return e});
  return o;
};
// 出典の行（右下、字幕の大きさ）
F.caption=function(text){const e=$("div","el src cap",F.page,text);return e};
// 文 n（1 始まり）の時刻
F.S=n=>F.TL.sentences[n-1]||{start:F.TL.flip,dur:2};

let SC=null;
F.scene=function(sc){SC=sc;init()};
async function init(){
  const j=async u=>(await fetch(u,{cache:"no-store"})).json();
  F.TL=await j("timeline.json");
  try{F.V=await j("values.json")}catch(e){F.V={}}
  F.AG=(await j("../_shell/agents.json")).agents;
  const st=document.getElementById("stage")||$("div","",document.body);st.id="stage";
  const base=$("div","paper noise",st);base.id="base";
  // 柱
  const pl=$("div","",st);pl.id="pillar";$("div","kubun",pl,"動いている係");
  const list=$("div","list",pl);F.lit=$("div","lit",list);
  F.rows=F.AG.map(a=>{const r=$("div","row",list,`<div class="nm">${a.name}</div><div class="md">${a.model}</div>`);r.dataset.id=a.id;return r});
  $("div","frame",st);
  // 本文の板
  F.page=$("div","paper noise",st);F.page.id="page";F.shade=$("div","shade",F.page);
  $("div","",st,`<svg id="cursor" viewBox="0 0 40 56"><path d="M3 3L3 44L14 34L22 52L30 48L22 31L37 31Z" fill="#fff" stroke="#1a1a1c" stroke-width="3" stroke-linejoin="round"/></svg>`);
  $("div","",st).id="ripple";
  F.o=SC.build(F)||{};
  await Promise.all([...document.images].map(im=>im.decode().catch(()=>{})));
  await document.fonts.ready;
  window.READY=true;
  seekSync(+(new URLSearchParams(location.search).get("t")||0));
}
function seekSync(t){
  // 柱：前の係から今の係へ点が移る（0.3〜0.8 秒）
  const ids=F.AG.map(a=>a.id),cur=ids.indexOf(SC.agent),prev=SC.prev!=null?ids.indexOf(SC.prev):Math.max(0,cur-1);
  const p=eio(P(t,.3,.5)),row=prev+(cur-prev)*p;
  F.lit.style.top=(row*104)+"px";
  F.rows.forEach((r,i)=>r.classList.toggle("on",i===(p>=.5?cur:prev)));
  // めくれで入る：左の端（柱の境）を軸に、奥から手前へ
  const f=eio(P(t,0,F.TL.flip));
  F.page.style.transform=f<1?`rotateY(${-92*(1-f)}deg)`:"";F.shade.style.opacity=(1-f)*.35;
  const w=SC.seek(F,t,F.o);
  seekCursor(t);
  return w;
}
// 場面の seek が Promise を返したら（画像の差し替えなど）、それを待ってから撮る
window.seek=async t=>{await seekSync(t);return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))};
})();
