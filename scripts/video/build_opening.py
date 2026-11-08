"""提出動画の冒頭（2 カット、合計 20 秒前後）の初版を作る（11/8、動画係 Opus。最初は 45 秒で作り、方針変更で詰めた）。

入口：python -I -X utf8 scripts/video/build_opening.py [--skip-tts]

1. 仮のナレーション（Windows の合成音声 Microsoft Haruka Desktop）を 1 文ずつ wav にし、カットごとにつなぐ
   → out/video/opening_v1/narration/
2. wav の長さ（ffprobe）から各文の出る時刻を決める（字幕は 1 秒 4.5 文字より速くしない）
3. Pillow で 1920×1080・30 fps のフレームを描き、ffmpeg に流して H.264＋AAC
   → out/video/opening_v1.mp4
4. 要所の静止画 → out/video/opening_v1/frames/

写真は data/photos/public/ の公開版だけ（索引の public_file）。GPU は使わない（Edge・Blender を起こさない）ので鍵 gpu は取らない。
字体は Windows に入っている BIZ UDPGothic（BIZ-UDGothicB.ttc の 2 番目）。ダウンロードしない。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "out" / "video"
WORK = OUT / "opening_v1"
NARR = WORK / "narration"
FRAMES = WORK / "frames"
PHOTOS = REPO / "data" / "photos"

W, H, FPS = 1920, 1080, 30
BG = (16, 16, 18)
FG = (245, 245, 242)
ACCENT = (232, 163, 61)      # 差し色 1 色（キャンパスのタイルに寄せた橙）
FONT_FILE = "C:/Windows/Fonts/BIZ-UDGothicB.ttc"
FONT_INDEX = 1               # BIZ UDPGothic Bold
SR = 44100

# 写真：索引の id → 公開版。選んだ理由は opening_v1.md
PHOTO = {
    "roof": "本部棟屋上",                       # 上から見た本部棟と周り（塗りつぶし無し）
    "tape": "本部棟側面柱74.5cm",                # メジャーを当てる手（塗りつぶし無し）
}

# 文：字幕（画面）と読み（合成音声が読み違えないための仮名）。字幕の文は勝ち筋 §3 のまま
LINES = [
    # cut, id, 字幕, 読み
    (1, "c1_s1", "「地方の受験生は、\n志望校を見ないまま決めている」", "地方の受験生は、志望校を見ないまま決めている。"),
    (1, "c1_s2", "自分もそうだった。", "自分も、そうだった。"),
    (2, "c2_s1", "それには2か月と専門知識が要る", "それには、にかげつと、専門知識が要る。"),
    (2, "c2_s2", "大学の広報には\n予算も専門家もいない", "大学の広報には、予算も、専門家も、いない。"),
]

# 各文の前の間（秒）と、最後の文の後の余韻。11/8 の方針変更で冒頭は合計 20 秒前後（各カット約 10 秒、文は 2 つずつ）
LEAD = {"c1_s1": 0.7, "c1_s2": 0.5, "c2_s1": 0.6, "c2_s2": 0.7}
TAIL = {1: 0.9, 2: 1.2}
CUT_MIN = {1: 9.5, 2: 10.0}


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_FILE, size, index=FONT_INDEX)


def public_path(pid: str) -> Path:
    idx = json.loads((PHOTOS / "index.json").read_text(encoding="utf-8"))
    for p in idx["photos"]:
        if p["id"] == pid:
            assert p.get("period") == "during", pid
            assert p.get("public") is not False and p.get("public_file"), pid
            return PHOTOS / p["public_file"]
    raise KeyError(pid)


# ---------- 1. ナレーション ----------
def tts(text: str, path: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.SelectVoice('Microsoft Haruka Desktop');$s.Rate=-1;"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak([IO.File]::ReadAllText('{path}.txt',[Text.Encoding]::UTF8));$s.Dispose()"
    )
    Path(str(path) + ".txt").write_text(text, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    Path(str(path) + ".txt").unlink()


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)


def write_wav(path: Path, a: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.clip(a, -32768, 32767).astype(np.int16).tobytes())


def ffprobe_len(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def build_narration(skip: bool) -> list[dict]:
    NARR.mkdir(parents=True, exist_ok=True)
    tl, t, cut_start = [], 0.0, {}
    for cut in (1, 2):
        cut_start[cut] = t
        start = t
        audio = []
        for c, lid, sub, yomi in LINES:
            if c != cut:
                continue
            p = NARR / f"{lid}.wav"
            if not skip or not p.exists():
                tts(yomi, p)
            dur = ffprobe_len(p)
            t += LEAD[lid]
            chars = len(sub.replace("\n", "").replace("「", "").replace("」", ""))
            show = max(dur, chars / 4.5)
            tl.append({"cut": cut, "id": lid, "sub": sub, "t0": round(t, 3), "speech": round(dur, 3),
                       "chars": chars, "show_min": round(show, 3)})
            audio.append((t - start, read_wav(p)))
            t += show
        t += TAIL[cut]
        t = max(t, start + CUT_MIN[cut])
        mix = np.zeros(int((t - start) * SR) + 1, np.float32)
        for off, a in audio:
            i = int(off * SR); mix[i:i + len(a)] += a
        write_wav(NARR / f"cut{cut}.wav", mix)
        for e in tl:
            if e["cut"] == cut:
                e["cut_t0"], e["cut_t1"] = round(start, 3), round(t, 3)
    # 文が消える時刻＝次の文が出る少し前（同じカットの中）
    for i, e in enumerate(tl):
        nxt = tl[i + 1] if i + 1 < len(tl) and tl[i + 1]["cut"] == e["cut"] else None
        e["t1"] = round((nxt["t0"] - 0.35) if nxt else (e["cut_t1"] - 0.6), 3)
    full = np.concatenate([read_wav(NARR / "cut1.wav"), read_wav(NARR / "cut2.wav")])
    write_wav(WORK / "narration_all.wav", full)
    (WORK / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    return tl


# ---------- 2. 絵 ----------
def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class Photo:
    """写真を画面いっぱいに置き、静かに寄る（拡大は 1.00→1.06）。"""

    def __init__(self, pid: str, focus=(0.5, 0.5)):
        im = Image.open(public_path(pid)).convert("RGB")
        # 画面を覆う大きさの 1.06 倍まで一度だけ高画質で拡大しておく
        s = max(W / im.width, H / im.height) * 1.06
        self.im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        self.focus = focus

    def frame(self, k: float) -> Image.Image:
        z = 1.06 - 0.06 * (1 - k)          # 画面に見える範囲の倍率：寄るほど狭く切る
        cw = min(W * 1.06 / z, self.im.width); ch = min(H * 1.06 / z, self.im.height)
        fx = self.focus[0] * self.im.width; fy = self.focus[1] * self.im.height
        x0 = min(max(fx - cw / 2, 0), self.im.width - cw); y0 = min(max(fy - ch / 2, 0), self.im.height - ch)
        return self.im.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))


def darken(im: Image.Image, a: float) -> Image.Image:
    return Image.blend(im, Image.new("RGB", (W, H), BG), a) if a > 0 else im


def bottom_shade(im: Image.Image) -> Image.Image:
    """字幕の下地：下 38% を下へ向かって暗く（帯の形を出さない）。"""
    arr = np.asarray(im).astype(np.float32)
    y = np.linspace(0, 1, H)[:, None, None]
    m = np.clip((y - 0.55) / 0.45, 0, 1) ** 1.2 * 0.78
    arr = arr * (1 - m) + np.array(BG, np.float32) * m
    return Image.fromarray(arr.astype(np.uint8))


def draw_text(im: Image.Image, text: str, size: int, cx: float, cy: float, reveal: float = 1.0,
              accent: tuple[str, ...] = (), alpha: float = 1.0, shadow: bool = True) -> None:
    """中央揃えで描く。reveal は 0..1 で左から 1 文字ずつ出す（行をまたいで順に）。"""
    f = font(size)
    lines = text.split("\n")
    lh = int(size * 1.42)
    total = sum(len(l) for l in lines)
    shown = reveal * total
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = cy - lh * len(lines) / 2
    n = 0
    for line in lines:
        lw = d.textlength(line, font=f)
        x = cx - lw / 2
        # 差し色にする語の位置
        col = [FG] * len(line)
        for w in accent:
            i = line.find(w)
            if i >= 0:
                col[i:i + len(w)] = [ACCENT] * len(w)
        for j, ch in enumerate(line):
            a = min(max(shown - n, 0.0), 1.0) * alpha
            n += 1
            if a > 0:
                if shadow:
                    d.text((x + 3, y + 4), ch, font=f, fill=(0, 0, 0, int(150 * a)))
                d.text((x, y), ch, font=f, fill=col[j] + (int(255 * a),))
            x += d.textlength(ch, font=f)
        y += lh
    im.paste(layer, (0, 0), layer)


def label(im: Image.Image, text: str, alpha: float) -> None:
    if alpha <= 0:
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font(44)
    d.rectangle((120, 98, 128, 150), fill=ACCENT + (int(255 * alpha),))
    d.text((148, 96), text, font=f, fill=ACCENT + (int(255 * alpha),))
    im.paste(layer, (0, 0), layer)


def render(tl: list[dict]):
    by = {e["id"]: e for e in tl}
    T = tl[-1]["cut_t1"]
    c2 = by["c2_s1"]["cut_t0"]
    ph = {k: Photo(v, f) for (k, v), f in zip(PHOTO.items(), [(0.5, 0.45), (0.4, 0.75)])}

    def sub_alpha(e, t):
        return ease((t - e["t0"] + 0.05) / 0.3) * (1 - ease((t - e["t1"]) / 0.3))

    def reveal(e, t):
        # 読む速さに合わせて 1 文字ずつ（話す長さの 85% で全部出る）
        return (t - e["t0"]) / max(e["speech"] * 0.85, 0.4)

    def frame(t: float) -> Image.Image:
        if t < c2:
            # ---- カット 1 ----
            e1, e2 = by["c1_s1"], by["c1_s2"]
            a = ph["roof"].frame(t / c2)
            # 冒頭は暗く、問いの文が強く読める。問いが終わると明るくする
            dk = 0.62 * (1 - ease((t - e1["t1"]) / 0.8))
            a = darken(a, dk)
            a = Image.blend(Image.new("RGB", (W, H), BG), a, ease(t / 1.0))   # 黒から出る
            a = bottom_shade(a)
            if t < e1["t1"] + 0.4:
                draw_text(a, e1["sub"], 76, W / 2, H / 2, reveal(e1, t), alpha=sub_alpha(e1, t))
            if e2["t0"] - 0.1 < t < e2["t1"] + 0.4:
                draw_text(a, e2["sub"], 64, W / 2, H - 175, reveal(e2, t), alpha=sub_alpha(e2, t))
        else:
            # ---- カット 2 ----
            e1, e2 = by["c2_s1"], by["c2_s2"]
            ts = t - c2
            if t < e2["t0"] - 0.5:
                a = ph["tape"].frame((t - c2) / (e2["t0"] - c2))
                a = bottom_shade(darken(a, 0.15))
            else:
                # 予算も専門家もいない：写真を出さず、言葉だけ（暗い地）
                a = Image.new("RGB", (W, H), BG)
            # 場面の切り替えは短い暗転をはさむ
            for edge in (c2, e2["t0"] - 0.5):
                d = abs(t - edge)
                if d < 0.35:
                    a = darken(a, 1 - d / 0.35)
            if t > T - 1.0:
                a = darken(a, ease((t - (T - 1.0)) / 1.0))
            label(a, "問題", ease((ts - 0.4) / 0.5) * (1 - ease((t - (T - 1.0)) / 0.6)))
            if e1["t0"] - 0.1 < t < e1["t1"] + 0.4:
                draw_text(a, e1["sub"], 64, W / 2, H - 175, reveal(e1, t), accent=("2か月",), alpha=sub_alpha(e1, t))
            if e2["t0"] - 0.1 < t < e2["t1"] + 0.4:
                draw_text(a, e2["sub"], 84, W / 2, H / 2, reveal(e2, t), accent=("予算", "専門家"), alpha=sub_alpha(e2, t), shadow=False)
        return a

    return frame, T


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tts", action="store_true", help="wav が既にあれば作り直さない")
    ap.add_argument("--stills-only", action="store_true")
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True); FRAMES.mkdir(parents=True, exist_ok=True)
    tl = build_narration(args.skip_tts)
    frame, T = render(tl)
    n = int(round(T * FPS))
    print(f"尺 {T:.2f} 秒（{n} フレーム）", flush=True)
    for e in tl:
        print(f"  {e['id']}: {e['t0']:6.2f}〜{e['t1']:6.2f} 話す {e['speech']:.2f} 秒 「{e['sub'].replace(chr(10), '')}」")
    stills = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
    for s in stills:
        if s < T:
            frame(s + 0.01).save(FRAMES / f"t{s:02d}.png")
    if args.stills_only:
        return
    mp4 = OUT / "opening_v1.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(WORK / "narration_all.wav"),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", str(mp4)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(frame(i / FPS).tobytes())
        if i % 150 == 0:
            print(f"  {i}/{n}", flush=True)
    p.stdin.close()
    if p.wait() != 0:
        sys.exit("ffmpeg が失敗")
    print("書き出し", mp4, f"{ffprobe_len(mp4):.2f} 秒")


if __name__ == "__main__":
    main()
