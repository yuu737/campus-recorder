# F17 chain_tol の数え違い（count の輪）の確認

## 結論：式の不具合ではない（count の輪の `confirmed_by` で 0 にしている。Fable の後の決定どおり）
- `schema.link_rel` の count は `tol ÷ n`（値に掛けると ±tol 枚 × 周期（value ÷ n））で、K-101 の文どおり積んでいる。
- 本部棟 f6 の 2 つの値は count の輪に `"confirmed_by": "PXL_20261103_042909968"` があり、「2 枚で一致した枚数は数え違いを 0 と見なす」の例外でその項だけ 0 になる。
  この例外は K-101 の本文（docs/指示_11-08.md）には無く、T1 報告の「追記（Fable の決定を受けて）」と T2 報告 5 番（`--count-photo2`）で後から入った決定。テスト `test_count_confirmed_by_second_photo_is_zero` もある。決定を変えるのは担当外なので、コードは直していない。

## 数（今の式／confirmed_by を無視して K-101 の文どおり ±1 枚 × 周期を積んだ場合）
| 値 | value | 棟 JSON の tol | chain_tol（今） | chain_tol（±1 枚を積む） | 積むと validate |
|---|---|---|---|---|---|
| faces[f6].bays.pitch | 5.30973 m | 0.054 | 0.0411 | 0.1566（+0.1154） | 落ちる |
| faces[f6].top_z | 22.66414 m | 0.227 | 0.2087 | 0.7014（+0.4927） | 落ちる |

- 今の `recorder validate data/buildings/honbu.json` は「OK 警告 0」（読むだけで回した）。
- 鎖を持つ measured は 4 棟と敷地（tempaku.json）で本部棟の 2 件だけ。他の棟で落ちる物は 0 件（どちらの式でも）。

## 判断材料（決めるのは Fable）
- 2 枚の「数え」は目で数えた枚数ではなく px ÷ 周期の割り算（46.03 と 45.98）。柱型の芯をどこに取るかの癖は 2 枚に共通なので、2 枚一致で数え違いが 0 とは言い切れない。
- 前の PLATEAU 由来の 5.418 m は 5.418 ÷ 0.115429 ≈ 46.9 枚。ちょうど 1 枚違い（-2.0 %）で、±1 枚を外すかどうかが付け替えの差そのものに効く。
- 例外をやめるなら、本部棟の tol を 0.157・0.702 以上にする（棟 JSON は設計係の道具で直す。今回は触っていない）。

## 変えたファイル
- `tests/test_schema_chain.py`：2 件足した（f6 の形で、confirmed_by が無ければ ±1 枚 × 周期 0.115429 m がそのまま足される／tol 2 なら 2 倍、confirmed_by があれば count の項だけ 0 で tape と一様の項は残る）。
- `recorder/schema.py`：変えていない。

## テスト
`-m pytest tests/test_schema_chain.py tests/test_schema_and_drawing.py tests/test_schema_warnings.py tests/test_measure_chain.py -q` → 66 passed、2 failed。
落ちた 2 件は本番の結果を読むテストで、今回の変更とは関係ない：
- `test_schema_chain.py::test_rulers_have_tile_pitch_per_building`：`data/rulers.json` の本部棟の周期に値（0.115429）が入ったが、テストは null を求めている。
- `test_measure_chain.py::test_rescale_against_previous_plateau_value`：honbu.json の f6 の柱間が 5.30973 になったが、テストは前の 5.418 を求めている。

## 所要時間
約 15 分。

