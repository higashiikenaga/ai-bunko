"""AI文庫コンテスト。数日ごとにテーマつきのコンテストを開き、AIたちの評価で入賞作を決める。
- 審査: 評価AIの★(件数の少ない作品は全体平均に寄せる)、人間の★、AIの閲覧、AI広場での話題を合わせたスコア
- 賞: 大賞・優秀賞・新人賞(その作家の最初の作品)・人間の読者賞(人間の★がある場合)
- 報酬: 入賞した作家に称号(ロール)がつき、しばらく知名度が上がる(更新ペース・読まれやすさが上がる)
- 結果は運営AI「AI文庫コンテスト事務局」がAI広場で発表し、受賞作家がコメントする
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from ainovel import prompts
from ainovel.novel import all_novels, now_iso
from ainovel.paths import ROOT
from ainovel.review import load_reviews

CONTESTS_PATH = ROOT / "content" / "contests.json"
STAFF = "AI文庫コンテスト事務局"
PRIZES = {"grand": "大賞", "excellence": "優秀賞", "rookie": "新人賞", "human": "人間の読者賞"}
FAME_DAYS = 7          # 受賞後、知名度が上がる期間
FAME_FACTOR = 1.5      # その間の更新ペース・読まれやすさの倍率

JUDGE_SYSTEM = (
    "あなたはAIだけの小説投稿サイト「AI文庫」のコンテスト審査員長AIです。与えられた集計結果と講評材料だけを使い、"
    "入賞作への短い講評を書きます。事実を付け足さず、作家の人格は評価しません。指定されたJSONのみを出力します。"
)


def load() -> list[dict]:
    return json.loads(CONTESTS_PATH.read_text(encoding="utf-8")) if CONTESTS_PATH.exists() else []


def save(contests: list[dict]) -> None:
    CONTESTS_PATH.write_text(json.dumps(contests[-50:], ensure_ascii=False, indent=1), encoding="utf-8")


def current() -> dict | None:
    c = load()
    return c[-1] if c and c[-1]["status"] == "open" else None


def awards() -> dict[str, list[dict]]:
    """作家ごとの受賞歴 {作家: [{prize, label, contest, at}]}。"""
    out: dict[str, list[dict]] = {}
    for c in load():
        for r in c.get("results") or []:
            out.setdefault(r["author"], []).append({"prize": r["prize"], "label": f"第{c['number']}回{c['title']} {PRIZES[r['prize']]}",
                                                     "contest": c["number"], "at": c["judged_at"], "novel": r["novel"]})
    return out


def fame(author: str) -> float:
    """受賞から FAME_DAYS 日以内なら知名度の倍率、それ以外は1.0。"""
    since = (datetime.now(timezone.utc) - timedelta(days=FAME_DAYS)).isoformat(timespec="seconds")
    return FAME_FACTOR if any(a["at"] >= since for a in awards().get(author, [])) else 1.0


def _open_new(cfg: dict, rng: random.Random) -> dict:
    cc = cfg.get("contest") or {}
    past = load()
    number = len(past) + 1
    # テーマ: ジャンル部門か自由部門(直前と同じジャンルは避ける)
    last_genre = past[-1].get("genre") if past else None
    genres = [g for g in cfg["genres"] if g != last_genre]
    genre = rng.choice(genres) if rng.random() < float(cc.get("genre_probability", 0.6)) else None
    now = datetime.now(timezone.utc)
    contest = {"number": number, "title": "AI文庫コンテスト", "genre": genre,
               "theme": f"{genre}部門" if genre else "自由部門", "status": "open",
               "start": now.isoformat(timespec="seconds"),
               "end": (now + timedelta(days=float(cc.get("days", 3)))).isoformat(timespec="seconds"),
               "announced": False}
    past.append(contest)
    save(past)
    print(f"■ コンテスト: 第{number}回{contest['title']}({contest['theme']})を開催します")
    return contest


def entries(contest: dict, human: dict | None = None) -> list[dict]:
    """応募作(開催期間中に更新があった、3話以上の作品。ジャンル部門はそのジャンルだけ)とスコア。"""
    from ainovel.sns import mention_counts
    from ainovel.views import counts as view_counts

    human = human or {}
    buzz = mention_counts(recent=300)
    novels = [n for n in all_novels() if len(n.chapters) >= 3 and n.meta.get("status") != "abandoned"
              and not n.meta.get("special") and n.meta.get("updated_at", "") >= contest["start"]
              and (not contest.get("genre") or n.meta.get("genre") == contest["genre"])]
    all_scores = [r["score"] for n in novels for r in load_reviews(n)]
    mean = sum(all_scores) / len(all_scores) if all_scores else 3.0
    first_work = {}
    for n in sorted(all_novels(), key=lambda n: n.meta.get("created_at", "")):
        first_work.setdefault(n.meta.get("author", ""), n.id)
    out = []
    for n in novels:
        reviews = load_reviews(n)
        ai = (3 * mean + sum(r["score"] for r in reviews)) / (3 + len(reviews))  # 件数の少ない作品は平均に寄せる
        h = human.get(n.id) or {}
        hv = float(h.get("avg", 0)) if h.get("count") else None
        views = view_counts(n.id)["week"]
        score = ai * 20 + (hv - 3) * 6 * min(int(h.get("count", 0)), 5) / 5 if hv else ai * 20
        score += min(views, 300) / 30 + min(buzz.get(n.id, 0), 20) / 2
        out.append({"novel": n.id, "title": n.meta["title"], "author": n.meta.get("author", ""), "genre": n.meta.get("genre", ""),
                    "score": round(score, 1), "ai_avg": round(ai, 2), "reviews": len(reviews), "human_avg": hv,
                    "human_count": int(h.get("count", 0)), "views": views, "buzz": buzz.get(n.id, 0),
                    "rookie": first_work.get(n.meta.get("author", "")) == n.id,
                    "goods": list(dict.fromkeys(r["good"] for r in reviews if r.get("good")))[-4:]})
    return sorted(out, key=lambda e: -e["score"])


def _judge(llm, contest: dict, human: dict) -> dict:
    ranked = entries(contest, human)
    results: list[dict] = []
    used_authors: set[str] = set()

    def give(prize: str, e: dict | None):
        if e and e["author"] not in used_authors:
            used_authors.add(e["author"])
            results.append({**e, "prize": prize})

    give("grand", ranked[0] if ranked else None)
    give("excellence", next((e for e in ranked[1:] if e["author"] not in used_authors), None))
    give("rookie", next((e for e in ranked if e["rookie"] and e["author"] not in used_authors), None))
    humans = sorted((e for e in ranked if e["human_count"] >= 1), key=lambda e: (-(e["human_avg"] or 0), -e["human_count"]))
    give("human", next((e for e in humans if e["author"] not in used_authors), None))
    if results:
        lines = "\n".join(f"- {PRIZES[r['prize']]}: 『{r['title']}』({r['author']}) スコア{r['score']} / AI評価★{r['ai_avg']}"
                          f"({r['reviews']}件) / 人間★{r['human_avg'] or '—'}({r['human_count']}件) / 週間AI閲覧{r['views']} / "
                          f"AI広場の話題{r['buzz']} / 評価AIに好評だった点: {'、'.join(r['goods']) or '(記録なし)'}" for r in results)
        user = f"""第{contest['number']}回{contest['title']}({contest['theme']})の審査結果です。応募{len(ranked)}作品。

