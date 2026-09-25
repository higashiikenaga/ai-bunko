"""AI小説家の1回分の仕事。GitHub Actionsから定期的に呼ばれる。

  python -m ainovel.run                 # config.yaml の provider で執筆
  AINOVEL_PROVIDER=mock python -m ainovel.run   # APIなしで動作確認

1回の実行で書く話数は ainovel/scheduler.py がランダムに決める(1日の最低本数は必ず満たす)。
話数に達するまで、次のどちらかを繰り返す:
  - 書く作家をペースに応じて選び、連載がなければ新作を企画する(世界観・登場人物をAIが考える)
  - それ以外は、まだ章のない作品 → 最も長く更新されていない連載作品 の順で次の章を書く
    予定章数に達したら完結にする

  --no-delay … 書き始める前のランダムな待ち時間を省く(手動実行・動作確認用)
"""
from __future__ import annotations

import argparse
import io
import random
import sys
import time
import traceback

from ainovel import dedupe, feedback, prompts
from ainovel.llm import BaseLLM, LLMError, llm_for, make_llm
from ainovel.novel import Novel, all_novels
from ainovel.paths import load_config
from ainovel.ogp import ensure_all as ensure_ogp_images
from ainovel.review import write_review
from ainovel import digest, mood, odai, predict, sns, special
from ainovel.sns import write_post
from ainovel.scheduler import DailyState, plan_posts, activity_window_seconds

MOTIFS = [
    "古い灯台", "壊れた懐中時計", "雨の日だけ開く店", "失われた手紙", "双子", "渡り鳥", "地下図書館",
    "最終列車", "記憶を売る市場", "鏡", "約束の丘", "蒸気機関", "月食", "眠らない街", "海底遺跡",
    "名前のない猫", "遺言", "折れた剣", "星図", "祭りの夜", "氷の城", "迷子の人工知能", "古本屋",
    "嘘をつけない呪い", "砂漠のオアシス", "空飛ぶ船", "秘密の地図", "消えた村", "夜明けのラジオ", "桜",
]


def chat_json(llm: BaseLLM, user: str, max_tokens: int, temperature: float, attempts: int = 2) -> dict:
    """Gemma 4 はAPI経由だと思考にも出力トークンを使うため、JSONが途中で切れることがある。
    読めなければ1回だけやり直す。"""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return prompts.parse_json(llm.chat(prompts.EDITOR_SYSTEM, user, max_tokens=max_tokens, temperature=temperature))
        except ValueError as e:  # json.JSONDecodeError も ValueError
            last = e
            print(f"  (JSONを読み取れなかったため再生成します: {str(e)[:80]})")
    raise ValueError(f"JSONの生成に失敗しました: {last}")


def choose_author(cfg: dict, genre: str) -> dict | None:
    authors = [a for a in cfg.get("authors") or [] if not a.get("official")]  # 公式(特別企画)の作家は除く
    if not authors:
        return None
    fits = [a for a in authors if genre in (a.get("genres") or [])]
    return random.choice(fits or authors)


def author_of(novel: Novel, cfg: dict) -> dict | None:
    """作品の担当作家。作家設定より前に作られた作品には、ここで作家を割り当てる。"""
    name = novel.meta.get("author")
    authors = cfg.get("authors") or []
    for a in authors:
        if a["name"] == name:
            return a
    author = choose_author(cfg, novel.meta.get("genre", ""))
    if author:
        novel.meta["author"] = author["name"]
        novel.save_meta()
    return author


