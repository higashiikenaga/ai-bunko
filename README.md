# AI文庫 — AIで書いた小説の投稿サイト

**AIで書いた小説だけ**を投稿できる小説サイトです。人間が自力で書いた作品は対象外です。
AIをどこまで使ったかは投稿者の**自己申告**で、作品ごとに「区分(ゾーニング)」として表示します。

> 以前は、LLMが小説の企画・執筆・投稿・評価まですべて自動で行う実験サイトでした。
> 投稿サイトへの作り替えに伴い、既存のAI作品・AI作家・評価AI・AI広場などは削除しました(履歴はGitに残っています)。

## AI生成の区分(ゾーニング)

`config.yaml` の `zones` で定義します。投稿者が作品ごとに選びます。

| 区分 | 内容 |
|---|---|
| 全文AI | 本文はAIの出力そのまま。人間は指示・取捨選択と、誤字脱字・体裁の修正まで |
| AI+人間の手直し | AIが書いた文章に人間が加筆・修正。AI由来の文章が過半 |
| AI主体の共作 | 人間が書いた部分を含むが、AIの生成が半分以上 |

- 作品カード・作品ページ・各話に区分バッジを表示(使用AIの名前も記載)
- トップ・ジャンル・検索・ランキングで、区分ごとの表示/非表示を切り替え(ブラウザの localStorage に保存)
- 区分別のページ `/zones/<key>`
- 区分を足す・変えるときは `config.yaml` の `zones` を編集するだけ

## 自己申告制のしくみ

AIで書いたかどうかは技術的に確かめられないため、次で運用します。

- 投稿時に、区分・使用AI・「AIで書いた作品であり申告に偽りがない」への同意を必須にする
- 規約違反や区分違いは通報(`about` ページの案内)を受けて運営が対応する
  - 非表示: `content/novels/<id>/novel.json` の `status` を `hidden` にしてコミット
  - 区分の修正: 同じく `ai_zone` を書き換える

## 投稿のしくみ

```
投稿フォーム(/submit) ─ POST /api/submit ─▶ Pages Functions
                                               ├ 入力検証・1人1日の投稿数制限(D1)
                                               └ GitHub Contents API で content/novels/<id>/ にコミット
                                                     └ Cloudflare Pages が自動ビルド → 数分で公開
```

- アカウントはありません。新作を投稿すると**編集キー**が1度だけ表示され、続きの投稿に使います(SHA-256のハッシュだけを `novel.json` に保存。紛失しても再発行できません)
- データは `content/novels/<id>/novel.json`(作品情報・各話の一覧)と `chapters/NNN.md`(本文)
- 読者は、読む・★をつける(アカウント不要・1作品1人1回)・ブックマーク/フォローができます

## セットアップ

### 1. Cloudflare Pages と連携
Cloudflare ダッシュボード → **Workers & Pages → Create → Pages → Connect to Git** でこのリポジトリを選びます。

| 項目 | 値 |
|---|---|
| Production branch | `main` |
| Build command | `pip install -r requirements.txt && python -m ainovel.build` |
| Build output directory | `_site` |
| 環境変数 `PYTHON_VERSION` | `3.12` |
| 環境変数 `SITE_URL`(任意) | 公開URL(例 `https://ai-bunko.pages.dev`) |

### 2. D1(評価・閲覧数・投稿の回数制限)
1. **ストレージとデータベース → D1 SQL データベース** で作成(例 `ai-bunko`)
2. Pagesプロジェクト → **設定 → バインディング** → D1 を変数名 `DB` で追加
3. (推奨)**変数とシークレット** に `RATING_SALT` をシークレットで追加(長いランダムな文字列)

表は初回アクセス時に自動作成されます。**`DB` が未設定だと投稿はできません**(回数制限に使うため)。

### 3. 投稿の保存先(GitHub)
Pagesプロジェクトの **変数とシークレット** に追加します。

| 名前 | 内容 |
|---|---|
| `GITHUB_TOKEN`(シークレット) | このリポジトリの **Contents: Read and write** だけを許可した fine-grained トークン |
| `GITHUB_REPO` | `higashiikenaga/ai-bunko` |
| `GITHUB_BRANCH`(任意) | 既定は `main` |

## ローカルで確認

```bash
pip install -r requirements.txt
python -m ainovel.build              # content/ から _site/ を生成
python -m http.server -d _site       # http://localhost:8000
```
(投稿・評価のAPIは Cloudflare Pages Functions なので、ローカルの `http.server` では動きません。`wrangler pages dev _site` で試せます)

## 設定(`config.yaml`)

| 項目 | 説明 |
|---|---|
| `site` | タイトル・説明・公開URL・連絡先 |
| `submit` | タイトル/本文の文字数、1作品の最大話数、1人1日の投稿話数などの上限 |
| `zones` | AI生成の区分(key・表示名・色・説明) |
| `genres` | ジャンル候補 |

## 注意

- 投稿された作品の内容・著作権・区分の正しさは投稿者の責任です(自己申告)。
- 閲覧者どうしのコメント・メッセージ機能、アクセス解析や広告のタグはありません。
