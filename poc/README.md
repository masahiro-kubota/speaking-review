# PoC

`poc/transcribe_mp3.py` は、リポジトリ直下の `.env` にある `OPENAI_API_KEY` を使って指定した mp3 を文字起こしする簡易スクリプトです。

`poc/merge_transcripts.py` は、重なりありで分割した transcript JSON 2本を受け取り、前半 transcript の末尾 sentence 群を anchor にして後半 transcript の重複 prefix を探し、重複部分と結合結果を JSON で出力する簡易スクリプトです。

## 使い方

```bash
uv run python poc/transcribe_mp3.py data/2026_4_24_9_00.mp3
```

出力先は `poc/output/*.transcript.json` です。

## 重複 transcript の結合

```bash
uv run python poc/merge_transcripts.py \
  poc/output/2026_4_24_9_00_0to800s.transcript.json \
  poc/output/2026_4_24_9_00_700to1500s.transcript.json
```

出力先はデフォルトで `poc/output/*__*.merged.json` です。
