# Results: detailed records

These passages were moved out of README.en.md on 11/8 unchanged (only heading levels and link paths relative to `docs/` were adjusted).

## The 11/7 dress rehearsal (08:35–16:28)

- Time: the four buildings ran in parallel; 7 h 52 min from the first agent (08:36) to the last report (16:28), including about 2 h 55 min stopped at the usage limit. The agents' times add up to 16 h 13 min, about 3.3 × the elapsed time without the stop.
- Tokens (sum of each agent's final context size, a rough guide): about 13.3 M, 3.0–3.6 M per building. Cost will come from the SDK in the real API run and is not given here.
- The only thing the rehearsal did not produce was the report page's screenshots (3 evidence-viewer images and 1 walk-through image per building): headless Edge failed to open its DevTools port (J-030). Generation itself had finished; only the step that photographs the screen failed. After the rehearsal, at 21:06–21:11, the same tool was run unchanged and all 4 buildings' images were captured. They render the rehearsal's own outputs; no value was changed (see 結果.md, "報告の絵について").

## Trial on other campuses, up to the site base (early 11/8)

- **Nagoya University, Higashiyama campus:** given only the area, `campus init` → `site build` → `site export-web` all ran (about 3 min).
  - The tool found the OSM campus boundary (`way/144866408`) by itself.
  - The origin is at 35.154° N, 136.9665° E, in plane rectangular zone VII. OSM lists 494 buildings.
  - No PLATEAU data was prepared, so the base has building heights as `unknown`.
  - `recorder new --from-site` also made an empty building JSON from one OSM outline (height `unknown`, 11 photo requests).
- **University of Tokyo, Hongo campus:** the coordinate system switched to zone IX automatically.
  - The boundary is `relation/5414648`, and OSM lists 162 buildings.
  - `site export-web` also ran (after one wait while Overpass was busy).
- **Only the site base (terrain, ground cover, boundary, building list) was produced.** There are no photos and no building built from photos; that needs photos from the university's own staff. Where there is no PLATEAU, the only reference length is OSM edges.
