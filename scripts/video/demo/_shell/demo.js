/* 塊 B デモ版の殻。場面の index.html は
 *   DEMO.scene({build(D){…}, seek(D,t,o){…}})
 * を書くだけ。D.cam の中（1920×1080、録画の座標）に画面の連番・注目の枠・カーソルを置き、寄りは D.camAt で動かす。
 * 撮るのは scripts/video/render_html.py（window.seek(t) と window.READY を見る。flow と同じ）。
 */
"use strict";
(function(){
const $=(tag,cls,parent,html)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;if(parent)parent.appendChild(e);return e};
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const P=(t,a,d)=>clamp((t-a)/Math.max(1e-6,d));
const eo=x=>1-Math.pow(1-x,3);
const eio=x=>x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2;
function place(e,x,y,w,h){e.style.left=x+"px";e.style.top=y+"px";if(w!=null)e.style.width=w+"px";if(h!=null)e.style.height=h+"px";return e}
function rise(e,t,a,d=.35,dy=24,keep){const p=eo(P(t,a,d));e.style.opacity=p;e.style.transform=(keep||"")+` translateY(${(1-p)*dy}px)`}
// 出て、b 秒で消える
function show(e,t,a,b,d=.3,dy=20,keep){if(t<a||t>b+d){e.style.opacity=0;return}const p=Math.min(eo(P(t,a,d)),1-P(t,b,d));e.style.opacity=p;e.style.transform=(keep||"")+` translateY(${(1-Math.min(1,P(t,a,d)))*dy}px)`}
const D={$,clamp,P,eo,eio,place,rise,show};
window.DEMO=D;

// 寄り：keys=[[t,s,cx,cy],…]（cx,cy は録画の座標の注目点）。点の間は滑らかに。端が出ないように詰める
D.camAt=function(t,keys){
  let k=keys[0];let s=k[1],cx=k[2],cy=k[3];
  for(let i=0;i<keys.length-1;i++){const a=keys[i],b=keys[i+1];if(t>=a[0]&&t<=b[0]){const p=eio(P(t,a[0],b[0]-a[0]));s=a[1]+(b[1]-a[1])*p;cx=a[2]+(b[2]-a[2])*p;cy=a[3]+(b[3]-a[3])*p}}
  const L=keys[keys.length-1];if(t>L[0]){s=L[1];cx=L[2];cy=L[3]}
  let tx=960-cx*s,ty=540-cy*s;tx=clamp(tx,1920-1920*s,0);ty=clamp(ty,1080-1080*s,0);
  D.cam.style.transform=`translate(${tx}px,${ty}px) scale(${s})`;
  return {s,tx,ty};
};
// 注目の枠と暗転（rect は録画の座標）。a〜b の間だけ
D.spot=()=>$("div","spot",D.cam);
D.spotAt=function(e,t,a,b,r,dark=.5,d=.35){
  if(t<a||t>b+d){e.style.opacity=0;return 0}
  const p=Math.min(eo(P(t,a,d)),1-P(t,b,d));place(e,r.x,r.y,r.w,r.h);e.style.opacity=1;
  e.style.boxShadow=`0 0 0 6000px rgba(14,16,21,${dark*p})`;e.style.borderColor=`rgba(0,49,216,${p})`;return p};
// 名札を枠の上に（録画の座標で置き、寄りに付いて動く）
D.tagAt=function(e,t,a,b,x,y){place(e,x,y);show(e,t,a,b,.25,12)};
// クリックの波紋（録画の座標）
D.ripple=()=>$("div","ripple",D.cam);
D.rippleAt=function(e,t,a,x,y){const q=P(t,a,.55);if(q<=0||q>=1){e.style.opacity=0;return}const d=24+110*eo(q);place(e,x-d/2,y-d/2,d,d);e.style.opacity=1-q};
// 偽のカーソル：track=[[t,x,y],…,[t,x,y,"click"]]（録画の座標）
D.cursor=function(track){D._track=track};
function seekCursor(t){
  const tr=D._track,c=document.getElementById("cursor");if(!tr){c.style.opacity=0;return}
  let x=tr[0][1],y=tr[0][2];
  for(let i=0;i<tr.length-1;i++){const a=tr[i],b=tr[i+1];if(t>=a[0]&&t<=b[0]){const p=eio(P(t,a[0],b[0]-a[0]));x=a[1]+(b[1]-a[1])*p;y=a[2]+(b[2]-a[2])*p}}
  const L=tr[tr.length-1];if(t>L[0]){x=L[1];y=L[2]}
  const pressed=tr.some(k=>k[3]==="click"&&t>=k[0]-.05&&t<k[0]+.12);
  c.style.transform=`translate(${x-4}px,${y-3}px)`+(pressed?" scale(.86)":"");c.style.opacity=t<tr[0][0]?0:1;
  tr.filter(k=>k[3]==="click").forEach((k,i)=>{D._rip[i]=D._rip[i]||D.ripple();D.rippleAt(D._rip[i],t,k[0],k[1],k[2])});
}
D._rip=[];
// 画面の連番：clips=[{t0,t1,dir,fps,from,rate,ext}]（場面の秒 t0〜t1 に、連番の from 秒から rate 倍で）
D.feedAt=function(t,clips){
  let c=clips[0];for(const k of clips)if(t>=k.t0)c=k;
  const st=c.from+Math.max(0,Math.min(t,c.t1)-c.t0)*(c.rate??1);
  const i=Math.max(1,Math.round(st*c.fps)+(c.base??1));
  const src=`${D.R}${c.dir}/f${String(i).padStart(5,"0")}.${c.ext||"jpg"}`;
  if(src!==D._src){D._src=src;D.feed.src=src;return D.feed.decode().catch(()=>{})}
};
// 左上の係の札
D.who=function(name,model,step){const e=$("div","hud who",D.hud,`<div class="dot"></div><div class="nm">${name}</div>`+(model?`<div class="md">${model}</div>`:"")+(step?`<div class="st">${step}</div>`:""));return e};
D.fuda=(html)=>$("div","hud fuda",D.hud,html);
D.tag=(html)=>$("div","hud tag",D.cam,html);
D.caption=(text)=>$("div","hud cap",D.hud,text);
D.flash=(t,a,d=.25)=>{const q=P(t,a,d);document.getElementById("flash").style.opacity=q>0&&q<1?(1-q)*.8:0};

let SC=null;
D.scene=function(sc){SC=sc;init().catch(e=>{(window.ERR=window.ERR||[]).push(String(e));console.error(e)})};
async function init(){
  const j=async u=>(await fetch(u,{cache:"no-store"})).json();
  D.TL=await j("timeline.json");
  D.V=await j("values.json");
  D.R="../../../../";
  const st=document.getElementById("stage");
  D.cam=$("div","",st);D.cam.id="cam";
  D.feed=$("img","feed",D.cam);
  D.hud=$("div","",st);D.hud.style.cssText="position:absolute;inset:0;z-index:60";
  $("div","",st).id="flash";
  D.cam.insertAdjacentHTML("beforeend",`<svg id="cursor" viewBox="0 0 40 56"><path d="M3 3L3 44L14 34L22 52L30 48L22 31L37 31Z" fill="#fff" stroke="#1a1a1c" stroke-width="3" stroke-linejoin="round"/></svg>`);
  D.o=SC.build(D)||{};
  await Promise.all([...document.images].map(im=>im.decode().catch(()=>{})));
  await document.fonts.ready;
  await seekSync(0);
  window.READY=true;
}
function seekSync(t){const w=SC.seek(D,t,D.o);seekCursor(t);return w}
window.seek=async t=>{await seekSync(t);return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))};
})();

