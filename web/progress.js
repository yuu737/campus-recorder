// 待機画面（web/progress.html）。11/8、F8。
// 絵と数はすべて生成物から：/api/artifacts（今ある生成物の一覧）・/api/steps（工程 → 係の名前・モデル・一言）・
// /api/pipeline（最新の記録の要約と pipeline_state.json）・/api/progress（report.json の段と数）。
// 無い物は場面ごと飛ばす。偽の進捗は作らない。大学・棟・面・写真の名前はここに書かない（データから読む）。
"use strict";
(() => {
  const Q = new URLSearchParams(location.search);
  const B = (Q.get("building") || "").trim();
  const LIVE = Q.get("live") === "1" || Q.has("live") && Q.get("live") !== "0";
  const SPEED = Math.max(0.1, parseFloat(Q.get("speed")) || 1);
  const AT = Q.has("at") ? Math.min(1, Math.max(0, parseFloat(Q.get("at")) || 0)) : null;   // 絵を撮る時：その場面のこの時点で止める
  const ONLY = (Q.get("scene") || "").trim();
  const LOOP = Q.get("loop") === "1";
  document.documentElement.dataset.theme = Q.get("theme") === "dark" ? "dark" : "light";
  const ROOT = location.pathname.replace(/\/web\/[^/]*$/, "/");
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const u = p => (p && p.startsWith("/") ? ROOT + p.slice(1) : p);
  async function getJSON(path) {
    try { const r = await fetch(u(path), {cache: "no-store"}); return r.ok ? await r.json() : null; } catch (e) { return null; }
  }
  const fmtHMS = s => { if (s == null || !isFinite(s)) return "—"; s = Math.max(0, Math.round(s)); return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  const fmtMS = s => { if (s == null) return ""; s = Math.round(s); return s >= 3600 ? fmtHMS(s) : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const firstSentence = (t, kw) => {
    const ss = String(t || "").split("。").map(x => x.trim()).filter(Boolean);
    return (kw && ss.find(x => x.includes(kw))) || ss[0] || "";
  };

  // --- 状態 ---
  let STEPS = {}, ORDER = [], ART = null, PIPE = null, PROG = null, START_COUNTS = null;
  const prevNums = {};

  // 報告の 6 段（recorder/report.py の STAGES と同じ鍵と題）
  const STAGES = [["photos", "写真"], ["elev", "立面図"], ["evidence", "証拠"], ["model", "模型"], ["walk", "歩ける 3D"], ["rest", "まだ無い物"]];
  const ST_JA = {done: "済", partial: "途中", todo: "まだ"};

  // --- 場面（順。step は説明と「どの係の工程か」に使う。ok が偽なら飛ばす） ---
  const SCENES = [
    {key: "a", step: "satsuei", title: "写真", film: "photos", dur: 6, ok: A => A.photos.length > 0, build: A => buildPhotos(A, false)},
    {key: "b", step: "shiwake", title: "仕分け", film: "photos", dur: 6, ok: A => A.photos.some(p => p.face), build: A => buildPhotos(A, true)},
    {key: "c", step: "sokuryo", title: "正面化", film: "elev", dur: 8, ok: A => A.compare.some(c => c.rectified && c.masks_ok && c.photo_src), build: buildRect,
     desc: "写真を真正面に直す。消失点と焦点距離から"},
    {key: "d", step: "sekkei", title: "設計", film: "elev", dur: 7, ok: A => !!(A.report && A.report.counts && A.report.counts.measured_kinds), build: buildDesign},
    {key: "e", step: "seizu", title: "立面図", film: "elev", dur: 7, ok: A => A.elev.length > 0, build: buildElev},
    {key: "f", step: "sokuryo", kw: "製図板", title: "製図板", film: "evidence", dur: 8, ok: A => A.coverage.some(c => c.masks_ok), build: buildBoard},
    {key: "g", step: "kenshu", title: "検収", film: "model", dur: 8, ok: A => A.kenshu.some(k => k.photo_src && k.view), build: buildKenshu},
    // 進行役（事件の時だけ）：生きている記録で、進行役が考えている時か決定がある時（F20）
    {key: "s", step: "shinko", title: "進行役", film: null, dur: 8,
     ok: () => LIVE && !!(PIPE && PIPE.state) && (PIPE.state.current === "shinko" || (PIPE.state.shinko || []).length > 0), build: buildShinko},
    {key: "h", step: "mejirushi", title: "証拠", film: "evidence", dur: 6, ok: A => !!(A.evidence.photo || A.evidence.three), build: buildEvidence},
    {key: "i", step: "web", title: "歩く画面", film: "walk", dur: 8, ok: A => !!A.walk, build: buildWalk},
  ];

  // ---------------------------------------------------------------- 場面の中身
  function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function stageSize() { const s = $("#stage"); return [s.clientWidth || 800, s.clientHeight || 450]; }

  // 灰色の箱。polys は絵の座標の多角形 [{label, pts:[[x,y],…]}]、W×H は絵の大きさ（その座標の単位）。
  // 札の文字は絵の高さの 2 %（ただし画面で 9 px より小さくはしない）。縁は画面で 2 px
  const SVGNS = "http://www.w3.org/2000/svg";
  function maskGroup(polys, W, H, screenH) {
    const g = document.createElementNS(SVGNS, "g"); g.setAttribute("class", "masks");
    const fs = Math.max(0.02 * H, screenH ? 9 * H / screenH : 0);
    for (const m of polys) {
      const q = document.createElementNS(SVGNS, "polygon");
      q.setAttribute("points", m.pts.map(([x, y]) => `${x},${y}`).join(" "));
      q.setAttribute("fill", "#8a8a8a"); q.setAttribute("stroke", "#282828"); q.setAttribute("stroke-width", "2");
      q.setAttribute("vector-effect", "non-scaling-stroke"); q.setAttribute("stroke-linejoin", "round");
      g.appendChild(q);
    }
    for (const m of polys) {   // 札は塗りの後（左上：いちばん上、同じ高さなら左の点）
      if (!m.label) continue;
      const [x, y] = m.pts.reduce((a, b) => (b[1] < a[1] - 1e-9 || (Math.abs(b[1] - a[1]) < 1e-9 && b[0] < a[0]) ? b : a));
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", x + fs * 0.25); t.setAttribute("y", y + fs * 0.95); t.setAttribute("font-size", fs);
      t.setAttribute("fill", "#fff"); t.textContent = m.label;
      const r = document.createElementNS(SVGNS, "rect");
      r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("height", fs * 1.25);
      r.setAttribute("width", fs * (0.5 + 1.05 * m.label.length)); r.setAttribute("fill", "#282828");
      g.append(r, t);
    }
    return g;
  }
  const boxPolys = (masks, W, H) => (masks || []).filter(m => m.box).map(m => {
    const [x0, y0, x1, y1] = m.box;
    return {label: m.label || m.kind, pts: [[x0 * W, y0 * H], [x1 * W, y0 * H], [x1 * W, y1 * H], [x0 * W, y1 * H]]};
  });
  // 絵（img）の上に重ねる SVG。読み込んだ後に natural の大きさで箱（と extra の線）を描く
  function overlayMasks(host, img, polysFn) {
    const svg = document.createElementNS(SVGNS, "svg"); svg.setAttribute("class", "ovl"); svg.setAttribute("preserveAspectRatio", "none");
    host.appendChild(svg);
    const draw = () => {
      const W = img.naturalWidth, H = img.naturalHeight; if (!W) return;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.querySelectorAll("g.masks").forEach(g => g.remove());
      const polys = polysFn(W, H);
      if (polys.length) svg.appendChild(maskGroup(polys, W, H, img.clientHeight));
    };
    if (img.complete && img.naturalWidth) draw(); else img.addEventListener("load", draw);
    return svg;
  }

  function pickPhotos(A, n) {
    const masked = A.photos.filter(p => p.masks && p.masks.length);
    const rest = A.photos.filter(p => !(p.masks && p.masks.length));
    const m = masked.slice(0, Math.min(masked.length, Math.ceil(n / 4)));
    const k = Math.max(0, n - m.length), out = [...m];
    for (let i = 0; i < k && i < rest.length; i++) out.push(rest[Math.floor(i * rest.length / Math.min(k, rest.length))]);
    return [...new Set(out)].sort((a, b) => String(a.taken_at || a.id).localeCompare(String(b.taken_at || b.id)));
  }
  function photoCell(p, withFace) {
    const c = el("div", "ph");
    const pi = el("div", "pi"); pi.style.cssText = "position:relative;height:100%;aspect-ratio:4/3;margin:auto;max-width:100%";
    const img = el("img"); img.alt = ""; img.decoding = "async"; img.src = u(p.src);
    img.onload = () => { if (img.naturalWidth) pi.style.aspectRatio = `${img.naturalWidth}/${img.naturalHeight}`; };
    img.onerror = () => { c.remove(); };
    img.style.cssText = "width:100%;height:100%;object-fit:fill";
    pi.appendChild(img);
    if ((p.masks || []).length) overlayMasks(pi, img, (W, H) => boxPolys(p.masks, W, H));
    if (withFace) pi.appendChild(el("span", "fc" + (p.face ? "" : " none"), esc(p.face || "面なし")));
    c.appendChild(pi);
    return c;
  }
  function buildPhotos(A, withFace) {
    const [w, h] = stageSize();
    const cols = w / h > 1.9 ? 7 : 6, cw = (w - 6 * (cols - 1)) / cols, rows = Math.max(2, Math.floor((h + 6) / (cw * 0.75 + 6)));
    const n = Math.min(A.photos.length, cols * rows, 35);
    const sel = pickPhotos(A, n);
    const g = el("div", "grid"); g.style.gridTemplateColumns = `repeat(${cols},minmax(0,1fr))`; g.style.gridAutoRows = `${cw * 0.75}px`;
    const cells = sel.map(p => { const c = photoCell(p, withFace); c.style.opacity = "0"; c.style.transition = "opacity .45s, transform .45s"; c.style.transform = "scale(.95)"; g.appendChild(c); return c; });
    const nMask = A.photos.reduce((s, p) => s + (p.masks || []).length, 0);
    const tl = [];
    cells.forEach((c, i) => tl.push([withFace ? 0 : 0.05 + 0.65 * i / Math.max(1, cells.length), () => { c.style.opacity = "1"; c.style.transform = "none"; }]));
    let foot;
    if (withFace) {
      cells.forEach((c, i) => tl.push([0.12 + 0.6 * i / Math.max(1, cells.length), () => { const f = c.querySelector(".fc"); if (f) f.classList.add("on"); }]));
      const by = {};
      A.photos.forEach(p => { const k = p.face || "面なし"; by[k] = (by[k] || 0) + 1; });
      const parts = Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} <b>${v}</b>`);
      foot = `写真 <b>${A.photos.length}</b> 枚の面：${parts.join("・")}`;
    } else {
      foot = `写真 <b>${A.photos.length}</b> 枚のうち ${sel.length} 枚（係に見せる縮小版）。` +
        (nMask ? `顔・掲示物などに灰色の箱＋理由 <b>${nMask}</b> 個` : "灰色の箱の付いた写真はありません");
    }
    return {el: g, foot, tl};
  }

  function figure(src, label) {
    const f = el("div", "fig"), bx = el("div", "box"), img = el("img");
    img.src = u(src); img.alt = "";
    bx.appendChild(img);
    if (label) bx.appendChild(el("span", "lbl", esc(label)));
    f.appendChild(bx);
    return {f, bx, img};
  }
  function buildRect(A) {
    const tf = A.tape && A.tape.face;
    const cs = A.compare.filter(c => c.rectified && c.masks_ok);   // 箱を写せない正面化の絵は見せない
    const c = cs.find(x => x.face === tf && x.rectified && x.sokuryo_lines) || cs.find(x => x.rectified && x.sokuryo_lines) || cs.find(x => x.rectified) || cs[0];
    const wrap = el("div", "pair");
    const L = figure(c.photo_src, "写真");
    if ((c.masks || []).length) overlayMasks(L.bx, L.img, (W, H) => boxPolys(c.masks, W, H));
    const R = figure(c.rectified || c.png, c.rectified ? "正面化した絵" : "正面化の照合");
    // 正面化した絵の箱（H で写した多角形）は線より先に、絵と同時に出す
    if ((c.rect_masks || []).length) overlayMasks(R.bx, R.img, () => c.rect_masks.map(m => ({label: m.label, pts: m.poly})));
    const ar = el("div", "arrow fadein", "→");
    R.f.classList.add("fadein");
    wrap.append(L.f, ar, R.f);
    // 線（照合の JSON と測量係の線の JSON から）。正面化の絵の画素の座標
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg"); svg.setAttribute("class", "ovl fadein"); svg.setAttribute("preserveAspectRatio", "none");
    R.bx.appendChild(svg);
    const draw = () => {
      if (!c.rectified) return;
      const W = R.img.naturalWidth, H = R.img.naturalHeight; if (!W) return;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.innerHTML = "";
      const fs = Math.max(14, W / 55), ppm = c.px_per_m;
      const add = (x1, y1, x2, y2, col, text, tx, ty) => {
        const l = document.createElementNS(svgNS, "line");
        Object.entries({x1, y1, x2, y2, stroke: col}).forEach(([k, v]) => l.setAttribute(k, v)); svg.appendChild(l);
        if (text) {
          const t = document.createElementNS(svgNS, "text"); t.setAttribute("x", tx); t.setAttribute("y", ty); t.setAttribute("font-size", fs);
          t.setAttribute("fill", col); t.setAttribute("stroke", "#fff"); t.setAttribute("stroke-width", fs / 6); t.setAttribute("paint-order", "stroke");
          t.setAttribute("font-weight", "700"); t.textContent = text; svg.appendChild(t);
        }
      };
      const css = getComputedStyle(document.documentElement);
      const OK = css.getPropertyValue("--ok").trim(), NG = css.getPropertyValue("--ng").trim(), AC = css.getPropertyValue("--acc").trim();
      if (ppm && c.x_origin_px != null && c.z_origin_px != null) {
        for (const ln of c.lines || []) {
          const m = ln.photo_m != null ? ln.photo_m : ln.json_m; if (m == null) continue;
          const col = ln.ok ? OK : NG, lab = `${ln.what}${ln.diff_m != null ? ` ${ln.diff_m >= 0 ? "+" : ""}${ln.diff_m.toFixed(2)} m` : ""}`;
          if (ln.axis === "x") { const x = c.x_origin_px + m * ppm; add(x, 0, x, H, col, lab, Math.min(x + 8, W - fs * 8), fs * 1.4); }
          else { const y = c.z_origin_px - m * ppm; add(0, y, W, y, col, lab, 8, y - 8); }
        }
        add(0, c.z_origin_px, W, c.z_origin_px, AC, "地面（高さの原点）", 8, c.z_origin_px - 8);
      }
      const sl = c.sokuryo_lines;
      if (sl) {
        if (sl.x_origin_px != null && sl.x_origin_px >= 0 && sl.x_origin_px <= W) add(sl.x_origin_px, 0, sl.x_origin_px, H, AC, "横の原点", sl.x_origin_px + 8, H - fs);
        for (const [k, v] of Object.entries(sl.z_named || {})) if (v >= 0 && v <= H) add(0, v, W, v, AC, k, 8, v - 8);
        for (const [k, v] of Object.entries(sl.x_named || {})) if (v >= 0 && v <= W) add(v, 0, v, H, AC, k, v + 8, fs * 2.8);
      }
    };
    R.img.addEventListener("load", draw);
    const t = A.tape;
    let foot2 = c.px_per_m ? `縮尺 <b>${c.px_per_m}</b> px/m（正面化の照合）` : "";
    if (t && t.pitch_m) {
      foot2 += `${foot2 ? "　" : ""}メジャー ${esc(t.read)} ÷ ${t.n_tiles} 周期 ＝ <b>${(t.pitch_m * 100).toFixed(2)} cm</b> ± ${(t.pitch_tol_m * 100).toFixed(2)}` +
        (t.face === c.face && c.px_per_m ? `（この縮尺で 1 周期 ≈ <b>${(t.pitch_m * c.px_per_m).toFixed(1)} px</b>）` : "") +
        `<span class="note">　測量係の読み。${esc(t.status_note || "")}</span>`;
    }
    const foot1 = `${esc(c.photo)} の ${esc(c.face)} 面`;
    return {el: wrap, foot: foot1, tl: [[0.12, () => { ar.classList.add("on"); R.f.classList.add("on"); }],
      [0.5, () => { svg.classList.add("on"); draw(); setFoot(`${foot1}　${foot2}`); }]]};
  }

  function buildDesign(A) {
    const cn = A.report.counts, mk = cn.measured_kinds || {};
    const wrap = el("div", "nums");
    const tot = (cn.measured || 0) + (cn.estimated || 0) + (cn.unknown || 0) || 1;
    const css = getComputedStyle(document.documentElement);
    const rows = [["measured（写真で測った）", cn.measured, "--ok"], ["estimated（推定と根拠）", cn.estimated, "--mid"], ["unknown（測れない）", cn.unknown, "--gray"]];
    const left = el("div", "card", `<h4>値の状態（棟 JSON の全部の値）</h4><div class="bars">${rows.map(([k, v, c]) =>
      `<span>${k}</span><div class="bar"><i style="background:var(${c})" data-w="${Math.max(0.6, 100 * (v || 0) / tot)}"></i></div><span class="big" style="font-size:30px">${v ?? "—"}</span>`).join("")}</div>
      <p class="note" style="margin:10px 0 0">measured の内訳：直接 <b>${mk.direct ?? 0}</b>／鎖 <b>${mk.chain ?? 0}</b>／個数 <b>${mk.count ?? 0}</b>` +
      `${mk.no_chain ? `／鎖の無い長さ ${mk.no_chain}` : ""}。推定から実測に変わると、上の緑が伸びる</p>`);
    const right = el("div", "card");
    const tl = [[0.08, () => left.querySelectorAll(".bar i").forEach(i => { i.style.width = i.dataset.w + "%"; })]];
    const chainHtml = (links, value, unit) => {
      const parts = links.map(l => `<span class="lk">${esc(l.link || "")}<small>${esc([l.photo, l.read, l.n != null ? `${l.n} 個` : ""].filter(Boolean).join("・"))}</small></span>`);
      return `<div class="chain">${parts.join('<span class="ar">→</span>')}<span class="ar">→</span><span class="lk val">${esc(value)} ${esc(unit || "")}</span></div>`;
    };
    if (A.chains && A.chains.length) {
      right.innerHTML = `<h4>根拠の鎖（measured の値。棟 JSON の chain）</h4>` + A.chains.slice(0, 3).map(c =>
        `<div class="note">${esc(c.where)}（${esc(c.measured_kind || "")}）</div>` + chainHtml(c.chain || [], c.value, c.unit)).join("");
    } else if (A.tape) {
      const t = A.tape;
      right.innerHTML = `<h4>メジャーからの鎖（測量係の読み）</h4>` +
        `<div class="chain"><span class="lk">メジャーの写真<small>${esc(t.photo)}</small></span><span class="ar">→</span>` +
        `<span class="lk">目盛りの読み<small>${esc(t.read)}</small></span><span class="ar">→</span>` +
        `<span class="lk">写真で数えた周期<small>${esc(t.n_tiles)} 個</small></span><span class="ar">→</span>` +
        `<span class="lk val">${(t.pitch_m * 100).toFixed(2)} cm<small>± ${(t.pitch_tol_m * 100).toFixed(2)} cm</small></span></div>` +
        `<p class="note">${esc(t.status_note || "")}</p><p class="note">棟 JSON の鎖のある measured はまだ 0 件</p>`;
    } else {
      right.innerHTML = `<h4>根拠の鎖</h4><p class="note">棟 JSON に鎖（chain）のある measured はまだありません。メジャーの写真が届くと、ここに「写真 → 枚数 → 値」が出ます</p>`;
    }
    right.querySelectorAll(".lk").forEach((l, i) => { l.style.animationDelay = `${(0.5 + 0.35 * i) / SPEED}s`; });
    wrap.append(left, right);
    return {el: wrap, foot: `撮影依頼（職員宛て）<b>${cn.requests ?? "—"}</b>・道具の作業 ${cn.tool_requests ?? "—"}・未確定の値 ${cn.unknowns ?? "—"}`, tl, focus: ["measured", "estimated", "unknown"]};
  }

  function buildElev(A) {
    // 最初に四面図・屋根伏せ・鳥瞰のシート（sheet.png、棟 JSON から自動生成）を全体で見せ、その後に面ごとの立面図 SVG を順に
    const w = el("div", "elevwrap"), tabs = el("div", "facetabs");
    const list = A.elev;
    const items = (A.sheet ? [{src: A.sheet, face: "四面図", name: "四面図・屋根伏せ・鳥瞰（棟 JSON から自動生成）", sheet: true}] : [])
      .concat(list.map(e => ({src: e.svg, face: e.face, name: e.name})));
    items.forEach(e => tabs.appendChild(el("span", "", esc(e.face))));
    const host = el("div", "", ""); host.style.cssText = "position:absolute;inset:0 0 26px 0;display:flex;align-items:center;justify-content:center";
    w.append(host, tabs);
    const SHEET = A.sheet ? 0.4 : 0;                      // シートに場面の 4 割
    const at = i => (!A.sheet ? i / items.length : i === 0 ? 0 : SHEET + (1 - SHEET) * (i - 1) / Math.max(1, list.length));
    const tl = items.map((e, i) => [at(i), () => {
      host.innerHTML = ""; const im = el("img"); im.src = u(e.src); im.alt = ""; im.style.cssText = "max-width:100%;max-height:100%;object-fit:contain"; host.appendChild(im);
      tabs.querySelectorAll("span").forEach((s, j) => s.classList.toggle("on", j === i));
      setFoot(e.sheet ? `<b>四面図</b>　${esc(e.name)}　＝ 図面は全部、棟 JSON から道具が描く（手で直さない）`
        : `<b>${esc(e.face)}</b>　${esc(e.name || "")}　（根拠の色：実測・推定・不明）`);
    }]);
    return {el: w, foot: "", tl, dur: Math.max(8, Math.min(14, (A.sheet ? 4 : 0) + list.length * 1.1))};
  }

  function buildBoard(A) {
    const cv = A.coverage.find(c => c.masks_ok), W = cv.length_m, top = cv.top_z, H = cv.top_z - cv.ground_z;
    const ps = [...cv.photos].filter(p => p.outline && p.outline.length).sort((a, b) => (a.order || 0) - (b.order || 0));
    const NS = "http://www.w3.org/2000/svg";
    const box = el("div", "board"), pop = el("div", "pop fadein", "");
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const [sw, sh] = stageSize(); const k = Math.min(sw / W, (sh - 4) / H); svg.setAttribute("width", W * k); svg.setAttribute("height", H * k);
    const defs = document.createElementNS(NS, "defs"); svg.appendChild(defs);
    const frame = document.createElementNS(NS, "rect");
    Object.entries({x: 0, y: 0, width: W, height: H, fill: "var(--bg)", stroke: "var(--line)", "stroke-width": 0.15}).forEach(([a, v]) => frame.setAttribute(a, v));
    svg.appendChild(frame);
    const pts = poly => poly.map(([s, z]) => `${s},${top - z}`).join(" ");
    const items = ps.map((p, i) => {
      const cp = document.createElementNS(NS, "clipPath"); cp.setAttribute("id", `cp${i}`);
      p.outline.forEach(poly => { const g = document.createElementNS(NS, "polygon"); g.setAttribute("points", pts(poly)); cp.appendChild(g); });
      defs.appendChild(cp);
      const im = document.createElementNS(NS, "image");
      Object.entries({href: u(cv.png), x: 0, y: 0, width: W, height: H, preserveAspectRatio: "none", "clip-path": `url(#cp${i})`, class: "pc"}).forEach(([a, v]) => im.setAttribute(a, v));
      svg.appendChild(im);
      if ((cv.masks || []).length) {   // 面の (s, z) に写した箱。この写真の使える所の中だけに出す
        const mg = maskGroup(cv.masks.map(m => ({label: m.label, pts: m.poly.map(([s_, z_]) => [s_, top - z_])})), W, H, H * k);
        mg.setAttribute("clip-path", `url(#cp${i})`); mg.classList.add("pc");
        svg.appendChild(mg);
        return {p, im, mg};
      }
      return {p, im};
    });
    const ols = ps.map(p => { const g = document.createElementNS(NS, "g"); g.setAttribute("class", "ol"); p.outline.forEach(poly => { const q = document.createElementNS(NS, "polygon"); q.setAttribute("points", pts(poly)); g.appendChild(q); }); svg.appendChild(g); return g; });
    box.append(svg, pop);
    const tl = items.map((it, i) => [0.04 + 0.86 * i / Math.max(1, items.length), () => {
      it.im.classList.add("on"); if (it.mg) it.mg.classList.add("on"); ols.forEach((o, j) => { o.classList.toggle("on", j === i); o.classList.toggle("done", j < i); });
      pop.classList.add("on"); pop.textContent = `${it.p.photo}　面 ${cv.face}　${it.p.used_m2 != null ? it.p.used_m2 + " m²" : ""}`;
      setFoot(`<b>${esc(cv.face)}</b> 面の製図板：写真 ${i + 1} / ${items.length} 枚目を貼った（使える所だけ。斜め・遮蔽は捨てる）`);
    }]);
    tl.push([0.95, () => { pop.classList.remove("on"); ols.forEach(o => { o.classList.remove("on"); o.classList.add("done"); });
      setFoot(`<b>${esc(cv.face)}</b> 面の製図板：写真 <b>${items.length}</b> 枚で面の <b>${cv.coverage_pct ?? "—"}%</b> を覆った　${esc(cv.name || "")}`); }]);
    return {el: box, foot: "", tl, dur: Math.max(6, Math.min(11, items.length * 0.5))};
  }

  function buildKenshu(A) {
    const k = A.kenshu.find(x => x.photo_src && x.view);
    const w = el("div", "kensa");
    const kl = el("div", "kl"), kr = el("div", "kr"), kres = el("div", "kres"), knum = el("div", "knum");
    const im = (s, lab) => { const f = el("div", "", ""); f.style.cssText = "position:relative;max-width:100%;max-height:100%;display:flex"; const i = el("img"); i.src = u(s); i.alt = ""; f.appendChild(i);
      if ((k.masks || []).length) overlayMasks(f, i, (W, H) => boxPolys(k.masks, W, H));
      if (lab) f.appendChild(el("span", "lbl", esc(lab))); f.querySelector(".lbl") && (f.querySelector(".lbl").style.cssText = "position:absolute;left:6px;top:6px;font-size:12px;background:var(--card);border-radius:5px;padding:0 6px"); return f; };
    kl.appendChild(im(k.photo_src, "写真"));
    kr.appendChild(im(k.view, "模型（写真と同じ視点の描画）"));
    kres.appendChild(im(k.lines_png, "検収の重ね（写真の上に模型の線）"));
    const lines = [...k.lines].sort((a, b) => Math.abs(b.diff_m) - Math.abs(a.diff_m));
    const tol = k.tol_m ?? 0.15;
    knum.innerHTML = `<div class="card" style="padding:8px 10px"><div class="note">許容 ±${tol} m に入った線</div><div class="big">${k.n_ok ?? "—"} <span style="font-size:20px;color:var(--sub)">/ ${k.n_lines ?? "—"} 本</span></div>` +
      `<div class="note">外れ ${k.n_ng ?? 0}・縁が見えない線は数えない</div></div><div class="note">ずれの大きい線から（写真の上で測った m）</div>` +
      lines.slice(0, 5).map(l => `<div class="row"><span class="d ${Math.abs(l.diff_m) <= tol ? "ok" : "ng"}">${l.diff_m >= 0 ? "+" : "−"}${Math.abs(l.diff_m).toFixed(2)}</span><span class="w">${esc(l.what)}</span></div>`).join("");
    w.append(kl, kr, kres, knum);
    const f1 = `${esc(k.photo)}（${esc(k.face)} 面）：左が写真、右が同じ視点から描いた模型`;
    return {el: w, foot: f1, tl: [[0.3, () => { w.classList.add("merge"); setFoot(`${esc(k.photo)}：写真と模型を重ねる（模型を半透明に）`); }],
      [0.6, () => { w.classList.add("result"); setFoot(`<b>${esc(k.face)}</b> 面は検収で合格（検収係）。この写真の線 ${k.n_lines} 本のうち許容 ±${tol} m に ${k.n_ok} 本`); }]]};
  }

  function buildEvidence(A) {
    const w = el("div", "ev");
    const big = el("div", "evbig"), small = el("div", "evsmall fadein");
    // 証拠ビューアの写真の区画は絵の中の位置が決まっていないので、灰色の箱を写せない。
    // だから模型と視点の絵（evidence_three）を大きく、写真の区画のある絵（evidence_photo）は右下に小さく
    const three = A.evidence.three || A.evidence.overview;
    if (three) { big.style.cssText = `background:var(--bg) url("${u(three)}") center/contain no-repeat`; w.appendChild(big); }
    if (A.evidence.photo) { small.style.cssText = `width:22%;background-image:url("${u(A.evidence.photo)}")`; w.appendChild(small); }
    const st = A.report.stages && A.report.stages.evidence;
    return {el: w, foot: st ? esc(st.result) : "", tl: [[0.3, () => small.classList.add("on")]]};
  }

  // 進行役の場面：考えている間は「決めています（n 分）」と事件、決まったら最新の決定の要約（F20）
  function buildShinko() {
    const st = (PIPE && PIPE.state) || {}, L = (PIPE && PIPE.live) || {};
    const sh = (st.shinko || []).filter(x => x && typeof x === "object"), last = sh[sh.length - 1];
    const thinking = st.current === "shinko" || L.phase === "shinko";
    const w = el("div", "nums");
    const left = el("div", "card");
    if (thinking) {
      const n = sinceSec(L.updated_at || st.updated_at);
      const TL = PIPE && PIPE.timeline, r = TL && TL.rounds && TL.rounds[TL.rounds.length - 1];
      // 事件は「今回進行役に出した物」（/api/pipeline の incident。止まった時の stopped.events ではない）
      const inc = PIPE && PIPE.incident, evl = (inc && inc.events) || [];
      const evs = evl.map(e => `<li>${esc(e.why || "")}</li>`).join("");
      left.innerHTML = `<h4>${esc(who("shinko"))}が決めています</h4><div class="big">${n != null ? Math.floor(n / 60) + " 分" : "—"}</div>` +
        (evs ? `<p class="note">進行役に出した事件（${evl.length} 件）：</p><ul class="note">${evs}</ul>`
          : `<p class="note">事件：${esc((r && r.why) || "（記録に事件の文がありません）")}</p>`);
    } else left.innerHTML = `<h4>進行役は今は動いていません</h4><p class="note">事件が出た時だけ呼ばれます。</p>`;
    const right = el("div", "card");
    const levs = last && !thinking ? (last.events || []).map(x => String(typeof x === "string" ? x : (x && x.why) || "").replace(/^[a-z]+[|]/, "")) : [];
    right.innerHTML = last ? `<h4>最新の決定 #${esc(last.n)}（${esc(hhmm(last.at))}、${esc(stepInfo(last.step).name)}の後）</h4>` +
      (levs.length ? `<p class="note">受け取った事件：${levs.map(esc).join("／")}</p>` : "") +
      `<p style="margin:0 0 6px;font-size:16px">${esc(last.summary || (last.failed ? "形にならず：" + last.failed : "要約なし"))}</p>` +
      `<p class="note">${last.redo_from ? `${esc(stepInfo(last.redo_from).name)}からやり直す` : "次の工程へ進める"}・` +
      `${last.review ? (last.review.verdict === "approve" ? "職員が承認済み" : "職員が取り消し") : "職員の確認待ち（職員の画面で承認／取り消し）"}` +
      `${last.cost_usd != null ? `・${Number(last.cost_usd).toFixed(2)} USD` : ""}</p>` +
      (last.decision_file ? `<p class="note"><code>${esc(last.decision_file)}</code></p>` : "")
      : `<h4>決定</h4><p class="note">まだありません（決まると、ここに要約が出ます）</p>`;
    w.append(left, right);
    return {el: w, foot: `進行役の決定は ${sh.length} 件${sh.filter(x => !x.review).length ? `（確認待ち ${sh.filter(x => !x.review).length}）` : ""}`, tl: []};
  }
  // 生成物がまだ無い工程の札：係・状態・この周の最新の 1 行（偽の絵は出さない）
  function buildStepCard(step) {
    const L = (PIPE && PIPE.live) || {}, TL = PIPE && PIPE.timeline;
    const rn = TL && TL.rounds && TL.rounds.length ? TL.rounds[TL.rounds.length - 1].n : null;
    const lastRes = TL ? TL.items.filter(i => i.type === "result" && i.step === step && i.round === rn).pop() : null;
    const going = ["running", "shinko"].includes(L.phase) && L.current === step;
    const w = el("div", "nums"), c = el("div", "card");
    c.innerHTML = `<h4>${esc(who(step))}</h4><div class="big" style="font-size:30px">${going ? "動いています" : (L.phase === "stopped" || L.phase === "stale" ? "止まっています" : "この工程")}</div>` +
      `<p class="note">${esc(stepInfo(step).line || "")}</p>` +
      (lastRes && lastRes.summary ? `<p class="note">この周の最新の 1 行：${esc(lastRes.summary)}</p>` : `<p class="note">生成物が出ると、ここに本物の絵が出ます。</p>`);
    w.append(c);
    return {el: w, foot: "", tl: []};
  }

  function countsNow() {
    const c = (PROG && PROG.report && PROG.report.counts) || (ART && ART.report && ART.report.counts) || {};
    return c;
  }
  function buildWalk(A) {
    const w = el("div", "walk");
    const im = el("div", "wimg"); im.style.backgroundImage = `url("${u(A.walk)}")`;
    const sum = el("div", "sum");
    const c = countsNow(), mk = c.measured_kinds || {};
    const rows = [["写真", "photos"], ["登録した写真", "registered"], ["measured", "measured"], ["estimated", "estimated"], ["unknown", "unknown"], ["撮影依頼（職員宛て）", "requests"]];
    const before = LIVE && START_COUNTS;
    sum.innerHTML = `<div class="card"><h4>${before ? "この画面を開いた時 → 今" : "今の記録"}</h4><div class="ba">` +
      rows.map(([k, key]) => before ? `<span class="k">${k}</span><span>${START_COUNTS[key] ?? "—"}</span><span class="ar">→</span><b>${c[key] ?? "—"}</b>`
        : `<span class="k">${k}</span><span></span><span></span><b>${c[key] ?? "—"}</b>`).join("") +
      `</div><p class="note" style="margin:8px 0 0">measured の内訳：直接 ${mk.direct ?? 0}／鎖 ${mk.chain ?? 0}／個数 ${mk.count ?? 0}</p></div>`;
    const btns = el("div", "", ""); btns.style.cssText = "display:flex;gap:10px;flex-wrap:wrap";
    if (A.report_html) btns.innerHTML += `<a class="btn pri" href="${esc(u(A.report_html))}">報告を見る</a>`;
    const q = (A.walk_query ? A.walk_query + "&" : "") + "boxes=0";
    btns.innerHTML += `<a class="btn" href="${esc(ROOT + "web/index.html?" + q)}">歩く画面で見る</a>`;
    sum.appendChild(btns);
    const st = A.report.stages && A.report.stages.walk;
    w.append(im, sum);
    return {el: w, foot: st ? esc(st.result) : "", tl: []};
  }

  // ---------------------------------------------------------------- 画面の枠
  function setFoot(h) { $("#sFoot").innerHTML = h; }
  // 工程の一覧（/api/steps）に無い係：進行役は事件の時だけ呼ばれる（results の step "shinko"。F19）
  const EXTRA_STEPS = {shinko: {step: "shinko", agent: "shinko", name: "進行役", model_ja: "Claude Fable", line: "事件の時だけ呼ばれて決める"}};
  function stepInfo(s) { return STEPS[s] || EXTRA_STEPS[s] || {name: s, model_ja: "", line: ""}; }
  function who(s) { const i = stepInfo(s); return `${i.name}（${i.model_ja || "—"}）`; }
  const hhmm = at => at ? String(at).slice(11, 16) : "";
  const sinceSec = at => at ? Math.max(0, (Date.now() - new Date(at).getTime()) / 1000) : null;

  function renderNums(focus) {
    const c = countsNow(), mk = c.measured_kinds || {};
    const rows = [["measured", "measured（直接／鎖／個数）", `${mk.direct ?? 0} / ${mk.chain ?? 0} / ${mk.count ?? 0}`, "--ok"],
      ["estimated", "estimated", c.estimated, "--mid"], ["unknown", "unknown", c.unknown, "--gray"],
      ["requests", "撮影依頼（職員宛て）", c.requests], ["tool_requests", "道具・係の作業", c.tool_requests],
      ["photos", "写真", c.photos], ["registered", "登録した写真", c.registered]];
    $("#nums").innerHTML = rows.map(([k, lab, v, col]) => {
      const val = v == null || v === "" ? "—" : v;
      const changed = prevNums[k] !== undefined && prevNums[k] !== String(val);
      prevNums[k] = String(val);
      return `<span class="k">${col ? `<i class="sw" style="background:var(${col})"></i>` : ""}${lab}</span><span class="v${changed ? " flash" : ""}${(focus || []).includes(k) ? " focus" : ""}">${esc(val)}</span>`;
    }).join("");
    setTimeout(() => document.querySelectorAll("#nums .flash").forEach(e => e.classList.remove("flash")), 1600);
  }

  // --- 係の一言（右の欄）。周（起動・再開 1 回分）を混ぜない（F20）
  function roundHeader(r) {
    if (!r) return "";
    return `<div class="rh">${r.kind === "start" ? "起動" : "再開"}（${r.n} 周目、${esc(hhmm(r.at))}〜${r.ended_at ? esc(hhmm(r.ended_at)) : ""}` +
      `${r.from_step ? `、${esc(stepInfo(r.from_step).name)}の後の事件から` : ""}${r.redo_from ? `、${esc(stepInfo(r.redo_from).name)}からやり直し` : ""}` +
      `${r.rewound ? "、記録を戻して取り下げ" : ""}）</div>`;
  }
  function logLine(i) {
    if (i.type === "run") {
      if (i.kind === "shinko") return `<div><b>進行役</b> <span class="m">決定 #${esc(i.n)}・${esc(hhmm(i.at))}</span><br>${i.failed ? "形にならず：" + esc(i.failed) : (i.redo_from ? `${esc(stepInfo(i.redo_from).name)}からやり直す` : "次の工程へ進める")}</div>`;
      if (i.kind === "rewind") return `<div><b>記録を戻した</b> <span class="m">${esc(hhmm(i.at))}</span><br>${esc(i.to || "")}へ</div>`;
      return `<div><b>${esc(i.kind)}</b> <span class="m">${esc(hhmm(i.at))}</span></div>`;
    }
    const inf = stepInfo(i.step);
    const warn = i.warn ? ` <span class="m wn" title="${esc(i.warn)}">（警告あり）</span>` : "";
    return `<div><b>${esc(inf.name)}</b> <span class="m">${esc(inf.model_ja || i.model || "")}${i.seconds ? "・" + fmtMS(i.seconds) : ""}</span>${warn}<br>${esc(i.summary || "")}</div>`;
  }
  function renderLog(uptoStep) {
    const TL = PIPE && PIPE.timeline;
    if (TL && TL.rounds && TL.rounds.length) {
      const H = TL.history || {}, L = PIPE.live || {};
      let rn, items;
      if (LIVE) {
        rn = (["running", "shinko"].includes(L.phase) ? TL.rounds[TL.rounds.length - 1].n : H.this_round_n) || TL.rounds[TL.rounds.length - 1].n;
        items = TL.items.filter(i => i.round === rn && ((i.type === "result" && i.summary) || (i.type === "run" && !["start", "resume"].includes(i.kind))));
      } else {
        // 再生：その工程を最後に回した周（取り下げた周は除く）だけ。1 周目と 2 周目の同じ工程を混ぜない
        const live = TL.rounds.filter(r => !r.rewound);
        const has = r => TL.items.some(i => i.round === r.n && i.type === "result" && i.step === uptoStep);
        const r = uptoStep ? live.filter(has).pop() : live[live.length - 1];
        rn = (r || live[live.length - 1] || {}).n;
        items = TL.items.filter(i => i.round === rn && ((i.type === "result" && i.summary) || (i.type === "run" && !["start", "resume"].includes(i.kind))));
        if (uptoStep) { const k = items.map(i => i.step).lastIndexOf(uptoStep); if (k >= 0) items = items.slice(0, k + 1); }
      }
      const hdr = roundHeader(TL.rounds.find(r => r.n === rn));
      $("#log").innerHTML = hdr + (items.slice(-5).map(logLine).join("") ||
        `<div class="m">この周の係の一言はまだありません（係が終わると、ここに 1 行ずつ出ます）</div>`);
      return;
    }
    const steps = (PIPE && PIPE.record && PIPE.record.steps) || [];
    let list = steps.filter(s => s.summary);
    if (uptoStep) {
      const order = ORDER, lim = order.indexOf(uptoStep);
      if (lim >= 0) list = list.filter(s => { const j = order.indexOf(s.step); return j < 0 || j <= lim; });
    }
    $("#log").innerHTML = list.slice(-5).map(s => logLine({type: "result", ...s})).join("") ||
      `<div class="m">係の一言はまだありません（pipeline の記録が出ると、ここに係ごとの 1 行が出ます）</div>`;
  }

  // --- 下の列：生きている時も再生の時も、記録（pipeline_state）があれば「この記録」の done・current から済／進行中／まだ（F20）。
  // ファイルがあるだけ（前の記録の生成物）は済にせず、別の印にする
  const FILM_STEPS = {photos: ["satsuei", "shiwake"], elev: ["sokuryo", "sekkei", "seizu"], evidence: ["sokuryo", "kenshu"],
    model: ["seizu", "kenshu"], walk: ["seizu_final", "web"]};
  const FILM_JA = {done: "済", partial: "途中", run: "進行中", todo: "まだ"};
  function filmStatus(k, rep) {
    const st = PIPE && PIPE.state;
    if (!st || !st.steps || !FILM_STEPS[k]) return rep;   // 再生でも記録がある時は工程の済で（中身の途中・済は title の report の文）
    const want = FILM_STEPS[k].filter(s => st.steps.includes(s));
    if (!want.length) return rep;
    const done = new Set(st.done || []), cur = PIPE.live && ["running", "shinko"].includes(PIPE.live.phase) ? PIPE.live.current : null;
    if (cur && want.includes(cur)) return "run";
    const n = want.filter(s => done.has(s)).length;
    return n === want.length ? "done" : (n ? "partial" : "todo");
  }
  function renderFilm(curKey) {
    const st = (PROG && PROG.report && PROG.report.stages) || (ART && ART.report && ART.report.stages) || {};
    const A = ART || {photos: [], elev: [], evidence: {}};
    const c = countsNow();
    const img = {photos: A.photos && A.photos[0] && A.photos[0].src, elev: A.elev && A.elev[0] && A.elev[0].svg,
      evidence: A.evidence && (A.evidence.photo || A.evidence.three), model: A.sheet || (A.kenshu && A.kenshu[0] && A.kenshu[0].lines_png),
      walk: A.walk, rest: null};
    $("#film").innerHTML = STAGES.map(([k, t]) => {
      const s = st[k] || {}, cls = filmStatus(k, s.status || "todo");
      const old = LIVE && img[k] && cls !== "done";   // 生成物はあるが、この記録ではまだ済んでいない
      const bg = img[k] ? `style="background-image:url('${esc(u(img[k]))}')"` : "";
      const inner = !img[k] && k === "rest" ? `<span title="撮影依頼">${c.requests ?? "—"}</span>` : "";
      return `<div class="cell${k === curKey ? " cur" : ""}" data-film="${k}" title="${esc((s.status && s.status !== cls ? `中身は${ST_JA[s.status] || s.status}：` : "") + (s.result || ""))}"><div class="im${k === "elev" ? " contain" : ""}${old ? " old" : ""}" ${bg}>${inner}</div>` +
        `<div class="lab"><span class="chip ${cls}">${FILM_JA[cls] || "まだ"}</span>${t}${old ? `<small class="oldtag" title="ファイルは前の記録の物">前の記録の生成物あり</small>` : ""}</div></div>`;
    }).join("");
  }

  function liveState() {
    const st = PIPE && PIPE.state, run = (PIPE && PIPE.run) || (PROG && PROG.run);
    return {st, run};
  }
  // サーバの「今どうなっているか」（/api/pipeline の live。職員の画面と同じ言葉）。古いサーバ向けに状態だけから推す
  function livePhase() {
    if (PIPE && PIPE.live) return PIPE.live;
    const {st, run} = liveState();
    if (run && run.state === "running") return {phase: st && st.status === "running" && st.current ? (st.current === "shinko" ? "shinko" : "running") : "starting", current: st && st.current};
    if (st && st.status === "running") return {phase: st.current === "shinko" ? "shinko" : "running", current: st.current, where: "other"};
    if (st && st.status === "stopped") return {phase: "stopped", step: st.stopped && st.stopped.step, reason: st.stopped && st.stopped.reason};
    if (st && st.status === "done") return {phase: "done"};
    return {phase: "idle"};
  }
  function renderTop(scene) {
    const name = (ART && ART.name) || B;
    $("#bname").textContent = name;
    document.title = `${name}：記録の待機画面`;
    $("#mode").textContent = LIVE ? "生きている記録" : "再生（今ある生成物）";
    const {st, run} = liveState();
    const TL = PIPE && PIPE.timeline, H = (TL && TL.history) || {};
    let stepTxt = "", el_ = "", cost = "", hist = [];
    const stopbar = $("#stopbar");
    if (LIVE) {
      const L = livePhase(), steps = (st && st.steps) || ORDER, cur = L.current;
      const redo = st && st.redo && (st.redo.steps || []).includes(cur) ? "（やり直し）" : "";
      if (L.phase === "running") stepTxt = `${steps.indexOf(cur) + 1}/${steps.length} ${who(cur)}が動いています${redo}`;
      else if (L.phase === "shinko") stepTxt = `${who("shinko")}が決めています`;
      else if (L.phase === "starting") stepTxt = "係を起動しています";
      else if (L.phase === "stopped" || L.phase === "stale") stepTxt = "止まっています";
      else if (L.phase === "done") stepTxt = "全部の工程が終わりました";
      else stepTxt = "記録はまだ動いていません";
      if (L.where === "other") stepTxt += "（このサーバの外で動いています）";
      // 帯：止まった時だけ「止まっています」、進行役が考えている時は「決めています」。動いている時は出さない
      if (L.phase === "stopped" || L.phase === "stale") {
        const s = (st && st.stopped) || (run && run.stopped) || {};
        const why = s.label || s.reason || (run && run.error) || (L.phase === "stale" ? "10 分以上更新が無いので落ちたと見ます" : "");
        const rs = PIPE && PIPE.resume;
        const key = JSON.stringify([L.phase, s.step, why, s.reason, rs && rs.code, rs && rs.need_max_usd, rs && rs.spent_usd]);
        if (stopbar.dataset.key !== key) {   // 1 秒ごとの描き直しで、打っている上限の欄を消さない
          stopbar.dataset.key = key;
          const step = s.step || L.step;
          const head = `止まっています。${step ? `工程 ${esc(stepInfo(step).name)}` : ""}${why ? `（${esc(why)}）` : ""}`;
          let ctl;
          if (rs && rs.ok) {
            // 安全優先で広めに（F19）：進行役を待つ時は使った額＋進行役の費用＋1、それ以外は使った額＋20 と上限×1.5 の大きい方
            const sc = Number(rs.shinko_cost_usd) || 10, spent = Number(rs.spent_usd) || 0;
            const sug = Math.ceil(Math.max(spent + (rs.shinko_pending ? sc + 1 : 20), (Number(rs.max_usd) || 0) * 1.5));
            ctl = `<span class="sbctl">` +
              (rs.need_max_usd ? `<label>費用の上限 <input id="sbMax" type="number" min="0" step="1" value="${sug}" aria-label="費用の上限（USD）"> USD` +
                `<small>（使った額 ${Number(rs.spent_usd).toFixed(2)} USD ${rs.shinko_pending ? `＋進行役の費用 ${sc} USD 分より大きく` : "より大きく"}）</small></label>` : "") +
              `<button id="sbGo" type="button">続きから</button><span id="sbMsg" class="sbmsg"></span></span>`;
          } else if (rs) ctl = `<small class="sbwhy">${esc(rs.why)}</small>`;   // 続けられない理由は小さく 1 行（ボタンは出さない）
          else ctl = `<small class="sbwhy">続きは <code>recorder agent resume ${esc(B)}</code></small>`;
          stopbar.innerHTML = head + ctl;
          stopbar.className = "stopbar on";
        }
      } else if (L.phase === "shinko") {
        const n = sinceSec(L.updated_at);
        stopbar.className = "stopbar on info";
        const inc = PIPE && PIPE.incident, evl = (inc && inc.events) || [];
        stopbar.innerHTML = `${esc(who("shinko"))}が決めています${n != null ? `（${Math.floor(n / 60)} 分）` : ""}。決まると工程が続きます` +
          (evl.length ? `<small class="sbwhy">事件：${esc(evl.map(e => e.why).join("／"))}</small>` : "");
        delete stopbar.dataset.key;
      } else { stopbar.className = "stopbar"; stopbar.innerHTML = ""; delete stopbar.dataset.key; }
      // 経過：この起動・再開から と 全体（係が動いていた時間の合計。止まっていた間は入れない）
      const going = ["running", "shinko", "starting"].includes(L.phase);
      const r0 = L.phase === "starting" && run && run.started_at ? run.started_at : H.this_round_at || (st && st.started_at) || (run && run.started_at);
      const r1 = going ? Date.now() : new Date(H.this_round_ended_at || (st && st.updated_at) || (run && run.finished_at) || Date.now()).getTime();
      if (r0) {
        const thisSec = (r1 - new Date(r0).getTime()) / 1000;
        const total = H.work_seconds_ended != null ? H.work_seconds_ended + (going && (H.running || L.phase === "starting") ? thisSec : 0) : null;
        const kind = L.phase === "starting" || H.this_round_kind === "resume" ? "この再開" : "この起動";
        el_ = `${kind}から <b>${fmtHMS(thisSec)}</b>` + (total != null && H.rounds > 1 ? `・全体（動いていた時間） <b>${fmtHMS(total)}</b>` : "");
      }
      const spent = st && st.spent_usd != null ? st.spent_usd : run && run.spent_usd;
      const cap = (st && st.max_usd) ?? (run && run.max_usd);
      cost = `費用 <b>${spent != null ? Number(spent).toFixed(2) : "—"}</b> / ${cap ? cap + " USD" : "上限なし"}` +
        (H.rounds > 1 && H.this_round_cost_usd != null ? `（この${H.this_round_kind === "resume" ? "再開" : "起動"} ${Number(H.this_round_cost_usd).toFixed(2)}）` : "");
      if (run && run.dry_run) cost += "（係を呼ばない試し）";
      // 上の帯の 2 行目：再開・戻した・自動の再開・進行役・確認待ち を 1 行に
      if (H.resumes) hist.push(`再開 <b>${H.resumes}</b> 回（今は ${H.this_round_n || H.rounds} 周目）`);
      (H.rewinds || []).slice(-1).forEach(w => hist.push(`記録を戻した <b>${(H.rewinds || []).length}</b> 回（最後は ${esc(hhmm(w.at))}、${esc(w.to || "")}へ）`));
      const ar = (st && st.auto_resumes) || {};
      const arAll = Object.values(ar).flat().filter(e => e && typeof e === "object");
      if (arAll.length) {
        const KJA = {max_turns: "手数の上限", max_budget: "1 回の費用の上限", rate_limit: "混雑", crash: "落ちた", partial: "係が区切った"};
        const last = arAll[arAll.length - 1], k = last.kind || last.reason;
        hist.push(`<span title="同じ会話から自動で続けた回数">自動で再開 <b>${arAll.length}</b> 回（最後は${esc(KJA[k] || k || "理由不明")}）</span>`);
      }
      // 進行役（shinko）が事件を自動で決めた回数と最新の要約（11/8、F19。承認／取り消しは職員の画面で）
      const sh = ((st && st.shinko) || []).filter(e => e && typeof e === "object");
      if (sh.length) {
        const last = sh[sh.length - 1], wait = sh.filter(e => !e.review).length;
        hist.push(`<span title="事件が出た時に進行役が自動で決めた回数。職員の画面で承認／取り消し">進行役が決めました <b>${sh.length}</b> 回（${esc(last.summary || "要約なし")}）` +
          `${wait ? `・確認待ち ${wait}` : ""}</span>`);
      }
      if (st && st.halt) hist.push(`職員が止めました（進行役の決定の取り消し）`);
    } else if (scene) {
      stopbar.className = "stopbar";
      const steps = ORDER, i = steps.indexOf(scene.step);
      stepTxt = `${i >= 0 ? `${i + 1}/${steps.length} ` : ""}${who(scene.step)}の工程の生成物`;
      const r = PIPE && PIPE.record;
      if (r) {
        el_ = `記録の経過 <b>${fmtHMS(r.wall_seconds)}</b>（係の秒の合計 ${fmtHMS(r.seconds_total)}）`;
        cost = r.cost_usd != null ? `費用 <b>${Number(r.cost_usd).toFixed(2)}</b>${r.max_usd ? ` / ${r.max_usd} USD` : " USD"}` : "費用の記録なし";
      } else el_ = "pipeline の記録なし";
      if (H.rounds > 1) hist.push(`この記録は ${H.rounds} 周（再開 ${H.resumes} 回${(H.rewinds || []).length ? `・記録を戻した ${(H.rewinds || []).length} 回` : ""}）。係の一言はその工程を最後に回した周の物`);
    }
    $("#step").textContent = stepTxt;
    $("#elapsed").innerHTML = el_;
    $("#cost").innerHTML = cost;
    $("#hist").innerHTML = hist.join("　・　");
    $("#costNote").textContent = LIVE ? "費用の上限は棟ごと。止まっても続きから再開できます" : "再生：今ある生成物を最初から見せています";
  }

  // ---------------------------------------------------------------- 進め方
  // 再生（replay）は場面を動画のように順に流す。生きている記録（live）は「今の工程の場面」に留め、中身だけ数秒ごとに更新する。
  // 工程が進んだ時だけ 1 回切り替える。人が下の列・点・矢印で別の場面を見た時は「今の工程に戻る」で戻る（F20）
  let avail = [], idx = -1, timers = [], nextTimer = null, paused = false, cur = null;
  let liveStep = undefined, manual = false, artKey = "";
  const STEP_SCENE = {satsuei: "a", shiwake: "b", sokuryo: "c", sekkei: "d", seizu: "e", seizu_final: "e", kenshu: "g", mejirushi: "h", web: "i", shinko: "s"};
  function okScene(s) { try { return !!ART && s.ok(ART); } catch (e) { return false; } }
  function allowedScenes() {
    if (!ART) return [];
    let ok = SCENES.filter(okScene);
    if (LIVE) {
      const {st} = liveState();
      if (st && st.steps) {
        const done = new Set(st.done || []); if (st.current) done.add(st.current);
        ok = ok.filter(s => done.has(s.step) || !st.steps.includes(s.step));   // 工程の済んだ所まで（工程に無い場面は生成物があれば出す）
      }
    }
    if (ONLY) ok = ok.filter(s => s.key === ONLY);
    return ok;
  }
  // 生きている記録の「今の工程」：動いている → current、止まった → 止まった工程、起動直後 → やり直す最初の工程、済んだ → 最後
  function targetStep() {
    const L = livePhase(), st = PIPE && PIPE.state;
    if (L.phase === "running" || L.phase === "shinko") return L.current;
    if (L.phase === "stopped" || L.phase === "stale") return L.step || (st && st.stopped && st.stopped.step) || (st && st.current) || ((st && st.done) || []).slice(-1)[0] || null;
    if (L.phase === "starting") {
      const steps = (st && st.steps) || ORDER, done = new Set((st && st.done) || []);
      return steps.find(s => !done.has(s)) || null;
    }
    if (L.phase === "done") return ((st && st.steps) || ORDER).slice(-1)[0] || null;
    return null;
  }
  // 工程の場面。生成物が無くて描けない時は、その工程の札（係・状態・最新の 1 行）
  function sceneForStep(step) {
    if (!step) return null;
    const sc = SCENES.find(s => s.key === STEP_SCENE[step]) || SCENES.find(s => s.step === step);
    if (sc && okScene(sc)) return sc;
    return {key: "w:" + step, step, title: stepInfo(step).name, film: null, dur: 8, build: () => buildStepCard(step)};
  }
  function clearTimers() { timers.forEach(clearTimeout); timers = []; if (nextTimer) clearTimeout(nextTimer); nextTimer = null; }
  function paintBack() {
    let b = $("#backLive");
    if (!LIVE || !manual) { if (b) b.remove(); return; }
    if (!b) { b = el("button", "backlive", "今の工程に戻る"); b.id = "backLive"; b.type = "button"; $(".cap").appendChild(b);
      b.addEventListener("click", () => { manual = false; showLive(true); }); }
  }
  // 場面を 1 つ描く。still＝動きの段取りを全部すぐ当てる（生きている時の中身の更新。流し直さない）
  function renderScene(sc, still) {
    clearTimers();
    cur = sc;
    avail = allowedScenes();
    const j = avail.findIndex(s => s.key === sc.key); idx = j;
    window.__replay = {scene: sc.key, index: j, count: avail.length, ended: false, live: LIVE, manual};   // 録画の道具が読む印（scripts/record_replay.py）
    const info = stepInfo(sc.step);
    $("#sTitle").textContent = sc.title;
    $("#sDesc").textContent = sc.desc || firstSentence(info.line, sc.kw);
    $("#sWho").textContent = `${who(sc.step)}`;
    $("#pager").innerHTML = avail.map((s, k) => `<i class="${s.key === sc.key ? "on" : ""}" data-scene="${esc(s.key)}" title="${esc(s.title)}"></i>`).join("");
    let built;
    try { built = sc.build(ART); } catch (e) { console.error(e); built = {el: el("div", "empty", "この場面を描けませんでした（生成物の形が想定と違う）"), foot: esc(String(e)), tl: []}; }
    const stage = $("#stage"); stage.innerHTML = ""; stage.appendChild(built.el);
    setFoot(built.foot || "");
    renderTop(sc); renderNums(built.focus); renderLog(LIVE ? null : sc.step); renderFilm(sc.film);
    paintBack();
    const dur = (built.dur || sc.dur) * 1000 / SPEED;
    const tl = (built.tl || []).slice().sort((a, b) => a[0] - b[0]);
    if (AT != null || still) { tl.filter(([f]) => AT == null || f <= AT).forEach(([, fn]) => fn()); if (AT != null) window.__sceneReady = sc.key; return; }
    tl.forEach(([f, fn]) => timers.push(setTimeout(fn, f * dur)));
    if (!LIVE && !paused) nextTimer = setTimeout(next, dur);   // 生きている時は次の場面へ流さない
  }
  function emptyStage() {
    clearTimers();
    $("#stage").innerHTML = `<div class="empty">${ART ? "見せられる生成物がまだありません。係が生成物を出すと、ここに本物の絵が順に出ます。" : "棟 id（?building=）が無いか、生成物の一覧を読めません。recorder serve から開いてください。"}</div>`;
    $("#sTitle").textContent = ""; $("#sDesc").textContent = ""; $("#sWho").textContent = ""; setFoot("");
    renderTop(null); renderNums(); renderLog(); renderFilm(null);
    window.__replay = {scene: null, index: -1, count: 0, ended: true, empty: true};
  }
  function show(i) {
    avail = allowedScenes();
    if (!avail.length) { emptyStage(); return; }
    idx = ((i % avail.length) + avail.length) % avail.length;
    renderScene(avail[idx], false);
  }
  // 生きている記録：今の工程の場面へ（変わった時は 1 回だけ動きを付け、同じ工程なら中身の更新だけ）
  function showLive(changed) {
    const t = targetStep();
    const sc = sceneForStep(t) || allowedScenes()[0];
    if (!sc) { emptyStage(); return; }
    renderScene(sc, !changed);
  }
  function next() {
    if (!LIVE && !LOOP && idx === avail.length - 1) { paused = true; window.__replay = Object.assign({}, window.__replay, {ended: true}); return; }
    show(idx + 1);
  }
  function goManual(sc) { if (!sc) return; if (LIVE) manual = true; renderScene(sc, false); }
  document.addEventListener("keydown", e => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault(); const av = allowedScenes(); if (!av.length) return;
      const k = av.findIndex(s => cur && s.key === cur.key), d = e.key === "ArrowRight" ? 1 : -1;
      goManual(av[((k + d) % av.length + av.length) % av.length]);
    } else if (e.key === " " && !LIVE) {
      e.preventDefault(); paused = !paused;
      if (paused) { if (nextTimer) clearTimeout(nextTimer); nextTimer = null; $("#ctl").textContent = "一時停止中（スペースで続ける）"; }
      else { $("#ctl").textContent = "← → 場面　スペース 一時停止"; nextTimer = setTimeout(next, 2500 / SPEED); }
    }
  });
  // 人が下の列・点を押した時だけ別の場面（生きている時は「今の工程に戻る」が出る）
  $("#film").addEventListener("click", ev => {
    const c = ev.target.closest("[data-film]"); if (!c) return;
    goManual(allowedScenes().find(s => s.film === c.dataset.film));
  });
  $("#pager").addEventListener("click", ev => {
    const d = ev.target.closest("[data-scene]"); if (!d) return;
    goManual(allowedScenes().find(s => s.key === d.dataset.scene));
  });

  // ---------------------------------------------------------------- 読み込み
  async function loadArtifacts() { const a = await getJSON(`/api/artifacts?building=${encodeURIComponent(B)}`); if (a) ART = a; }
  async function loadPipeline() {
    const p = await getJSON(`/api/pipeline?building=${encodeURIComponent(B)}&t=${Date.now()}`);
    if (p) { PIPE = p; return; }
    const st = await getJSON(`/out/${encodeURIComponent(B)}/pipeline_state.json?t=${Date.now()}`);   // API の無いサーバ：状態だけ
    PIPE = st ? {state: st, record: null, run: null} : null;
  }
  async function loadProgress() {
    const r = await getJSON(`/api/progress?buildings=${encodeURIComponent(B)}&t=${Date.now()}`);
    if (r && r.buildings && r.buildings[B]) { PROG = r.buildings[B]; return; }
    const rep = await getJSON(`/out/${encodeURIComponent(B)}/report.json?t=${Date.now()}`);
    PROG = rep ? {report: {stages: rep.stages, counts: rep.counts}, run: null} : PROG;
  }
  // 生きている記録の 1 回の更新：工程が変わった時だけ場面を切り替え、同じなら中身（数・一言・費用・経過）だけ
  async function tick(n) {
    await Promise.all([loadPipeline(), loadProgress(), n % 3 === 0 ? loadArtifacts() : null]);
    const t = targetStep();
    if (t !== liveStep) {   // 工程が進んだ（再開・進行役も）：人が別の場面を見ていても今の工程へ 1 回切り替える
      liveStep = t; manual = false; await loadArtifacts(); artKey = JSON.stringify(ART);
      showLive(true); return;
    }
    const k = JSON.stringify(ART);
    if (!manual && (k !== artKey || (cur && (cur.key.startsWith("w:") || cur.key === "s")))) {   // 生成物が増えた・札の場面：描き直す（流し直さない）
      artKey = k; showLive(false); return;
    }
    renderNums(cur && cur.key === "d" ? ["measured", "estimated", "unknown"] : null); renderLog(); renderTop(cur); renderFilm(cur && cur.film);
  }
  // 「止まっています」の帯の「続きから」（11/8、F15）：POST /api/resume。成功したらすぐ読み直して生きている表示に戻す
  $("#stopbar").addEventListener("click", async ev => {
    const btn = ev.target.closest("#sbGo"); if (!btn || !LIVE) return;
    const msg = $("#sbMsg"), inp = $("#sbMax"), rs = (PIPE && PIPE.resume) || {};
    const body = {building: B};
    if (inp) {
      const v = Number(inp.value);
      if (!(v > (Number(rs.spent_usd) || 0))) { msg.textContent = `使った額 ${Number(rs.spent_usd || 0).toFixed(2)} USD より大きい上限を入れてください`; return; }
      body.max_usd = v;
    }
    btn.disabled = true; msg.textContent = "続きを起動しています…";
    let r = null, j = {};
    try { r = await fetch(u("/api/resume"), {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); j = await r.json(); } catch (e) {}
    if (!r || !r.ok) { msg.textContent = (j && j.error) || "サーバに届きません"; btn.disabled = false; return; }
    delete $("#stopbar").dataset.key;
    await tick(0);
  });

  async function init() {
    if (!/^[A-Za-z0-9_-]+$/.test(B)) { ART = null; show(0); return; }
    const steps = await getJSON("/api/steps");
    if (steps) { (steps.steps || []).forEach(s => { STEPS[s.step] = s; }); ORDER = steps.pipeline || []; }
    await Promise.all([loadArtifacts(), loadPipeline(), loadProgress()]);
    if (LIVE && PIPE && PIPE.state && PIPE.state.mode === "continue" && steps) ORDER = steps.continue || ORDER;
    START_COUNTS = JSON.parse(JSON.stringify(countsNow()));
    renderNums();
    if (!LIVE) { show(0); return; }
    $("#ctl").textContent = "下の列・点・← → で別の場面（「今の工程に戻る」で戻る）";
    // 再開の直後も、済んだ工程の場面を流し直さず、今の工程の場面から始める
    liveStep = targetStep(); artKey = JSON.stringify(ART);
    if (ONLY) show(0); else showLive(true);
    let n = 0;
    setInterval(() => { n++; tick(n); }, 4000);
    setInterval(() => renderTop(cur), 1000);
  }
  init();
})();
