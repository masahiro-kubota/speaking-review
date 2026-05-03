# Student Turn Review UI

`student_turns.json` を見ながら、先生の prompt と生徒の返答を turn 単位で確認するための最小 UI です。

## 起動

```bash
uv run uvicorn app:app --app-dir poc/ui_student_turns --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## 前提

- `poc/output/*.student_turns.json` があること
- `student_turns.json` から音声ファイルに辿れること

## できること

- `student_turns.json` の一覧表示
- turn ごとの prompt / student text の確認
- `Play Student` で生徒側の区間再生
- `Play Prompt + Student` で直前 prompt を含めた区間再生
