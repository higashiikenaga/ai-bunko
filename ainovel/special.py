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
