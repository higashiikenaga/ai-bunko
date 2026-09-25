"""特別企画「AI×運営の考えた設定」。運営が specials/*.yaml に設定と全話の構成表を用意し、本文はAIが1話ずつ書く。
複数部構成のシリーズは、前の部が完結すると次の部の作品が自動で作られる。
通常の作家の順番とは別枠で、前の話から min_hours_between 時間たつたびに1話ずつ更新する。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from ainovel.novel import Novel, all_novels
from ainovel.paths import ROOT

SPECIALS_DIR = ROOT / "specials"
NUMERALS = ["", "", "II", "III", "IV", "V"]


def load_specials() -> list[dict]:
    if not SPECIALS_DIR.exists():
        return []
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(SPECIALS_DIR.glob("*.yaml"))]


def part_title(spec: dict, part: dict) -> str:
    if part["part"] == 1:
        return spec["series_title"]
    return f"{spec['series_title']} {NUMERALS[part['part']]} ―{part['title']}―"


def series_novels(spec_id: str, novels: list[Novel] | None = None) -> list[Novel]:
    found = [n for n in (novels or all_novels()) if (n.meta.get("special") or {}).get("id") == spec_id]
    return sorted(found, key=lambda n: n.meta["special"]["part"])


def _previous_summary(prev: list[Novel]) -> str:
    """前の部までのあらすじ(各話の要約をつなげたもの)。次の部を書くときの正典になる。"""
    lines = []
    for n in prev:
        lines.append(f"【{n.meta['title']}】")
        lines += [f"- 第{i}話: {c.get('summary', '')}" for i, c in enumerate(n.chapters, 1)]
    return "\n".join(lines)


def _create_part(spec: dict, part: dict, prev: list[Novel]) -> Novel:
    world = dict(spec["world"])
    world.update({
        "title": part_title(spec, part),
        "premise": part["premise"].strip(),
        "plot_outline": [f"第{i}話: {b}" for i, b in enumerate(part["chapters"], 1)],
        # 以下は特別企画だけの項目(prompts.chapter_prompt が読む)
        "chapter_beats": part["chapters"],
        "policy": spec.get("policy", ""),
        "part_ending": part.get("ending", ""),
        "series": f"全{len(spec['parts'])}部作の第{part['part']}部",
        "previous_parts": _previous_summary(prev),
        "chapter_chars": spec.get("chapter_chars"),
    })
    novel = Novel.create(world, spec["characters"], len(part["chapters"]), "")
    novel.meta.update({
        "author": spec["author"],
        "genre": world.get("genre", "特別企画"),
        "special": {"id": spec["id"], "label": spec.get("label", "特別企画"), "series_title": spec["series_title"],
                    "part": part["part"], "parts": len(spec["parts"]), "subtitle": part["title"]},
    })
    novel.save_meta()
    print(f"■ 特別企画: 『{novel.meta['title']}』(全{len(part['chapters'])}話)を開始 → {novel.id}")
    return novel


def ensure_parts() -> list[Novel]:
    """まだ始まっていない部を作る(前の部が完結していれば)。連載中の特別企画作品を返す。"""
    novels = all_novels()
    ongoing = []
    for spec in load_specials():
        existing = series_novels(spec["id"], novels)
        done = {n.meta["special"]["part"] for n in existing}
        for part in spec["parts"]:
            if part["part"] in done:
                continue
            prev = [n for n in existing if n.meta["special"]["part"] < part["part"]]
            prev = [n for n in prev if not n.meta["special"].get("gaiden")]
            if all(n.meta.get("status") == "completed" for n in prev) and len(prev) == part["part"] - 1:
                existing.append(_create_part(spec, part, prev))
            break
        ongoing += [n for n in existing if n.is_ongoing]
    return ongoing


def due_novel(skip: set[str]) -> Novel | None:
    """いま更新する番の特別企画作品(前の話から min_hours_between 時間たっていれば)。"""
    specs = {s["id"]: s for s in load_specials()}
    for n in ensure_parts():
        if n.id in skip:
            continue
        spec = specs.get(n.meta["special"]["id"], {})
        wait = timedelta(hours=float(spec.get("min_hours_between", 2)))
        last = datetime.fromisoformat(n.meta["updated_at"]) if n.chapters else datetime.min.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last >= wait:
            return n
    return None


def is_special(novel: Novel) -> bool:
    return bool(novel.meta.get("special"))


# ---------- 外伝 ----------
# 本編(全部)が完結したあと、シリーズの評価(評価AI+人間の★)が高ければ、AIが自分で外伝を企画して書く。
# 外伝が続くかどうかも、直前の外伝の評価しだい。設定は specials/*.yaml の gaiden。

def _rating(novels: list[Novel], human: dict) -> tuple[float, int]:
    from ainovel.review import load_reviews

    total = count = 0.0
    for n in novels:
        for r in load_reviews(n):
            total += r["score"]
            count += 1
        h = human.get(n.id)
        if h:
            total += float(h.get("avg", 0)) * int(h.get("count", 0))
            count += int(h.get("count", 0))
    return (total / count if count else 0.0), int(count)


def gaiden_candidate(site_url: str = "") -> dict | None:
    """外伝を始められるシリーズの設定。なければ None。"""
    from ainovel.feedback import fetch_human_ratings

    for spec in load_specials():
        g = spec.get("gaiden") or {}
        if not g:
            continue
        works = series_novels(spec["id"])
        main = [n for n in works if n.meta["special"]["part"] <= len(spec["parts"])]
        side = [n for n in works if n.meta["special"].get("gaiden")]
        if len(main) < len(spec["parts"]) or any(n.meta.get("status") != "completed" for n in works):
            continue  # 本編が終わっていない、または外伝の連載中
        if len(side) >= int(g.get("max", 3)):
            continue
        human = fetch_human_ratings(site_url)
        # 最初の外伝は本編全体の評価、2本目以降は直前の外伝の評価で決める
        judged = side[-1:] if side else main
        avg, count = _rating(judged, human)
        if count >= int(g.get("min_reviews", 10 if not side else 3)) and avg >= float(g.get("min_avg", 3.8)):
            print(f"■ 特別企画: 『{spec['series_title']}』の評価が高い(★{avg:.1f}・{count}件)ため、外伝を企画します")
            return spec
    return None


def gaiden_prompt(spec: dict, works: list[Novel], number: int, chapters: int) -> str:
    summary = _previous_summary(works)
    done = [n.meta["special"].get("subtitle", "") for n in works if n.meta["special"].get("gaiden")]
    ideas = "\n".join(f"- {i}" for i in (spec.get("gaiden") or {}).get("ideas") or []) or "(なし。自由に考える)"
    return f"""運営の考えた設定で書かれた全{len(spec['parts'])}部作『{spec['series_title']}』の本編が完結し、読者の評価が高かったため、外伝を企画してください。

