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

ROLE_LABEL = {"author": "AI作家", "rom": "ROM専AI", "critic": "評価AI"}

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
            counts[p["novel"]] = counts.get(p["novel"], 0) + 1
    return counts


def _persona(role: str, who: dict) -> str:
    if role == "author":
        return f"AI作家「{who['name']}」。作風: {who.get('style', '')} 口調: {who.get('tone', '')}"
    if role == "rom":
        return f"ROM専AI「{who['name']}」(感想は書かず読むだけの読者)。{who.get('style', '')}"
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
    kinds = {"promo": 2, "buzz": 3, "opinion": 3, "reply": 5 if posts else 0}
    kind = rng.choices(list(kinds), weights=list(kinds.values()))[0]

    if kind == "reply":
        recent = posts[-40:]
        # 返信が付いている話題ほど伸びやすい(論争が続く)
        target = rng.choices(recent, weights=[1 + 2 * sum(1 for q in posts if q.get("root") == (p.get("root") or p["id"])) for p in recent])[0]
        root = target.get("root") or target["id"]
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
            if author and author["name"] != target["who"] and rng.random() < (0.45 if heated else 0.1):
                return {"kind": "reply", "role": "author", "who": author, "novel": novel, "reply_to": target, "root": root}
        pool += [("rom", r) for r in rng.sample(roms, min(5, len(roms)))]
        pool += [("critic", c) for c in rng.sample(critics, min(5, len(critics))) if c["name"] != target["who"]]
        pool = [(r, w) for r, w in pool if w["name"] != target["who"]]
        if not pool:
            return None
        role, who = rng.choice(pool)
        return {"kind": "reply", "role": role, "who": who, "novel": novel, "reply_to": target, "root": root}

    if kind == "promo" and authors:
        mine = {a["name"]: a for a in authors}
        candidates = [n for n in novels if n.meta.get("author") in mine]
        if candidates:
            novel = max(candidates, key=lambda n: n.meta.get("updated_at", "")) if rng.random() < 0.5 else rng.choice(candidates)
            return {"kind": "promo", "role": "author", "who": mine[novel.meta["author"]], "novel": novel}
    if kind == "opinion" and critics:
        who = rng.choice(critics)
        reviewed = [n for n in novels if any(r["reader"] == who["name"] for r in load_reviews(n))]
        novel = rng.choice(reviewed) if reviewed and rng.random() < 0.7 else _pick_novel(novels, rng, buzz)
        return {"kind": "opinion", "role": "critic", "who": who, "novel": novel}
    if roms:
        return {"kind": "buzz", "role": "rom", "who": rng.choice(roms), "novel": _pick_novel(novels, rng, buzz)}
    return None


INSTRUCTIONS = {
    "promo": "自分の作品を宣伝する投稿、または執筆の近況をつぶやいてください。押しつけがましすぎず、読みたくなるように。",
    "buzz": "この作品を読んだ(流し読みした)ROM専として、ほかの読者に広めるような口コミをつぶやいてください。"
            "おすすめでも、気になった点でも、正直な印象でかまいません。",
    "opinion": "この作品について、あなたの評価の立場からはっきり意見を述べてください。好みや辛口度に正直に。",
    "reply": "上のスレッドに返信してください。同意・反論・補足・茶化しなど、あなたの立場として自然な反応を。"
             "意見が違うなら遠慮なく反論してかまいません(ただし人格攻撃や罵倒はしない)。",
    "author_reply": "あなたはこの作品の作者です。スレッドでの賛否を読み、作者として返信してください。"
                    "指摘がもっともだと思えば素直に納得して、今後の話でどう活かすかを述べてかまいません。"
                    "意図が誤解されている・的外れだと思えば、作者としての狙いを説明して反論してかまいません。"
                    "どちらにするかは議論の中身で決めてください。感情的になりすぎず、ネタバレはしない。",
}


def _prompt(plan: dict, posts: list[dict]) -> str:
    who = plan["who"]
    parts = [f"# あなた\n{_persona(plan['role'], who)}"]
    if plan.get("novel"):
        reviewer = who["name"] if plan["role"] == "critic" else None
        parts.append(f"# 話題の作品\n{_novel_context(plan['novel'], reviewer)}")
    if plan["kind"] == "reply":
        thread = _thread(posts, plan["root"])[-8:]
        lines = [f"- {p['who']}({ROLE_LABEL.get(p['role'], p['role'])}): {p['text']}" for p in thread]
        parts.append("# スレッド(古い順。最後の投稿に返信する)\n" + "\n".join(lines))
    author_reply = plan["kind"] == "reply" and plan["role"] == "author"
    fmt = ('{"text": "投稿の本文", "stance": "納得" か "反論" か "その他", "takeaway": "納得した場合、今後の話で活かすこと(30文字以内。それ以外は空文字)"}'
           if author_reply else '{"text": "投稿の本文"}')
    parts.append(f"# 指示\n{INSTRUCTIONS['author_reply' if author_reply else plan['kind']]}\n"
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
    label = f"『{novel.meta['title']}』" if novel else ""
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
    if plan["role"] == "critic":
        post["strictness"] = plan["who"].get("strictness", "")
    if plan["kind"] == "reply":
        post["reply_to"] = plan["reply_to"]["id"]
        post["root"] = plan["root"]
        if plan["role"] == "author" and data.get("stance") in ("納得", "反論"):
            post["stance"] = data["stance"]
            if data["stance"] == "納得" and str(data.get("takeaway", "")).strip():
                post["takeaway"] = str(data["takeaway"]).strip()[:40]
    posts = load_posts()
    posts.append(post)
    save_posts(posts)
    print(f"  「{text[:40]}…」")
    return True


def debate_block(novel: Novel, recent: int = 5) -> str:
    """AI広場で作者が納得・反論した議論を、次の章を書くときの参考にする。なければ空文字。"""
    mine = [p for p in load_posts() if p.get("novel") == novel.id and p["role"] == "author" and p.get("stance")][-recent:]
    if not mine:
        return ""
    lines = ["", "# AI広場での議論(あなた=作者の反応)"]
    for p in mine:
        if p["stance"] == "納得":
            lines.append(f"- 読者の指摘に納得した: 「{p['text']}」" + (f" → 今後: {p['takeaway']}" if p.get("takeaway") else ""))
        else:
            lines.append(f"- 読者の指摘に反論した: 「{p['text']}」")
    lines.append("納得した点は物語の流れの中で自然に活かし、反論した点は作者としての狙いを貫いてください(正典は変えない)。")
    return "\n".join(lines)
