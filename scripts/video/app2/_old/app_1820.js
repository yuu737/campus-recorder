/* 塊 B「アプリ版」2 の共通の殻。場面の index.html は
 *   APP.scene({step:"satsuei", build(F,S){…}, seek(F,t,o){…}})
 * を書くだけ。S は舞台（1920×1080、左上 0,0）。t は場面の頭からの秒。使い方は ../README.md。
 *
 * 殻が自動で動かす物：
 *  - 上の帯（今の係だけ青）と、帯の下の時間の線（values.clock：記録の再生の時刻と費用）
 *  - アプリの画面（values.app）：0〜0.6 秒 めくれで入る（全体）→ 注目の枠と暗転 → shots の切り出しへ寄る
 *  - F.claudeVoice / F.tag / F.ripple で作った物（t0 の秒に出る・打ち出す）
 * 撮るのは scripts/video/render_html.py（window.READY・window.seek）。
 */
"use strict";
(function(){
const $=(tag,cls,parent,html)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;if(parent)parent.appendChild(e);return e};
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const P=(t,a,d)=>d<=0?(t>=a?1:0):clamp((t-a)/d);
const eo=x=>1-Math.pow(1-x,3);
const eio=x=>x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2;
const back=x=>{const c=1.6;return 1+(c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2)};
const lerp=(a,b,p)=>a+(b-a)*p;
function css(e,o){for(const k in o)e.style[k]=o[k]}
function place(e,x,y,w,h){css(e,{left:x+"px",top:y+"px"});if(w!=null)e.style.width=w+"px";if(h!=null)e.style.height=h+"px";return e}
function rise(e,t,a,d=.38,dy=30){const p=eo(P(t,a,d));e.style.opacity=p;e.style.transform=`translateY(${(1-p)*dy}px)`}
function pop(e,t,a,d=.32){const x=P(t,a,d);e.style.opacity=clamp(x*3);e.style.transform=`scale(${x<=0?1.25:1+.25*(1-back(x))})`}
function draw(e,t,a,d){const p=eio(P(t,a,d));e.style.clipPath=`inset(0 ${(1-p)*100}% 0 0)`}
function countTo(e,str,p,pre="",post=""){const s=String(str),n=Number(s.replace(/,/g,""));if(!isFinite(n)){e.textContent=pre+s+post;return}
  const dec=(s.split(".")[1]||"").length;const v=(n*p).toLocaleString("en-US",{minimumFractionDigits:dec,maximumFractionDigits:dec,useGrouping:s.includes(",")});e.textContent=pre+v+post}
function pulse(e,t,a,d=.5,col="0,49,216"){const q=P(t,a,d);e.style.outline=q>0&&q<1?`6px solid rgba(${col},${Math.sin(q*Math.PI)})`:"none";e.style.outlineOffset="4px"}
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
// 記録の時刻 "2026-11-08T04:15:16" → 秒（日付をまたぐ時も比べられるように日も入れる）
const sec=s=>{const m=String(s).match(/(\d+)-(\d+)-(\d+)T(\d+):(\d+):?(\d+)?/);return m?((+m[3])*86400+(+m[4])*3600+(+m[5])*60+(+(m[6]||0))):NaN};
const hhmm=x=>{x=((x%86400)+86400)%86400;const h=Math.floor(x/3600),m=Math.floor(x%3600/60);return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`};

// 舞台の決まり
const WHOLE={x:60,y:206,w:1800,h:820};   // 文脈（アプリの全体）を置く箱
const AREA={x:60,y:206,w:1190,h:820};    // 切り出しを置く箱（左、画面の 62 %）。右の欄は x 1290〜1862
const F={R:"../../../../",$,clamp,P,eo,eio,back,lerp,css,place,rise,pop,draw,countTo,pulse,esc,sec,hhmm,WHOLE,AREA,RIGHT:{x:1290,w:572}};
window.APP=F;
const DRIVE=[];   // 殻が毎コマ動かす物（声・札・波紋）

/* ── Claude の声 ─────────────────────────────────────────────
 * F.claudeVoice({who:"撮影係", model:"Claude Haiku", took:"1:43", text:"…", src:"…", x, y, w, t0, ms:70})
 *  who・model・took（所要）・text は記録にある物だけ。text は 3 行まで（はみ出すと window.WARN に出る）。
 *  t0 の 0.35 秒前に板が出て、t0 から 1 字 ms ミリ秒で打ち出す。打ち終わるとカーソルが点滅。返り値 .end＝打ち終わりの秒。 */
F.claudeVoice=function(o){
  const w=o.w||F.RIGHT.w,el=$("div","cv",o.parent||F.S);place(el,o.x!=null?o.x:F.RIGHT.x,o.y!=null?o.y:206,w);
  $("div","kind",el,o.kind||"係が書いた文（そのまま）");
  $("div","who",el,`<span class="nm">${esc(o.who)}</span><span class="dot">·</span><span class="md">${esc(o.model)}</span>`+(o.took?`<span class="dot">·</span><span class="tk">${esc(o.took)}</span>`:""));
  const tx=$("div","tx",el);if(o.size){tx.style.fontSize=o.size+"px";tx.style.maxHeight=(o.size*1.5*3)+"px"}$("span","gh",tx,esc(o.text)+'<span class="cur"></span>');const ty=$("div","ty",tx);
  if(o.src)$("div","src",el,"出典 "+esc(o.src));
  if(o.tail===false)el.classList.add("notail");
  const ch=Array.from(o.text),ms=o.ms||70,t0=o.t0||0;
  const v={el,tx,t0,end:t0+ch.length*ms/1000,
    seek(t){rise(el,t,t0-.35,.3,24);const n=clamp(Math.floor((t-t0)*1000/ms),0,ch.length);
      const done=n>=ch.length,blink=done?(Math.floor((t-v.end)*2.2)%2===0):true;
      ty.innerHTML=esc(ch.slice(0,n).join(""))+`<span class="cur" style="opacity:${t>=t0-.1&&blink?1:0}"></span>`}};
  DRIVE.push(v);return v};

/* ── 「判断は Claude／測るのはコード」の札 ─────────────────────
 * F.tag("claude"|"code"|"human", "面の判定は Claude が決めた", {x,y | at:[画像のx,y], dx,dy, t0, parent})
 *  claude＝青地白字、code＝黒地白字、human＝白地黒字。at を渡すとアプリの画面の点に付いて一緒に動く。 */
F.tag=function(kind,text,o={}){
  const el=$("div","who-tag "+kind,o.parent||F.S,esc(text));
  if(o.x!=null)place(el,o.x,o.y);
  const v={el,t0:o.t0||0,seek(t){pop(el,t,v.t0);if(o.at){const [x,y]=F.toStage(o.at[0],o.at[1]);place(el,x+(o.dx||0),y+(o.dy||0))}}};
  DRIVE.push(v);return v};


/* ── 小窓の起点 → 青い線 → 中の仕組み（focusWindow） ─────────────
 * F.link({from:[画像x,y,w,h], to:<F.claudeVoice の返り値 | 要素 | [舞台x,y]>, t0, d:.45})
 *  アプリの画面の本物の要素（係の一言・写真・寸法線）を青い枠で囲み、その右の真ん中から to の左（声の板なら吹き出しの先）へ線を引く。
 *  values.app.anchor=[x,y,w,h] と anchor_t を書けば、殻が最初の Claude の声へ自動で引く（場面は書かなくてよい）。 */
F.link=function(o){
  const NS="http://www.w3.org/2000/svg";
  if(!F.svg){F.svg=document.createElementNS(NS,"svg");F.svg.setAttribute("width","1920");F.svg.setAttribute("height","1080");
    css(F.svg,{position:"absolute",left:0,top:0,zIndex:49,pointerEvents:"none",overflow:"visible"});F.S.appendChild(F.svg)}
  const path=document.createElementNS(NS,"path");path.setAttribute("fill","none");path.setAttribute("stroke","#0031d8");path.setAttribute("stroke-width","5");F.svg.appendChild(path);
  const dot=document.createElementNS(NS,"circle");dot.setAttribute("r","9");dot.setAttribute("fill","#0031d8");F.svg.appendChild(dot);
  const box=$("div","",F.S);css(box,{position:"absolute",border:"5px solid var(--acc)",boxSizing:"border-box",zIndex:49,background:"rgba(0,49,216,.06)",pointerEvents:"none"});
  const t0=o.t0||0,d=o.d||.45;
  const v={seek(t){const [a,b,w,h]=typeof o.from==="function"?o.from(t):o.from,[x0,y0]=F.toStage(a,b),[x1,y1]=F.toStage(a+w,b+h);
    const pb=eo(P(t,t0,.25));place(box,x0-6,y0-6,x1-x0+12,y1-y0+12);box.style.opacity=o.box===false?0:pb;
    let tx,ty;const T=o.to&&o.to.el?o.to.el:o.to;
    if(Array.isArray(T)){[tx,ty]=T}else{const r=T.getBoundingClientRect();tx=r.left-(o.to.el?22:6);ty=r.top+(o.to.el?48:r.height/2)}
    const sx=x1+6,sy=(y0+y1)/2,dx=Math.max(60,(tx-sx)*.45);
    path.setAttribute("d",`M${sx} ${sy}C${sx+dx} ${sy},${tx-dx} ${ty},${tx} ${ty}`);
    const L=path.getTotalLength(),r=eio(P(t,t0+.15,d));path.style.strokeDasharray=L;path.style.strokeDashoffset=L*(1-r);
    path.style.opacity=r>0?1:0;dot.setAttribute("cx",sx);dot.setAttribute("cy",sy);dot.style.opacity=pb}};
  DRIVE.push(v);return v};
F.focusWindow=F.link;

/* 波紋（クリック）：F.ripple(画像のx, 画像のy, t0) */
F.ripple=function(ix,iy,t0,opt={}){
  const els=[0,1].map(()=>$("div","ripple",F.S));els.forEach(e=>e.style.zIndex=47);
  const v={seek(t){const [x,y]=opt.stage?[ix,iy]:F.toStage(ix,iy);els.forEach((e,i)=>{const p=P(t,t0+i*.16,.6),r=lerp(14,90,eo(p));
      place(e,x-r,y-r,2*r,2*r);e.style.opacity=p>0&&p<1?(1-p):0})}};
  DRIVE.push(v);return v};

/* 板：F.board("見出し", x, y, w, h, 中身の html) */
F.board=(title,x,y,w,h,html="",parent)=>{const e=$("div","board2",parent||F.S,(title?`<div class="bt">${title}</div>`:"")+html);place(e,x,y,w,h);return e};

/* アプリの画面の点（画像の px）→ 舞台の px（今の寄りで） */
F.miniSpot=t=>{const m=F.V.app.mini;if(!m.spots)return m.spot;let r=m.spots[0].r;m.spots.forEach(x=>{if(t>=x.t)r=x.r});return r};
F.toStage=(x,y)=>{const c=F.cam;return [c.rx+(x-c.cx)*c.s,c.ry+(y-c.cy)*c.s]};

let SC=null;
F.scene=function(sc){SC=sc;init().catch(e=>{(window.ERR=window.ERR||[]).push(String(e&&e.stack||e))})};
async function init(){
  const j=async u=>(await fetch(u,{cache:"no-store"})).json();
  F.TL=await j("timeline.json");
  F.V=await j("values.json");
  const AG=(await j("../_shell/agents.json")).agents;
  const st=document.getElementById("stage");F.S=st;
  $("div","paper noise",st).id="base";
  // 上の帯
  const bar=$("div","",st);bar.id="bar";const steps=$("div","steps",bar);
  const cur=AG.findIndex(a=>a.id===SC.step);
  AG.forEach((a,i)=>{if(i)$("div","sep"+(i<=cur?" done":""),steps);
    $("div","st"+(i<cur?" done":i===cur?" on":""),steps,a.short+(i===cur&&a.model&&a.model!=="人"?`<span class="md">${F.V.pill||a.model}</span>`:""))});
  $("div","brand",bar,`<b>campus-recorder</b>　本部棟の記録（11/8）`);
  $("div","frame",st);
  await buildClock(j);
  buildApp();
  F.o=SC.build(F,st)||{};
  const A0=F.V.app;if(A0&&A0.mini&&A0.mini.link_to)F.link({from:A0.mini.spot||(t=>F.miniSpot(t)),to:A0.mini.link_to,t0:A0.mini.link_t!=null?A0.mini.link_t:2.3,box:false});
  else if(A0&&A0.anchor){const vo=DRIVE.find(x=>x.tx);if(vo||A0.anchor_to)F.link({from:A0.anchor,to:A0.anchor_to||vo,t0:A0.anchor_t!=null?A0.anchor_t:(vo?vo.t0-.6:3)})}
  await Promise.all([...document.images].map(im=>im.decode().catch(()=>{})));
  await document.fonts.ready;
  if(F.clockFix)F.clockFix();
  // 声の板が 3 行を超えていないか
  DRIVE.forEach(v=>{if(v.tx&&v.tx.scrollHeight>v.tx.clientHeight+2)(window.WARN=window.WARN||[]).push("声が 3 行を超えた："+v.tx.textContent.slice(0,20))});
  window.READY=true;
  await seekAll(+(new URLSearchParams(location.search).get("t")||0));
}

/* ── 時間の線 ─────────────────────────────────────────────
 * values.clock = {state:"data/reports/honbu/pipeline_state_20261108_final.json", from:"<記録の時刻>", at:"<記録の時刻>", d0:.2, d:1.2}
 * 線は記録の最初の工程の始まり〜最後の工程の終わり。費用は at までに終わった工程の cost_usd の和。 */
async function buildClock(j){
  const C=F.V.clock;if(!C)return;
  const S=await j(F.R+(C.state||"data/reports/honbu/pipeline_state_20261108_final.json"));
  const R=S.results||[];const t0=Math.min(...R.map(r=>sec(r.started_at))),t1=Math.max(...R.map(r=>sec(r.ended_at)));
  const box=$("div","",F.S);box.id="clock";
  $("div","lb",box,"記録の再生");
  const tr=$("div","tr",box);
  R.forEach(r=>{const e=$("div","tk"+(/^claude/.test(r.model||"")?" ai":""),tr);e.style.left=((sec(r.started_at)-t0)/(t1-t0)*100)+"%"});
  const fl=$("div","fl",tr),hd=$("div","hd",tr),rt=$("div","rt",box);
  const cost=at=>R.filter(r=>sec(r.ended_at)<=at).reduce((a,r)=>a+(r.cost_usd||0),0);
  const a=sec(C.at),f=C.from?sec(C.from):a,max=S.max_usd;
  F.clock={t0,t1,cost,seek(t){const p=eio(P(t,C.d0!=null?C.d0:.2,C.d||1.2)),x=lerp(f,a,p),q=(x-t0)/(t1-t0)*100;
    fl.style.width=q+"%";hd.style.left=q+"%";
    rt.innerHTML=`<b>${hhmm(x)}</b> <span>費用</span> <b>${cost(x).toFixed(2)}</b> <span>/ 上限 ${max} USD</span>　<span>${hhmm(t0)}→${hhmm(t1)}</span>`}
    // つまみが右端に来ても時刻の文字と重ならないよう、線の右端を文字の左 40 px までにする（文字の幅は最長の形で固定）
    };
  rt.innerHTML=`<b>00:00</b> <span>費用</span> <b>${(cost(t1)).toFixed(2)}</b> <span>/ 上限 ${max} USD</span>　<span>${hhmm(t0)}→${hhmm(t1)}</span>`;
  F.clockFix=()=>{tr.style.right=(rt.offsetWidth+30+40)+"px"};
}

/* ── アプリの画面 ─────────────────────────────────────────────
 * values.app = {w,h, src | seq:{pattern:"out/…/f{n}.jpg", pad:5, keys:[[秒,コマ],…]}, url, spot_at:.55,
 *   shots:[{t:1.0, d:1.4, crop:[x,y,w,h], cut:false, area:{x,y,w,h}}, …]}
 * shots の 1 つ目の crop に spot_at から枠と暗転が付き、t から d 秒で寄る。cut:true は寄らずに切り替え（0.3 秒で出る）。 */
function buildApp(){
  const A=F.V.app;if(!A)return;
  const wrap=$("div","el",F.S);place(wrap,0,0,1920,1080);wrap.style.zIndex=42;
  const cap=$("div","",wrap,`<b>アプリの画面</b>${esc(A.url||"")}`);cap.id="appcap";
  const box=$("div","",wrap);box.id="appbox";const inn=$("div","in",box);
  const img=$("img","",inn);img.width=A.w;img.height=A.h;css(img,{width:A.w+"px",height:A.h+"px"});
  const lay=$("div","lay",inn);css(lay,{width:A.w+"px",height:A.h+"px"});
  const edge=$("div","",wrap);edge.id="appedge";
  const sh=(A.shots||[]);const dim=(sh.length||A.mini)?$("div","dim",inn):null;
  if(dim){const c=A.mini?(A.mini.spot||A.mini.spots[0].r):sh[0].crop;place(dim,c[0],c[1],c[2],c[3])}
  F.app={wrap,cap,box,inn,img,lay,edge,dim};F.lay=lay;
  const s0=Math.min(WHOLE.w/A.w,WHOLE.h/A.h);
  const whole={c:[0,0,A.w,A.h],r:{x:WHOLE.x+(WHOLE.w-A.w*s0)/2,y:WHOLE.y,w:A.w*s0}};
  F.states=[whole,...sh.map(s=>{const ar=s.area||AREA,c=s.crop,k=Math.min(ar.w/c[2],ar.h/c[3]);
    return {c,r:{x:ar.x+(s.align==="center"?(ar.w-c[2]*k)/2:0),y:ar.y,w:c[2]*k}}})];
  F.srcAt=t=>{if(!A.seq)return A.src;const K=A.seq.keys;let n=K[0][1];
    for(let i=0;i<K.length;i++){if(t>=K[i][0]){n=K[i][1];if(i+1<K.length&&t<K[i+1][0]){const p=(t-K[i][0])/(K[i+1][0]-K[i][0]);n=Math.round(lerp(K[i][1],K[i+1][1],p))}}}
    return A.seq.pattern.replace("{n}",String(n).padStart(A.seq.pad||5,"0"))};
}
function camAt(t){
  const A=F.V.app;
  if(A.mini){ // 全画面の小窓：寄り(z0)→全体(0〜1.2 秒)→左上の小窓へ縮む(1.2〜2.4 秒)
    const m=A.mini,W=F.states[0],z=lerp(m.z0||1.5,1,eio(P(t,0,m.pull||1.2))),cw=A.w/z,ch=A.h/z;
    const cx=clamp(m.focus[0]-cw/2,0,A.w-cw),cy=clamp(m.focus[1]-ch/2,0,A.h-ch);
    const q=eio(P(t,m.t_shrink!=null?m.t_shrink:1.2,m.d_shrink||1.2)),mw=m.w||520;
    const rw=lerp(W.r.w,mw,q),rx=lerp(W.r.x,m.x!=null?m.x:60,q),ry=lerp(W.r.y,m.y!=null?m.y:206,q),s=rw/cw;
    return {cx,cy,s,rx,ry,rw,rh:ch*s,z:0,fade:1,mini:q}}
  const sh=A.shots||[];let st=F.states[0],z=0,fade=1;
  for(let i=0;i<sh.length;i++){const s=sh[i];if(t<s.t)break;
    const a=F.states[i],b=F.states[i+1];
    if(s.cut){st=b;fade=eo(P(t,s.t,.3));z=1;continue}
    const p=eio(P(t,s.t,s.d||1.4));
    const c=a.c.map((v,k)=>lerp(v,b.c[k],p));
    st={c,r:{x:lerp(a.r.x,b.r.x,p),y:lerp(a.r.y,b.r.y,p),w:lerp(a.r.w,b.r.w,p)}};z=i===0?p:1}
  const s=st.r.w/st.c[2];
  return {cx:st.c[0],cy:st.c[1],s,rx:st.r.x,ry:st.r.y,rw:st.r.w,rh:st.c[3]*s,z,fade}}
async function seekApp(t){
  const A=F.V.app;if(!A)return;const G=F.app,c=camAt(t);F.cam=c;
  const src=F.R+F.srcAt(t);if(G.img.getAttribute("src")!==src){G.img.src=src;try{await G.img.decode()}catch(e){}}
  place(G.box,c.rx,c.ry,c.rw,c.rh);place(G.edge,c.rx,c.ry,c.rw,c.rh);
  G.inn.style.transform=`translate(${-c.cx*c.s}px,${-c.cy*c.s}px) scale(${c.s})`;
  const bw=lerp(3,6,c.z);if(c.mini!=null){G.edge.style.border="3px solid var(--ink)";G.edge.style.boxShadow="var(--shadow)"}else G.edge.style.border=`${bw}px solid ${c.z>.5?"var(--acc)":"var(--ink)"}`;G.edge.style.boxShadow="var(--shadow)";
  place(G.cap,c.rx,c.ry-34);
  // めくれで入る（0〜0.6 秒）
  const f=A.mini?1:eo(P(t,0,.6));G.wrap.style.transformOrigin=`${c.rx+c.rw/2}px ${c.ry}px`;
  G.wrap.style.transform=`perspective(2400px) rotateX(${(1-f)*-78}deg)`;G.wrap.style.opacity=clamp(f*2.5)*c.fade;
  // 注目の枠と暗転（1 つ目の寄りの前）
  if(G.dim&&A.mini){const r=F.miniSpot(t);place(G.dim,r[0],r[1],r[2],r[3]);const a=eo(P(t,A.mini.spot_at!=null?A.mini.spot_at:.5,.3));
    G.dim.style.opacity=a;G.dim.style.borderWidth=(5/c.s)+"px";G.dim.style.boxShadow=`0 0 0 6000px rgba(14,16,21,${a*lerp(.5,.3,c.mini)})`}
  else if(G.dim){const sh=A.shots[0],a=eo(P(t,A.spot_at!=null?A.spot_at:.55,.3)),out=c.z;
    G.dim.style.opacity=a*(1-out);G.dim.style.borderWidth=(6/c.s)+"px";G.dim.style.boxShadow=`0 0 0 6000px rgba(14,16,21,${.5*a*(1-out)})`}
}
async function seekAll(t){
  await seekApp(t);
  if(F.clock)F.clock.seek(t);
  try{DRIVE.forEach(v=>v.seek(t));if(SC.seek)SC.seek(F,t,F.o)}catch(e){(window.ERR=window.ERR||[]).push(String(e&&e.stack||e));
    let d=document.getElementById("err");if(!d){d=$("div","",F.S);d.id="err";css(d,{position:"absolute",left:"40px",bottom:"40px",zIndex:99,color:"#c00",font:"20px monospace",background:"#fff"})}d.textContent="seek の誤り："+e}
}
window.seek=async t=>{await seekAll(t);return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))};
})();