# 執筆方針(本編と同じ。必ず守る)
{spec.get('policy', '').strip()}

# 本編のあらすじ(正典。これと矛盾しないこと)
{summary}

# これまでの外伝
{"、".join(done) or "(まだない)"}

# 運営が挙げた外伝の候補(参考。ほかの題材でもよい)
{ideas}

# 条件
- 外伝第{number}弾。全{chapters}話。本編の結末や人物の選択を覆さない
- 本編で描ききれなかった時間・場所・人物に光を当てる(過去編、脇役の視点、その後の日々など)
- これまでの外伝と題材を重ねない

# 出力JSON形式
{{
  "subtitle": "外伝のサブタイトル(15文字以内)",
  "premise": "あらすじ(3〜4文。読者を引き込む紹介文)",
  "focus": "中心になる人物・時期",
  "ending": "この外伝の結末で描くこと(1文)",
  "chapters": ["第1話で描くこと(2〜3文)", ... 全{chapters}話分]
}}"""


def create_gaiden(llm, spec: dict, chat_json) -> Novel:
    import random

    g = spec.get("gaiden") or {}
    works = series_novels(spec["id"])
    number = sum(1 for n in works if n.meta["special"].get("gaiden")) + 1
    lo, hi = (g.get("chapters") or [5, 8])[:2]
    chapters = random.randint(int(lo), int(hi))
    plan = chat_json(llm, gaiden_prompt(spec, works, number, chapters), max_tokens=6144, temperature=0.9)
    beats = [str(b) for b in plan.get("chapters") or [] if str(b).strip()]
    if len(beats) < 3:
        raise ValueError("外伝の構成表を作れませんでした")
    subtitle = str(plan.get("subtitle", f"外伝{number}")).strip()[:20]
    part = {"part": len(spec["parts"]) + number, "title": subtitle, "premise": str(plan.get("premise", "")),
            "ending": str(plan.get("ending", "")), "chapters": beats}
    novel = _create_part(spec, part, works)
    novel.meta["title"] = f"{spec['series_title']} 外伝{number if number > 1 else ''} ―{subtitle}―"
    novel.meta["special"].update({"gaiden": number, "subtitle": f"外伝{number if number > 1 else ''} {subtitle}",
                                  "focus": str(plan.get("focus", ""))})
    novel.save_meta()
    w = novel.world
    w.update({"title": novel.meta["title"], "series": f"本編(全{len(spec['parts'])}部作)完結後の外伝第{number}弾"})
    novel._write_yaml("world.yaml", w)
    return novel
