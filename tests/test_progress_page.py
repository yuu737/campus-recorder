"""待機画面 web/progress.html（11/8、F8）：参照先があること、固有名を埋め込んでいないこと。"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "progress.html"
JS = ROOT / "web" / "progress.js"


def test_local_references_exist():
    html = HTML.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"#:]+?)(?:\?[^"]*)?"', html)
    local = [r for r in refs if not r.startswith(("http", "//"))]
    assert "progress.js" in local
    for r in local:
        assert (HTML.parent / r).exists(), r
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert all(e.startswith("https://fonts.g") for e in ext), ext      # 外から読むのは字だけ（three.js などは要らない）


def test_api_paths_used_by_page_are_served():
    js = JS.read_text(encoding="utf-8")
    serve_src = (ROOT / "recorder" / "serve.py").read_text(encoding="utf-8")
    for api in set(re.findall(r'"/api/([a-z/]+)', js)) | set(re.findall(r'`/api/([a-z/]+)', js)):
        assert f'u.path == "/api/{api}"' in serve_src, api
    staff = (ROOT / "web" / "staff.html").read_text(encoding="utf-8")
    assert "progress.html?building=" in staff and "&live=1" in staff and "&replay=1" in staff


def _names():
    """データにある固有名（大学・キャンパス・棟の名前と id、面の名前、写真 id の頭）。"""
    out = set()
    for p in (ROOT / "data" / "site").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        for k in ("university", "campus_name"):
            if isinstance(d.get(k), str) and len(d[k]) >= 2:
                out.add(d[k])
        for t in (d.get("scope") or {}).get("targets") or []:
            out.add(t)
    for p in (ROOT / "data" / "buildings").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.add(p.stem)
        if d.get("name"):
            out.add(d["name"])
    out |= {"名城", "天白", "PXL_"}
    return {n for n in out if n}


def test_no_proper_names_in_page():
    text = HTML.read_text(encoding="utf-8") + JS.read_text(encoding="utf-8")
    hits = [n for n in _names() if n in text]
    assert not hits, f"固有名を埋め込んでいる: {hits}"
    assert not re.search(r'["\'`]f\d+["\'`]', text), "面の id を埋め込んでいる"


def test_header_links_and_masks_on_derived_images():
    html = HTML.read_text(encoding="utf-8")
    assert 'href="staff.html"' in html and 'href="hub.html"' in html
    assert (HTML.parent / "hub.html").exists()
    js = JS.read_text(encoding="utf-8")
    # 元の写真から作った絵は、箱を写せた物だけ（正面化・製図板）。箱の色は paint_masks と同じ
    assert "c.masks_ok" in js and "c => c.masks_ok" in js and "#8a8a8a" in js
