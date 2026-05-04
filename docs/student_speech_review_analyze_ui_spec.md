# Student Speech Review Analyze UI

## 目的

- ダウンロード済み mp3 を UI から解析開始できるようにする
- `poc/build_lesson_review_bundle.py` の進捗を UI 上で確認できるようにする
- 解析完了後、そのまま下段の review UI で結果を確認できるようにする

## 画面構成

- 1ページ構成
- 外側のページ名は `Student Speech Review`
- 上段に `Analyze` セクション
- 下段に既存の `Review` セクション

## Analyze セクション

表示要素:

- `data/*.mp3` の一覧セレクト
- `Analyze` ボタン
- 全体 status
- 現在の phase
- `7` フェーズの進捗表示
- 最新ログ数行

実行コマンド:

```bash
uv run python poc/build_lesson_review_bundle.py "data/<selected>.mp3"
```

reuse フラグは v1 では UI から指定しない。

## フェーズ

表示するフェーズは固定で次の 7 個。

1. `split`
2. `diarize`
3. `merge`
4. `speaker_roles`
5. `turns`
6. `exchanges`
7. `reviews`

各フェーズの状態:

- `pending`
- `running`
- `done`
- `error`

## バックエンド API

- `GET /api/input-mp3s`
  - `data/*.mp3` 一覧を返す
- `POST /api/analyze`
  - 解析ジョブを開始する
  - input: `{ "mp3_name": "..." }`
  - output: `{ "job_id": "..." }`
- `GET /api/analyze-status?job_id=...`
  - 現在の phase, 全体 status, ログ, lesson 名を返す

進捗取得は WebSocket ではなくポーリングでよい。

## ジョブ管理

- バックエンドで subprocess を起動する
- stdout を読みながら phase を更新する
- v1 では同時実行ジョブは 1 本まで
- 既に実行中ジョブがあれば新規開始はエラー

## Review セクション

- 今の `ui_student_turns` の review 機能をそのまま使う
- `turn` / `exchange` 切り替え
- 音声再生
- exchange review 表示

解析完了後:

- lesson 一覧を再取得する
- 今回の lesson を自動選択する
- 可能なら unit は `exchange` に寄せる

## フロント構成

- `page shell`
- `analyze module`
- `review module`

既存 review 部分と analyze 部分は JS を分ける。
React / Vue は入れず、今の `FastAPI + static HTML/JS` のまま実装する。

## v1 でやらないこと

- ジョブ履歴の永続化
- 複数同時ジョブ
- ジョブキャンセル
- WebSocket
- reuse フラグの UI 指定
