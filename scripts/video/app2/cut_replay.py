"""待機画面の再生（out/video/replay_honbu.mp4）の区間を 10 fps の連番 PNG にする（小窓で動画として流す F.miniVideo 用）。

    python -I -X utf8 scripts/video/app2/cut_replay.py <場面> <始め秒> <終わり秒>
    → out/video/app2/_src/replay_<場面>/f0001.png …（values.json の app.video = {dir, fps:10, n} に書く値を表示）
"""
import shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
n, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
out = ROOT / "out" / "video" / "app2" / "_src" / f"replay_{n}"
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)
ff = shutil.which("ffmpeg") or "ffmpeg"
subprocess.run([ff, "-v", "error", "-ss", f"{a}", "-t", f"{b - a}", "-i", str(ROOT / "out/video/replay_honbu.mp4"), "-vf", "fps=10",
                str(out / "f%04d.png")], check=True)
k = len(list(out.glob("f*.png")))
print(f'"video": {{"dir": "{out.relative_to(ROOT).as_posix()}", "fps": 10, "n": {k}}}')
