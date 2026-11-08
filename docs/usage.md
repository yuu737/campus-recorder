# Setup and usage (details)

These passages were moved out of README.md on 11/8 unchanged (only heading levels and link paths relative to `docs/` were adjusted). Installation is in the README.

Run everything as `python -I -X utf8 -m recorder <command>`:

| Command | What it does |
|---|---|
| `new <id> <name>` (`--from-site`) | Create an empty building JSON (or a base from PLATEAU and 5 m elevation) |
| `validate <json>` | Check the evidence rules |
| `counts <json>…` | Count measured / estimated / unknown values, and independent observations with copies grouped |
| `draw <json>` | Elevation and plan SVGs; unknowns shown in red |
| `unknowns <json>` | List unknown values (feeds photo requests) |
| `model build <json>` | Blender model, four views, bird's-eye, glb (`--cameras` for photo viewpoints) |
| `run <json>` | validate, draw, model, overlay, and `run_report.json` in one go |
| `site build` / `site export-web` / `site confirm` | Site base from public data; export for the viewer; record staff confirmation |
| `photos import` | Read EXIF, add to the index (`--period before` or `during`) |
| `photos rectify` / `measure` / `stitch` | Straighten a wall; count bay and floor pitch; join two one-sided photos |
| `photos compare` / `pose` / `overlay` | Inspection: line-by-line offsets in m; recover the camera; overlay model on the photo |

Walk the campus (see [`web/README.md`](../web/README.md)):
```bash
python -I -X utf8 -m recorder site export-web
cd web && python -m http.server 8000     # open http://localhost:8000/
```
The entry page (to the staff screen, reports and walk view) is served by `python -I -X utf8 -m recorder serve --port 8767` at http://localhost:8767/web/hub.html.
To resume after the agents stop on an incident or a limit (also from the 3rd stop on), use the staff screen buttons 「続きから」 (continue) or 「進行役に決めさせて続ける」 (let the coordinator decide and continue), or the CLI `python -I -X utf8 -m recorder agent resume <bldg>` (`--shinko` to call the coordinator, `--redo-from <step> --decision <decision file>` to redo from a step). Run `recorder agent preflight <bldg> --call-models` before pressing.
PC: WASD and drag. Phone: left stick to move, drag to look. Colored buildings have evidence; gray boxes are unmeasured PLATEAU shapes.

Tests: `python -I -X utf8 -m pytest` (test files are in `tests/`).
