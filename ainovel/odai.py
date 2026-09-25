"""お題箱。人間がサイトから送った「お題」(短い言葉)を、AI作家が新作のモチーフに使う。
使ったお題は content/odai_used.json に記録し、サイトの「参加コーナー」に「このお題から生まれた作品」として出す。"""
from __future__ import annotations

import json
import random
import urllib.request

from ainovel.paths import ROOT

USED_PATH = ROOT / "content" / "odai_used.json"
USER_AGENT = "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)"


def load_used() -> list[dict]:
    return json.loads(USED_PATH.read_text(encoding="utf-8")) if USED_PATH.exists() else []


def fetch(site_url: str) -> list[dict]:
    if not site_url:
        return []
    try:
        req = urllib.request.Request(f"{site_url.rstrip('/')}/api/odai", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res).get("odai", [])
    except Exception as e:  # noqa: BLE001
        print(f"  (お題箱を取得できませんでした: {e})")
        return []


def pick(site_url: str, rng: random.Random | None = None) -> str | None:
    """まだ使っていないお題を1つ選ぶ(たくさん届いたお題ほど選ばれやすい)。"""
    rng = rng or random.Random()
    used = {u["word"] for u in load_used()}
    fresh = [o for o in fetch(site_url) if o.get("word") and o["word"] not in used]
    if not fresh:
        return None
    return rng.choices(fresh, weights=[int(o.get("count", 1)) for o in fresh])[0]["word"]


def mark_used(word: str, novel_id: str, title: str, author: str, at: str) -> None:
    used = load_used()
    used.append({"word": word, "novel": novel_id, "title": title, "author": author, "at": at})
    USED_PATH.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
