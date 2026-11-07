"""顔・ナンバーの自動の箱と塗りつぶし（recorder/privacy.py）、取り込みで作る係に見せる縮小版 send_file（11/8、K-104）。"""

import hashlib
import json

import pytest
from PIL import Image

from recorder import photos, privacy


def _jpg(path, size=(2400, 1800), color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=95)
    return path


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_cells_to_box():
    assert privacy.cells_to_box("C3:E5") == [0.2, 0.2, 0.5, 0.5]
    assert privacy.cells_to_box("E5:C3") == [0.2, 0.2, 0.5, 0.5]
    assert privacy.cells_to_box("A1") == [0.0, 0.0, 0.1, 0.1]
    assert privacy.cells_to_box("J10") == [0.9, 0.9, 1.0, 1.0]
    for bad in ("K1", "A11", "A0", "", "C3:E5:F6", "3C"):
        with pytest.raises(ValueError):
            privacy.cells_to_box(bad)


def test_paint_masks_gray_box_with_label_outside_unchanged():
    im = Image.new("RGB", (1000, 800), (200, 30, 30))
    out = privacy.paint_masks(im, [{"kind": "poster", "box": [0.1, 0.1, 0.5, 0.5], "by": "hogo"}])
    assert im.getpixel((150, 120)) == (200, 30, 30)          # 元の画像はそのまま
    assert out.getpixel((102, 82)) == privacy.GRAY            # 箱の角は灰色
    assert out.getpixel((700, 700)) == (200, 30, 30)          # 箱の外は変えない
    inside = [out.getpixel((x, y)) for x in range(100, 500, 7) for y in range(80, 400, 7)]
    assert any(p == (255, 255, 255) for p in inside)          # 理由の文字（白）が入っている
    assert all(p in (privacy.GRAY, (255, 255, 255)) or max(abs(a - b) for a, b in zip(p, privacy.GRAY)) < 128 for p in inside)


def test_apply_crop():
    im = Image.new("RGB", (1000, 800))
    assert privacy.apply_crop(im, privacy.cells_to_box("A1:H10")).size == (800, 800)


def test_plates_are_unsupported_and_status_is_recorded(monkeypatch):
    monkeypatch.setattr(privacy, "detect_faces", lambda im, model=None, score=None: [])
    masks, st = privacy.auto_masks(Image.new("RGB", (100, 80)))
    assert masks == [] and st["plates"] == "unsupported" and st["faces"] == privacy.YUNET_ID and st["n_faces"] == 0


def test_make_send_without_model_warns_and_keeps_original(tmp_path):
    src = _jpg(tmp_path / "orig" / "A.jpg")
    before = _sha(src)
    res = privacy.make_send(src, tmp_path / "send" / "A.jpg", model=tmp_path / "none.onnx")
    out = Image.open(tmp_path / "send" / "A.jpg")
    assert max(out.size) == 1600 and not out.getexif()
    assert res["masks"] == [] and res["auto_mask"]["faces"] == "no_model" and res["warnings"]
    assert _sha(src) == before


@pytest.mark.skipif(not privacy.YUNET_PATH.exists(), reason="YuNet のモデルが無い（scripts/fetch_yunet.py）")
def test_yunet_runs_on_a_blank_wall():
    faces = privacy.detect_faces(Image.new("RGB", (1600, 1200), (180, 170, 160)))
    assert faces == []


def test_import_makes_send_file_with_auto_boxes(tmp_path, monkeypatch):
    """取り込みで send/<id>.jpg を作り、自動の箱を masks（by auto）に残す。元の写真は書き換えない。"""
    fake = [{"kind": "face", "box": [0.4, 0.4, 0.5, 0.55], "by": "auto", "score": 0.9, "model": privacy.YUNET_ID}]
    monkeypatch.setattr(privacy, "detect_faces", lambda im, model=None, score=None: [dict(f) for f in fake])
    root = tmp_path / "data" / "photos"
    src = _jpg(root / "20261108" / "honbu" / "P1.jpg")
    before = _sha(src)
    idx_path = root / "index.json"
    idx, added = photos.import_photos(root / "20261108", None, index_path=idx_path, root=root, period="during")
    rec = idx["photos"][0]
    assert rec["send_file"] == "send/P1.jpg" and (root / "send" / "P1.jpg").exists()
    assert rec["masks"] == fake and rec["auto_mask"]["plates"] == "unsupported" and rec["auto_mask"]["n_faces"] == 1
    sent = Image.open(root / "send" / "P1.jpg")
    w, h = sent.size
    assert sent.getpixel((int(0.45 * w), int(0.42 * h)))[0] < 160   # 顔の箱は灰色（元は赤 200）
    assert _sha(src) == before
    # 係が指した箱は、取り込みをやり直しても残る（auto だけ置き換え）
    doc = json.loads(idx_path.read_text(encoding="utf-8"))
    doc["photos"][0]["masks"].append({"kind": "poster", "box": [0, 0, 0.1, 0.1], "by": "hogo"})
    idx_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    res = photos.make_sends(idx_path, force=True)
    got = json.loads(idx_path.read_text(encoding="utf-8"))["photos"][0]
    assert res["made"] == 1 and [m["by"] for m in got["masks"]] == ["auto", "hogo"]


def test_import_send_can_be_turned_off(tmp_path):
    root = tmp_path / "p"
    _jpg(root / "d" / "Q.jpg", size=(400, 300))
    idx, _ = photos.import_photos(root / "d", None, index_path=root / "index.json", root=root, period="during", send=False)
    assert "send_file" not in idx["photos"][0] and not (root / "send").exists()
