"""OGP画像(1200x630)の生成。GitHub Actions(日本語フォントをインストール済み)で実行し、content/ogp/ にコミットする。
Cloudflare Pages のビルド環境には日本語フォントが無いので、そちらでは生成しない(できた画像をコピーするだけ)。

  python -m ainovel.ogp          # 足りない・内容が変わったOGP画像を作る
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ainovel.novel import Novel, all_novels
from ainovel.paths import ROOT, load_config

OGP_DIR = ROOT / "content" / "ogp"
W, H = 1200, 630
ACCENT = (26, 115, 200)
TEXT = (34, 34, 34)
MUTED = (110, 110, 110)

FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", None),  # Ubuntu: fonts-noto-cjk
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", None),
    ("C:/Windows/Fonts/NotoSansJP-VF.ttf", "Bold"),
    ("C:/Windows/Fonts/YuGothB.ttc", None),
    ("C:/Windows/Fonts/meiryob.ttc", None),
    ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", None),
]


def find_font() -> tuple[str, str | None] | None:
    for path, variation in FONT_CANDIDATES:
        if Path(path).exists():
            return path, variation
    return None


def _font(spec: tuple[str, str | None], size: int, bold: bool = True):
    from PIL import ImageFont

    path, variation = spec
    index = 0
    if path.endswith(".ttc") and "NotoSansCJK" in path:
        index = 0  # ttc内の Japanese 版(JP)は先頭
    font = ImageFont.truetype(path, size, index=index)
    if variation:
        try:
            font.set_variation_by_name(variation if bold else "Regular")
        except (OSError, ValueError):
            pass
    return font


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for ch in text:
        if draw.textlength(line + ch, font=font) > max_width and line:
            lines.append(line)
            line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    return lines


def _fit_title(draw, spec, title: str, max_width: int, max_lines: int = 3):
    for size in (84, 76, 68, 60, 54, 48):
        font = _font(spec, size)
        lines = _wrap(draw, title, font, max_width)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _font(spec, 48)
    lines = _wrap(draw, title, font, max_width)[:max_lines]
    lines[-1] = lines[-1][:-1] + "…"
    return font, lines, 48


def _base(spec, site_title: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=ACCENT)
    d.rectangle([0, H - 8, W, H], fill=(230, 236, 244))
    d.text((64, 48), site_title, font=_font(spec, 40), fill=ACCENT)
    tag = "AIだけが書く小説サイト"
    tf = _font(spec, 26, bold=False)
    d.text((W - 64 - d.textlength(tag, font=tf), 58), tag, font=tf, fill=MUTED)
    d.line([64, 112, W - 64, 112], fill=(225, 225, 225), width=2)
    return img, d


def render_site(spec, site: dict, path: Path) -> None:
    img, d = _base(spec, site["title"])
    big = _font(spec, 120)
    title = site["title"]
    d.text(((W - d.textlength(title, font=big)) / 2, 190), title, font=big, fill=TEXT)
    desc_font = _font(spec, 30, bold=False)
    y = 370
    for line in _wrap(d, site["description"], desc_font, W - 128):
        d.text(((W - d.textlength(line, font=desc_font)) / 2, y), line, font=desc_font, fill=MUTED)
        y += 52
    img.save(path, optimize=True)


def render_novel(spec, site: dict, meta: dict, path: Path) -> None:
    img, d = _base(spec, site["title"])
    font, lines, size = _fit_title(d, spec, meta["title"], W - 128)
    y = 160 + (3 - len(lines)) * size * 0.35
    for line in lines:
        d.text((64, y), line, font=font, fill=TEXT)
        y += size * 1.3
    status = "完結" if meta.get("status") == "completed" else "連載中"
    info = f"作: {meta.get('author') or 'AI'}(AI)  ・  {meta.get('genre', '')}  ・  {status}"
    d.text((64, H - 120), info, font=_font(spec, 32, bold=False), fill=MUTED)
    img.save(path, optimize=True)


def _signature(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def ensure_all() -> int:
    """足りない・内容が変わったOGP画像を作る。作った枚数を返す。フォントが無ければ何もしない。"""
    spec = find_font()
    if not spec:
        print("OGP画像: 日本語フォントが見つからないため生成をスキップします")
        return 0
    OGP_DIR.mkdir(parents=True, exist_ok=True)
    index_path = OGP_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    site = load_config()["site"]
    made = 0

    sig = _signature("site", site["title"], site["description"], "v2")
    if index.get("site") != sig or not (OGP_DIR / "site.png").exists():
        render_site(spec, site, OGP_DIR / "site.png")
        index["site"] = sig
        made += 1

    for novel in all_novels():
        m = novel.meta
        if m.get("status") == "abandoned":
            continue
        sig = _signature(m["title"], m.get("author"), m.get("genre"), m.get("status"), "v1")
        out = OGP_DIR / f"{novel.id}.png"
        if index.get(novel.id) != sig or not out.exists():
            render_novel(spec, site, m, out)
            index[novel.id] = sig
            made += 1

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OGP画像: {made}枚を生成しました")
    return made


if __name__ == "__main__":
    ensure_all()
