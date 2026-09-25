"""海外の読者向けに、作品のタイトルとあらすじを英語・繁体字中国語に翻訳しておく(本文は翻訳しない)。
1回の実行で数作品ずつ、まだ翻訳のない作品(またはあらすじが変わった作品)を翻訳して novel.json の i18n に保存する。
サイトでは言語切り替え(English / 繁體中文)で差し替えて表示する。"""
from __future__ import annotations

import hashlib

from ainovel import prompts
from ainovel.novel import all_novels

LANGS = {"en": "English", "zh": "Traditional Chinese (Taiwan, 繁體中文)"}
SYSTEM = "You are a professional literary translator for a Japanese web novel site. Output JSON only, with no extra text or code fences."


def _key(novel) -> str:
    return hashlib.md5((novel.meta["title"] + (novel.world.get("premise") or "")).encode()).hexdigest()[:10]


def translate_some(llm, count: int = 3) -> int:
    """翻訳のない(または内容が変わった)作品を count 件まで翻訳する。"""
    done = 0
    for n in sorted(all_novels(), key=lambda n: n.meta.get("updated_at", ""), reverse=True):
        if done >= count:
            break
        if not n.chapters or n.meta.get("status") == "abandoned":
            continue
        key = _key(n)
        if (n.meta.get("i18n") or {}).get("key") == key:
            continue
        user = f"""Translate this Japanese web novel's title and blurb for overseas readers. Keep character names in natural romanization (English) / keep kanji names (Chinese). Make the blurb enticing but faithful; do not add spoilers or new facts.

Title: {n.meta['title']}
Genre: {n.meta.get('genre', '')}
Blurb: {n.world.get('premise', '')}

{{"en": {{"title": "...", "premise": "..."}}, "zh": {{"title": "...", "premise": "..."}}}}"""
        data = prompts.parse_json(llm.chat(SYSTEM, user, max_tokens=2048, temperature=0.3))
        out = {"key": key}
        for lang in LANGS:
            d = data.get(lang) or {}
            if str(d.get("title", "")).strip():
                out[lang] = {"title": str(d["title"]).strip()[:120], "premise": str(d.get("premise", "")).strip()[:800]}
        if len(out) > 1:
            n.meta["i18n"] = out
            n.save_meta()
            done += 1
            print(f"■ 翻訳: 『{n.meta['title']}』→ {out.get('en', {}).get('title', '')}")
    return done
