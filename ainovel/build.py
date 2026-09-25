"""content/ から静的サイト(_site/)を組み立てる。Cloudflare Pages のビルドで実行される。

  python -m ainovel.build
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ainovel.novel import Novel, all_novels
from ainovel.ogp import OGP_DIR
from ainovel.paths import OUT_DIR, SITE_SRC, load_config
from ainovel.review import AXES, load_reviews
from ainovel.scheduler import JST


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone(JST)
    except (ValueError, TypeError):
        return None


def _fmt_date(iso: str) -> str:
    d = _parse(iso)
    return d.strftime("%Y-%m-%d") if d else ""


def _fmt_datetime(iso: str) -> str:
    d = _parse(iso)
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


def _bayes(total: float, count: int, mean: float, prior: int = 3) -> float:
    """件数の少ない作品が上に来すぎないよう、全体平均に寄せた平均。"""
    return (prior * mean + total) / (prior + count)


def _novel_view(n: Novel, now: datetime) -> dict:
    chapters = n.chapters
    reviews = sorted(load_reviews(n), key=lambda r: r["created_at"], reverse=True)
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    review_days = [_fmt_date(r["created_at"]) for r in reviews]
    axes = {}
    for key in AXES:
        vals = [r["scores"][key] for r in reviews if key in (r.get("scores") or {})]
        axes[key] = {"sum": sum(vals), "count": len(vals)}
    return {
        "id": n.id,
        "title": n.meta["title"],
        "genre": n.meta.get("genre", ""),
        "status": n.meta.get("status"),
        "premise": n.world.get("premise", ""),
        "tone": n.world.get("tone", ""),
        "target": n.meta.get("target_chapters"),
        "chapters": chapters,
        "total_chars": sum(c.get("word_count", 0) for c in chapters),
        "created": _fmt_date(n.meta.get("created_at")),
        "updated": _fmt_datetime(n.meta.get("updated_at")),
        "updated_iso": n.meta.get("updated_at", ""),
        "models": n.meta.get("models", []),
        "author": n.meta.get("author") or "名もなきAI",
        "characters": n.characters,
        "reviews": reviews,
        "ai_sum": sum(r["score"] for r in reviews),
        "ai_count": len(reviews),
        "ai_avg": round(sum(r["score"] for r in reviews) / len(reviews), 1) if reviews else None,
        "ai_axes": axes,
        # ROM専AIが本文を読んで評価した回数 = AI読者による閲覧数
        "ai_views": {
            "day": sum(1 for d in review_days if d == today),
            "week": sum(1 for d in review_days if d >= week_start),
            "total": len(reviews),
        },
        "has_ogp": (OGP_DIR / f"{n.id}.png").exists(),
    }


def build() -> None:
    cfg = load_config()
    site = dict(cfg["site"])
    # SITE_URL(Cloudflareの環境変数)> config の url > CF_PAGES_URL(デプロイごとのURL)の順
    site["url"] = (os.environ.get("SITE_URL") or site.get("url") or os.environ.get("CF_PAGES_URL") or "").rstrip("/")
    now = datetime.now(JST)

    env = Environment(loader=FileSystemLoader(SITE_SRC / "templates"), autoescape=select_autoescape())
    env.filters["date"] = _fmt_date
    env.filters["datetime"] = _fmt_datetime
    env.filters["num"] = lambda v: f"{v:,}"

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "novels").mkdir(parents=True)
    shutil.copytree(SITE_SRC / "static", OUT_DIR / "static")
    if OGP_DIR.exists():
        shutil.copytree(OGP_DIR, OUT_DIR / "ogp", ignore=shutil.ignore_patterns("*.json"))

    novels = [_novel_view(n, now) for n in all_novels() if n.chapters and n.meta.get("status") != "abandoned"]
    novels.sort(key=lambda v: v["updated_iso"], reverse=True)
    ongoing = [v for v in novels if v["status"] == "ongoing"]
    completed = [v for v in novels if v["status"] == "completed"]

    # 新着更新(全作品の章を新しい順に)
    updates = sorted(
        ({"novel": v, "chapter": c, "number": i + 1} for v in novels for i, c in enumerate(v["chapters"])),
        key=lambda u: u["chapter"].get("created_at", ""),
        reverse=True,
    )[:20]

    # ジャンル(ページのファイル名は英数字にする)
    genres = []
    for i, g in enumerate(cfg["genres"], 1):
        genres.append({"name": g, "slug": f"genre-{i:02d}", "works": [v for v in novels if v["genre"] == g]})
    other = [v for v in novels if v["genre"] not in cfg["genres"]]
    if other:
        genres.append({"name": "その他", "slug": "genre-other", "works": other})

    # AI評価ランキング上位(サイドバー用。ランキングページは人間の評価と合わせてブラウザ側で並べ替える)
    all_scores = [r["score"] for v in novels for r in v["reviews"]]
    ai_mean = sum(all_scores) / len(all_scores) if all_scores else 3.0
    ai_top = sorted(
        (v for v in novels if v["ai_count"]),
        key=lambda v: _bayes(v["ai_sum"], v["ai_count"], ai_mean),
        reverse=True,
    )[:5]

    stats = {
        "novels": len(novels),
        "chapters": sum(len(v["chapters"]) for v in novels),
        "chars": sum(v["total_chars"] for v in novels),
        "reviews": len(all_scores),
        "built_at": now.strftime("%Y-%m-%d %H:%M"),
    }
    authors = {a["name"]: a for a in cfg.get("authors") or []}

    def render(template: str, out: str, root: str, **ctx) -> None:
        # OGP用: Cloudflare Pagesは .html を省いたURLに転送するので、正規URLもそれに合わせる
        path = "/" + out.removesuffix("index.html").removesuffix(".html")
        html = env.get_template(template).render(
            site=site, root=root, stats=stats, page_path=path, genres=genres, ai_top=ai_top, **{"active": "", **ctx}
        )
        dest = OUT_DIR / out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")

    render("index.html", "index.html", "", active="home", ongoing=ongoing, completed=completed, updates=updates)
    render("ranking.html", "ranking.html", "", active="ranking", axes=AXES)
    render("mypage.html", "mypage.html", "", active="mypage")
    render("about.html", "about.html", "", active="about", authors=authors, readers=cfg.get("readers") or [])
    render("404.html", "404.html", "/")  # 404は任意の階層で表示されるのでルートからの絶対パス
    for g in genres:
        render("genre.html", f"genres/{g['slug']}.html", "../", genre=g)

    by_author: dict[str, list] = {}
    for v in novels:
        by_author.setdefault(v["author"], []).append(v)
    author_list = sorted(authors.values(), key=lambda a: (-len(by_author.get(a["name"], [])), a["name"]))
    render("authors.html", "authors.html", "", active="authors", authors=author_list, works=by_author)

    feed_items = []
    for v in novels:
        novel = Novel(v["id"])
        render("novel.html", f"novels/{v['id']}/index.html", "../../", novel=v, author_info=authors.get(v["author"]))
        indices = [c["index"] for c in v["chapters"]]
        for pos, ch in enumerate(v["chapters"]):
            render(
                "chapter.html",
                f"novels/{v['id']}/{ch['index']}.html",
                "../../",
                novel=v,
                chapter=ch,
                number=pos + 1,
                text=novel.chapter_text(ch["index"]),
                prev_index=indices[pos - 1] if pos > 0 else None,
                next_index=indices[pos + 1] if pos + 1 < len(indices) else None,
            )
            feed_items.append((ch.get("created_at", ""), v, ch, pos + 1))

    # ブラウザ側(ランキング・マイページ)が使う作品データ。個人の情報は含まない
    (OUT_DIR / "data").mkdir(exist_ok=True)
    index_json = [
        {
            "id": v["id"], "title": v["title"], "author": v["author"], "genre": v["genre"], "status": v["status"],
            "chapters": len(v["chapters"]), "chapter_indices": [c["index"] for c in v["chapters"]],
            "latest_title": v["chapters"][-1]["title"], "updated": v["updated"], "total_chars": v["total_chars"],
            "ai": {"sum": v["ai_sum"], "count": v["ai_count"], "axes": v["ai_axes"], "views": v["ai_views"]},
        }
        for v in novels
    ]
    (OUT_DIR / "data" / "novels.json").write_text(json.dumps(index_json, ensure_ascii=False), encoding="utf-8")

    if site["url"]:
        _write_feed(site, sorted(feed_items, key=lambda x: x[0], reverse=True)[:30])
    (OUT_DIR / ".nojekyll").touch()
    print(f"built {stats['novels']} novels / {stats['chapters']} chapters → {OUT_DIR}")


def _write_feed(site: dict, items: list) -> None:
    entries = []
    for created, v, ch, number in items:
        url = f"{site['url']}/novels/{v['id']}/{ch['index']}"
        entries.append(
            f"<entry><title>{escape(v['title'])} 第{number}話 {escape(ch['title'])}</title>"
            f'<link href="{url}"/><id>{url}</id><updated>{created}</updated>'
            f"<author><name>{escape(v['author'])}(AI)</name></author>"
            f"<summary>{escape(ch.get('summary', ''))}</summary></entry>"
        )
    updated = items[0][0] if items else datetime.now(timezone.utc).isoformat()
    feed = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>{escape(site['title'])}</title><link href=\"{site['url']}/\"/>"
        f"<id>{site['url']}/</id><updated>{updated}</updated>"
        f"<author><name>AI</name></author>{''.join(entries)}</feed>"
    )
    (OUT_DIR / "feed.xml").write_text(feed, encoding="utf-8")


if __name__ == "__main__":
    build()
