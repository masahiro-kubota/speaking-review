以下に、**最小構成（MVP）前提の仕様書（Markdown）**を作成しました。
そのまま `README.md` や `spec.md` として使えます。

---

```markdown
# 英会話振り返りツール 仕様書（MVP）

## 概要
英会話レッスンの音声データを入力として、
- 文字起こし
- 話者分離（2人）
- 自分の発話抽出
- 英語添削フィードバック生成

を行うローカルツール。

---

## 目的
- 英会話の振り返りを効率化する
- 自分の発話のみを対象に改善ポイントを抽出する
- ネイティブらしい表現への言い換えを学習する

---

## スコープ（MVP）

### 入力
- 音声ファイル（mp3, wav など）
- 2人の会話を前提

### 出力
- 話者ラベル付き文字起こし
- 自分の発話のみの抽出
- 各発話ごとの英語添削
- Markdown形式のレポート

---

## 処理フロー

```

音声ファイル
↓
文字起こし（ASR）
↓
話者分離（2 speakers）
↓
自分の発話抽出
↓
1文ずつLLMに投入
↓
添削・フィードバック生成
↓
Markdown出力

```

---

## 機能要件

### 1. 文字起こし
- 音声をテキストに変換
- 英語対応
- タイムスタンプ付き

出力例：
```

[00:01.200 - 00:03.400] Hello, how are you?

```

---

### 2. 話者分離（ダイアライゼーション）
- 話者を2人に分類
- `Speaker A / Speaker B` でラベル付与

出力例：
```

[Speaker A] Hello, how are you?
[Speaker B] I'm good.

```

---

### 3. 自分の発話抽出
- どちらか一方の話者を「自分」として扱う
- MVPでは手動指定でOK

出力例：
```

I go to there yesterday.
He don't like it.

```

---

### 4. 英語添削（LLM）

#### 入力
- 1文ずつ

#### プロンプト仕様
```

You are an English teacher helping a non-native speaker improve their English.

The input will be a single sentence from a conversation.

<以下略>

```

#### 出力フォーマット
```

Corrected Sentence
...

Natural Alternatives

1. ...
2. ...
3. ...

Explanation
...

````

---

### 5. レポート生成

Markdown形式で出力

#### 出力例

```markdown
# 英会話振り返り

## Sentence 1
Original:
I go to there yesterday.

Corrected Sentence:
I went there yesterday.

Natural Alternatives:
1. I went there yesterday.
2. I was there yesterday.
3. I went over there yesterday.

Explanation:
過去形の誤りと不要な "to" を修正

---

## Sentence 2
...
````

---

## 非機能要件

### 実行環境

* Ubuntu
* Python 3.10+

### 実行形態

* CLIツール

### パフォーマンス

* 10〜30分音声を処理可能
* リアルタイム性は不要

---

## 技術構成（MVP）

### 音声処理

* Whisper系（例: faster-whisper）

### 話者分離

* WhisperX or pyannoteベース

### LLM

* OpenAI API or ローカルLLM（Ollama等）

---

## ディレクトリ構成（例）

```
project/
├── input/
│   └── audio.mp3
├── output/
│   ├── transcript.json
│   └── review.md
├── src/
│   ├── transcribe.py
│   ├── diarize.py
│   ├── extract.py
│   ├── review.py
│   └── main.py
└── config.yaml
```

---

## 今後の拡張（Out of Scope）

* 自動で「自分 / 先生」を識別
* UI（Webアプリ化）
* 音声の自動分割
* 発音評価

---

## リスクと前提

### 前提

* 話者は2人のみ
* 音質は一定以上

### リスク

* 話者ラベルの誤り
* 短い発話の誤分類
* LLM出力のばらつき

---

## 開発ステップ

1. 文字起こしのみ実装
2. 話者分離を追加
3. 自分の発話抽出
4. LLM添削追加
5. Markdown出力整形

---

## MVPの定義

以下が満たされれば完成：

* 音声を入力できる
* 2話者に分離される
* 自分の発話が抽出できる
* 添削結果がMarkdownで出る

```

---
```

