# Student Turn Review UI

`student_turns.json` と `student_utterances.json` を見ながら、先生の prompt と生徒の返答を turn / utterance 単位で確認するための最小 UI です。

対応する `student_turn_reviews.json` があれば、各 turn の添削結果も表示します。

## 起動

```bash
uv run uvicorn app:app --app-dir poc/ui_student_turns --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## 前提

- `poc/output/*.student_turns.json` または `poc/output/*.student_utterances.json` があること
- それらの JSON から音声ファイルに辿れること

## できること

- turn / utterance の一覧表示
- turn / utterance ごとの prompt / student text の確認
- `Play Student` で生徒側の区間再生
- `Play Prompt + Student` で直前 prompt を含めた区間再生
- `student_turn_reviews.json` があれば corrected / natural / feedback / issues の表示
