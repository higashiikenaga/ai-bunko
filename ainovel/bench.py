"""AI文庫inside: AIベンチマーク。サイトでAIが書いた章を、いろいろなモデルに採点させる。
- 採点は伏せ字(書いたモデル名は審査モデルに教えない)。基準は skills/judge/SKILL.md で、毎回必ず読み込む
- 観点: 文章構造力・日本語力・人物描写・独創性・一貫性(各1〜10)
- 1回の実行で1章を選び、まだその章を採点していないモデルの中から、採点数の少ないモデルに採点させる
- 結果は content/bench.jsonl に1行ずつ追記する(執筆モデル × 審査モデル の表・自己評価の偏りも集計する)
"""
from __future__ import annotations

import json
import random
from collections import defaultdict

from ainovel import prompts
from ainovel.llm import LLMError, available_models, chat_with_model
from ainovel.novel import all_novels, now_iso
from ainovel.paths import ROOT

BENCH_PATH = ROOT / "content" / "bench.jsonl"
AXES = {"structure": "文章構造力", "japanese": "日本語力", "character": "人物描写",
        "originality": "独創性", "consistency": "一貫性"}


def load() -> list[dict]:
    if not BENCH_PATH.exists():
        return []
    out = []
    for line in BENCH_PATH.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(rec: dict) -> None:
    with BENCH_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def judges(llm, cfg: dict) -> list[str]:
    b = cfg.get("bench") or {}
    avail = available_models(llm)
    return [m for m in (b.get("judges") or avail) if m in avail]


def _prompt(n, ch: dict, text: str) -> str:
    w = n.world
    before = "\n".join(f"- 第{c['index']}話: {c.get('summary', '')}" for c in n.chapters if c["index"] < ch["index"])[-1500:]
    return f"""# 作品
タイトル: {n.meta['title']} / ジャンル: {n.meta.get('genre', '')}
あらすじ: {w.get('premise', '')}

# ここまでの流れ
{before or '(これが第1話)'}

# 採点する章: 第{ch['index']}話「{ch.get('title', '')}」
{text[:9000]}"""


def _parse(raw: str) -> dict:
    data = prompts.parse_json(raw)
    scores = {}
    for k in AXES:
        v = int(float(data[k]))
        if not 1 <= v <= 10:
            raise ValueError(f"{k} が範囲外: {v}")
        scores[k] = v
    return {"scores": scores, "comment": str(data.get("comment", "")).strip()[:200]}


def judge_some(llm, cfg: dict, rng: random.Random | None = None) -> int:
    """1章を選び、何モデルかに採点させる。採点した件数を返す。"""
    rng = rng or random.Random()
    b = cfg.get("bench") or {}
    if not b.get("enabled", True):
        return 0
    js = judges(llm, cfg)
    if not js:
        return 0
    recs = load()
    done = defaultdict(set)
    per_judge = defaultdict(int)
    for r in recs:
        done[(r["novel"], r["chapter"])].add(r["judge"])
        per_judge[r["judge"]] += 1
    cands = [(n, ch) for n in all_novels() for ch in n.chapters
             if ch.get("model") and not n.meta.get("special") and len(done[(n.id, ch["index"])]) < len(js)]
    if not cands:
        return 0
    # 採点の少ない章を優先(同じくらいならランダム)
    rng.shuffle(cands)
    n, ch = min(cands, key=lambda t: len(done[(t[0].id, t[1]["index"])]))
    todo = sorted((j for j in js if j not in done[(n.id, ch["index"])]), key=lambda j: (per_judge[j], rng.random()))
    todo = todo[:int(b.get("judges_per_run", 3))]
    text = n.chapter_text(ch["index"])
    user = _prompt(n, ch, text)
    system = prompts.skill("judge")
    count = 0
    print(f"■ ベンチ採点: 『{n.meta['title']}』第{ch['index']}話(執筆 {ch['model']})を {', '.join(todo)} が採点")
    for j in todo:
        try:
            res = _parse(chat_with_model(llm, j, system, user, max_tokens=2048, temperature=0.2))
        except (LLMError, ValueError, KeyError, TypeError) as e:
            print(f"  ✗ {j}: {str(e)[:150]}")
            continue
        _append({"novel": n.id, "chapter": ch["index"], "writer": ch["model"], "judge": j,
                 **res, "chars": len(text), "at": now_iso()})
        count += 1
        print(f"  {j}: " + " ".join(f"{AXES[k]}{v}" for k, v in res["scores"].items()))
    return count


def _mean(xs) -> float | None:
    xs = list(xs)
    return round(sum(xs) / len(xs), 2) if xs else None


def summary() -> dict:
    """サイト表示用の集計。"""
    recs = load()
    for r in recs:
        r["total"] = sum(r["scores"].values()) / len(AXES)
    writers = sorted({r["writer"] for r in recs})
    js = sorted({r["judge"] for r in recs})
    board = []
    for w in writers:
        rs = [r for r in recs if r["writer"] == w]
        others = [r for r in rs if r["judge"] != w]  # 自分の作品への自己採点を除いた値
        board.append({"model": w, "n": len(rs), "chapters": len({(r["novel"], r["chapter"]) for r in rs}),
                      "total": _mean(r["total"] for r in (others or rs)),
                      "axes": {k: _mean(r["scores"][k] for r in (others or rs)) for k in AXES}})
    board.sort(key=lambda x: -(x["total"] or 0))
    matrix = {j: {w: _mean(r["total"] for r in recs if r["judge"] == j and r["writer"] == w) for w in writers} for j in js}
    judge_stats = []
    for j in js:
        rs = [r for r in recs if r["judge"] == j]
        own = [r["total"] for r in rs if r["writer"] == j]
        others_on_own = [r["total"] for r in recs if r["writer"] == j and r["judge"] != j]
        judge_stats.append({"model": j, "n": len(rs), "mean": _mean(r["total"] for r in rs),
                            "self_bias": round(_mean(own) - _mean(others_on_own), 2) if own and others_on_own else None})
    # 章ごとの平均点と、モデルごとの代表作
    by_ch = defaultdict(list)
    for r in recs:
        by_ch[(r["novel"], r["chapter"], r["writer"])].append(r)
    titles = {n.id: n.meta["title"] for n in all_novels()}
    chapters = [{"novel": k[0], "chapter": k[1], "writer": k[2], "title": titles.get(k[0], k[0]),
                 "total": _mean(r["total"] for r in v), "judges": len(v),
                 "comments": [{"judge": r["judge"], "comment": r["comment"], "total": round(r["total"], 1)} for r in v]}
                for k, v in by_ch.items() if k[0] in titles]
    chapters.sort(key=lambda c: -(c["total"] or 0))
    best = {w: [c for c in chapters if c["writer"] == w][:3] for w in writers}
    return {"axes": AXES, "board": board, "writers": writers, "judges": js, "matrix": matrix,
            "judge_stats": judge_stats, "best": best, "recent": sorted(recs, key=lambda r: r["at"], reverse=True)[:20],
            "count": len(recs), "chapters": len(chapters), "titles": titles}
