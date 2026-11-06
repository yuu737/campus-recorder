// 歩く画面の見た目（11/6）：空・太陽・影・接触影・霞。three.js 0.170 の付属モジュール（MIT）だけを使う。
//   objects/Sky.js（物理的な空）／ csm/CSM.js（Cascaded Shadow Maps：近くは細かく遠くは粗い影）／
//   postprocessing/GTAOPass.js（接触影＝角や足元の暗がり）・SMAAPass.js（縁のギザギザ取り）・OutputPass.js（トーンマップ）
// 模型の素材の色（根拠の塗り分けを含む）は変えない。変えるのは光・影・空・霞・後処理だけ。
// index.html から ?look=0 で読まれない（旧の見た目：HDRI＋平行光 1 枚）。
import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { CSM } from "three/addons/csm/CSM.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const D2R = Math.PI / 180;

// 太陽の位置（簡単な式。誤差は 1° 程度）：日付 doy（1 月 1 日＝1）、時刻 t（日本時間の時）、緯度・経度（°）
// 返り値は 高度 h・方位 az（北から時計回り）。式：赤緯は Cooper、均時差は Spencer の近似を簡略化した物
export function sunPosition(doy, t, lat, lon) {
  const B = 2 * Math.PI * (doy - 81) / 364;
  const eot = 9.87 * Math.sin(2 * B) - 7.53 * Math.cos(B) - 1.5 * Math.sin(B);   // 分
  const dec = 23.45 * Math.sin(2 * Math.PI * (284 + doy) / 365) * D2R;
  const solar = t + (lon - 135) / 15 + eot / 60;                                  // 日本標準時の子午線は東経 135°
  const H = 15 * (solar - 12) * D2R, phi = lat * D2R;
  const h = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi)) + Math.PI;
  return { h, az };
}

