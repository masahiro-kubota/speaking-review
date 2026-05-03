# PoC

`poc/transcribe_mp3_gpt4o.py` は、リポジトリ直下の `.env` にある `OPENAI_API_KEY` を使って指定した mp3 を `gpt-4o-transcribe` で文字起こしする簡易スクリプトです。

`poc/transcribe_mp3_gpt4o_diarize.py` は、同じ API キーを使って指定した mp3 を `gpt-4o-transcribe-diarize` で話者ラベル付き文字起こしする簡易スクリプトです。

`poc/infer_student_speaker.py` は、diarized transcript JSON を入力にして、`A/B/C` の各 raw speaker が `student` と `teacher` のどちらに属するかを OpenAI API で推定する簡易スクリプトです。

`poc/extract_student_turns.py` は、diarized transcript JSON と `speaker_roles.json` を入力にして、生徒側の発話だけを turn 単位にまとめた JSON を出力する簡易スクリプトです。

`poc/group_student_utterances.py` は、`student_turns.json` を入力にして、曖昧な境界だけを OpenAI API で判定しながら、生徒発話をより大きい utterance 単位に再グルーピングする簡易スクリプトです。

`poc/review_student_turns.py` は、`student_turns.json` を入力にして、生徒発話 turn ごとの添削結果を OpenAI API で生成する簡易スクリプトです。

`poc/merge_transcripts.py` は、重なりありで分割した transcript JSON 2本を受け取り、前半 transcript の末尾 sentence 群を anchor にして後半 transcript の重複 prefix を探し、重複部分と結合結果を JSON で出力する簡易スクリプトです。

`poc/ui_segments` には、diarized transcript を見ながら segment 区間を再生して確認するための最小 UI があります。

`poc/ui_student_turns` には、`student_turns.json` を見ながら生徒発話の turn 単位で prompt と返答を確認するための最小 UI があります。

## 使い方

```bash
uv run python poc/transcribe_mp3_gpt4o.py data/2026_4_24_9_00.mp3
```

出力先は `poc/output/*.transcript.json` です。

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

## 重複 transcript の結合

```bash
uv run python poc/merge_transcripts.py \
  poc/output/2026_4_24_9_00_0to800s.transcript.json \
  poc/output/2026_4_24_9_00_700to1500s.transcript.json
```

出力先はデフォルトで `poc/output/*__*.merged.json` です。
