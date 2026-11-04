"""CC0 アセットを data/assets/catalog.json から assets/ に取る（recorder assets fetch と同じ）。

    .venv/Scripts/python.exe -I -X utf8 scripts/fetch_assets.py [id ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recorder.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["assets", "fetch", *sys.argv[1:]]))
