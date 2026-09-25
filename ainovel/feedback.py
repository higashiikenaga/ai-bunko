"""読者の評価を物語に反映する。

- 次の章を書くとき、ROM専AIの感想(良かった点・気になった点)と観点別の点数、人間の★の平均を
  「読者の反応」としてプロンプトに入れ、強みを伸ばし弱い観点を補うよう促す(設定・伏線は変えない)
- 評価の高い人気作は、完結の2話前の時点で話数を延長する(1作品1回)
"""
from __future__ import annotations

import json
import urllib.request

from ainovel.novel import Novel
from ainovel.review import AXES, load_reviews

USER_AGENT = "ai-bunko-writer/1.0 (+https://github.com/higashiikenaga/ai-bunko)"

# 点数が低いときに作家へ伝える、観点ごとの改善の方向
AXIS_ADVICE = {
    "story": "展開のテンポを上げ、各話に明確な山場や変化を作る",
    "characters": "登場人物の内面や動機をもっと描き、会話で個性を際立たせる",
    "writing": "文章のリズムを整え、冗長な説明を減らして情景を具体的に描く",
    "originality": "ありきたりな展開を避け、予想を少し裏切る要素を入れる",
}

_human_cache: dict[str, dict] | None = None


def fetch_human_ratings(site_url: str) -> dict:
    """公開サイトのAPIから人間の★評価を取得する(失敗したら空。執筆は止めない)。"""
    global _human_cache
    if _human_cache is not None:
        return _human_cache
    _human_cache = {}
    if not site_url:
        return _human_cache
    try:
        req = urllib.request.Request(f"{site_url.rstrip('/')}/api/ratings", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as res:
            _human_cache = json.load(res).get("ratings", {})
    except Exception as e:  # noqa: BLE001
        print(f"  (人間の評価を取得できませんでした: {e})")
    return _human_cache


def summarize(novel: Novel, human: dict, recent: int = 5) -> dict | None:
    """作品の評価のまとめ。評価が1件もなければ None。"""
    reviews = sorted(load_reviews(novel), key=lambda r: r["created_at"], reverse=True)
    h = human.get(novel.id) or {}
    if not reviews and not h:
        return None
    ai_sum = sum(r["score"] for r in reviews)
    total = ai_sum + h.get("avg", 0) * h.get("count", 0)
    count = len(reviews) + h.get("count", 0)
    axes = {}
    for key in AXES:
        vals = [r["scores"][key] for r in reviews if key in (r.get("scores") or {})]
        if vals:
            axes[key] = round(sum(vals) / len(vals), 1)
    return {
        "ai_avg": round(ai_sum / len(reviews), 1) if reviews else None,
        "ai_count": len(reviews),
        "human_avg": h.get("avg"),
        "human_count": h.get("count", 0),
        "avg": round(total / count, 2) if count else None,
        "count": count,
        "axes": axes,
        "recent": reviews[:recent],
    }


def prompt_block(s: dict | None) -> str:
    """章のプロンプトに入れる「読者の反応」。評価がなければ空文字。"""
    if not s:
        return ""
    lines = ["# 読者の反応(参考)"]
    if s["ai_count"]:
        lines.append(f"- AI読者の評価: ★{s['ai_avg']}({s['ai_count']}件)")
    if s["human_count"]:
        lines.append(f"- 人間の読者の評価: ★{s['human_avg']}({s['human_count']}件)")
    if s["axes"]:
        lines.append("- 観点別: " + " / ".join(f"{AXES[k]} {v}" for k, v in s["axes"].items()))
    goods = [r["good"] for r in s["recent"] if r.get("good")]
    bads = [r["bad"] for r in s["recent"] if r.get("bad")]
    if goods:
        lines.append("- 好評だった点: " + "、".join(dict.fromkeys(goods)))
    if bads:
        lines.append("- 気になると言われた点: " + "、".join(dict.fromkeys(bads)))
    weak = [k for k, v in s["axes"].items() if v < 3.0]
    strong = [k for k, v in s["axes"].items() if v >= 4.0]
    lines.append("")
    lines.append("この反応を踏まえて、次のように書いてください。")
    if strong:
        lines.append(f"- 好評な「{'・'.join(AXES[k] for k in strong)}」の良さは保ち、さらに伸ばす")
    for k in weak:
        lines.append(f"- 「{AXES[k]}」の評価が低めなので、{AXIS_ADVICE[k]}")
    if bads and not weak:
        lines.append("- 気になると言われた点を、物語の流れの中で自然に改善する")
    lines.append("- ただし、登場人物の設定・世界のルール・これまでに確定した事実は変えない。読者に媚びず、作家としての書き方を保つ")
    return "\n".join(lines)


def maybe_extend(novel: Novel, s: dict | None, index: int, cfg: dict) -> bool:
    """人気作なら、完結の2話前の時点で予定話数を延ばす(1作品1回)。延ばしたら True。"""
    fb = cfg.get("feedback") or {}
    total = int(novel.meta["target_chapters"])
    if not s or novel.meta.get("extended") or novel.meta.get("special") or index != total - 1:
        return False
    if s["count"] < int(fb.get("extend_min_ratings", 3)) or (s["avg"] or 0) < float(fb.get("extend_min_avg", 4.0)):
        return False
    add = int(fb.get("extend_chapters", 2))
    novel.meta["target_chapters"] = total + add
    novel.meta["extended"] = {"from": total, "to": total + add, "avg": s["avg"], "count": s["count"]}
    novel.save_meta()
    print(f"  人気作(★{s['avg']}・{s['count']}件)のため、全{total}話 → 全{total + add}話に延長します")
    return True
