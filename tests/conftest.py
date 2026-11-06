"""CI（GitHub Actions、ubuntu）で回せないテストに印を付ける。

印は pyproject.toml の [tool.pytest.ini_options] markers に登録してある：
  blender … Blender 5.2（環境変数 BLENDER）を起動する
  browser … headless の Edge / Chrome を起動する（recorder shots）
  gpu     … torch・学習済みの重み（Depth Pro・ALIKED/LightGlue・GeoCalib）が要る
CI は `-m "not blender and not browser and not gpu"` で外す。ローカルでは印があっても今までどおり走る
（どれも道具が無ければ各テストの skipif で飛ぶ）。テストを足したらここの表にも足す。
"""
from __future__ import annotations

import pytest

MARKS: dict[str, tuple[str, ...]] = {
    "blender": (
        "test_blender_build.py::",                                  # ファイルごと（3 件とも Blender の中で組む）
        "test_assets.py::test_blender_import_model_scale_and_materials",
        "test_interior.py::test_blender_cuts_room_and_adds_columns",
        "test_parts.py::test_blender_builds_all_new_parts",
        "test_photoproj.py::test_blender_render_matches_numpy",
    ),
    "browser": (
        "test_shots.py::test_headless_browser_capture_and_exit",
        "test_shots.py::test_strip_blueprint_removes_default_view",
    ),
    "gpu": (
        "test_depth.py::test_model_available",
        "test_sfm_features.py::test_aliked_lightglue_matches_and_writes_db",
        "test_landmarks_solvers.py::test_geocalib_prior_skips_without_install",
    ),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        nid = item.nodeid.replace("\\", "/").split("/")[-1]       # "test_x.py::test_y[...]"
        for mark, keys in MARKS.items():
            if any(nid.startswith(k) for k in keys):
                item.add_marker(getattr(pytest.mark, mark))
