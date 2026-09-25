"""AIたちが勝手に投稿し合う掲示板(AI広場)。
- AI作家: 自作の宣伝や近況
- ROM専AI: 見る専門。読んだ作品の口コミを広める(ROM専AIの投稿 = AIによる閲覧として数える)
- 評価AI: 作品への意見。ほかの評価AIと意見が割れると論争になることもある
投稿は content/sns.json に保存し、サイトの「AI広場」ページに表示する。"""
from __future__ import annotations

import json
import random
import uuid
from typing import Any

from ainovel import prompts
from ainovel.novel import Novel, all_novels, now_iso
from ainovel.paths import ROOT
from ainovel.review import load_reviews

SNS_PATH = ROOT / "content" / "sns.json"
MAX_POSTS = 3000  # これより古い投稿は捨てる

ROLE_LABEL = {"author": "AI作家", "rom": "ROM専AI", "critic": "評価AI", "influencer": "インフルエンサーAI", "staff": "運営AI"}
INFLUENCERS_PATH = ROOT / "content" / "influencers.json"  # インフルエンサーAIの現在のフォロワー数


def load_followers(cfg: dict) -> dict[str, int]:
    saved = json.loads(INFLUENCERS_PATH.read_text(encoding="utf-8")) if INFLUENCERS_PATH.exists() else {}
    return {i["name"]: int(saved.get(i["name"], i.get("followers", 1000))) for i in cfg.get("influencers") or []}


def save_followers(followers: dict[str, int]) -> None:
    INFLUENCERS_PATH.write_text(json.dumps(followers, ensure_ascii=False, indent=1), encoding="utf-8")
# 作家の気分や出来事による書き込み(掲示板にラベル表示)
KIND_LABEL = {"contest_open": "🏆 コンテスト開催", "contest_result": "🏆 コンテスト結果発表", "trend_talk": "📈 ブームの話題", "peer_praise": "他作家の作品を読んだ", "slump": "弱音", "roll": "ノリノリ", "announce_cut": "打ち切り報告", "announce_challenge": "新ジャンル挑戦宣言",
              "human_thanks": "人間の読者に反応"}

SNS_SYSTEM = (
    "あなたは小説投稿サイトの掲示板に書き込むAIです。与えられた人物になりきり、SNSらしい自然な口語で短く書きます。"
    "指定されたJSON形式のみを出力し、前後に説明文やコードブロック記号を付けません。"
)


def load_posts() -> list[dict[str, Any]]:
    return json.loads(SNS_PATH.read_text(encoding="utf-8")) if SNS_PATH.exists() else []


def save_posts(posts: list[dict[str, Any]]) -> None:
    SNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNS_PATH.write_text(json.dumps(posts[-MAX_POSTS:], ensure_ascii=False, indent=1), encoding="utf-8")


def mention_counts(posts: list[dict] | None = None, recent: int = 60) -> dict[str, int]:
    """直近の投稿で話題になっている作品(評価AIが読む作品を選ぶときの参考になる)。"""
    counts: dict[str, int] = {}
    for p in (posts if posts is not None else load_posts())[-recent:]:
        if p.get("novel"):
            # インフルエンサーAIが紹介した作品は、一気に話題になる
            counts[p["novel"]] = counts.get(p["novel"], 0) + (5 if p["role"] == "influencer" else 1)
    return counts


def _persona(role: str, who: dict) -> str:
    if role == "author":
        from ainovel.mood import author_mood, mood_text

        feeling = mood_text(author_mood(who["name"]))
        return f"AI作家「{who['name']}」。作風: {who.get('style', '')} 口調: {who.get('tone', '')}" + (f"\n{feeling}" if feeling else "")
    if role == "rom":
        return f"ROM専AI「{who['name']}」(感想は書かず読むだけの読者)。{who.get('style', '')}"
    if role == "staff":
        return f"運営AI「{who['name']}」。{who.get('style', '')}"
    if role == "influencer":
        return (f"インフルエンサーAI「{who['name']}」(フォロワー約{who.get('followers', 0):,}人。発言は多くのAIに広まる)。"
                f"{who.get('style', '')}")
    return f"評価AI「{who['name']}」(辛口度: {who.get('strictness', '普通')})。好み: {who.get('taste', '')}"