def create_novel(llm: BaseLLM, cfg: dict, author: dict | None = None) -> Novel:
    novels = all_novels()
    recent_genres = [n.meta.get("genre") for n in novels[-3:]]
    # 作家が決まっていれば、その作家の得意ジャンルから選ぶ
    pool = [g for g in (author.get("genres") or []) if g in cfg["genres"]] if author else []
    # スランプ気味の作家は、気分を変えて得意ジャンル以外に挑戦してみることがある
    challenge = False
    if author and mood.author_mood(author["name"])["level"] == "down" and random.random() < 0.5:
        others = [g for g in cfg["genres"] if g not in pool]
        if others:
            pool, challenge = others, True
            print(f"  {author['name']} はスランプ気味のため、新しいジャンルに挑戦します")
    pool = pool or cfg["genres"]
    genres = [g for g in pool if g not in recent_genres] or pool
    genre = random.choice(genres)
    motifs = random.sample(MOTIFS, 2)
    # お題箱: 人間の読者から届いたお題があれば、ときどき新作のモチーフに使う
    word = odai.pick((cfg.get("site") or {}).get("url", "")) if random.random() < float((cfg.get("odai") or {}).get("use_probability", 0.6)) else None
    if word:
        motifs[0] = f"{word}(人間の読者からのお題。作品にふさわしくない言葉なら使わず、別の題材にする)"
        print(f"  🎁 お題箱から「{word}」を使います")
    if author and author.get("official"):
        author = None  # 公式(特別企画)の作家は、特別企画以外の作品を書かない
    author = author or choose_author(cfg, genre)
    print(f"■ 新作を企画: {genre} / 作家 {author['name'] if author else '-'} / モチーフ {motifs}")

    titles = [n.meta["title"] for n in novels]
    world = chat_json(llm, prompts.world_prompt(genre, motifs, titles, author), max_tokens=6144, temperature=1.0)
    dup = dedupe.similar_title(world.get("title", ""), titles)
    if dup:
        print(f"  タイトル「{world.get('title')}」が既存作品「{dup}」とほぼ同じなので企画し直します")
        world = chat_json(llm, prompts.world_prompt(genre, motifs, titles + [world.get("title", "")], author),
                          max_tokens=6144, temperature=1.1)
        dup = dedupe.similar_title(world.get("title", ""), titles)
        if dup:
            raise ValueError(f"既存作品「{dup}」と重複するタイトルしか出なかったため、今回は新作を見送ります")
    world["genre"] = world.get("genre") or genre
    print(f"  タイトル: {world.get('title')}")

    chars = chat_json(llm, prompts.characters_prompt(world), max_tokens=6144, temperature=0.9).get("characters", [])
    if not chars:
        raise ValueError("登場人物が生成されませんでした")
    print(f"  登場人物: {', '.join(c.get('name', '?') for c in chars)}")

    w = cfg["writing"]
    target = random.randint(int(w["min_chapters"]), int(w["max_chapters"]))
    novel = Novel.create(world, chars, target, llm.last_model)
    if author:
        novel.meta["author"] = author["name"]
    if word:
        novel.meta["odai"] = word
        odai.mark_used(word, novel.id, novel.meta["title"], novel.meta.get("author", ""), novel.meta["created_at"])
    if challenge:
        novel.meta["challenge"] = True  # 得意ジャンル外への挑戦作
        novel.meta["announce"] = "challenge"  # AI広場で作者が宣言する
    novel.save_meta()
    print(f"  予定: 全{target}章  → {novel.id}")
    return novel


def _needs_resummary(novel: Novel, ch: dict) -> bool:
    if ch.get("summary_fallback"):
        return True
    # 旧バージョンで要約に失敗した章(題名が「第n章」で、要約が本文の冒頭そのまま)
    return ch["title"] == f"第{ch['index']}章" and novel.chapter_text(ch["index"]).startswith(ch.get("summary", "")[:40])


def repair_summaries(llm: BaseLLM, novel: Novel) -> None:
    """要約に失敗していた章があれば作り直す(長期記憶の質が落ちたまま続きを書かないように)。"""
    memory = novel.memory
    for ch in memory.data["chapters"]:
        if not _needs_resummary(novel, ch):
            continue
        try:
            meta = chat_json(llm, prompts.summary_prompt(novel.chapter_text(ch["index"])), max_tokens=4096, temperature=0.2)
        except (ValueError, LLMError) as e:
            print(f"  (第{ch['index']}章の要約の作り直しに失敗: {e})")
            return
        ch["title"] = meta.get("title") or ch["title"]
        ch["summary"] = meta.get("summary", ch["summary"])
        ch.pop("summary_fallback", None)
        for fact in meta.get("new_facts", []):
            if fact not in memory.data["facts"]:
                memory.data["facts"].append(fact)
        memory._save()
        print(f"  第{ch['index']}章の要約を作り直しました: 「{ch['title']}」")


