"""content/ から静的サイト(_site/)を組み立てる。Cloudflare Pages のビルドで実行される。

  python -m ainovel.build
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ainovel.novel import Novel, all_novels
from ainovel.paths import OUT_DIR, ROOT, SITE_SRC, load_config

JST = timezone(timedelta(hours=9))


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


def _novel_view(n: Novel, zones: dict[str, dict]) -> dict:
    m = n.meta
    chapters = n.chapters
    zone = zones.get(m.get("ai_zone"))
    return {
        "id": n.id,
        "title": m["title"],
        "genre": m.get("genre", ""),
        "status": m.get("status", "ongoing"),
        "premise": m.get("premise", ""),
        "chapters": chapters,
        "total_chars": sum(c.get("word_count", 0) for c in chapters),
        "created": _fmt_date(m.get("created_at")),
        "updated": _fmt_datetime(m.get("updated_at")),
        "updated_iso": m.get("updated_at", ""),
        "author": m.get("author") or "名無しさん",
        "ai_tools": m.get("ai_tools", ""),
        "zone": zone,
    }


def build() -> None:
    cfg = load_config()
    site = dict(cfg["site"])
    # SITE_URL(Cloudflareの環境変数)> config の url > CF_PAGES_URL(デプロイごとのURL)の順
    site["url"] = (os.environ.get("SITE_URL") or site.get("url") or os.environ.get("CF_PAGES_URL") or "").rstrip("/")
    now = datetime.now(JST)
    zones = {z["key"]: z for z in cfg["zones"]}

    env = Environment(loader=FileSystemLoader(SITE_SRC / "templates"), autoescape=select_autoescape())
    env.filters["date"] = _fmt_date
    env.filters["datetime"] = _fmt_datetime
    env.filters["num"] = lambda v: f"{v:,}"

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "novels").mkdir(parents=True)
    shutil.copytree(SITE_SRC / "static", OUT_DIR / "static")
    ogp_dir = ROOT / "content" / "ogp"
    if ogp_dir.exists():
        shutil.copytree(ogp_dir, OUT_DIR / "ogp")

    # 運営が status を hidden にした作品や、まだ1話もない作品は出さない
    novels = [_novel_view(n, zones) for n in all_novels() if n.chapters and n.meta.get("status") != "hidden"]
    novels.sort(key=lambda v: v["updated_iso"], reverse=True)
    ongoing = [v for v in novels if v["status"] == "ongoing"]
    completed = [v for v in novels if v["status"] == "completed"]

    updates = sorted(
        ({"novel": v, "chapter": c, "number": i + 1} for v in novels for i, c in enumerate(v["chapters"])),
        key=lambda u: u["chapter"].get("created_at", ""),
        reverse=True,
    )[:20]

    genres = []
    for i, g in enumerate(cfg["genres"], 1):
        genres.append({"name": g, "slug": f"genre-{i:02d}", "works": [v for v in novels if v["genre"] == g]})
    other = [v for v in novels if v["genre"] not in cfg["genres"]]
    if other:
        genres.append({"name": "その他", "slug": "genre-other", "works": other})

    zone_list = [{**z, "works": [v for v in novels if v["zone"] and v["zone"]["key"] == z["key"]]} for z in cfg["zones"]]

    stats = {
        "novels": len(novels),
        "chapters": sum(len(v["chapters"]) for v in novels),
        "chars": sum(v["total_chars"] for v in novels),
        "built_at": now.strftime("%Y-%m-%d %H:%M"),
    }

    def render(template: str, out: str, root: str, **ctx) -> None:
        # Cloudflare Pagesは .html を省いたURLに転送するので、正規URLもそれに合わせる
        path = "/" + out.removesuffix("index.html").removesuffix(".html")
        html = env.get_template(template).render(
            site=site, root=root, stats=stats, page_path=path, genres=genres, zones=zone_list, **{"active": "", **ctx}
        )
        dest = OUT_DIR / out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")

    render("index.html", "index.html", "", active="home", ongoing=ongoing, completed=completed, updates=updates)
    render("ranking.html", "ranking.html", "", active="ranking")
    render("mypage.html", "mypage.html", "", active="mypage")
    render("search.html", "search.html", "", active="search")
    render("submit.html", "submit.html", "", active="submit", rules=cfg["submit"])
    render("about.html", "about.html", "", active="about", rules=cfg["submit"])
    render("404.html", "404.html", "/")  # 404は任意の階層で表示されるのでルートからの絶対パス
    for g in genres:
        render("genre.html", f"genres/{g['slug']}.html", "../", genre=g)
    for z in zone_list:
        render("zone.html", f"zones/{z['key']}.html", "../", zone=z)

    feed_items = []
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
            feed_items.append((ch.get("created_at", ""), v, ch, pos + 1))

    # ブラウザ側(ランキング・検索・マイページ)と投稿API(functions/api/submit.js)が使うデータ
    (OUT_DIR / "data").mkdir(exist_ok=True)
    index_json = [
        {
            "id": v["id"], "title": v["title"], "author": v["author"], "genre": v["genre"], "status": v["status"],
            "chapters": len(v["chapters"]), "chapter_indices": [c["index"] for c in v["chapters"]],
            "latest_title": v["chapters"][-1]["title"], "updated": v["updated"], "total_chars": v["total_chars"],
            "premise": v["premise"], "updated_iso": v["updated_iso"],
            "zone": v["zone"]["key"] if v["zone"] else "", "ai_tools": v["ai_tools"],
        }
        for v in novels
    ]
    (OUT_DIR / "data" / "novels.json").write_text(json.dumps(index_json, ensure_ascii=False), encoding="utf-8")
    meta = {"genres": cfg["genres"], "zones": [{k: z[k] for k in ("key", "label", "short", "color")} for z in cfg["zones"]],
            "rules": cfg["submit"]}
    (OUT_DIR / "data" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    if site["url"]:
        _write_feed(site, sorted(feed_items, key=lambda x: x[0], reverse=True)[:30])
        _write_sitemap(site, novels, genres, zone_list)
    robots = "User-agent: *\nAllow: /\nDisallow: /api/\n"
    if site["url"]:
        robots += f"Sitemap: {site['url']}/sitemap.xml\n"
    (OUT_DIR / "robots.txt").write_text(robots, encoding="utf-8")
    # ブラウザが自動で取りに来る /favicon.ico(中身はPNG。主要ブラウザはこれで表示できる)
    shutil.copy(SITE_SRC / "static" / "icon-32.png", OUT_DIR / "favicon.ico")
    (OUT_DIR / ".nojekyll").touch()
    print(f"built {stats['novels']} novels / {stats['chapters']} chapters → {OUT_DIR}")


def _write_sitemap(site: dict, novels: list, genres: list, zones: list) -> None:
    """検索エンジン向けのページ一覧。URLはCloudflare Pagesの正規形(.htmlなし)。"""
    base = site["url"]
    pages = ["ranking", "about", "search", "submit"]
    urls = [(f"{base}/", None)] + [(f"{base}/{p}", None) for p in pages]
    urls += [(f"{base}/genres/{g['slug']}", None) for g in genres if g["works"]]
    urls += [(f"{base}/zones/{z['key']}", None) for z in zones]
    for v in novels:
        urls.append((f"{base}/novels/{v['id']}/", v["updated_iso"]))
        urls += [(f"{base}/novels/{v['id']}/{c['index']}", c.get("created_at")) for c in v["chapters"]]
    items = "".join(
        f"<url><loc>{escape(u)}</loc>" + (f"<lastmod>{escape(m[:10])}</lastmod>" if m else "") + "</url>\n"
        for u, m in urls
    )
    (OUT_DIR / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>\n',
        encoding="utf-8",
    )
    (OUT_DIR / "sitemap.txt").write_text("".join(f"{u}\n" for u, _ in urls), encoding="utf-8")
    (OUT_DIR / "_headers").write_text(
        "/sitemap.xml\n  Content-Type: application/xml; charset=utf-8\n  Cache-Control: public, max-age=600\n"
        "/feed.xml\n  Content-Type: application/atom+xml; charset=utf-8\n"
        "/robots.txt\n  Content-Type: text/plain; charset=utf-8\n"
        "/sitemap.txt\n  Content-Type: text/plain; charset=utf-8\n",
        encoding="utf-8",
    )


def _write_feed(site: dict, items: list) -> None:
    entries = []
    for created, v, ch, number in items:
        url = f"{site['url']}/novels/{v['id']}/{ch['index']}"
        zone = f"【{v['zone']['label']}】" if v["zone"] else ""
        entries.append(
            f"<entry><title>{zone}{escape(v['title'])} 第{number}話 {escape(ch['title'])}</title>"
            f'<link href="{url}"/><id>{url}</id><updated>{created}</updated>'
            f"<author><name>{escape(v['author'])}</name></author>"
            f"<summary>{escape(v['premise'])}</summary></entry>"
        )
    updated = items[0][0] if items else datetime.now(timezone.utc).isoformat()
    feed = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>{escape(site['title'])}</title><link href=\"{site['url']}/\"/>"
        f"<id>{site['url']}/</id><updated>{updated}</updated>"
        f"<author><name>{escape(site['title'])}</name></author>{''.join(entries)}</feed>"
    )
    (OUT_DIR / "feed.xml").write_text(feed, encoding="utf-8")


if __name__ == "__main__":
    build()
