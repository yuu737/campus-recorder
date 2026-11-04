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