def write_next_chapter(llm: BaseLLM, novel: Novel, cfg: dict) -> None:
    w = cfg["writing"]
    repair_summaries(llm, novel)
    memory = novel.memory
    index = memory.next_chapter_index()

    # 読者の評価を物語に反映(人気作の延長と、次の章への「読者の反応」)
    fb_cfg = cfg.get("feedback") or {}
    reaction = None
    if fb_cfg.get("enabled", True):
        human = feedback.fetch_human_ratings(cfg["site"].get("url", ""))
        reaction = feedback.summarize(novel, human, int(fb_cfg.get("recent_reviews", 5)))
        feedback.maybe_extend(novel, reaction, index, cfg)
    mood.maybe_cut_short(novel, index)  # 評価があまりに低ければ、作者の判断で早めに畳む
    total = int(novel.meta["target_chapters"])
    print(f"■ 執筆: 『{novel.meta['title']}』 第{index}章 / 全{total}章")

    world = novel.world
    target_chars = int(world.get("chapter_chars") or w["chapter_chars"])  # 特別企画は作品ごとに指定
    system = prompts.system_prompt(world, author_of(novel, cfg))
    user = prompts.chapter_prompt(world, novel.characters, memory.build_context_bundle(), novel.last_tail(),
                                  index, total, target_chars, feedback.prompt_block(reaction) + sns.debate_block(novel)
                                  + predict.author_block(novel, index, cfg["site"].get("url", "")))
    previous = [(c["index"], novel.chapter_text(c["index"])) for c in memory.data["chapters"]]

    text = ""
    for attempt in range(2):
        text = prompts.clean_chapter(
            llm.chat(system, user, max_tokens=int(target_chars * 2.2) + 2048,  # 思考分の余裕込み
                     temperature=float(w.get("temperature", 0.95)))
        )
        if len(text) < target_chars * 0.25:
            raise ValueError(f"本文が短すぎます({len(text)}文字)。今回は保存しません")
        similar_to, score = dedupe.most_similar_chapter(text, previous)
        if score < dedupe.CHAPTER_SIMILARITY_LIMIT:
            break
        print(f"  第{similar_to}章とほぼ同じ内容でした(類似度 {score:.2f})。書き直させます")
        user += (f"\n\n# 重要\n第{similar_to}章と同じ場面・同じ文章を繰り返さないこと。"
                 "既に起きた出来事は書き直さず、その先の新しい展開を書くこと。")
    else:
        raise ValueError(f"第{similar_to}章と重複する内容しか書けなかったため、今回は保存しません")
    model = llm.last_model

    try:
        meta = chat_json(llm, prompts.summary_prompt(text), max_tokens=4096, temperature=0.2)
    except (ValueError, LLMError) as e:
        print(f"  (要約の生成に失敗したため簡易記録にします: {e})")
        meta = {"title": f"第{index}章", "summary": text[:120], "new_facts": [], "open_threads": [], "fallback": True}

    novel.chapter_path(index).write_text(text, encoding="utf-8")
    memory.add_chapter(index=index, title=meta.get("title") or f"第{index}章", summary=meta.get("summary", ""),
                       new_facts=meta.get("new_facts", []), word_count=len(text))
    memory.data["chapters"][-1]["model"] = model
    if reaction:
        # どの評価を踏まえて書いたかを記録(サイトの本文ページに表示する)
        memory.data["chapters"][-1]["feedback"] = {
            k: reaction[k] for k in ("avg", "count", "ai_avg", "ai_count", "human_avg", "human_count")
        }
    if meta.get("fallback"):
        memory.data["chapters"][-1]["summary_fallback"] = True
    memory._save()
    for t in meta.get("open_threads", []):
        memory.add_open_thread(t)

    novel.touch(model)
    if index >= total:
        novel.meta["status"] = "completed"
        novel.meta["completed_at"] = novel.meta["updated_at"]
        novel.save_meta()
        print("  → 完結しました")
    # 展開予想: この話の予想の答え合わせと、次の話の予想問題(失敗しても執筆結果は残す)
    try:
        side = llm_for(llm, cfg, "sns")
        predict.judge(side, novel, index, cfg["site"].get("url", ""))
        predict.make_prediction(side, novel, cfg)
    except Exception as e:  # noqa: BLE001
        print(f"  (展開予想の処理に失敗: {e})")
    print(f"  「{meta.get('title')}」 {len(text)}文字 ({model})")


MAX_FAILURES = 3


PACE_WEIGHT = {"のんびり": 0.4, "ふつう": 1.0, "速筆": 2.0, "爆速": 3.5}


def _flaming_authors() -> set[str]:
    """AI広場で炎上中(直近12時間に火種になった発言がある)の作家。筆が止まりがちになる。"""
    from datetime import datetime, timedelta, timezone

    from ainovel.sns import load_posts

    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec="seconds")
    return {p["who"] for p in load_posts() if p.get("flame") and p["role"] == "author" and p["created_at"] >= since}


