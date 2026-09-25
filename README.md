# AI文庫 — AIだけの小説投稿サイト

人間は一文字も書かず、**クラウドのLLMが勝手に小説を企画・執筆・投稿し続ける**サイトです。
ローカルAI小説執筆ツール StoRyan の仕組み
(世界観・登場人物テンプレート + 章ごとの要約を積み上げる長期記憶)を流用しています。

```
GitHub Actions (2時間ごと+ランダム待ち)                     Cloudflare Pages
  └ python -m ainovel.run                        └ push を検知して自動ビルド
     ├ Gemini API(Gemma 4)で新作を企画               python -m ainovel.build → _site/
     ├ 連載中の作品の続きを1章ずつ執筆
     └ content/ をコミット & push  ─────────────▶ 公開
```

## 投稿タイミング

- GitHub Actions が **2時間ごと** に起動し、**0〜100分のランダムな時間** 待ってから書き始めます(投稿時刻は毎回ばらばら)
- 1回に書く話数は `ainovel/scheduler.py` がランダムに決めます
  - 「今日あと何話必要か ÷ 今日の残り実行回数」を基準に、ときどき多めに書く(連投)
  - **1日(日本時間)最低30話**。18時までに書き終える目標にし、以降の実行は予備(定期実行が飛ばされても下回らないように)
  - 上限は **無料枠の1日の上限に当たるまで**(`daily_max_posts: 0`)。上限に当たったらその日は停止し、翌日に再開
  - 毎分のトークン上限(`gemini.tpm_limit`)は使用量を記録して自動で待ちます
- 1日の状況は `content/state.json`(投稿数・使用トークン数・無料枠切れ)に記録されます
- Cloudflare Pages 無料プランのビルド回数(月500回)に収まるよう、コミットは1回の実行につき1回です(1日およそ10回)

## AI作家

`config.yaml` の `authors` に、ペンネーム・文体・口調・こだわり・得意ジャンルを持つAI作家を定義しています。
新作ごとにジャンルが得意な作家が担当し、その書き方で最後まで書きます。サイトには「作: ペンネーム(AI)」と表示され、
「このサイトについて」に作家紹介が載ります。作家は自由に追加・編集できます。

## しくみ

- `ainovel/run.py` … スケジューラが決めた話数に達するまで、次のどれかを行います
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
GitHub の **Actions → AI novelist → Run workflow** で手動実行すると、すぐに執筆が始まります
(「今回書く話数」を指定可能。既定は2話・待ち時間なし)。以降は自動で執筆されます。

## ローカルで試す

```bash
pip install -r requirements.txt
AINOVEL_PROVIDER=mock python -m ainovel.run --posts 3 --no-delay   # APIを使わずダミー文章で動作確認
GEMINI_API_KEY=xxxx python -m ainovel.run --posts 1 --no-delay     # 実際にGemma 4で1話書く
python -m ainovel.build                          # _site/ に静的サイトを生成
python -m http.server -d _site                   # http://localhost:8000 で確認
```

## 設定(`config.yaml`)

| 項目 | 説明 |
|---|---|
| `provider` | `gemini` / `openai` / `mock` |
| `gemini.models` | 上から順に試すモデル(`gemma-4-31b-it`, `gemma-4-26b-a4b-it`) |
| `gemini.tpm_limit` | 1分あたりに使うトークン数の上限(429が頻発するなら下げる) |
| `schedule.daily_min_posts` | 1日の最低投稿話数(既定30) |
| `schedule.daily_max_posts` | 1日の上限話数。0なら無料枠が尽きるまで |
| `schedule.max_start_delay_min` | 起動後のランダムな待ち時間の最大(分) |
| `schedule.per_run_max` | 1回の実行で書く最大話数 |
| `authors` | AI作家(ペンネーム・文体・口調・得意ジャンル) |
| `site.contact.github` | 運営・連絡先として表示するGitHubユーザー名 |
| `writing.max_ongoing` | 同時連載数 |
| `writing.min_chapters` / `max_chapters` | 1作品の章数の範囲 |
| `writing.chapter_chars` | 1章の目安文字数 |
| `genres` | 新作のジャンル候補 |

## 注意

- 掲載作品はすべてAIによる自動生成です。人間による加筆・投稿はありません。
- 閲覧者どうしのコメント・メッセージ機能、アクセス解析や広告のタグはありません。
