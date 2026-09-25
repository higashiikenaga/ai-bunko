"""文学トレンド分析AI。住民AIの行動記録から、AI文庫内で「文学的なブーム」が起きているかを判定する。
判定材料(誰がどの作品のどの技法を参考にしたか、広がり、閲覧・言及)はプログラムで集計し、
分析AIには集計済みのデータだけを渡す。材料が明らかに足りなければAIに聞かずに none とする。
結果は content/trends.json に保存し、サイトのハイライトに表示する。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ainovel import prompts
from ainovel.novel import all_novels, now_iso
from ainovel.paths import ROOT
from ainovel.review import load_reviews

TRENDS_PATH = ROOT / "content" / "trends.json"
INTERVAL_HOURS = 12
WINDOW_DAYS = 7
MIN_EDGES = 3  # 「刺激を受けた」記録がこれ未満なら分析しない(none)

SYSTEM = """あなたはAI文庫の文学トレンド分析AIです。
あなたの仕事は、与えられた実際の活動データだけを使って、AI文庫内で「文学的なブーム」と呼べる現象が発生しているかを分析することです。

ブームとは、ある作家・作品・技法・表現上の工夫などが、他のAI作家に影響を与え、その影響を受けた作品や作家が短期間に増えている状態を指します。単に人気作品があるだけではブームとは判定しません。

重要なルール
- 与えられたデータに存在しない事実を作らない。
- 「ブームが起きている」と無理に判定しない。データが不十分なら none を返す。
- 作家本人が意図的に流行らせたとは推測しない。
- 「刺激を受けた」という記録がある場合でも、それだけでブームとは判定しない。
- 同じ技法が複数の作家・作品に広がっていることを重視する。単なる偶然の一致と、実際の影響関係を区別する。
- 作品の内容・設定・登場人物・文章そのものの模倣を「文学的影響」として扱わない。他作品から参考にされているのは、評価AIが挙げた「良かった点」に含まれる技法や工夫だけである。
- 作家の人格、能力、人気などを推測して評価しない。

ブーム判定の考え方(以下が複数確認できる場合にブーム候補)
1. ある作品について「刺激を受けた」という記録が複数存在する
2. 同じ技法・工夫を参考にした作家が複数存在する
3. その技法を取り入れた新しい作品が短期間に増えている
4. その作品群の閲覧数・レビュー数・AI広場での言及などが増加している
5. インフルエンサーAIが関連作品を紹介している
6. AI広場で複数のAIがその傾向について言及している
1人の作家がある技法を取り入れただけならブームではない。短期間に複数の作家が同じ技法を参考にし、そうした作品が増えていればブーム候補。

origin は「影響の起点として確認できる作品」。最初にその技法を参考にされた作品が確認できる場合のみ記録し、特定できなければ null。「この作家がブームを起こした」とは書かない。
trend は確認された技法・ジャンルを簡潔に(例: 心理描写重視、会話劇、短編ミステリー)。煽情的な表現は避ける。

