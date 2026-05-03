# Segment Review UI

segment ごとの音声を再生しながら、文字起こしを確認するための最小 UI です。

## 起動

```bash
uv run uvicorn app:app --app-dir poc/ui_segments --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## 前提

- `poc/output/*.diarized.transcript.json` があること
- transcript JSON の `source_file` が実在すること

## できること

- diarized transcript の一覧表示
- `*.speaker_roles.json` があれば role 表示
- segment ごとの区間再生
- `Play +1s` で前後 1 秒つき再生
