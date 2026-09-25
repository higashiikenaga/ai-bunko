"""評価AIによる評価。書かずに読んで評価するAIが、ランダムに作品を選んで本文を読み、
総合★と観点別の点数、短い感想を残す。1回のレビュー = AI読者による1回の閲覧として、アクセス数にも数える。"""
from __future__ import annotations

import json
import random
from typing import Any

from ainovel import prompts
from ainovel.llm import BaseLLM
from ainovel.novel import Novel, all_novels, now_iso

# 観点別評価(ランキングの種類にもなる)
AXES = {"story": "ストーリー", "characters": "キャラクター", "writing": "文章", "originality": "独創性"}

READER_SYSTEM = (
    "あなたは小説投稿サイトで読むだけのROM専読者です。与えられた自分の好みに正直に、"
    "指定されたJSON形式のみを出力します。前後に説明文やコードブロック記号を付けません。"
)


def load_reviews(novel: Novel) -> list[dict[str, Any]]:
    path = novel.dir / "reviews.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_reviews(novel: Novel, reviews: list[dict[str, Any]]) -> None:
    (novel.dir / "reviews.json").write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


def _review_prompt(novel: Novel, reader: dict, chapter: dict, excerpt: str, read_upto: int) -> str:
    world = novel.world
    summaries = "\n".join(
        f"- 第{i}話「{c['title']}」: {c.get('summary', '')}" for i, c in enumerate(novel.chapters[:read_upto], 1)
    )
    return f"""# あなた(読者)
- 名前: {reader['name']}
- 好み: {reader['taste']}
- 評価の厳しさ: {reader.get('strictness', '普通')}

# 読んだ作品
タイトル: {novel.meta['title']} / ジャンル: {novel.meta.get('genre', '')} / 作: {novel.meta.get('author', 'AI')}
あらすじ: {world.get('premise', '')}

# ここまでの話(あなたが読んだ範囲: 第1話〜第{read_upto}話)
{summaries}

# 特に印象に残った「{chapter['title']}」の本文(抜粋)
{excerpt}

# 指示
あなたの好みと厳しさに正直に、この作品を評価してください。お世辞は不要です。好みに合わなければ低い点でもかまいません。
感想は、読者が投稿サイトに書くような自然な口語で、具体的な場面や人物に触れて書いてください。ネタバレになる結末の断定は避けます。

{{
  "score": 総合評価(1から5の整数),
  "scores": {{"story": ストーリー(1〜5), "characters": キャラクターの魅力(1〜5), "writing": 文章・文体(1〜5), "originality": 独創性(1〜5)}},
  "comment": "感想(60〜140文字)",
  "good": "良かった点(20文字以内)",
  "bad": "気になった点(20文字以内。なければ空文字)"
}}"""


def _pick(cfg: dict, rng: random.Random) -> tuple[Novel, dict] | None:
    readers = cfg.get("readers") or []
    limit = int((cfg.get("reviews") or {}).get("max_per_reader_novel", 3))
    from ainovel.sns import mention_counts  # 循環importを避ける

    buzz = mention_counts()  # AI広場で話題の作品は読まれやすい
    candidates = []
    for novel in all_novels():
        if not novel.chapters or novel.meta.get("status") == "abandoned":
            continue
        reviews = load_reviews(novel)
        for reader in readers:
            mine = [r for r in reviews if r["reader"] == reader["name"]]
            # 同じ読者は、前回から話が進んでいて上限未満のときだけ再評価する
            if len(mine) >= limit or (mine and mine[-1]["chapters_read"] >= len(novel.chapters)):
                continue
            weight = 1.0 + 0.5 * len(novel.chapters) + (2.0 if not reviews else 0.0) + 0.5 * buzz.get(novel.id, 0)
            # まだ評価のない作品・AI広場で話題の作品は読まれやすい
            candidates.append((weight, novel, reader))
    if not candidates:
        return None
    total = sum(w for w, _, _ in candidates)
    x = rng.uniform(0, total)
    for w, novel, reader in candidates:
        x -= w
        if x <= 0:
            return novel, reader
    return candidates[-1][1], candidates[-1][2]


def write_review(llm: BaseLLM, cfg: dict, rng: random.Random | None = None) -> bool:
    """1件レビューを書く。書く対象がなければ False。"""
    rng = rng or random.Random()
    picked = _pick(cfg, rng)
    if not picked:
        return False
    novel, reader = picked
    chapters = novel.chapters
    # 最新話付近まで読んだことにする。再評価のときは前回より先まで読んでいること
    mine = [r for r in load_reviews(novel) if r["reader"] == reader["name"]]
    first = max(1, len(chapters) - 2, (mine[-1]["chapters_read"] + 1) if mine else 1)
    read_upto = rng.randint(min(first, len(chapters)), len(chapters))
    chapter = rng.choice(chapters[:read_upto])
    text = novel.chapter_text(chapter["index"])
    start = rng.randint(0, max(0, len(text) - 2000))
    excerpt = text[start : start + 2000]

    print(f"■ レビュー: {reader['name']} が『{novel.meta['title']}』を第{read_upto}話まで読む")
    data = prompts.parse_json(
        llm.chat(READER_SYSTEM, _review_prompt(novel, reader, chapter, excerpt, read_upto), max_tokens=3072, temperature=0.9)
    )
    score = max(1, min(5, int(data.get("score", 3))))
    raw_axes = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    axes = {}
    for key in AXES:
        try:
            axes[key] = max(1, min(5, int(raw_axes[key])))
        except (KeyError, TypeError, ValueError):
            pass
    comment = str(data.get("comment", "")).strip()[:200]
    if not comment:
        raise ValueError("感想が空でした")
    reviews = load_reviews(novel)
    reviews.append(
        {
            "reader": reader["name"],
            "score": score,
            "scores": axes,
            "comment": comment,
            "good": str(data.get("good", "")).strip()[:30],
            "bad": str(data.get("bad", "")).strip()[:30],
            "chapters_read": read_upto,
            "created_at": now_iso(),
            "model": llm.last_model,
        }
    )
    save_reviews(novel, reviews)
    print(f"  ★{score} 「{comment[:40]}…」")
    return True
