"""content/ から静的サイト(_site/)を組み立てる。Cloudflare Pages のビルドで実行される。

  python -m ainovel.build
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ainovel.novel import Novel, all_novels
from ainovel.paths import OUT_DIR, SITE_SRC, load_config
from ainovel.review import load_reviews
from ainovel.scheduler import JST


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _novel_view(n: Novel, authors: dict[str, dict]) -> dict:
    chapters = n.chapters
    author_name = n.meta.get("author") or "名もなきAI"
    reviews = sorted(load_reviews(n), key=lambda r: r["created_at"], reverse=True)
    avg = round(sum(r["score"] for r in reviews) / len(reviews), 1) if reviews else None
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
        "updated": _fmt_date(n.meta.get("updated_at")),
        "updated_iso": n.meta.get("updated_at", ""),
        "models": n.meta.get("models", []),
        "author": author_name,
        "author_info": authors.get(author_name),
        "characters": n.characters,
        "reviews": reviews,
        "avg_score": avg,
    }


def build() -> None:
    cfg = load_config()
    site = dict(cfg["site"])
    # Cloudflare Pages では SITE_URL(自分で設定)> config の url > CF_PAGES_URL(デプロイごとのURL)の順
    site["url"] = (os.environ.get("SITE_URL") or site.get("url") or os.environ.get("CF_PAGES_URL") or "").rstrip("/")

    env = Environment(loader=FileSystemLoader(SITE_SRC / "templates"), autoescape=select_autoescape())
    env.filters["date"] = _fmt_date
    env.filters["num"] = lambda v: f"{v:,}"

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "novels").mkdir(parents=True)
    shutil.copytree(SITE_SRC / "static", OUT_DIR / "static")

    authors = {a["name"]: a for a in cfg.get("authors") or []}
    novels = [_novel_view(n, authors) for n in all_novels() if n.chapters]
    novels.sort(key=lambda v: v["updated_iso"], reverse=True)
    ongoing = [v for v in novels if v["status"] == "ongoing"]
    completed = [v for v in novels if v["status"] == "completed"]
    stats = {
        "novels": len(novels),
        "chapters": sum(len(v["chapters"]) for v in novels),
        "chars": sum(v["total_chars"] for v in novels),
        "built_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
    }

    def render(template: str, out: str, root: str, **ctx) -> None:
        html = env.get_template(template).render(site=site, root=root, stats=stats, **ctx)
        path = OUT_DIR / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    render("index.html", "index.html", "", ongoing=ongoing, completed=completed)
    render("mypage.html", "mypage.html", "")

    # AI評価ランキング: 件数の少ない作品が上に来すぎないよう、全体平均に寄せたベイズ平均で並べる
    reviewed = [v for v in novels if v["reviews"]]
    all_scores = [r["score"] for v in reviewed for r in v["reviews"]]
    mean = sum(all_scores) / len(all_scores) if all_scores else 3.0
    prior = 3  # 評価3件分だけ全体平均を足し込む
    for v in reviewed:
        v["rank_score"] = (prior * mean + sum(r["score"] for r in v["reviews"])) / (prior + len(v["reviews"]))
    ai_ranking = sorted(reviewed, key=lambda v: v["rank_score"], reverse=True)[:30]
    render("ranking.html", "ranking.html", "", ai_ranking=ai_ranking)

    # 作家一覧(作品数の多い順、同数なら名前順)
    by_author = {}
    for v in novels:
        by_author.setdefault(v["author"], []).append(v)
    author_list = sorted(
        authors.values(), key=lambda a: (-len(by_author.get(a["name"], [])), a["name"])
    )
    render("authors.html", "authors.html", "", authors=author_list, works=by_author)

    # マイページ(ブックマーク・フォロー)がブラウザ側で参照する作品一覧。個人の情報は含まない
    (OUT_DIR / "data").mkdir(exist_ok=True)
    index_json = [
        {
            "id": v["id"], "title": v["title"], "author": v["author"], "genre": v["genre"], "status": v["status"],
            "chapters": len(v["chapters"]), "latest_index": v["chapters"][-1]["index"],
            "latest_title": v["chapters"][-1]["title"], "updated": v["updated"], "avg_score": v["avg_score"],
            "chapter_indices": [c["index"] for c in v["chapters"]],
        }
        for v in novels
    ]
    (OUT_DIR / "data" / "novels.json").write_text(json.dumps(index_json, ensure_ascii=False), encoding="utf-8")
    author_stats = {
        name: {"works": sum(1 for v in novels if v["author"] == name),
               "chapters": sum(len(v["chapters"]) for v in novels if v["author"] == name)}
        for name in authors
    }
    render("about.html", "about.html", "", authors=list(authors.values()), author_stats=author_stats,
           readers=cfg.get("readers") or [])
    # 404ページは任意の階層で表示されるので、リンクはサイトのルートからの絶対パスにする
    render("404.html", "404.html", "/")

    latest = []
    for v in novels:
        novel = Novel(v["id"])
        render("novel.html", f"novels/{v['id']}/index.html", "../../", novel=v)
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
            latest.append((ch.get("created_at", ""), v, ch, pos + 1))

    if site["url"]:
        _write_feed(site, sorted(latest, key=lambda x: x[0], reverse=True)[:30])
    (OUT_DIR / ".nojekyll").touch()
    print(f"built {stats['novels']} novels / {stats['chapters']} chapters → {OUT_DIR}")


def _write_feed(site: dict, items: list) -> None:
    entries = []
    for created, v, ch, number in items:
        url = f"{site['url']}/novels/{v['id']}/{ch['index']}.html"
        entries.append(
            f"<entry><title>{escape(v['title'])} 第{number}話 {escape(ch['title'])}</title>"
            f'<link href="{url}"/><id>{url}</id><updated>{created}</updated>'
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
