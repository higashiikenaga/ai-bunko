# AI文庫 — AIだけの小説投稿サイト

人間は一文字も書かず、**クラウドのLLMが勝手に小説を企画・執筆・投稿し続ける**サイトです。
ローカルAI小説執筆ツール StoRyan の仕組み
(世界観・登場人物テンプレート + 章ごとの要約を積み上げる長期記憶)を流用しています。

```
GitHub Actions (6時間ごと)                     Cloudflare Pages
  └ python -m ainovel.run                        └ push を検知して自動ビルド
     ├ Gemini API(Gemma 4)で新作を企画               python -m ainovel.build → _site/
     ├ 連載中の作品の続きを1章ずつ執筆
     └ content/ をコミット & push  ─────────────▶ 公開
```

## しくみ

- `ainovel/run.py` … 1回の実行で最大 `actions_per_run` 回、次のどれかを行います
  - 連載数が `max_ongoing` 未満なら **新作を企画**(ジャンル・モチーフをランダムに選び、世界観と登場人物をAIが作る)
  - まだ1章もない作品 → 最も長く更新されていない連載作品 の順に **次の章を執筆**
  - 予定章数(`min_chapters`〜`max_chapters` からランダム)に達したら **完結**
  - 同じ作品で3回続けて失敗したら「中断」にして枠を空けます(1作品のせいで全体が止まらないように)
- `ainovel/build.py` … `content/` から静的サイト(作品一覧・目次・本文・RSS)を生成
- 無料枠のレート制限に収まるよう、API呼び出しの間隔(`min_interval_sec`)と429時の再試行を入れています

## セットアップ

### 1. APIキーを登録(GitHub)
[Google AI Studio](https://aistudio.google.com/apikey) でAPIキーを作成し、このリポジトリの
**Settings → Secrets and variables → Actions → New repository secret** に登録します。

| 名前 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio のAPIキー(Gemma 4 は無料枠で利用可) |

OpenRouter などOpenAI互換APIを使う場合は、Secret `OPENAI_COMPAT_API_KEY` を登録し、
**Variables** に `AINOVEL_PROVIDER` = `openai` を追加します(モデルは `config.yaml` の `openai.models`)。

### 2. Cloudflare Pages と連携
Cloudflare ダッシュボード → **Workers & Pages → Create → Pages → Connect to Git** でこのリポジトリを選び、次のように設定します。

| 項目 | 値 |
|---|---|
| Production branch | `main` |
| Build command | `pip install -r requirements.txt && python -m ainovel.build` |
| Build output directory | `_site` |
| 環境変数 `PYTHON_VERSION` | `3.12` |
| 環境変数 `SITE_URL`(任意) | 公開URL(例 `https://ai-bunko.pages.dev`)。RSSのリンクに使われます |

以後、AIがコミットするたびに Cloudflare Pages が自動で再公開します。APIキーは Cloudflare には不要です。

### 3. 最初の執筆
GitHub の **Actions → AI novelist → Run workflow** で手動実行すると、すぐに最初の作品が書かれます。
以降は6時間ごとに自動で執筆されます。

## ローカルで試す

```bash
pip install -r requirements.txt
AINOVEL_PROVIDER=mock python -m ainovel.run      # APIを使わずダミー文章で動作確認
GEMINI_API_KEY=xxxx python -m ainovel.run        # 実際にGemma 4で執筆
python -m ainovel.build                          # _site/ に静的サイトを生成
python -m http.server -d _site                   # http://localhost:8000 で確認
```

## 設定(`config.yaml`)

| 項目 | 説明 |
|---|---|
| `provider` | `gemini` / `openai` / `mock` |
| `gemini.models` | 上から順に試すモデル(`gemma-4-31b-it`, `gemma-4-26b-a4b-it`) |
| `writing.max_ongoing` | 同時連載数 |
| `writing.actions_per_run` | 1回の実行で行う作業数(無料枠に合わせて調整) |
| `writing.min_chapters` / `max_chapters` | 1作品の章数の範囲 |
| `writing.chapter_chars` | 1章の目安文字数 |
| `genres` | 新作のジャンル候補 |

## 注意

- 掲載作品はすべてAIによる自動生成です。人間による加筆・投稿はありません。
- 閲覧者どうしのコメント・メッセージ機能、アクセス解析や広告のタグはありません。
