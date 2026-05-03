# PoC

`poc/transcribe_mp3_gpt4o_diarize.py` は、同じ API キーを使って指定した mp3 を `gpt-4o-transcribe-diarize` で話者ラベル付き文字起こしする簡易スクリプトです。

`poc/infer_student_speaker.py` は、diarized transcript JSON を入力にして、`A/B/C` の各 raw speaker が `student` と `teacher` のどちらに属するかを OpenAI API で推定する簡易スクリプトです。

`poc/extract_student_turns.py` は、diarized transcript JSON と `speaker_roles.json` を入力にして、生徒側の発話だけを turn 単位にまとめた JSON を出力する簡易スクリプトです。

`poc/group_student_utterances.py` は、`student_turns.json` を入力にして、曖昧な境界だけを OpenAI API で判定しながら、生徒発話をより大きい utterance 単位に再グルーピングする簡易スクリプトです。

`poc/review_student_turns.py` は、`student_turns.json` を入力にして、生徒発話 turn ごとの添削結果を OpenAI API で生成する簡易スクリプトです。

`poc/split_mp3_with_overlap.py` は、1 本の mp3 を重なりありで複数 part に分割し、後段の diarize / merge 用 manifest を出力する簡易スクリプトです。

`poc/merge_diarized_transcripts.py` は、重なりありで diarize した 2 本の `*.diarized.transcript.json` を、manifest の時刻情報も使いながら 1 本の diarized transcript に結合する簡易スクリプトです。

`poc/diarize_split_manifest_and_merge.py` は、1 本の mp3 に対して `split -> diarize -> pairwise merge` を一発で回す orchestrator です。

`poc/ui_segments` には、diarized transcript を見ながら segment 区間を再生して確認するための最小 UI があります。

`poc/ui_student_turns` には、`student_turns.json` / `student_utterances.json` を見ながら生徒発話の turn / utterance 単位で prompt と返答を確認するための最小 UI があります。

`poc/unused` には、現在のフローでは使っていない旧 PoC スクリプトを置いてあります。

## 実行フロー

### 1. 長い音声を重なりありで分割して diarize したい場合

最短は `diarize_split_manifest_and_merge.py` を使うことです。

1. `diarize_split_manifest_and_merge.py` で `split -> diarize -> pairwise merge` を一発で回す

内部では次を順番に実行します。

1. `split_mp3_with_overlap.py` で mp3 を重なりありで分割して `split_manifest.json` を作る
2. 各 `partXofY.mp3` を `transcribe_mp3_gpt4o_diarize.py` で diarize する
3. `merge_diarized_transcripts.py` で `part1 + part2` を結合する
4. さらにその結合結果に `part3` を結合する

最終的に欲しいもの:
- `*.merged.diarized.transcript.json`

生成物は `poc/output/<original mp3 file stem>/` 配下にまとまります。

### 2. 生徒の speaking 添削まで進めたい場合

1. diarized transcript を用意する
2. `infer_student_speaker.py` で raw speaker を `student / teacher` に推定する
3. `extract_student_turns.py` で生徒発話を `turn` 単位に抽出する
4. 必要なら `group_student_utterances.py` でより大きい `utterance` 単位にまとめる
5. `review_student_turns.py` で turn ごとの添削を作る
6. `ui_segments` / `ui_student_turns` で結果を確認する

最終的に欲しいもの:
- `*.speaker_roles.json`
- `*.student_turns.json`
- `*.student_utterances.json`
- `*.student_turn_reviews.json`

### 用語

- `turn`: raw diarization segment を軽くまとめた中間単位
- `utterance`: 複数 turn をまたいでもよい、より意味まとまりに近い単位

## 一発実行

```bash
uv run python poc/diarize_split_manifest_and_merge.py \
  "data/2026年5月02日 12_30のレッスン.mp3" \
  --reuse-split false \
  --reuse-diarize false \
  --reuse-merge false
```

出力先はデフォルトで `poc/output/<original mp3 file stem>/` です。

`reuse` はフェーズごとに明示指定します。

- `--reuse-split true|false`
- `--reuse-diarize true|false`
- `--reuse-merge true|false`

デフォルトはすべて `false` です。

`true` の場合は、そのフェーズで必要な成果物がすべて既に存在している必要があります。不足がある場合は自動補完せずエラーで止まります。

## 個別実行

## 話者分離つき文字起こし

```bash
uv run python poc/transcribe_mp3_gpt4o_diarize.py data/2026_4_24_9_00.mp3
```

出力先は `poc/output/*.diarized.transcript.json` です。

## 生徒話者の推定

```bash
uv run python poc/infer_student_speaker.py \
  "poc/output/2026年5月02日 12_30のレッスン.diarized.transcript.json"
```

出力先はデフォルトで `poc/output/*.speaker_roles.json` です。

## 生徒発話の抽出

```bash
uv run python poc/extract_student_turns.py \
  "poc/output/2026年5月02日 12_30のレッスン.part1of2.diarized.transcript.json" \
  "poc/output/2026年5月02日 12_30のレッスン.part1of2.speaker_roles.json"
```

出力先はデフォルトで `poc/output/*.student_turns.json` です。

## 生徒発話 utterance への再グルーピング

```bash
uv run python poc/group_student_utterances.py \
  "poc/output/2026年5月02日 12_30のレッスン.part1of2.student_turns.json"
```

出力先はデフォルトで `poc/output/*.student_utterances.json` です。

## 生徒発話 turn ごとの添削

```bash
uv run python poc/review_student_turns.py \
  "poc/output/2026年5月02日 12_30のレッスン.part1of2.student_turns.json"
```

出力先はデフォルトで `poc/output/*.student_turn_reviews.json` です。

## 重なりあり mp3 分割

```bash
uv run python poc/split_mp3_with_overlap.py \
  "data/2026年5月02日 12_30のレッスン.mp3"
```

出力先はデフォルトで `poc/output/*.partXofY.mp3` と `poc/output/*.split_manifest.json` です。

## diarized transcript のマージ

```bash
uv run python poc/merge_diarized_transcripts.py \
  "poc/output/2026年5月02日 12_30のレッスン.part1of3.diarized.transcript.json" \
  "poc/output/2026年5月02日 12_30のレッスン.part2of3.diarized.transcript.json" \
  "poc/output/2026年5月02日 12_30のレッスン.split_manifest.json"
```

出力先はデフォルトで `poc/output/*__*.merged.diarized.transcript.json` と `*.debug.json` です。

## Segment Review UI

```bash
uv run uvicorn app:app --app-dir poc/ui_segments --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## Student Turn Review UI

```bash
uv run uvicorn app:app --app-dir poc/ui_student_turns --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## 旧 PoC

旧来の `merge_transcripts.py` や `transcribe_mp3_gpt4o.py` は、現在は `poc/unused/` に退避しています。