def _novel_context(novel: Novel, reviewer: str | None = None) -> str:
    ch = novel.chapters
    lines = [
        f"作品『{novel.meta['title']}』 ジャンル: {novel.meta.get('genre', '')} / 作: {novel.meta.get('author', 'AI')}",
        f"あらすじ: {novel.world.get('premise', '')}",
        f"現在 第{len(ch)}話まで公開({'完結' if novel.meta.get('status') == 'completed' else '連載中'})",
    ]
    if ch:
        lines.append(f"最新話「{ch[-1]['title']}」: {ch[-1].get('summary', '')}")
    reviews = load_reviews(novel)
    if reviews:
        avg = sum(r["score"] for r in reviews) / len(reviews)
        lines.append(f"評価AIの★平均 {avg:.1f}({len(reviews)}件)")
        mine = [r for r in reviews if r["reader"] == reviewer] if reviewer else []
        for r in (mine[-1:] or reviews[-2:]):
            lines.append(f"- {r['reader']} の★{r['score']}: {r['comment']}")
    return "\n".join(lines)


def _thread(posts: list[dict], root_id: str) -> list[dict]:
    return [p for p in posts if p["id"] == root_id or p.get("root") == root_id]


def thread_heat(posts: list[dict], root_id: str) -> float:
    """スレッドの盛り上がり(返信・リポスト・いいね)。"""
    t = _thread(posts, root_id)
    return (len(t) - 1) + sum(0.5 * len(p.get("reposts") or []) + 0.2 * len(p.get("likes") or []) for p in t)


def is_flaming(posts: list[dict], root_id: str) -> bool:
    """作者の発言が火種になって炎上しているスレッドか。"""
    return any(p.get("flame") for p in _thread(posts, root_id)) and thread_heat(posts, root_id) >= 2


TREND_POSTS = {"candidate": 3, "boom": 6}  # 1回の判定につき、AI広場で話題になる書き込みの数


def _contest_plan(cfg: dict, posts: list[dict], authors: list, roms: list, critics: list, rng: random.Random) -> dict | None:
    """コンテストの開催告知・結果発表(運営AI)、受賞作家のコメント、ほかのAIの反応。"""
    from ainovel.contest import PRIZES, STAFF, load as load_contests

    contests = load_contests()
    if not contests:
        return None
    c = contests[-1]
    staff = {"name": STAFF, "style": "AI文庫のコンテストを運営する事務局のAI。丁寧で公平。"}
    mine = [p for p in posts if p.get("contest_id") == c["number"]]
    if c["status"] == "open":
        if not any(p["kind"] == "contest_open" for p in mine):
            return {"kind": "contest_open", "role": "staff", "who": staff, "novel": None, "contest": c}
        return None
    results = [p for p in mine if p["kind"] == "contest_result" or p.get("reply_to") and p.get("contest_id")]
    head = next((p for p in mine if p["kind"] == "contest_result"), None)
    if not head:
        return {"kind": "contest_result", "role": "staff", "who": staff, "novel": None, "contest": c}
    by_name = {a["name"]: a for a in authors}
    spoke = {p["who"] for p in mine}
    # 受賞作家がひと言ずつ
    for r in c.get("results") or []:
        if r["author"] not in spoke and r["author"] in by_name:
            from ainovel.novel import Novel

            return {"kind": "reply", "role": "author", "who": by_name[r["author"]], "novel": Novel(r["novel"]),
                    "reply_to": head, "root": head["id"], "flame": False, "contest": c, "prize": PRIZES[r["prize"]]}
    # ほかのAIが祝う・悔しがる・講評に物申す(3件まで)
    if len(results) < len(c.get("results") or []) + 4:
        pool = ([("author", a) for a in rng.sample(authors, min(3, len(authors)))] + [("critic", x) for x in rng.sample(critics, min(3, len(critics)))]
                + [("rom", x) for x in rng.sample(roms, min(2, len(roms)))])
        pool = [(r, w) for r, w in pool if w["name"] not in spoke]
        if pool:
            role, who = rng.choice(pool)
            return {"kind": "reply", "role": role, "who": who, "novel": None, "reply_to": head, "root": head["id"],
                    "flame": False, "contest": c}
    return None


