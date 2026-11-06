"""投稿された1作品分のデータ(content/novels/<id>/)の読み込み。

content/novels/<id>/
  novel.json       作品情報と、各話の一覧(index・title・word_count・created_at)
  chapters/001.md  各話の本文

novel.json の主な項目:
  id / title / author(ペンネーム) / genre / premise(あらすじ)
  status      ongoing | completed | hidden(運営が非表示にした作品。サイトには出ない)
  ai_zone     AI生成の区分(config.yaml の zones の key)。投稿者の自己申告
  ai_tools    使ったAI(自由記入)
  edit_key_hash  続きの投稿に使う「編集キー」のSHA-256(キー本体は保存しない)
  chapters    [{index, title, word_count, created_at}]
作品は functions/api/submit.js が GitHub に直接コミットして追加する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ainovel.paths import CONTENT_DIR


class Novel:
    def __init__(self, novel_id: str):
        self.id = novel_id
        self.dir = CONTENT_DIR / novel_id
        self.meta: dict[str, Any] = json.loads((self.dir / "novel.json").read_text(encoding="utf-8"))

    @property
    def chapters(self) -> list[dict]:
        return self.meta.get("chapters") or []

    def chapter_text(self, index: int) -> str:
        return (self.dir / "chapters" / f"{index:03d}.md").read_text(encoding="utf-8")


def all_novels() -> list[Novel]:
    if not CONTENT_DIR.exists():
        return []
    return [Novel(p.name) for p in sorted(CONTENT_DIR.iterdir()) if (p / "novel.json").exists()]