// opt: { renderer, scene, camera, sun（旧の平行光。新では消す）, Q（URL 引数）, mobile, lat, lon, date, time, materials（後で使う素材） }
// 日付・時刻・緯度経度の既定は data/daylight.json（site.json の daylight。Blender の描画と同じ物）。?t= ?d= で上書き
export function setupLook(opt) {
  const { renderer, scene, camera, Q, mobile } = opt;
  const qnum = (k, d) => { const v = parseFloat(Q.get(k)); return Number.isFinite(v) ? v : d; };
  const tq = (Q.get("t") || opt.time || "13:30").split(":").map(Number);
  const hour = (Number.isFinite(tq[0]) ? tq[0] : 13.5) + (Number.isFinite(tq[1]) ? tq[1] / 60 : 0);
  const date = new Date((Q.get("d") || opt.date || "2026-11-06") + "T00:00:00Z");
  const doy = Math.round((date - Date.UTC(date.getUTCFullYear(), 0, 1)) / 864e5) + 1;
  const { h, az } = sunPosition(doy, hour, opt.lat, opt.lon);
  const hClamp = Math.max(h, 2 * D2R);                                  // 地平線の下では使わない（夕方の上限）
  // three.js の向き：x=東, y=上, -z=北
  const sunVec = new THREE.Vector3(Math.sin(az) * Math.cos(hClamp), Math.sin(hClamp), -Math.cos(az) * Math.cos(hClamp));

  const low = h < 20 * D2R;                                            // 夕方（太陽の高さ 20° 未満）
  // 色の流儀：旧と同じ PBR Neutral（Blender の描画と同じ式。AgX・ACES は空と壁の色が褪せた）。?tm= で上書き
  const TM = { neutral: THREE.NeutralToneMapping, aces: THREE.ACESFilmicToneMapping, agx: THREE.AgXToneMapping };
  renderer.toneMapping = TM[Q.get("tm")] ?? THREE.NeutralToneMapping;
  renderer.toneMappingExposure = qnum("exp", low ? 1.0 : 0.72);   // 夕方は光が弱いので露出を上げる（11/6 に 0.85／1.15 から 15% 下げた。壁のタイルが飛んだため）

  // 1) 空：Preetham の空（Sky.js）。都市の霞を見込んで turbidity を上げる。夕方は赤みを強める
  const skyParams = { turbidity: qnum("turb", low ? 5 : 6), rayleigh: low ? 2.0 : 1.4, mieCoefficient: 0.006, mieDirectionalG: 0.8 };
  const makeSky = () => {
    const s = new Sky();
    for (const k in skyParams) s.material.uniforms[k].value = skyParams[k];
    s.material.uniforms.sunPosition.value.copy(sunVec);
    return s;
  };
  // Preetham の空の値は「明るさの単位」が模型の光と合わないので、空の板の明るさを掛け算で合わせる（SKY_GAIN）
  const SKY_GAIN = qnum("skyk", 0.55);
  const sky = makeSky();
  sky.material.fragmentShader = sky.material.fragmentShader.replace("#include <tonemapping_fragment>",
    `gl_FragColor.rgb *= ${SKY_GAIN.toFixed(3)};\n#include <tonemapping_fragment>`);
  sky.scale.setScalar(10000); sky.frustumCulled = false; sky.renderOrder = -1;
  scene.add(sky);
  scene.background = null;

  // 2) 環境光：同じ空を PMREM で畳んだ物（起動時 1 回）。空の下半分には「日の当たった地面の照り返し」の円板を置く
  //    （明るさ＝地面の反射率 0.2 × 太陽の照度 ÷ π。壁の足元や日陰が真っ青・真っ黒にならないように）
  const envI = qnum("env", 1.0);
  const sunI = qnum("sun", low ? 2.4 : 3.0) * Math.min(1, 0.35 + Math.sin(hClamp) * 1.3);
  const skyScene = new THREE.Scene();
  const sky2 = makeSky(); sky2.scale.setScalar(10);
  // 環境光用の空は青みを半分に落とす（Preetham の空をそのまま畳むと日陰が濃い紺になったため。11/6 の試し）
  sky2.material.fragmentShader = sky.material.fragmentShader.replace("#include <tonemapping_fragment>",
    `gl_FragColor.rgb = mix(vec3(dot(gl_FragColor.rgb, vec3(0.2126, 0.7152, 0.0722))), gl_FragColor.rgb, ${qnum("envsat", 0.5).toFixed(2)});
#include <tonemapping_fragment>`);
  skyScene.add(sky2);
  const groundL = 0.2 * sunI * Math.sin(hClamp) / Math.PI / envI * qnum("bounce", 1.0);
  const disk = new THREE.Mesh(new THREE.CircleGeometry(50, 32),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(groundL * 1.05, groundL, groundL * 0.88) }));
  disk.rotation.x = -Math.PI / 2; disk.position.y = -0.3;
  skyScene.add(disk);
  const pm = new THREE.PMREMGenerator(renderer);
  const envRT = pm.fromScene(skyScene, 0, 0.1, 100);
  scene.environment = envRT.texture;
  scene.environmentIntensity = envI;
  pm.dispose();

  // 3) 霞：地平線の空の色を測って、その色へ距離で溶かす（FogExp2）。空と遠くの棟の境目が消える
  const fogColor = (() => {
    const rt = new THREE.WebGLRenderTarget(4, 4, { type: THREE.FloatType });
    const cam = new THREE.PerspectiveCamera(20, 1, 0.1, 100);
    const px = new Float32Array(4 * 16), sum = new THREE.Vector3();
    const fogScene = new THREE.Scene(), sky3 = makeSky();             // 見えている空（青みを落とす前）で測る
    sky3.material.fragmentShader = sky.material.fragmentShader; sky3.scale.setScalar(10); fogScene.add(sky3);
    const tm = renderer.toneMapping; renderer.toneMapping = THREE.NoToneMapping;
    for (let i = 0; i < 4; i++) {                                     // 太陽から 45°・135°・225°・315° の 4 方向、仰角 2°
      const a = az + (45 + 90 * i) * D2R;
      cam.position.set(0, 0, 0);
      cam.lookAt(Math.sin(a), Math.tan(2 * D2R), -Math.cos(a));
      renderer.setRenderTarget(rt); renderer.render(fogScene, cam);
      renderer.readRenderTargetPixels(rt, 0, 0, 4, 4, px);
      for (let k = 0; k < 16; k++) sum.add(new THREE.Vector3(px[4 * k], px[4 * k + 1], px[4 * k + 2]));
    }
    renderer.setRenderTarget(null); renderer.toneMapping = tm; rt.dispose(); sky3.material.dispose();
    sum.divideScalar(64);
    return Number.isFinite(sum.x) && sum.length() > 0 ? new THREE.Color(sum.x, sum.y, sum.z) : new THREE.Color(0xc9d6e2);
  })();
  scene.fog = new THREE.FogExp2(fogColor, qnum("haze", 0.0004));      // 300 m で 1%、600 m で 6%、1000 m で 15% 霞む（11/6 に 0.0009 から半分以下へ。俯瞰で空と遠景が白く溶けたため）

  // 4) 太陽の光と影：旧の平行光は消し、CSM の段ごとの平行光に置き換える（近くは細かい影、遠くまで影が出る）
  opt.sun.visible = false; opt.sun.castShadow = false;
  const sunColor = new THREE.Color().setHSL(0.09, low ? 0.75 : 0.35, low ? 0.72 : 0.92);   // 夕方は橙寄り
  const csm = new CSM({
    camera, parent: scene,
    cascades: mobile ? 2 : 3,
    maxFar: mobile ? 160 : 450,
    shadowMapSize: mobile ? 1024 : 2048,
    mode: "practical",
    lightDirection: sunVec.clone().negate(),
    lightIntensity: sunI,
    lightMargin: 250, lightFar: 1500,
  });
  csm.fade = true;
  for (const l of csm.lights) { l.color.copy(sunColor); l.shadow.bias = -0.0003; l.shadow.normalBias = 0.05; }
  // CSM は素材の onBeforeCompile を上書きするので、既存の差し込み（地面のマスク・仮説の縞）と鎖にする。
  // プログラムの使い回しの鍵は元の差し込みの文字列＋印にして、別の差し込み同士が同じシェーダーを共有しないようにする
  const done = new WeakSet();
  const lit = (m) => m && (m.isMeshStandardMaterial || m.isMeshLambertMaterial || m.isMeshPhongMaterial);
  function prepMat(m) {
    if (!lit(m) || done.has(m)) return;
    done.add(m);
    const prev = m.onBeforeCompile, key = m.customProgramCacheKey();
    csm.setupMaterial(m);
    const mine = m.onBeforeCompile;
    m.onBeforeCompile = function (sh, r) { prev.call(this, sh, r); mine.call(this, sh, r); };
    m.customProgramCacheKey = () => key + "|csm";
    m.needsUpdate = true;
  }
  function prepare(root = scene) {
    root.traverse((o) => { if (o.isMesh) for (const m of [].concat(o.material)) prepMat(m); });
    for (const m of opt.materials || []) prepMat(m);
  }
  prepare();

  // 影の代役：影を落とす不透明な物（glb の約 6000 個の部品と PLATEAU の箱）を世界座標で 1 つの形に束ね、
  // 影の描画（CSM の段ごと）と GTAO の下絵はこの 1 つで済ませる。元の部品は画面の描画だけ（castShadow を外す）。
  // 束ねないと段ごとに部品の数だけ描画命令が出て、PC でも 1 枚 200 ms を超えた（11/6 の計測）。
  // 代役はレイヤー 1 にだけ置く（画面のカメラには映らない。影のカメラと GTAO の時だけ見る）
  const PROXY_LAYER = 1;
  const casters = [];
  const proxy = (() => {
    scene.updateMatrixWorld(true);
    let nv = 0, ni = 0;
    scene.traverse((o) => {
      if (!o.isMesh || o.isInstancedMesh || o.isSkinnedMesh || !o.castShadow || !o.geometry.attributes.position) return;
      for (let p = o; p; p = p.parent) if (!p.visible) return;
      if ([].concat(o.material).some((m) => m.transparent)) return;
      casters.push(o);
      const g = o.geometry; nv += g.attributes.position.count; ni += g.index ? g.index.count : g.attributes.position.count;
    });
    const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3), idx = new Uint32Array(ni);
    const v = new THREE.Vector3(), n = new THREE.Vector3(), nm = new THREE.Matrix3();
    let vo = 0, io = 0;
    for (const o of casters) {
      const g = o.geometry, P = g.attributes.position, N = g.attributes.normal;
      nm.getNormalMatrix(o.matrixWorld);
      for (let i = 0; i < P.count; i++) {
        v.fromBufferAttribute(P, i).applyMatrix4(o.matrixWorld); v.toArray(pos, (vo + i) * 3);
        if (N) { n.fromBufferAttribute(N, i).applyMatrix3(nm).normalize(); n.toArray(nor, (vo + i) * 3); }
      }
      if (g.index) for (let i = 0; i < g.index.count; i++) idx[io + i] = g.index.getX(i) + vo;
      else for (let i = 0; i < P.count; i++) idx[io + i] = i + vo;
      vo += P.count; io += g.index ? g.index.count : P.count;
      o.castShadow = false;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    geo.setIndex(new THREE.BufferAttribute(idx, 1));
    geo.computeBoundingSphere(); geo.computeBoundingBox();
    const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false }));
    m.material.shadowSide = THREE.DoubleSide;           // 庇や板（厚みの無い面）も影を落とす
    m.castShadow = true; m.receiveShadow = false;
    m.layers.set(PROXY_LAYER);
    m.name = "shadow_proxy";
    scene.add(m);
    return m;
  })();
  for (const l of csm.lights) l.shadow.camera.layers.enable(PROXY_LAYER);

  // 5) 後処理（PC）：GTAO の接触影 → トーンマップ → SMAA。スマホは後処理なし（端末の MSAA だけ）
  let composer = null, gtao = null, smaa = null;
  const usePost = !mobile && Q.get("post") !== "0";
  if (usePost) {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    if (Q.get("ao") !== "0") {
      gtao = new GTAOPass(scene, camera, 1, 1);
      gtao.updateGtaoMaterial({ radius: 1.6, distanceExponent: 1.5, thickness: 2.0, scale: 1.0, samples: 12 });
      gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 12 });
      gtao.blendIntensity = qnum("ao", 0.85);
      composer.addPass(gtao);
    }
    composer.addPass(new OutputPass());
    smaa = new SMAAPass(1, 1);
    composer.addPass(smaa);
  }
  // GTAO の下絵（法線と深さ）は影の代役＋地面で描く：元の部品・透けるガラス・根拠の重ね板・空は隠す
  //（ガラスの裏に偽の暗がりが出ない。描画命令も 1 つ＋α）。元に戻すのは GTAOPass 自身の restoreVisibility
  if (gtao) {
    const ov = gtao.overrideVisibility.bind(gtao), rv = gtao.restoreVisibility.bind(gtao);
    gtao.overrideVisibility = function () {
      ov();
      for (const o of casters) o.visible = false;
      scene.traverse((o) => { if (o.isMesh && [].concat(o.material).some((m) => m.transparent)) o.visible = false; });
      sky.visible = false;
      camera.layers.enable(PROXY_LAYER);
    };
    gtao.restoreVisibility = function () { rv(); camera.layers.disable(PROXY_LAYER); };
  }

  function resize(w, h) {
    csm.updateFrustums();
    if (composer) { composer.setPixelRatio(renderer.getPixelRatio()); composer.setSize(w, h); }
  }

  // 自動で切る（スマホ・幅 768 px 未満の時）：起動後 5 秒ごとの平均 FPS が 30 を割る間、重い順に 1 つずつ切る。
  // ?auto=0 で止める、?auto=1 で PC でも切る
  const cut = [];
  const ladder = [
    ["接触影（GTAO）", () => { if (!gtao) return false; gtao.enabled = false; return true; }],
    ["後処理（SMAA ほか）", () => { if (!composer) return false; composer = null; return true; }],
    ["画素密度 1.0", () => { if (renderer.getPixelRatio() <= 1) return false; renderer.setPixelRatio(1); opt.onResize && opt.onResize(); return true; }],
    ["影の段を 1 段に", () => { if (csm.cascades <= 1) return false;
      for (const l of csm.lights.slice(1)) { l.castShadow = false; l.visible = false; }
      csm.maxFar = 60; csm.updateFrustums(); return true; }],
  ];
  const meter = { n: 0, t0: 0, warm: 0, last: null };
  function autoCut(now) {
    if (Q.get("auto") === "0" || (!mobile && Q.get("auto") !== "1")) return;
    if (!meter.t0) { meter.t0 = now; meter.warm = now + 1500; return; }
    if (now < meter.warm) { meter.t0 = now; meter.n = 0; return; }
    meter.n++;
    if (now - meter.t0 < 5000) return;
    const fps = meter.n * 1000 / (now - meter.t0);
    meter.last = fps; meter.n = 0; meter.t0 = now;
    if (document.visibilityState !== "visible" || fps >= 30) return;
    for (const [name, f] of ladder) if (!cut.includes(name) && f()) { cut.push(name); console.info("見た目：FPS", fps.toFixed(1), "→", name, "を切った"); break; }
  }

  function render() {
    camera.updateMatrixWorld();
    csm.update();
    if (composer) composer.render(); else renderer.render(scene, camera);
    autoCut(performance.now());
  }

  const info = {
    t: hour, doy, sunAltDeg: +(h / D2R).toFixed(1), sunAzDeg: +(az / D2R).toFixed(1),
    cascades: csm.cascades, shadowMapSize: csm.shadowMapSize, post: usePost, ao: !!gtao, mobile, cut, meter,
    fog: "#" + fogColor.getHexString(), casters: casters.length, proxyTris: proxy.geometry.index.count / 3,
  };
  return { render, resize, prepare, info, csm, get composer() { return composer; } };
}
