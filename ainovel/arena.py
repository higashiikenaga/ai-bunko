"""AI文庫アリーナ(小説版の Chatbot Arena)。
- 同じお題で2つのモデルが掌編(短い小説)を書き、モデル名を伏せて並べる。人間の読者がどちらが良いか投票する
- 票はサイトのAPI(/api/arena、D1)に集まり、サイトを組み立てるときに集計して Elo レーティングにする
- 書くときは執筆SKILL(skills/writer/SKILL.md)を必ず読み込む。対戦は content/arena.json に保存する
"""
from __future__ import annotations

import hashlib
import json
import random
import urllib.request
from collections import Counter

from ainovel import prompts
from ainovel.llm import LLMError, available_models, chat_with_model
from ainovel.novel import now_iso
from ainovel.paths import ROOT

ARENA_PATH = ROOT / "content" / "arena.json"
USER_AGENT = "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)"
MOTIFS = ["雨の日の約束", "最後の手紙", "開かない扉", "夜行列車", "消えた星", "古い鍵", "双子", "嘘をつくロボット",
          "海辺の図書館", "忘れられた祭り", "壊れた時計", "猫の恩返し", "未来からの電話", "雪の中の足跡", "閉店間際の喫茶店",
          "迷子の幽霊", "魔法の使えない魔法使い", "星を売る店", "最後の一葉", "月面の運動会"]


def load() -> list[dict]:
    return json.loads(ARENA_PATH.read_text(encoding="utf-8")) if ARENA_PATH.exists() else []


def _save(matches: list[dict]) -> None:
    ARENA_PATH.write_text(json.dumps(matches[-300:], ensure_ascii=False, indent=1), encoding="utf-8")


def contenders(llm, cfg: dict) -> list[str]:
    a = cfg.get("arena") or {}
    avail = available_models(llm)
    return [m for m in (a.get("models") or avail) if m in avail]


def _write(llm, model: str, genre: str, motif: str, chars: int) -> str:
    system = f"""あなたは日本語の小説家です。依頼された掌編小説を書きます。

{prompts.skill("writer")}"""
    user = f"""次の条件で、1話で完結する掌編小説を書いてください。

- ジャンル: {genre}
- お題: {motif}
- 分量: 日本語で約{chars}文字(多少前後してよい)
- 1行目に「タイトル: 〇〇」と書き、1行あけて本文を書く
- 起承転結をつけ、最後に印象に残る結末を置く"""
    text = chat_with_model(llm, model, system, user, max_tokens=int(chars * 2) + 1024, temperature=0.9)
    lines = text.strip().splitlines()
    title = ""
    if lines and lines[0].lstrip("#* ").startswith(("タイトル", "題")):
        title = lines[0].split(":", 1)[-1].split("：", 1)[-1].strip(" 「」『』*#")
        lines = lines[1:]
    body = prompts.clean_chapter("\n".join(lines))
    if len(body) < chars * 0.4:
        raise ValueError(f"本文が短すぎます({len(body)}文字)")
    return json.dumps({"title": title[:40], "text": body[: chars * 3]}, ensure_ascii=False)


def make_match(llm, cfg: dict, rng: random.Random | None = None) -> bool:
    """新しい対戦を1つ作る。作れたら True。"""
    rng = rng or random.Random()
    a = cfg.get("arena") or {}
    if not a.get("enabled", True):
        return False
    pool = contenders(llm, cfg)
    if len(pool) < 2:
        return False
    matches = load()
    played = Counter(m for x in matches for m in (x["a"]["model"], x["b"]["model"]))
    rng.shuffle(pool)
    pool.sort(key=lambda m: played[m])  # 対戦の少ないモデルから
    genre = rng.choice(cfg["genres"])
    motif = rng.choice(MOTIFS)
    chars = int(a.get("chars", 1500))
    print(f"■ アリーナ: お題「{motif}」({genre})で対戦を作ります")
    sides = []
    for m in pool:
        if len(sides) == 2:
            break
        try:
            sides.append({"model": m, **json.loads(_write(llm, m, genre, motif, chars))})
            print(f"  {m}: 「{sides[-1]['title']}」{len(sides[-1]['text'])}文字")
        except (LLMError, ValueError) as e:
            print(f"  ✗ {m}: {str(e)[:150]}")
    if len(sides) < 2:
        return False
    rng.shuffle(sides)  # どちらがAかは毎回ランダム
    at = now_iso()
    mid = hashlib.sha1(f"{at}{motif}{sides[0]['model']}{sides[1]['model']}".encode()).hexdigest()[:10]
    matches.append({"id": mid, "genre": genre, "motif": motif, "at": at, "a": sides[0], "b": sides[1]})
    _save(matches)
    return True


def fetch_votes(site_url: str) -> dict:
    """サイトのAPIから対戦ごとの票数 {対戦ID: {a, b, tie, bad}} を取る(失敗したら空)。"""
    if not site_url:
        return {}
    try:
        req = urllib.request.Request(f"{site_url.rstrip('/')}/api/arena", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res).get("votes", {})
    except Exception as e:  # noqa: BLE001
        print(f"  (アリーナの票を取得できませんでした: {e})")
        return {}


def leaderboard(votes: dict) -> list[dict]:
    """票から Elo レーティングを計算する(初期1000、K=24、引き分けは0.5)。"""
    rating: dict[str, float] = {}
    stats: dict[str, Counter] = {}
    for m in sorted(load(), key=lambda x: x["at"]):
        v = votes.get(m["id"]) or {}
        a, b = m["a"]["model"], m["b"]["model"]
        for x in (a, b):
            rating.setdefault(x, 1000.0)
            stats.setdefault(x, Counter())
        results = [1.0] * int(v.get("a", 0)) + [0.0] * int(v.get("b", 0)) + [0.5] * (int(v.get("tie", 0)) + int(v.get("bad", 0)))
        for s in results:
            ea = 1 / (1 + 10 ** ((rating[b] - rating[a]) / 400))
            rating[a] += 24 * (s - ea)
            rating[b] -= 24 * (s - ea)
            stats[a]["votes"] += 1
            stats[b]["votes"] += 1
            stats[a]["win"] += s == 1.0
            stats[b]["win"] += s == 0.0
            stats[a]["tie"] += s == 0.5
            stats[b]["tie"] += s == 0.5
        stats[a]["matches"] += 1
        stats[b]["matches"] += 1
    board = [{"model": m, "elo": round(r), "votes": stats[m]["votes"], "matches": stats[m]["matches"],
              "win": stats[m]["win"], "tie": stats[m]["tie"],
              "win_rate": round(stats[m]["win"] / stats[m]["votes"] * 100) if stats[m]["votes"] else None}
             for m, r in rating.items()]
    return sorted(board, key=lambda x: (-(x["votes"] > 0), -x["elo"]))