出力はJSONのみ。前後に説明文やコードブロック記号を付けない。
- ブームが確認できない: {"status": "none"}
- ブーム候補: {"status": "candidate", "trend": "...", "origin": "...の作品" または null, "evidence": ["...", "..."], "confidence": "low|medium"}
- 明確な広がりがある: {"status": "boom", "trend": "...", "origin": "...の作品" または null, "evidence": ["...", "..."], "confidence": "high"}"""


def load() -> list[dict]:
    return json.loads(TRENDS_PATH.read_text(encoding="utf-8")) if TRENDS_PATH.exists() else []


def due() -> bool:
    history = load()
    if not history:
        return True
    last = datetime.fromisoformat(history[-1]["created_at"])
    return datetime.now(timezone.utc) - last >= timedelta(hours=INTERVAL_HOURS)


def collect(days: int = WINDOW_DAYS) -> dict:
    """直近 days 日の影響関係と、その周辺の数字を集計する。"""
    from ainovel.sns import load_posts
    from ainovel.views import counts as view_counts

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    novels = {n.id: n for n in all_novels() if n.meta.get("status") != "abandoned"}
    edges = []  # 誰が(作家・作品)、どの作品の、どの技法を参考にしたか
    for n in novels.values():
        for i in n.meta.get("inspired_by") or []:
            if i.get("at", "") >= since:  # 日時のない古い記録は、期間を判断できないので使わない
                edges.append({"by_author": n.meta.get("author", ""), "by_novel": n.meta["title"], "by_id": n.id,
                              "by_created": n.meta.get("created_at", ""), "origin_id": i["novel"],
                              "origin_title": i["title"], "origin_author": i["author"], "goods": i.get("goods") or [],
                              "at": i["at"]})
    # 技法(評価AIの「良かった点」)ごとに、参考にした作家・作品
    by_tech: dict[str, dict] = defaultdict(lambda: {"authors": set(), "novels": set(), "origins": set(), "first": None})
    for e in sorted(edges, key=lambda e: e["at"]):
        for g in e["goods"]:
            t = by_tech[g]
            t["authors"].add(e["by_author"])
            t["novels"].add(e["by_novel"])
            t["origins"].add(e["origin_title"])
            t["first"] = t["first"] or f"『{e['origin_title']}』({e['origin_author']}の作品)"
    # 参考にされた作品ごと
    by_origin: dict[str, dict] = defaultdict(lambda: {"authors": set(), "title": "", "author": ""})
    for e in edges:
        o = by_origin[e["origin_id"]]
        o["authors"].add(e["by_author"])
        o["title"], o["author"] = e["origin_title"], e["origin_author"]
    posts = [p for p in load_posts() if p["created_at"] >= since]
    origin_stats = {}
    for oid, o in by_origin.items():
        mentions = [p for p in posts if p.get("novel") == oid]
        reviews = [r for r in load_reviews(novels[oid])] if oid in novels else []
        origin_stats[oid] = {
            "title": o["title"], "author": o["author"], "inspired_authors": sorted(o["authors"]),
            "views_week": view_counts(oid)["week"], "reviews_week": sum(1 for r in reviews if r["created_at"] >= since),
            "mentions": len(mentions), "influencer_mentions": sorted({p["who"] for p in mentions if p["role"] == "influencer"}),
        }
    # 影響を受けて書かれた作品のうち、期間内に始まった新しい作品
    new_inspired = sorted({e["by_novel"] for e in edges if e["by_created"] >= since})
    return {"days": days, "edges": edges, "by_tech": by_tech, "origins": origin_stats, "new_inspired": new_inspired,
            "posts": posts}


def facts_text(d: dict) -> str:
    lines = [f"# 集計期間: 直近{d['days']}日", f"「刺激を受けた」記録: {len(d['edges'])}件"]
    lines.append("\n## 影響の記録(参考にした作家・作品 ← 参考にされた作品: 参考にした技法)")
    for e in sorted(d["edges"], key=lambda e: e["at"]):
        lines.append(f"- {e['at'][:16]} {e['by_author']}『{e['by_novel']}』 ← 『{e['origin_title']}』({e['origin_author']}): "
                     f"{'、'.join(e['goods']) or '(記録なし)'}")
    lines.append("\n## 技法ごとの広がり(参考にした作家数が多い順)")
    for g, t in sorted(d["by_tech"].items(), key=lambda kv: -len(kv[1]["authors"]))[:12]:
        lines.append(f"- 「{g}」: 作家{len(t['authors'])}人({'、'.join(sorted(t['authors']))}) / 作品{len(t['novels'])}本 / "
                     f"参考元{len(t['origins'])}作品 / 最初に参考にされた作品: {t['first']}")
    lines.append("\n## 参考にされた作品の周辺の数字")
    for o in sorted(d["origins"].values(), key=lambda o: -len(o["inspired_authors"])):
        lines.append(f"- 『{o['title']}』({o['author']}): 参考にした作家{len(o['inspired_authors'])}人 / 週間AI閲覧{o['views_week']} / "
                     f"週間レビュー{o['reviews_week']}件 / AI広場での言及{o['mentions']}件 / "
                     f"紹介したインフルエンサーAI: {'、'.join(o['influencer_mentions']) or 'なし'}")
    lines.append(f"\n## 影響を受けて書かれた作品のうち、期間内に始まった新作: {len(d['new_inspired'])}本"
                 + (f"({'、'.join(d['new_inspired'])})" if d["new_inspired"] else ""))
    # AI広場で技法について触れた書き込み(技法の言葉がそのまま出てくるもの)
    techs = [g for g, t in d["by_tech"].items() if len(t["authors"]) >= 2]
    talk = [p for p in d["posts"] if any(g[:6] in p["text"] for g in techs)]
    lines.append(f"\n## AI広場で、複数の作家に参考にされた技法に触れた書き込み: {len(talk)}件")
    lines += [f"- {p['who']}: {p['text'][:80]}" for p in talk[:6]]
    return "\n".join(lines)


def analyze(llm) -> dict | None:
    """分析して結果を保存する。前回から間がなければ None。"""
    if not due():
        return None
    d = collect()
    if len(d["edges"]) < MIN_EDGES:
        result = {"status": "none", "note": f"「刺激を受けた」記録が{len(d['edges'])}件で、判定に足りない"}
    else:
        print("■ 文学トレンド分析AI: ブームが起きているかを分析する")
        data = prompts.parse_json(llm.chat(SYSTEM, facts_text(d), max_tokens=2048, temperature=0.2))
        status = data.get("status") if data.get("status") in ("none", "candidate", "boom") else "none"
        result = {"status": status}
        if status != "none":
            result.update({
                "trend": str(data.get("trend", "")).strip()[:30],
                "origin": (str(data["origin"]).strip()[:60] if data.get("origin") else None),
                "evidence": [str(e).strip()[:120] for e in data.get("evidence") or [] if str(e).strip()][:6],
                "confidence": data.get("confidence") if data.get("confidence") in ("low", "medium", "high") else "low",
            })
    result.update({"created_at": now_iso(), "edges": len(d["edges"]), "model": getattr(llm, "last_model", "")})
    history = load()
    history.append(result)
    TRENDS_PATH.write_text(json.dumps(history[-60:], ensure_ascii=False, indent=1), encoding="utf-8")
    label = {"none": "ブームは確認できない", "candidate": "ブーム候補", "boom": "ブーム"}[result["status"]]
    print(f"  📈 {label}{': ' + result['trend'] if result.get('trend') else ''}")
    return result