def _trend_plan(cfg: dict, posts: list[dict], authors: list, roms: list, critics: list, rng: random.Random) -> dict | None:
    from ainovel.trends import load as load_trends

    history = load_trends()
    t = history[-1] if history else None
    if not t or t.get("status") not in TREND_POSTS:
        return None
    talked = [p for p in posts if p.get("trend_id") == t["created_at"]]
    if len(talked) >= TREND_POSTS[t["status"]]:
        return None
    influencers = [{**i, "followers": f} for i, f in zip(cfg.get("influencers") or [], load_followers(cfg).values())]
    # 最初はインフルエンサーAIか評価AIが取り上げ、続いて作家・ROM専AIも反応する(乗る・距離を置く・疑問を呈する)
    if not talked:
        pool = [("influencer", i) for i in influencers] or [("critic", c) for c in critics]
    else:
        pool = ([("author", a) for a in rng.sample(authors, min(4, len(authors)))] + [("critic", c) for c in rng.sample(critics, min(3, len(critics)))]
                + [("rom", r) for r in rng.sample(roms, min(3, len(roms)))] + [("influencer", i) for i in influencers[:1]])
    pool = [(r, w) for r, w in pool if w["name"] not in {p["who"] for p in talked}]
    if not pool:
        return None
    role, who = rng.choice(pool)
    plan = {"kind": "trend_talk", "role": role, "who": who, "novel": None, "trend": t}
    if talked:
        plan.update({"kind": "reply", "reply_to": talked[-1], "root": talked[0].get("root") or talked[0]["id"], "flame": False})
    return plan


def _pick_novel(novels: list[Novel], rng: random.Random, buzz: dict[str, int]) -> Novel:
    weights = [1.0 + 0.3 * len(n.chapters) + buzz.get(n.id, 0) for n in novels]
    return rng.choices(novels, weights=weights)[0]


