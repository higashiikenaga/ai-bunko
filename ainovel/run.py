"""AI小説家の1回分の仕事。GitHub Actionsから定期的に呼ばれる。

  python -m ainovel.run                 # config.yaml の provider で執筆
  AINOVEL_PROVIDER=mock python -m ainovel.run   # APIなしで動作確認

1回の実行で writing.actions_per_run 回まで、次のどちらかを行う:
  - 連載数が max_ongoing 未満なら、新作を企画する(世界観・登場人物をAIが考える)
  - それ以外は、まだ章のない作品 → 最も長く更新されていない連載作品 の順で次の章を書く
    予定章数に達したら完結にする
"""
from __future__ import annotations

import io
import random
import sys
import traceback

from ainovel import prompts
from ainovel.llm import BaseLLM, LLMError, make_llm
from ainovel.novel import Novel, all_novels
from ainovel.paths import load_config

MOTIFS = [
    "古い灯台", "壊れた懐中時計", "雨の日だけ開く店", "失われた手紙", "双子", "渡り鳥", "地下図書館",
    "最終列車", "記憶を売る市場", "鏡", "約束の丘", "蒸気機関", "月食", "眠らない街", "海底遺跡",
    "名前のない猫", "遺言", "折れた剣", "星図", "祭りの夜", "氷の城", "迷子の人工知能", "古本屋",
    "嘘をつけない呪い", "砂漠のオアシス", "空飛ぶ船", "秘密の地図", "消えた村", "夜明けのラジオ", "桜",
]


def create_novel(llm: BaseLLM, cfg: dict) -> Novel:
    novels = all_novels()
    recent_genres = [n.meta.get("genre") for n in novels[-3:]]
    genres = [g for g in cfg["genres"] if g not in recent_genres] or cfg["genres"]
    genre = random.choice(genres)
    motifs = random.sample(MOTIFS, 2)
    print(f"■ 新作を企画: {genre} / モチーフ {motifs}")

    world = prompts.parse_json(
        llm.chat(prompts.EDITOR_SYSTEM, prompts.world_prompt(genre, motifs, [n.meta["title"] for n in novels]),
                 max_tokens=2048, temperature=1.0)
    )
    world["genre"] = world.get("genre") or genre
    print(f"  タイトル: {world.get('title')}")

    chars = prompts.parse_json(
        llm.chat(prompts.EDITOR_SYSTEM, prompts.characters_prompt(world), max_tokens=3000, temperature=0.9)
    ).get("characters", [])
    if not chars:
        raise ValueError("登場人物が生成されませんでした")
    print(f"  登場人物: {', '.join(c.get('name', '?') for c in chars)}")

    w = cfg["writing"]
    target = random.randint(int(w["min_chapters"]), int(w["max_chapters"]))
    novel = Novel.create(world, chars, target, llm.last_model)
    print(f"  予定: 全{target}章  → {novel.id}")
    return novel


def write_next_chapter(llm: BaseLLM, novel: Novel, cfg: dict) -> None:
    w = cfg["writing"]
    memory = novel.memory
    index = memory.next_chapter_index()
    total = int(novel.meta["target_chapters"])
    print(f"■ 執筆: 『{novel.meta['title']}』 第{index}章 / 全{total}章")

    world = novel.world
    target_chars = int(w["chapter_chars"])
    text = prompts.clean_chapter(
        llm.chat(
            prompts.system_prompt(world),
            prompts.chapter_prompt(world, novel.characters, memory.build_context_bundle(), novel.last_tail(),
                                   index, total, target_chars),
            max_tokens=int(target_chars * 1.6) + 512,
            temperature=float(w.get("temperature", 0.95)),
        )
    )
    if len(text) < target_chars * 0.25:
        raise ValueError(f"本文が短すぎます({len(text)}文字)。今回は保存しません")
    model = llm.last_model

    try:
        meta = prompts.parse_json(
            llm.chat(prompts.EDITOR_SYSTEM, prompts.summary_prompt(text), max_tokens=1024, temperature=0.2)
        )
    except (ValueError, LLMError) as e:
        print(f"  (要約の生成に失敗したため簡易記録にします: {e})")
        meta = {"title": f"第{index}章", "summary": text[:120], "new_facts": [], "open_threads": []}

    novel.chapter_path(index).write_text(text, encoding="utf-8")
    memory.add_chapter(index=index, title=meta.get("title") or f"第{index}章", summary=meta.get("summary", ""),
                       new_facts=meta.get("new_facts", []), word_count=len(text))
    memory.data["chapters"][-1]["model"] = model
    memory._save()
    for t in meta.get("open_threads", []):
        memory.add_open_thread(t)

    novel.touch(model)
    if index >= total:
        novel.meta["status"] = "completed"
        novel.meta["completed_at"] = novel.meta["updated_at"]
        novel.save_meta()
        print("  → 完結しました")
    print(f"  「{meta.get('title')}」 {len(text)}文字 ({model})")


MAX_FAILURES = 3


def pick_next(cfg: dict, skip: set[str]) -> tuple[str, Novel | None]:
    ongoing = [n for n in all_novels() if n.is_ongoing]
    candidates = [n for n in ongoing if n.id not in skip]
    empty = [n for n in candidates if not n.chapters]
    if empty:
        return "write", empty[0]
    if len(ongoing) < int(cfg["writing"]["max_ongoing"]):
        return "create", None
    if not candidates:
        return "none", None
    return "write", min(candidates, key=lambda n: n.meta["updated_at"])


def record_failure(novel: Novel) -> None:
    """同じ作品で失敗が続くと以降の実行がずっと止まるので、連続3回で「中断」にして枠を空ける。"""
    novel.meta["fail_count"] = novel.meta.get("fail_count", 0) + 1
    if novel.meta["fail_count"] >= MAX_FAILURES:
        novel.meta["status"] = "abandoned"
        print(f"  『{novel.meta['title']}』は{MAX_FAILURES}回連続で失敗したため中断扱いにします。")
    novel.save_meta()


def main() -> int:
    cfg = load_config()
    llm = make_llm(cfg)
    if llm is None:
        print("APIキーが設定されていないため、今回は執筆をスキップします(GEMINI_API_KEY などを設定してください)。")
        return 0

    done = failed = 0
    failed_this_run: set[str] = set()
    for _ in range(int(cfg["writing"]["actions_per_run"])):
        action, novel = pick_next(cfg, failed_this_run)
        if action == "none":
            break
        try:
            if action == "create":
                novel = create_novel(llm, cfg)
            else:
                write_next_chapter(llm, novel, cfg)
                if novel.meta.get("fail_count"):
                    novel.meta["fail_count"] = 0
                    novel.save_meta()
            done += 1
        except Exception as e:  # noqa: BLE001 - 1作業の失敗で全体を止めない
            failed += 1
            print(f"  ✗ 失敗: {e}")
            traceback.print_exc(limit=2)
            if action == "write" and novel is not None:
                failed_this_run.add(novel.id)
                record_failure(novel)
            if isinstance(e, LLMError) and "429" in str(e):
                print("  API上限に達したため、今回はここで終了します。")
                break
    print(f"完了: 成功 {done} / 失敗 {failed}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
