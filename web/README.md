# キャンパスを歩く（Web 3D）
1. `.venv/Scripts/python -I -X utf8 -m recorder site export-web` で `web/data/`（site.json と模型 glb）を作る
2. `cd web` して `python -m http.server 8000`
3. ブラウザで http://localhost:8000/ を開く（スマホは同じ Wi-Fi から PC の IP で）
- PC：WASD で移動、ドラッグで向き、Shift で速く、棟のクリックで札
- スマホ：左のスティックで移動、右をドラッグで向き、棟のタップで札
- three.js 0.170.0 を jsdelivr から読む（要ネット）。写真は含まない
- 色付き＝根拠付きの棟（模型は Blender の glb）、灰色＝PLATEAU の箱（未測）
