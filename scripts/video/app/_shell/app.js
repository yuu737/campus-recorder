/* 塊 B「アプリ版」の共通の殻。場面の index.html は
 *   APP.scene({step:"sokuryo", app:{…}, build(F,M){…}, seek(F,t,o){…}})
 * を書くだけ。M は右の「中の仕組み」の欄（1268×900、左上 0,0）。t は場面の頭からの秒。
 *
 * 流れ（秒）：0〜Z アプリの画面を寄りから全体へ引く／注目の枠（app.spots）／S0〜S1 画面が左上に縮み、
 * 係の札と「中の仕組み」が出る／M0 から場面ごとの仕組みの動き。撮るのは scripts/video/render_html.py。
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
function countTo(e,str,p,pre="",post=""){const s=String(str),n=Number(s.replace(/,/g,""));if(!isFinite(n)){e.textContent=pre+s+post;return}
  const dec=(s.split(".")[1]||"").length;const v=(n*p).toLocaleString("en-US",{minimumFractionDigits:dec,maximumFractionDigits:dec,useGrouping:s.includes(",")});e.textContent=pre+v+post}
// 光らせる（a から d 秒、外枠が 1 回明滅）
function pulse(e,t,a,d=.5,col="0,49,216"){const q=P(t,a,d);e.style.outline=q>0&&q<1?`6px solid rgba(${col},${Math.sin(q*Math.PI)})`:"none";e.style.outlineOffset="4px"}

// 時刻の決まり（全場面で同じ。場面の values.json の timing で上書きできる）
const T={Z:2.4, S0:3.0, S1:4.0, M0:3.7};
const DW=1560, BIG={x:177,y:120,s:1}, SMALL={x:56,y:134,s:.31};
const F={R:"../../../../",$,clamp,P,eo,eio,back,css,place,rise,pop,draw,drawDown,countTo,pulse,T};
window.APP=F;
// 矢印（仕組みの欄の中、56×40）
F.arw=(M,x,y)=>{const e=$("div","el arw",M,`<svg width="56" height="40"><path d="M2 20H46M30 6L48 20L30 34" stroke="#0031d8" stroke-width="6" fill="none"/></svg>`);place(e,x,y);return e};
// 寄り（a から d 秒で s0→s1）
F.kb=(e,t,a,d,s0=1,s1=1.1,origin="50% 50%")=>{e.style.transformOrigin=origin;e.style.transform=`scale(${s0+(s1-s0)*eio(P(t,a,d))})`};

let SC=null;
F.scene=function(sc){SC=sc;init().catch(e=>{(window.ERR=window.ERR||[]).push(String(e&&e.stack||e))})};
async function init(){
  const j=async u=>(await fetch(u,{cache:"no-store"})).json();
  F.TL=await j("timeline.json");
  F.V=await j("values.json");
  Object.assign(T,F.V.timing||{});
  const AG=(await j("../_shell/agents.json")).agents;
  const st=document.getElementById("stage");
  $("div","paper noise",st).id="base";
  // 上の帯
  const bar=$("div","",st);bar.id="bar";const steps=$("div","steps",bar);
  const cur=AG.findIndex(a=>a.id===SC.step);
  AG.forEach((a,i)=>{if(i)$("div","sep"+(i<=cur?" done":""),steps);
    $("div","st"+(i<cur?" done":i===cur?" on":""),steps,a.short+(i===cur&&a.model?`<span class="md">${F.V.pill||SC.model||a.model}</span>`:""))});
  $("div","brand",bar,`<b>campus-recorder</b>　本部棟の記録（11/8）`);
  $("div","frame",st);
  // アプリの窓
  const A=F.V.app,k=DW/A.w,DH=A.h*k;F.k=k;F.DH=DH;
  const win=$("div","",st);win.id="appwin";
  $("div","tb",win,`<span class="lab">アプリの画面</span><span class="url">${A.url}</span>`);
  const scr=$("div","scr",win);scr.style.width=DW+"px";scr.style.height=DH+"px";
  const zw=$("div","zw",scr);place(zw,0,0,DW,DH);const img=$("img","",zw);img.src=F.R+A.src;
  F.win=win;F.zw=zw;
  F.spots=(A.spots||[]).map(s=>{const e=$("div","spot",zw);place(e,s.x*k,s.y*k,s.w*k,s.h*k);
    const tg=s.tag?$("div","tag",zw,s.tag):null;if(tg){const tx=Math.min(Math.max(8,s.x*k),DW-20-s.tag.length*31),ty=s.ty!=null?s.ty*k:(s.y*k>70?s.y*k-58:(s.y+s.h)*k+10);place(tg,tx,ty)}
    return {s,e,tg}});
  // 結ぶ線
  const NS="http://www.w3.org/2000/svg";const lk=document.createElementNS(NS,"svg");lk.id="link";st.appendChild(lk);
  F.link=document.createElementNS(NS,"path");F.link.setAttribute("fill","none");F.link.setAttribute("stroke","#0031d8");F.link.setAttribute("stroke-width","5");lk.appendChild(F.link);
  F.dot=document.createElementNS(NS,"circle");F.dot.setAttribute("r","9");F.dot.setAttribute("fill","#0031d8");lk.appendChild(F.dot);
  // 係の札
  const ag=AG[cur],ov=F.V.agent||{};
  const card=$("div","",st);card.id="agent";place(card,56,452);
  $("div","k",card,ov.k||ag.source);$("div","nm",card,ov.name||ag.name);$("div","",card,`<span class="md">${ov.model||ag.long_model||ag.model}</span>`);
  const ds=$("div","ds",card);F.dsl=(ov.desc||ag.desc).map(x=>$("div","",ds,x));F.card=card;
  // 中の仕組み
  const M=$("div","",st);M.id="mech";F.M=M;
  F.mlab=$("div","lab",M,"中の仕組み");
  F.mhd=$("div","hd",M,(F.V.head||"").replace(/\[(.+?)\]/g,'<span class="a">$1</span>'));
  F.mcap=$("div","cap",M,"出典 "+(F.V.cap||""));place(F.mcap,0,848,1268);
  F.o=SC.build(F,M)||{};
  await Promise.all([...document.images].map(im=>im.decode().catch(()=>{})));
  await document.fonts.ready;
  window.READY=true;
  seekSync(+(new URLSearchParams(location.search).get("t")||0));
}
// 窓の中の点（画像の px）→ 舞台の px
function toStage(x,y,g){return [g.x+g.s*(3+x*F.k),g.y+g.s*(3+44+2+y*F.k)]}
function seekSync(t){
  const A=F.V.app,k=F.k,DH=F.DH;
  // ① 寄り → 全体
  const p=eio(P(t,0,T.Z)),z=A.z0+(1-A.z0)*p;
  const fx=A.focus[0]*k,fy=A.focus[1]*k;
  const tx=clamp(DW/2-fx*z,DW-DW*z,0),ty=clamp(DH/2-fy*z,DH-DH*z,0);
  F.zw.style.transform=`translate(${tx}px,${ty}px) scale(${z})`;
  // ② 縮む
  const q=eio(P(t,T.S0,T.S1-T.S0));
  const g={x:BIG.x+(SMALL.x-BIG.x)*q,y:BIG.y+(SMALL.y-BIG.y)*q,s:BIG.s+(SMALL.s-BIG.s)*q};
  F.win.style.transform=`translate(${g.x}px,${g.y}px) scale(${g.s})`;
  // 注目の枠：縮む前は暗転つき、縮んだ後は「main」の枠だけ残す
  F.spots.forEach(({s,e,tg})=>{
    const on=t>=s.t0&&t<s.t1, keep=s.main&&t>=T.S0;
    const a=eo(P(t,s.t0,.3)), fade=1-P(t,s.t1-.2,.2);
    const vis=keep?1:(on?a*fade:0);
    e.style.opacity=vis;e.style.boxShadow=keep?"none":`0 0 0 4000px rgba(14,16,21,${.45*a*fade})`;
    e.style.borderWidth=keep?`${6+10*q}px`:"6px";
    if(tg)tg.style.opacity=on&&!keep?a*fade:0;
  });
  // ③ 係の札と仕組み
  rise(F.card,t,T.M0+.1,.4,40);F.dsl.forEach((d,i)=>rise(d,t,T.M0+.5+i*.35,.3,12));
  rise(F.mlab,t,T.M0,.35,20);rise(F.mhd,t,T.M0+.15,.4,30);rise(F.mcap,t,(F.V.cap_at||T.M0+1.0),.3,6);
  F.M.style.opacity=1;
  // 結ぶ線（main の枠の右の真ん中 → 仕組みの見出しの左）
  const m=F.spots.find(x=>x.s.main);
  if(m&&t>=T.S1-.05){const [x0,y0]=toStage(m.s.x+m.s.w,m.s.y+m.s.h/2,SMALL),x1=592,y1=136+22;
    const d=`M${x0} ${y0}C${x0+60} ${y0},${x1-70} ${y1},${x1} ${y1}`;F.link.setAttribute("d",d);
    const L=F.link.getTotalLength(),r=eio(P(t,T.S1,.45));F.link.style.strokeDasharray=L;F.link.style.strokeDashoffset=L*(1-r);
    F.dot.setAttribute("cx",x0);F.dot.setAttribute("cy",y0);F.dot.style.opacity=r>0?1:0;F.link.style.opacity=1}
  else{F.link.style.opacity=0;F.dot.style.opacity=0}
  return SC.seek(F,t,F.o);
}
window.seek=async t=>{await seekSync(t);return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))};
})();