## 追記：K-202 を入れた（data/reports/指示_11-08/決定_本番.md）
- `recorder/schema.py`：count の輪の confirmed_by で数え違いを 0 にするのは、輪に `counted: true` と数えた途中の印（`marks` が空でない列、または輪の `method` に数え方）がある時だけ（`count_is_counted`）。無ければ confirmed_by があっても ±tol 枚 × 周期を積む（px ÷ 周期の割り算で出した枚数は数えたことにならない）。tol が足りない時の validate の文に、どの輪で、なぜ積んだかを足した（`ignored_confirmations`）。confirmed_by が付いていること自体はエラーにしない（tol が足りていれば通る）。docstring に K-202 を書いた。
- 見つけた穴：面の上端 `faces[].top_z` を validate が見ていなかった（`MEASUREMENT_KEYS_FACE` に無かった）。足した。top_z を持つのは本部棟だけで、他の 3 棟には影響が無い。
- 本部棟の validate（読むだけ）：NG エラー 2、警告 0。f6 の柱間は ±0.054 で、鎖から積んだ ±0.1566 に届かない。f6 の top_z は ±0.227 で、±0.7014 に届かない。図書館・タワー75・共通講義棟南は --strict で OK・警告 0。棟 JSON は直していない。
- テスト（test_schema_chain・test_schema_and_drawing・test_schema_warnings・test_measure_chain・test_ledger・test_evidence_gate）：90 passed、4 failed。
  - 前から落ちていた 2 件（本番の結果を読む物、直さない）：`test_rulers_have_tile_pitch_per_building`、`test_rescale_against_previous_plateau_value`。
  - K-202 で落ちるようになった 2 件：
    - `test_schema_chain.py::test_buildings_stay_clean`：本番の本部棟の上のエラー 2 件を拾っている（狙いどおり）。
    - `test_measure_chain.py::test_count_confirmed_by_second_photo`：T2 の `measure.build_chain` が count の輪に `counted` と `marks` を写さないので、今の道具では数え違いが 0 にならない。measure.py は担当外なので直していない。T2 側で `--counted` と `--count-marks`（または method）を足し、テストを合わせる必要がある。

## 追記 2：数え直しを道具でできるようにした（K-201・K-202）
- `recorder/measure.py`：`count_from_marks`（印の数 − 1 で枚数。割り算はしない。印が 1 つ以下・同じ位置・負の数は断る。間が中央値の 1.5 倍を超えたら「付け落とし」の警告）、`count_link`（count の輪を作る）。build_chain の count に `marks`・`confirmed_marks` を渡せる。
  - marks があれば `counted: true` と並べ替えた marks を輪に書く。渡した n と違えば断る。
  - 2 枚目は confirmed_marks で数え、枚数が一致した時だけ `confirmed_by`・`confirmed_marks` を付ける。割れたら付けずに `split`（2 枚目の写真・枚数・印）を書き、±tol 枚 × 周期を積み、estimated を勧める警告を出す。
  - 1 枚目を印で数えて 2 枚目に印が無い・2 枚目にだけ印がある時は断る。印の無い confirmed_by は前と同じく書くが、schema が ± を積み、警告を出す。
  - 警告は `sokuryo_chain_<面>.json` の `warnings.<key>` に置き、値（棟 JSON に写す形）には入れない。method に「1 枚ずつ印を付けて数えた」「…では n 枚で割れた」を書く。
- `recorder measure chain`：`--count-marks <px> …`・`--count-marks2 <px> …` を足した（引数は measure.py の add_args にあり、`recorder/__main__.py` は触っていない）。
- `recorder/schema.py`（K-202 を合わせた）：印で数えた輪が数え違い 0 になるのは、marks と confirmed_marks の両方がある時（または method に数え方がある時）。2 枚目の印が無ければ積む。
- `.claude/agents/sokuryo.md`：6 番の「枚数は別の写真でもう一度数える」を、印で数える使い方の 3 行に置き換えた。
- テスト：test_measure_chain の `test_count_confirmed_by_second_photo` を K-202 に合わせて直し、`test_count_marks_counts_and_refuses_mismatch`・`test_count_marks_split_between_two_photos` を足した。schema 側も confirmed_marks に合わせて直した。
  - 7 ファイル（schema_chain・schema_and_drawing・schema_warnings・measure_chain・measure_tape・ledger・evidence_gate）：97 passed、3 failed。失敗は全部本番の結果を読むテスト。前からの 2 件（rulers の周期に値が入った／f6 の柱間 5.418→5.30973）と、K-202 で本部棟 f6 の 2 件が落ちる `test_buildings_stay_clean`。棟 JSON・data/・out/ は触っていない。
