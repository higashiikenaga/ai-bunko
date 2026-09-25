"""長期記憶ストア。

方針:
  - AI(LLM)の文脈窓には全履歴を入れきれないので、
    「正典(canon)」となる事実はシステム側のJSONファイルで別管理する。
  - 章を書くたびに、直前章の要約とその章で確定した新事実を
    このJSONに追記し、次章の生成時にはJSONから必要分だけを
    プロンプトへ再注入する(RAG的な状態管理)。
  - キャラクターシートや世界観設定(YAML)は「不変の正典」として
    毎回丸ごと注入し、可変情報(進行中の出来事)はJSON側に積む。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryStore:
    def __init__(self, memory_path: Path):
        self.path = Path(memory_path)
        if self.path.exists():
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "chapters": [],       # [{index, title, summary, new_facts, word_count, created_at}]
                "facts": [],          # 確定した恒久的事実の一覧(矛盾防止用)
                "open_threads": [],   # 未回収の伏線
                "character_state": {},# {キャラ名: {現在地, 感情/立場の変化など}}
            }
            self._save()

    # ---------- 永続化 ----------
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 書き込み ----------
    def add_chapter(
        self,
        index: int,
        title: str,
        summary: str,
        new_facts: list[str] | None = None,
        word_count: int = 0,
    ) -> None:
        self.data["chapters"].append(
            {
                "index": index,
                "title": title,
                "summary": summary,
                "new_facts": new_facts or [],
                "word_count": word_count,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        for fact in new_facts or []:
            if fact not in self.data["facts"]:
                self.data["facts"].append(fact)
        self._save()

    def add_open_thread(self, thread: str) -> None:
        if thread not in self.data["open_threads"]:
            self.data["open_threads"].append(thread)
            self._save()

    def resolve_open_thread(self, thread: str) -> None:
        if thread in self.data["open_threads"]:
            self.data["open_threads"].remove(thread)
            self._save()

    def update_character_state(self, name: str, **fields: Any) -> None:
        self.data["character_state"].setdefault(name, {}).update(fields)
        self._save()

    def set_chapter_published(self, index: int, value: bool) -> None:
        for c in self.data["chapters"]:
            if c["index"] == index:
                c["published"] = value
        self._save()

    def remove_chapter(self, index: int) -> None:
        """章削除。削除後も欠番のまま扱い、他の章番号は振り直さない
        (本文ファイル名やnext_chapter_indexとの整合を単純に保つため)。"""
        self.data["chapters"] = [c for c in self.data["chapters"] if c["index"] != index]
        self._save()

    # ---------- 読み出し ----------
    def last_chapters(self, n: int = 2) -> list[dict[str, Any]]:
        return self.data["chapters"][-n:]

    def next_chapter_index(self) -> int:
        """削除で欠番が出てもファイル名が衝突しないよう、既存の最大indexの次を返す。"""
        if not self.data["chapters"]:
            return 1
        return max(c["index"] for c in self.data["chapters"]) + 1

    def build_context_bundle(self, recent_n: int = 2, max_facts: int = 40) -> str:
        """次章生成プロンプトに差し込む「システム側が覚えている事実」のまとめ。"""
        lines: list[str] = []

        facts = self.data["facts"][-max_facts:]
        if facts:
            lines.append("## 確定した事実(矛盾させないこと)")
            lines.extend(f"- {f}" for f in facts)

        if self.data["open_threads"]:
            lines.append("\n## 未回収の伏線")
            lines.extend(f"- {t}" for t in self.data["open_threads"])

        if self.data["character_state"]:
            lines.append("\n## 各キャラクターの現在の状態")
            for name, state in self.data["character_state"].items():
                state_str = ", ".join(f"{k}: {v}" for k, v in state.items())
                lines.append(f"- {name}: {state_str}")

        recent = self.last_chapters(recent_n)
        if recent:
            lines.append("\n## 直近のあらすじ")
            for ch in recent:
                lines.append(f"- 第{ch['index']}章「{ch['title']}」: {ch['summary']}")

        return "\n".join(lines)