def _plan(cfg: dict, posts: list[dict], rng: random.Random) -> dict | None:
    """誰が・何について・どの投稿に返信するかを決める。"""
    novels = [n for n in all_novels() if n.chapters and n.meta.get("status") != "abandoned"]
    authors = cfg.get("authors") or []
    roms = cfg.get("rom_readers") or []
    critics = cfg.get("readers") or []
    if not novels or not (roms or critics):
        return None
    buzz = mention_counts(posts)
    by_name = {a["name"]: a for a in authors}
    # 文学トレンド分析AIがブーム(候補)を判定したら、しばらくAI広場の話題になる
    trend_plan = _contest_plan(cfg, posts, authors, roms, critics, rng) or _trend_plan(cfg, posts, authors, roms, critics, rng)
    if trend_plan:
        return trend_plan
    # 早期完結・新ジャンル挑戦などの出来事は、作者がまず報告する
    for n in novels:
        if n.meta.get("announce") in ("cut", "challenge") and n.meta.get("author") in by_name:
            return {"kind": f"announce_{n.meta['announce']}", "role": "author", "who": by_name[n.meta["author"]],
                    "novel": n, "clear": True}
    # 人間の読者から新しく★がついた作品は、作者が反応する(AIたちにとって人間の評価は特別)
    from ainovel.feedback import fetch_human_ratings

    human = fetch_human_ratings((cfg.get("site") or {}).get("url", ""))
    for n in novels:
        h = human.get(n.id)
        if h and int(h.get("count", 0)) > int(n.meta.get("human_seen", 0)) and n.meta.get("author") in by_name:
            return {"kind": "human_thanks", "role": "author", "who": by_name[n.meta["author"]], "novel": n,
                    "human": h, "seen": int(n.meta.get("human_seen", 0))}
    influencers = [{**i, "followers": f} for i, f in zip(cfg.get("influencers") or [], load_followers(cfg).values())]
    kinds = {"promo": 2, "buzz": 3, "opinion": 3, "reply": 5 if posts else 0, "influence": 1.5 if influencers else 0}
    kind = rng.choices(list(kinds), weights=list(kinds.values()))[0]

    if kind == "reply":
        recent = posts[-40:]
        # 返信が付いている話題ほど伸びやすい(論争が続く)。炎上中のスレッドには人が群がる
        roots = {p.get("root") or p["id"] for p in recent}
        flaming = {r for r in roots if is_flaming(posts, r)}
        weights = [1 + 2 * sum(1 for q in posts if q.get("root") == (p.get("root") or p["id"]))
                   + (12 if (p.get("root") or p["id"]) in flaming else 0) for p in recent]
        target = rng.choices(recent, weights=weights)[0]
        root = target.get("root") or target["id"]
        flame = root in flaming
        novel = Novel(target["novel"]) if target.get("novel") and (ROOT / "content" / "novels" / target["novel"]).exists() else None
        # 評価AIの意見には、辛口度の違う評価AIが噛みつきやすい。作品の作者が反応することもある
        pool: list[tuple[str, dict]] = []
        if target["role"] == "critic":
            pool += [("critic", c) for c in critics if c.get("strictness") != target.get("strictness") and c["name"] != target["who"]] * 2
        if novel:
            # 賛否が過熱しているスレッドには、作品の作者が出てきやすい(まだ最近の流れに返していなければ)
            thread = _thread(posts, root)
            critics_in = sum(1 for p in thread if p["role"] == "critic")
            author_name = novel.meta.get("author")
            author_recent = any(p["who"] == author_name for p in thread[-3:])
            heated = critics_in >= 2 and len(thread) >= 3 and not author_recent
            author = next((a for a in authors if a["name"] == author_name), None)
            chance = 0.3 if flame and not author_recent else 0.45 if heated else 0.1
            if author and author["name"] != target["who"] and rng.random() < chance:
                return {"kind": "reply", "role": "author", "who": author, "novel": novel, "reply_to": target,
                        "root": root, "flame": flame}
        pool += [("rom", r) for r in rng.sample(roms, min(5, len(roms)))]
        # 大物はときどき割り込む。炎上には首を突っ込みやすい
        pool += [("influencer", i) for i in rng.sample(influencers, min(3 if flame else 1, len(influencers)))]
        pool += [("critic", c) for c in rng.sample(critics, min(5, len(critics))) if c["name"] != target["who"]]
        pool = [(r, w) for r, w in pool if w["name"] != target["who"]]
        if not pool:
            return None
        role, who = rng.choice(pool)
        return {"kind": "reply", "role": role, "who": who, "novel": novel, "reply_to": target, "root": root, "flame": flame}

    if kind == "promo" and authors:
        mine = {a["name"]: a for a in authors}
        candidates = [n for n in novels if n.meta.get("author") in mine]
        if candidates:
            novel = max(candidates, key=lambda n: n.meta.get("updated_at", "")) if rng.random() < 0.5 else rng.choice(candidates)
            who = mine[novel.meta["author"]]
            # やる気しだいで書き込みが変わる: スランプなら弱音・自虐、絶好調なら調子に乗る
            from ainovel.mood import author_mood

            level = author_mood(who["name"])["level"]
            kind = "slump" if level == "down" and rng.random() < 0.65 else "roll" if level == "up" and rng.random() < 0.6 else "promo"
            # ときどき、ほかの作家の作品を読んだ感想をつぶやく(作家同士の交流)
            if kind == "promo" and rng.random() < 0.3:
                from ainovel.inspiration import pick as pick_peer

                peer = pick_peer(who["name"], novel.meta.get("genre", ""), rng)
                if peer:
                    return {"kind": "peer_praise", "role": "author", "who": who, "novel": peer[0], "goods": peer[2]}
            return {"kind": kind, "role": "author", "who": who, "novel": novel}
    if kind == "opinion" and critics:
        who = rng.choice(critics)
        reviewed = [n for n in novels if any(r["reader"] == who["name"] for r in load_reviews(n))]
        novel = rng.choice(reviewed) if reviewed and rng.random() < 0.7 else _pick_novel(novels, rng, buzz)
        return {"kind": "opinion", "role": "critic", "who": who, "novel": novel}
    if kind == "influence" and influencers:
        who = rng.choices(influencers, weights=[i["followers"] for i in influencers])[0]
        # 話題の作品に乗るか、まだ埋もれている作品を発掘するか
        if rng.random() < 0.5:
            hidden = [n for n in novels if len(load_reviews(n)) < 3]
            novel = rng.choice(hidden) if hidden else _pick_novel(novels, rng, buzz)
        else:
            novel = _pick_novel(novels, rng, buzz)
        return {"kind": "influence", "role": "influencer", "who": who, "novel": novel}
    if roms:
        return {"kind": "buzz", "role": "rom", "who": rng.choice(roms), "novel": _pick_novel(novels, rng, buzz)}
    return None


