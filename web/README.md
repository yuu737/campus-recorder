# キャンパスを歩く（Web 3D）
1. `.venv/Scripts/python -I -X utf8 -m recorder site export-web` で `web/data/`（site.json と模型 glb）を作る
2. `cd web` して `python -m http.server 8000`
3. ブラウザで http://localhost:8000/ を開く（スマホは同じ Wi-Fi から PC の IP で）
- PC：WASD で移動、ドラッグで向き、Shift で速く、棟のクリックで札
- スマホ：左のスティックで移動、右をドラッグで向き、棟のタップで札
- three.js 0.170.0 を jsdelivr から読む（要ネット）。写真は含まない
- 色付き＝根拠付きの棟（模型は Blender の glb）、灰色＝PLATEAU の箱（未測）

## 地面の二重化を避ける
模型の glb には 20 m の地形の面（mesh 名 `terrain`）が入るが、`index.html` は読み込み時に名前が `terrain` で始まる物を非表示にする。歩く画面の地面は `site.json` の格子（キャンパス全体）だけ。

## 手元で開く手順
`web/` で `python -m http.server 8000` を実行し、ブラウザで `http://localhost:8000/` を開く（`file://` では glb が読めない）。データの作り直しは `recorder site export-web`。

## 光と質感（11/4。Blender の描画に合わせる）
- 空と環境光は CC0 の HDRI（`hdri_overcast`、export-web が `web/data/env/` に写す）を PMREM で起動時 1 回。太陽は Blender と同じ向きの平行光で影あり（2048 px、視点のまわり ±90 m）。トーンマッピングは Neutral（Blender の Khronos PBR Neutral と同じ式。`?tm=aces` で ACES）
- 外壁のタイルは Blender のノードの模様なので、glb 書き出しの直前に周期の板へ焼いた画像（`out/<id>/tex/`）を glb に入れる。地面は CC0 の舗装の質感（`web/data/tex/`）。芝・舗装の区分は根拠が無いので塗り分けない
- 比較用：`?cam=x,y,z,yaw,pitch,fov&size=1400x1000&ui=0`（x=東, y=北, z=標高 m、yaw は北から時計回り°、縦の画角°）。値は `out/<id>/model_report.json` の `web_cameras`
- 調整用：`exp=`（露出）、`env=`（環境光の強さ、既定 0.8）、`sun=`（太陽、既定 2.2＝Blender と同じ）
