# Transcript Review UI 仕様書

## 目的

話者分離つき transcript を見ながら、各 segment の音声をすぐ再生して、
文字起こしが正しいかを確認できるようにする。

---

## スコープ

この UI でやることは次の 2 つだけ。

1. segment ごとの text を確認する
2. その segment の音声区間をすぐ再生する

speaker の補正や高度な編集はこの段階では扱わない。

---

## 入力

- `*.diarized.transcript.json` 1ファイル

前提:

- JSON には `source_file` が入っている
- JSON には `chunks[].segments[]` が入っている
- 音声ファイルは `source_file` にあるパスを使う

---

## 画面

1画面で完結する。

### 上部

- transcript JSON のファイル名
- 音声ファイル名
- 音声プレイヤー

### 下部

segment 一覧を表示する。

各行に表示するもの:

- `Play`
- `Play + Context`
- `start`
- `end`
- `speaker`
- `text`

---

## 再生仕様

### Play

- その segment の開始時刻から終了時刻まで再生する

### Play + Context

- `start - 1秒` から `end + 1秒` まで再生する
- 0秒未満にはしない

### 時刻の扱い

chunk がある場合、segment の `start` と `end` は chunk 内相対時刻として扱う。

UI 内では再生用に絶対時刻へ変換する。

- `absolute_start = chunk.start_seconds + segment.start`
- `absolute_end = chunk.start_seconds + segment.end`

---

## 操作

各行でできる操作:

- `Play`
- `Play + Context`

---

## 非スコープ

この段階では以下はやらない。

- speaker の修正
- transcript text の直接編集
- segment の結合や分割
- waveform 表示
- 保存機能
- 高度な検索や集計

---

## MVP 完了条件

以下ができれば MVP 完成とする。

- diarized transcript JSON を読み込める
- segment 一覧を表示できる
- 各 segment を区間再生できる