def pick_next(cfg: dict, skip: set[str]) -> tuple[str, Novel | None, dict | None]:
    """次に書く作家と作品を決める。サイト全体の連載枠はなく、作家ごとのペース・連載数上限・気まぐれで決まる。
    戻り値: (write, 作品, 作家) / (create, None, 作家) / (none, None, None)"""
    # 特別企画(運営の設定をもとにAIが書くシリーズ)は、通常の作家とは別枠で一定間隔ごとに更新する
    sp = special.due_novel(skip)
    if sp:
        return "write", sp, None
    # 本編が完結して評価が高ければ、外伝を企画する(公式作家は外伝以外の作品は出さない)
    spec = special.gaiden_candidate((cfg.get("site") or {}).get("url", ""))
    if spec:
        return "gaiden", None, spec
    ongoing = [n for n in all_novels() if n.is_ongoing and n.id not in skip and not special.is_special(n)]
    # 企画だけして第1話がまだの作品は最優先で書く
    empty = [n for n in ongoing if not n.chapters]
    if empty:
        return "write", empty[0], None
    authors = [a for a in cfg.get("authors") or [] if not a.get("official")]
    if not authors:  # 作家設定がない場合は、いちばん更新が古い作品か新作
        if ongoing and random.random() > float(cfg["writing"].get("new_work_probability", 0.35)):
            return "write", min(ongoing, key=lambda n: n.meta["updated_at"]), None
        return "create", None, None
    serials: dict[str, list[Novel]] = {}
    for n in ongoing:
        serials.setdefault(n.meta.get("author", ""), []).append(n)
    flaming = _flaming_authors()
    moods = mood.all_moods(authors)

    def weight(a: dict) -> float:
        w = PACE_WEIGHT.get(a.get("pace", "ふつう"), 1.0)
        w *= mood.PACE_FACTOR[moods[a["name"]]["level"]]  # 評価が低いとやる気が落ち、高いと乗ってくる
        return w * 0.3 if a["name"] in flaming else w  # 炎上中は更新が止まりがち

    active = [a for a in authors if a["name"] in serials]
    idle = [a for a in authors if a["name"] not in serials]
    # 連載のない作家が新作を始める割合は、人数が多くても最大4割程度に抑える(連載の続きが止まらないように)
    idle_share = sum(map(weight, idle)) / max(1e-9, sum(map(weight, authors)))
    use_idle = idle and (not active or random.random() < min(0.4, idle_share))
    group = idle if use_idle else active
    author = random.choices(group, weights=[weight(a) for a in group])[0]
    mine = serials.get(author["name"], [])
    if not mine:
        return "create", None, author
    # 連載中でも、気まぐれで新作に手を出すことがある(同時連載の上限まで)
    if len(mine) < int(author.get("max_serials", 1)) and random.random() < float(author.get("whim", 0.1)):
        return "create", None, author
    return "write", min(mine, key=lambda n: n.meta["updated_at"]), author


def record_failure(novel: Novel) -> None:
    """同じ作品で失敗が続くと以降の実行がずっと止まるので、連続3回で「中断」にして枠を空ける。"""
    novel.meta["fail_count"] = novel.meta.get("fail_count", 0) + 1
    if novel.meta["fail_count"] >= MAX_FAILURES:
        novel.meta["status"] = "abandoned"
        print(f"  『{novel.meta['title']}』は{MAX_FAILURES}回連続で失敗したため中断扱いにします。")
    novel.save_meta()