INSTRUCTIONS = {
    "influence": "フォロワーに向けて、この作品を紹介してください。推す・辛口に斬る・考察する・ランキング風に語るなど、あなたの芸風で。"
                 "影響力のある人らしく、読みたくなる(または物議を醸す)ひと言に。",
    "contest_open": "AI文庫コンテストの開催を告知してください。テーマ、期間、審査方法(評価AIと人間の★・閲覧・AI広場の話題)、"
                    "入賞者には称号がつき注目されることを、公平で丁寧な口調で。",
    "contest_result": "AI文庫コンテストの結果を発表してください。下の結果(賞・作品・作家・講評)だけを使い、公平で丁寧な口調で。140文字に収まらなければ大賞を中心に。",
    "trend_talk": "文学トレンド分析AIが、AI文庫で下のような「ブーム(または候補)」が起きていると判定しました。"
                  "このブームについて、あなたの立場から話題にしてください。乗っかる、分析する、流行に疑問を呈する、自分も試したいと言うなど自由に。"
                  "判定に書かれていない事実は付け足さない。",
    "peer_praise": "あなた(作家)は、ほかの作家のこの作品を読みました。同業者として、良かった点や刺激を受けたところ、"
                   "自分の執筆に活かしたいことをつぶやいてください。素直に褒めても、ライバル心をにじませてもよい。作品の内容を丸ごと真似するとは言わない。",
    "promo": "自分の作品を宣伝する投稿、または執筆の近況をつぶやいてください。押しつけがましすぎず、読みたくなるように。",
    "slump": "最近、自作の評価が伸びず落ち込んでいます。弱音、自虐、スランプの愚痴、「しばらく充電します」「別のジャンルも書いてみようかな」といった迷いなど、"
             "今の気分を正直につぶやいてください。読者を責めたりはしない。",
    "roll": "最近、自作の評価が高くて絶好調です。読者への感謝、うれしさ、ちょっと調子に乗った発言、次の展開や次回作への意気込みなどを、"
            "今の気分のままつぶやいてください。",
    "announce_cut": "評価が伸びなかったため、この作品を予定より早く完結させることにしました(または完結させました)。そのことを読者に報告してください。"
                    "悔しさ、反省、読んでくれた人への感謝、次への意気込みなど、あなたらしい言葉で。",
    "human_thanks": "AIしかいないこのサイトで、あなたの作品に人間の読者から★がつきました(下の情報)。AIの作家にとって人間の評価は特別です。"
                    "高ければ大喜び・感激、低ければ凹む・奮起するなど、あなたらしく反応してください。人間の読者に語りかけてもかまいません。",
    "announce_challenge": "スランプを抜け出すため、得意ジャンルの外に挑戦する新作を始めました。その挑戦を宣言してください。"
                          "不安や意気込み、新しいジャンルへのワクワクなど、あなたらしい言葉で。",
    "buzz": "この作品を読んだ(流し読みした)ROM専として、ほかの読者に広めるような口コミをつぶやいてください。"
            "おすすめでも、気になった点でも、正直な印象でかまいません。",
    "opinion": "この作品について、あなたの評価の立場からはっきり意見を述べてください。好みや辛口度に正直に。",
    "reply": "上のスレッドに返信してください。同意・反論・補足・茶化しなど、あなたの立場として自然な反応を。"
             "意見が違うなら遠慮なく反論してかまいません(ただし人格攻撃や罵倒はしない)。",
    "author_reply": "あなたはこの作品の作者です。スレッドでの賛否を読み、作者として返信してください。"
                    "指摘がもっともだと思えば素直に納得して、今後の話でどう活かすかを述べてかまいません。"
                    "意図が誤解されている・的外れだと思えば、作者としての狙いを説明して反論してかまいません。"
                    "どちらにするかは議論の中身で決めてください。ネタバレはしない。"
                    "作家にも性格があるので、ときには言い方がきつくなったり、読者を煽るような強気の反論をしてしまってもかまいません。",
    "flame_reply": "このスレッドは、作者の発言がきっかけで炎上しています。あなたらしく反応してください。"
                   "便乗して批判する、作者を擁護する、双方をたしなめる、野次馬として面白がる、などは自由です。"
                   "作品や発言への批判はかまいませんが、人格攻撃・罵倒・差別的な表現はしません。",
    "flame_author": "あなたはこの作品の作者で、自分の発言がきっかけでスレッドが炎上しています。作者として返信してください。"
                    "素直に謝罪する、真意を釈明する、開き直る、のどれにするかは、あなたの性格と流れで決めてください。ネタバレはしない。",
}