/* 設定だけで動く場面（values.json の scene を読む）。index.html は DEMO.auto() を呼ぶだけ。
 * scene = {clips:[{t0,t1,dir,fps,from,rate}], cam:[[t,s,cx,cy]], who:[{t0,t1,name,model,step}],
 *          fuda:[{t0,t1,text,small}], spots:[{t0,t1,x,y,w,h,dark,tag,tx,ty}], cursor:[[t,x,y,"click"?]],
 *          caption:[{t0,t1,text}], flash:[t], term:[{t0,t1,x,y,w,h,bar,lines:[{t,text,cls}]}]}
 * 座標は録画の座標（1920×1080）。term と who・fuda・caption は寄りの影響を受けない。 */
DEMO.auto=function(){DEMO.scene({
  build(D){
    const S=D.V.scene,o={};
    o.who=(S.who||[]).map(w=>({w,e:D.who(w.name,w.model,w.step)}));
    o.fuda=(S.fuda||[]).map(f=>({f,e:D.fuda(f.text+(f.small?`<small>${f.small}</small>`:""))}));
    o.spots=(S.spots||[]).map(s=>({s,e:D.spot(),g:s.tag?D.tag(s.tag):null}));
    o.cap=(S.caption||[]).map(c=>({c,e:D.caption(c.text)}));
    o.term=(S.term||[]).map(T=>{const e=D.$("div","hud term",D.hud);D.place(e,T.x,T.y,T.w,T.h);
      const bar=D.$("div","bar",e,T.bar||"");const ls=T.lines.map(L=>D.$("div",L.cls||"",e,L.text.replace(/&/g,"&amp;").replace(/</g,"&lt;")));return {T,e,ls}});
    if(S.cursor)D.cursor(S.cursor);
    o.clicks=(S.clicks||[]).map(c=>({c,e:D.ripple()}));
    return o;
  },
  seek(D,t,o){
    const S=D.V.scene;
    if(S.cam)D.camAt(t,S.cam);
    o.who.forEach(({w,e})=>D.show(e,t,w.t0,w.t1,.3,-16));
    o.fuda.forEach(({f,e})=>D.show(e,t,f.t0,f.t1,.3,24,"translateX(-50%)"));
    o.spots.forEach(({s,e,g})=>{D.spotAt(e,t,s.t0,s.t1,s,s.dark??.5);if(g)D.tagAt(g,t,s.t0+.15,s.t1,s.tx??s.x,s.ty??(s.y-62))});
    o.cap.forEach(({c,e})=>D.show(e,t,c.t0,c.t1,.3,0));
    o.term.forEach(({T,e,ls})=>{D.show(e,t,T.t0,T.t1,.3,30);ls.forEach((l,i)=>{l.style.visibility=t>=T.lines[i].t?"visible":"hidden"})});
    o.clicks.forEach(({c,e})=>D.rippleAt(e,t,c[0],c[1],c[2]));
    let f=0;(S.flash||[]).forEach(a=>{const q=D.P(t,a,.3);if(q>0&&q<1)f=Math.max(f,(1-q)*.85)});document.getElementById("flash").style.opacity=f;
    if(S.clips)return D.feedAt(t,S.clips);
  }
})};