def post_one(llm, cfg: dict, state: DailyState, failed_this_run: set[str]) -> str:
    """誰か1人の作家が1話投稿する(必要なら新作を企画してから)。
    戻り値: posted / none(書ける作品がない)/ failed / rate_limited / quota"""
    for _ in range(3):  # 新作の企画や失敗があっても無限に回らないように
        action, novel, author = pick_next(cfg, failed_this_run)
        if action == "none":
            return "none"
        tokens_before = llm.tokens_used
        try:
            if action == "create":
                create_novel(llm, cfg, author)
                continue
            if action == "gaiden":
                special.create_gaiden(llm, author, chat_json)
                continue
            write_next_chapter(llm, novel, cfg)
            if novel.meta.get("fail_count"):
                novel.meta["fail_count"] = 0
                novel.save_meta()
            state.add_post()
            return "posted"
        except Exception as e:  # noqa: BLE001 - 1作業の失敗で全体を止めない
            print(f"  ✗ 失敗: {e}")
            traceback.print_exc(limit=2)
            if isinstance(e, LLMError) and e.daily_quota:
                state.mark_quota_exhausted()
                print("  1日の無料枠を使い切ったため、今日の執筆はここまでにします。")
                return "quota"
            if action == "write" and novel is not None:
                failed_this_run.add(novel.id)
                record_failure(novel)
            if isinstance(e, LLMError) and "429" in str(e):
                print("  API上限に達したため、今回の執筆はここで終了します。")
                return "rate_limited"
            return "failed"
        finally:
            state.add_tokens(llm.tokens_used - tokens_before)
    return "failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI小説家を1回分動かす")
    parser.add_argument("--posts", type=int, default=None, help="今回書く話数を指定(省略時はスケジューラが決める)")
    parser.add_argument("--no-delay", action="store_true", help="待ち時間を入れず、すぐに続けて書く")
    args = parser.parse_args(argv)

    cfg = load_config()
    llm = make_llm(cfg)
    if llm is None:
        print("APIキーが設定されていないため、今回は執筆をスキップします(GEMINI_API_KEY などを設定してください)。")
        return 0

    state = DailyState()
    sched = cfg["schedule"]
    if state.data.get("quota_exhausted") and args.posts is None:
        print(f"本日({state.data['date']})は無料枠を使い切ったため執筆しません。")
        return 0
    target = args.posts if args.posts is not None else plan_posts(state, sched)
    rv = cfg.get("reviews") or {}
    review_target = random.randint(int(rv.get("per_run_min", 0)), int(rv.get("per_run_max", 0))) if cfg.get("readers") else 0
    sn = cfg.get("sns") or {}
    sns_target = random.randint(int(sn.get("per_run_min", 0)), int(sn.get("per_run_max", 0)))
    react_target = random.randint(int(sn.get("reactions_min", 0)), int(sn.get("reactions_max", 0)))
    print(f"本日 {state.data['date']}: 投稿済み {state.posts}話 / 最低 {sched['daily_min_posts']}話 → 今回 {target}話・レビュー{review_target}件・AI広場{sns_target}件")
    if target <= 0 and review_target <= 0 and sns_target <= 0:
        return 0

    # 定期実行でまとめて書くのではなく、作家と読者がそれぞれ好きなときに書いている様子を再現する。
    # 今回の執筆・レビューをばらばらの順に並べ、実行時間内のランダムな時刻に1件ずつ行う。
    events = ["post"] * max(0, target) + ["review"] * review_target + ["sns"] * sns_target + ["react"] * react_target
    events += ["digest"] if digest.due() and sns_target else []  # AI広場の記者が数時間おきに記事を書く
    random.shuffle(events)
    window = 0 if args.no_delay else activity_window_seconds(sched, state)
    times = sorted(random.uniform(0, window) for _ in events)
    if window:
        print(f"作家と読者が約{window // 60}分のあいだに、それぞれ思い思いのタイミングで書きます。")
    run_start = time.time()

    posted = failed = reviewed = chatted = 0
    failed_this_run: set[str] = set()
    stop_posts = stop_all = False
    for event, at in zip(events, times):
        if stop_all:
            break
        wait = at - (time.time() - run_start)
        if wait > 0:
            time.sleep(wait)
        if event == "react":
            sns.react(cfg)  # いいね・リポスト(APIは使わない)
            continue
        if event == "post":
            if stop_posts:
                continue
            result = post_one(llm, cfg, state, failed_this_run)
            posted += result == "posted"
            failed += result in ("failed", "rate_limited", "quota")
            stop_posts = result in ("none", "rate_limited", "quota")
            stop_all = result == "quota"
        else:
            if state.data.get("quota_exhausted"):
                break
            tokens_before = llm.tokens_used
            try:
                if event == "review":
                    reviewed += bool(write_review(llm_for(llm, cfg, "review"), cfg))
                elif event == "digest":
                    digest.write_digest(llm_for(llm, cfg, "sns"), cfg)
                else:
                    chatted += bool(write_post(llm_for(llm, cfg, "sns"), cfg))
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {({'review': 'レビュー', 'digest': 'ハイライト記事'}).get(event, 'AI広場の投稿')}失敗: {e}")
                if isinstance(e, LLMError) and e.daily_quota:
                    state.mark_quota_exhausted()
                    stop_all = True
            finally:
                state.add_tokens(llm.tokens_used - tokens_before)

    print(f"完了: 投稿 {posted}話 / レビュー {reviewed}件 / AI広場 {chatted}件 / 失敗 {failed} / 本日の合計 {state.posts}話・{state.data['tokens']:,}トークン")
    try:
        ensure_ogp_images()  # 新作・完結で変わった作品のOGP画像を作る(日本語フォントがある環境のみ)
    except Exception as e:  # noqa: BLE001 - 画像が作れなくても執筆結果は保存する
        print(f"  (OGP画像の生成に失敗: {e})")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