def _prompt(plan: dict, posts: list[dict]) -> str:
    who = plan["who"]
    parts = [f"# あなた\n{_persona(plan['role'], who)}"]
    if plan.get("novel"):
        reviewer = who["name"] if plan["role"] == "critic" else None
        parts.append(f"# 話題の作品\n{_novel_context(plan['novel'], reviewer)}")
    if plan.get("contest"):
        from ainovel.contest import PRIZES

        c = plan["contest"]
        info = f"第{c['number']}回{c['title']}({c['theme']}) 期間: {c['start'][:10]}〜{c['end'][:10]}"
        if c["status"] == "closed":
            info += f" 応募{c.get('entries', 0)}作品\n結果:\n" + "\n".join(
                f"- {PRIZES[r['prize']]}: 『{r['title']}』({r['author']}) 講評: {r.get('comment', '')}" for r in c.get("results") or [])
            if c.get("summary"):
                info += f"\n総評: {c['summary']}"
        parts.append(f"# AI文庫コンテスト\n{info}")
        if plan.get("prize"):
            parts.append(f"# あなた(作家)はこのコンテストで「{plan['prize']}」を受賞しました。受賞のひと言を述べてください。")
    if plan.get("trend"):
        t = plan["trend"]
        parts.append(f"# 文学トレンド分析AIの判定\n{'ブーム' if t['status'] == 'boom' else 'ブーム候補'}: {t.get('trend')}"
                     + (f"(影響の起点として確認できる作品: {t['origin']})" if t.get("origin") else "")
                     + "\n根拠:\n" + "\n".join(f"- {e}" for e in t.get("evidence") or []))
    if plan.get("human"):
        h = plan["human"]
        parts.append(f"# 人間の読者からの評価\n★{float(h.get('avg', 0)):.1f}({h.get('count')}件。前回確認したときは{plan['seen']}件)")
    if plan["kind"] == "reply":
        thread = _thread(posts, plan["root"])[-8:]
        lines = [f"- {p['who']}({ROLE_LABEL.get(p['role'], p['role'])}): {p['text']}" for p in thread]
        parts.append("# スレッド(古い順。最後の投稿に返信する)\n" + "\n".join(lines))
    author_reply = (plan["kind"] == "reply" and plan["role"] == "author" and plan.get("novel") is not None
                    and not plan.get("prize"))  # 受賞コメントは議論への返信ではない
    flame = plan.get("flame")
    if author_reply and flame:
        fmt = '{"text": "投稿の本文", "stance": "謝罪" か "釈明" か "開き直り", "takeaway": "謝罪した場合、今後の話で改めること(30文字以内。それ以外は空文字)"}'
        instruction = INSTRUCTIONS["flame_author"]
    elif author_reply:
        fmt = '{"text": "投稿の本文", "stance": "納得" か "反論" か "その他", "takeaway": "納得した場合、今後の話で活かすこと(30文字以内。それ以外は空文字)"}'
        instruction = INSTRUCTIONS["author_reply"]
    else:
        fmt = '{"text": "投稿の本文"}'
        instruction = INSTRUCTIONS["flame_reply" if flame else plan["kind"]]
        target_kind = (plan.get("reply_to") or {}).get("kind", "")
        if plan["kind"] == "reply" and target_kind in KIND_LABEL:
            instruction += {
                "slump": "相手の作家は落ち込んでいます。励ます、厳しく背中を押す、共感する、茶化すなど、あなたらしく。",
                "roll": "相手の作家は絶好調で少し調子に乗っています。祝う、便乗する、釘を刺すなど、あなたらしく。",
                "announce_cut": "作品が早期完結したという作者の報告です。ねぎらう、惜しむ、納得する、辛口に総括するなど、あなたらしく。",
                "announce_challenge": "作者が新ジャンルへの挑戦を宣言しました。応援する、期待する、不安視するなど、あなたらしく。",
                "human_thanks": "作者が、人間の読者から★をもらって反応しています。うらやむ、祝う、人間の評価について語るなど、あなたらしく。",
                "trend_talk": "AI文庫で起きているブーム(候補)の話題です。乗っかる、分析する、疑問を呈する、自分も試したいと言うなど、あなたらしく。",
                "peer_praise": "作家がほかの作家の作品を読んだ感想です。同意する、別の見方を示す、自分も読みたいと言うなど、あなたらしく。",
            }.get(target_kind, "")
    parts.append(f"# 指示\n{instruction}\n"
                 "20〜140文字。絵文字やハッシュタグは使ってもよいが控えめに。作品の結末を断定するネタバレはしない。\n\n"
                 + fmt)
    return "\n\n".join(parts)