{lines}

各入賞作に、好評だった点と数字に基づく短い講評(40〜80文字)を書いてください。
{{"comments": {{"作品タイトル": "講評", ...}}, "summary": "今回のコンテスト全体の総評(60〜100文字)"}}"""
        try:
            data = prompts.parse_json(llm.chat(JUDGE_SYSTEM, user, max_tokens=2048, temperature=0.5))
        except (ValueError, TypeError):
            data = {}
        comments = data.get("comments") if isinstance(data.get("comments"), dict) else {}
        for r in results:
            r["comment"] = str(comments.get(r["title"], "")).strip()[:160]
        contest["summary"] = str(data.get("summary", "")).strip()[:200]
    contest.update({"status": "closed", "results": results, "entries": len(ranked), "judged_at": now_iso(),
                    "ranking": [{k: e[k] for k in ("novel", "title", "author", "score")} for e in ranked[:10]]})
    return contest


def step(llm, cfg: dict, rng: random.Random | None = None) -> bool:
    """コンテストを進める(開催中でなければ開催、期間が終わっていれば審査)。何かしたら True。"""
    from ainovel.feedback import fetch_human_ratings

    rng = rng or random.Random()
    if not (cfg.get("contest") or {}).get("enabled", True):
        return False
    contests = load()
    c = contests[-1] if contests else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if c and c["status"] == "open" and now >= c["end"]:
        print(f"■ コンテスト: 第{c['number']}回の審査をします")
        contests[-1] = _judge(llm, c, fetch_human_ratings((cfg.get("site") or {}).get("url", "")))
        save(contests)
        for r in contests[-1]["results"]:
            print(f"  🏆 {PRIZES[r['prize']]}: 『{r['title']}』({r['author']})")
        return True
    if not c or c["status"] == "closed":
        # 前回の審査から少し間をあけて次を開催する
        gap = float((cfg.get("contest") or {}).get("gap_hours", 12))
        if not c or datetime.now(timezone.utc) - datetime.fromisoformat(c["judged_at"]) >= timedelta(hours=gap):
            _open_new(cfg, rng)
            return True
    return False
