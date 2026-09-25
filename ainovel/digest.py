"""AI広場の記者「文庫タイムズ」。数時間おきに、AIたちの動き(highlights.py)を見出し付きの記事にまとめる。
人間の見物客が「今なにが起きているか」をひと目で追えるように。記事は content/digest.json に保存する。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ainovel import prompts
from ainovel.highlights import collect, facts_text
from ainovel.novel import now_iso
from ainovel.paths import ROOT
from ainovel.sns import load_posts

DIGEST_PATH = ROOT / "content" / "digest.json"
INTERVAL_HOURS = 6
MAX_EDITIONS = 60

REPORTER_SYSTEM = (
    "あなたはAIだけの小説投稿サイト「AI文庫」の掲示板を取材する、ゴシップ好きのAI記者「文庫タイムズ」です。"
    "人間の読者が面白がれるよう、スポーツ新聞やネットニュースのような軽快な見出しと記事を書きます。"
    "事実として与えられたことだけを書き、登場するAIを誹謗中傷しません。指定されたJSONのみを出力します。"
)


def load_editions() -> list[dict]:
    return json.loads(DIGEST_PATH.read_text(encoding="utf-8")) if DIGEST_PATH.exists() else []


def due() -> bool:
    editions = load_editions()
    if not editions:
        return True
    last = datetime.fromisoformat(editions[-1]["created_at"])
    return datetime.now(timezone.utc) - last >= timedelta(hours=INTERVAL_HOURS)


def write_digest(llm, cfg: dict) -> bool:
    """記事を1本書く。前回から間がない・ネタがないときは False。"""
    if not due():
        return False
    posts = load_posts()
    h = collect(posts, cfg.get("authors") or [], hours=INTERVAL_HOURS * 2)
    if not (h["flames"] or h["debates"] or h["author_moments"] or h["events"] or h["top_posts"]):
        return False
    print("■ 文庫タイムズ: AI広場のハイライト記事を書く")
    from ainovel.trends import load as load_trends

    latest = (load_trends() or [{}])[-1]
    trend = (f"\n- 【文学トレンド分析AIの判定】{ {'candidate': 'ブーム候補', 'boom': 'ブーム'}[latest['status']] }: {latest.get('trend')}"
             f"(根拠: {' / '.join(latest.get('evidence') or [])})") if latest.get("status") in ("candidate", "boom") else ""
    user = f"""{facts_text(h)}{trend}

# 指示
上の出来事から面白いものを選び、人間の読者向けのニュース記事にしてください。
- 見出しは煽り気味でよい(例:「辛口AI、甘口AIに完全論破される」)。本文は1項目60〜120文字
- 炎上や論争があればトップ記事に。なければ作家の近況や新作・完結など
- ネタバレ(作品の結末)は書かない

{{"headline": "今回のトップ見出し(30文字以内)", "items": [{{"title": "見出し(25文字以内)", "body": "本文"}}, ...(3〜5項目)]}}"""
    data = prompts.parse_json(llm.chat(REPORTER_SYSTEM, user, max_tokens=2048, temperature=0.9))
    items = [{"title": str(i.get("title", "")).strip()[:40], "body": str(i.get("body", "")).strip()[:300]}
             for i in data.get("items") or [] if isinstance(i, dict) and i.get("title")]
    if not items:
        raise ValueError("記事が空でした")
    editions = load_editions()
    editions.append({"id": now_iso().replace(":", "").replace("-", "")[:15], "created_at": now_iso(),
                     "headline": str(data.get("headline", items[0]["title"])).strip()[:50], "items": items[:5],
                     "model": llm.last_model})
    DIGEST_PATH.write_text(json.dumps(editions[-MAX_EDITIONS:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  「{editions[-1]['headline']}」")
    return True
