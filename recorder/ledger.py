"""共有の台帳と GPU の鍵（11/7。審査のコードレビュー A7 の直し）。

4 棟を同時に回すと、写真の索引（data/photos/index.json）・登録台帳（data/photos/registry.json）・
歩く画面の site.json を、別々のプロセスが「読む → 書き換える → 保存」する。鍵が無いと、後から保存した方が
先の書き込みを黙って消す。GPU も 1 枚なので、Blender・headless の Edge を同時に走らせると落ちる。

- `lock("ledger")`：台帳を書くコマンドは、読む所から保存する所まで、この鍵を持つ（同時に 1 つ）。
- `lock("gpu")`：Blender と headless の Edge は、この鍵を持って 1 つずつ走る。
- 鍵はファイルの鍵（Windows は msvcrt、他は fcntl）。プロセスが落ちると OS が外すので、残った鍵で止まらない。
  同じプロセスの中では入れ子にできる（同じスレッドなら 2 回目はすぐ通る。別のスレッドは待つ）。
- 鍵の順は ledger → gpu。gpu を持ったまま ledger を取ろうとしたら、互いに待ち続けないようにエラーにする。
- 待った秒数は `out/.locks/waits.jsonl` に 1 行ずつ残す（疑似本番・本番の時間の記録で「仕事」と「待ち」を分けるため）。
- `write_text_atomic`：一時ファイルに書いてから置き換える。途中で落ちても台帳が半分だけ書かれた形にならない。

置き場所は環境変数 `RECORDER_LOCK_DIR`（既定は <リポジトリ>/out/.locks。git 管理外）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORDER = ("ledger", "gpu")          # 鍵を取る順。後ろの鍵を持ったまま前の鍵は取らない

_guard = threading.Lock()
_state: dict[str, dict] = {}       # 鍵の名前 → {"rlock", "depth", "fh", "owner"}
_held = threading.local()          # このスレッドが持っている鍵の名前


def lock_dir() -> Path:
    return Path(os.environ.get("RECORDER_LOCK_DIR") or (REPO / "out" / ".locks"))


def _st(name: str) -> dict:
    with _guard:
        return _state.setdefault(name, {"rlock": threading.RLock(), "depth": 0, "fh": None})


def _try_lock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _log_wait(name: str, label: str, waited: float) -> None:
    try:
        d = lock_dir()
        d.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.now().isoformat(timespec="seconds"), "lock": name, "label": label,
               "waited_s": round(waited, 2), "pid": os.getpid()}
        with open(d / "waits.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def held() -> list[str]:
    return list(getattr(_held, "names", []))


@contextmanager
def lock(name: str = "ledger", *, label: str = "", timeout: float | None = None, poll: float = 0.2):
    """名前の付いた鍵を取る（プロセスをまたいで 1 つ）。timeout 秒で取れなければ TimeoutError（None は待ち続ける）。"""
    mine = held()
    if name in ORDER:
        later = [h for h in mine if h in ORDER and ORDER.index(h) > ORDER.index(name)]
        if later and name not in mine:
            raise RuntimeError(f"鍵の順の違反：{later} を持ったまま {name} を取ろうとした（順は {' → '.join(ORDER)}）")
    st = _st(name)
    t0 = time.time()
    st["rlock"].acquire()
    try:
        st["depth"] += 1
        if st["depth"] == 1:
            d = lock_dir()
            d.mkdir(parents=True, exist_ok=True)
            fh = open(d / f"{name}.lock", "a+b")
            fh.seek(0, 2)
            if fh.tell() == 0:
                fh.write(b"0")
                fh.flush()
            while True:
                try:
                    _try_lock(fh)
                    break
                except OSError:
                    if timeout is not None and time.time() - t0 > timeout:
                        fh.close()
                        raise TimeoutError(f"鍵 {name} が {timeout} 秒で取れない（{label}）")
                    time.sleep(poll)
            st["fh"] = fh
            _log_wait(name, label, time.time() - t0)
        _held.names = mine + [name]
        yield
    finally:
        _held.names = mine
        st["depth"] -= 1
        if st["depth"] == 0 and st["fh"] is not None:
            try:
                _unlock(st["fh"])
            finally:
                st["fh"].close()
                st["fh"] = None
        st["rlock"].release()


def write_text_atomic(path: str | Path, text: str, *, retries: int = 50, wait: float = 0.1) -> None:
    """一時ファイルに書いてから置き換える。Windows で読み手が開いている間は置き換えに失敗するので、少し待ってやり直す。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    for i in range(retries):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if i == retries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(wait)
