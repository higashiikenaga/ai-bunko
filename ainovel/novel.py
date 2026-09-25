"""1作品分のデータ(content/novels/<id>/)の読み書き。

content/novels/<id>/
  novel.json       作品情報(タイトル・状態・予定章数など)
  world.yaml       世界観(AIが企画)
  characters.yaml  登場人物(AIが設計)
  memory.json      長期記憶(StoRyanと同じ形式。章の要約・確定事実・伏線)
  chapters/001.md  本文
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ainovel.memory import MemoryStore
from ainovel.paths import CONTENT_DIR


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Novel:
    def __init__(self, novel_id: str):
        self.id = novel_id
        self.dir = CONTENT_DIR / novel_id
        self.meta_path = self.dir / "novel.json"
        self.chapters_dir = self.dir / "chapters"
        self.meta: dict[str, Any] = (
            json.loads(self.meta_path.read_text(encoding="utf-8")) if self.meta_path.exists() else {}
        )

    # ---------- 作成 ----------
    @classmethod
    def create(cls, world: dict, characters: list[dict], target_chapters: int, model: str) -> "Novel":
        novel_id = datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(3)
        novel = cls(novel_id)
        novel.chapters_dir.mkdir(parents=True, exist_ok=True)
        novel._write_yaml("world.yaml", world)
        novel._write_yaml("characters.yaml", characters)
        novel.meta = {
            "id": novel_id,
            "title": world.get("title") or "無題",
            "genre": world.get("genre", ""),
            "status": "ongoing",
            "target_chapters": target_chapters,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "models": [model] if model else [],
        }
        novel.save_meta()
        MemoryStore(novel.dir / "memory.json")  # 空の長期記憶を作成
        return novel

    # ---------- 読み書き ----------
    def _write_yaml(self, name: str, data: Any) -> None:
        (self.dir / name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def save_meta(self) -> None:
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def world(self) -> dict:
        return yaml.safe_load((self.dir / "world.yaml").read_text(encoding="utf-8")) or {}

    @property
    def characters(self) -> list[dict]:
        return yaml.safe_load((self.dir / "characters.yaml").read_text(encoding="utf-8")) or []

    @property
    def memory(self) -> MemoryStore:
        return MemoryStore(self.dir / "memory.json")

    @property
    def chapters(self) -> list[dict]:
        return self.memory.data["chapters"]

    def chapter_path(self, index: int) -> Path:
        # gitは空フォルダを記録しないので、新作の chapters/ はチェックアウト後に存在しないことがある
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        return self.chapters_dir / f"{index:03d}.md"

    def chapter_text(self, index: int) -> str:
        return self.chapter_path(index).read_text(encoding="utf-8")

    def last_tail(self, chars: int = 1500) -> str:
        if not self.chapters:
            return ""
        return self.chapter_text(self.chapters[-1]["index"])[-chars:]

    @property
    def is_ongoing(self) -> bool:
        return self.meta.get("status") == "ongoing"

    def touch(self, model: Optional[str] = None) -> None:
        self.meta["updated_at"] = now_iso()
        if model and model not in self.meta.setdefault("models", []):
            self.meta["models"].append(model)
        self.save_meta()


def all_novels() -> list[Novel]:
    if not CONTENT_DIR.exists():
        return []
    return [Novel(p.name) for p in sorted(CONTENT_DIR.iterdir()) if (p / "novel.json").exists()]