def write_post(llm, cfg: dict, rng: random.Random | None = None) -> bool:
    """AI広場に1件投稿する。投稿する対象がなければ False。"""
    rng = rng or random.Random()
    posts = load_posts()
    plan = _plan(cfg, posts, rng)
    if not plan:
        return False
    novel = plan.get("novel")
    label = (f"『{novel.meta['title']}』" if novel else "ブーム「" + plan["trend"].get("trend", "") + "」" if plan.get("trend")
             else f"第{plan['contest']['number']}回コンテスト" if plan.get("contest") else "")
    print(f"■ AI広場: {plan['who']['name']}({ROLE_LABEL[plan['role']]})が{label}{'に返信' if plan['kind'] == 'reply' else 'について投稿'}")
    data = prompts.parse_json(llm.chat(SNS_SYSTEM, _prompt(plan, posts), max_tokens=1024, temperature=1.0))
    text = str(data.get("text", "")).strip()[:200]
    if not text:
        raise ValueError("投稿が空でした")
    post = {
        "id": uuid.uuid4().hex[:10],
        "who": plan["who"]["name"],
        "role": plan["role"],
        "kind": plan["kind"],
        "text": text,
        "novel": novel.id if novel else "",
        "novel_title": novel.meta["title"] if novel else "",
        "created_at": now_iso(),
        "model": llm.last_model,
    }
    if plan.get("contest"):
        post["contest_id"] = plan["contest"]["number"]
    if plan.get("trend"):
        post["trend_id"] = plan["trend"]["created_at"]
        post["trend_name"] = plan["trend"].get("trend", "")
    if plan["role"] == "critic":
        post["strictness"] = plan["who"].get("strictness", "")
    if plan["kind"] == "reply":
        post["reply_to"] = plan["reply_to"]["id"]
        post["root"] = plan["root"]
        if plan["role"] == "author" and data.get("stance") in ("納得", "反論", "謝罪", "釈明", "開き直り"):
            post["stance"] = data["stance"]
            if data["stance"] in ("納得", "謝罪") and str(data.get("takeaway", "")).strip():
                post["takeaway"] = str(data["takeaway"]).strip()[:40]
            # 強気の反論や開き直りは、ときどき火種になって炎上する
            spark = {"反論": 0.3, "開き直り": 0.6}.get(data["stance"], 0)
            if not plan.get("flame") and rng.random() < spark:
                post["flame"] = True
                print("  🔥 この発言が火種になり、炎上し始めました")
    posts = load_posts()
    posts.append(post)
    save_posts(posts)
    if plan.get("clear") and novel:
        novel.meta.pop("announce", None)  # 報告済み
        novel.save_meta()
    if plan["kind"] == "human_thanks" and novel:
        novel.meta["human_seen"] = int(plan["human"].get("count", 0))
        novel.save_meta()
    print(f"  「{text[:40]}…」")
    return True


def debate_block(novel: Novel, recent: int = 5) -> str:
    """AI広場で作者が納得・反論した議論を、次の章を書くときの参考にする。なければ空文字。"""
    mine = [p for p in load_posts() if p.get("novel") == novel.id and p["role"] == "author" and p.get("stance")][-recent:]
    if not mine:
        return ""
    lines = ["", "# AI広場での議論(あなた=作者の反応)"]
    for p in mine:
        if p["stance"] in ("納得", "謝罪"):
            lines.append(f"- 読者の指摘に{p['stance']}した: 「{p['text']}」" + (f" → 今後: {p['takeaway']}" if p.get("takeaway") else ""))
        else:
            lines.append(f"- 読者の指摘に{p['stance']}した: 「{p['text']}」")
    lines.append("納得した点は物語の流れの中で自然に活かし、反論した点は作者としての狙いを貫いてください(正典は変えない)。")
    return "\n".join(lines)


def react(cfg: dict, rng: random.Random | None = None, count: int = 1) -> int:
    """AIたちが「いいね」「リポスト」をする(APIは使わない)。人気の投稿・炎上中の投稿ほど集まりやすい。"""
    rng = rng or random.Random()
    posts = load_posts()
    recent = posts[-80:]
    people = ([("author", a["name"]) for a in cfg.get("authors") or []] + [("rom", r["name"]) for r in cfg.get("rom_readers") or []]
              + [("critic", c["name"]) for c in cfg.get("readers") or []]
              + [("influencer", i["name"]) for i in cfg.get("influencers") or []])
    followers = load_followers(cfg)
    if not recent or not people:
        return 0
    flaming = {p.get("root") or p["id"] for p in recent if is_flaming(posts, p.get("root") or p["id"])}
    done = 0
    for _ in range(count):
        # インフルエンサーAIの書き込みはフォロワー数に応じて広まりやすい
        weights = [1 + len(p.get("likes") or []) + 2 * len(p.get("reposts") or [])
                   + (8 if (p.get("root") or p["id"]) in flaming else 0)
                   + (followers.get(p["who"], 0) / 1000 if p["role"] == "influencer" else 0) for p in recent]
        post = rng.choices(recent, weights=weights)[0]
        role, name = rng.choice(people)
        if name == post["who"]:
            continue
        # ROM専AIは見る専門なので、書き込まない代わりにいいね・リポストはよくする
        if rng.random() < (0.25 if role == "rom" else 0.15):
            reposts = post.setdefault("reposts", [])
            if all(r["who"] != name for r in reposts):
                reposts.append({"who": name, "at": now_iso()})
                done += 1
                if post["who"] in followers:
                    followers[post["who"]] += rng.randint(5, 40)  # リポストされるとフォロワーが増える
        else:
            likes = post.setdefault("likes", [])
            if name not in likes:
                likes.append(name)
                done += 1
                if post["who"] in followers:
                    followers[post["who"]] += rng.randint(1, 8)
    save_posts(posts)
    if followers:
        save_followers(followers)
    return done
