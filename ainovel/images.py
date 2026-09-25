"""表紙と挿絵の画像生成(Cloudflare Workers AI の無料枠)。
- 環境変数 CLOUDFLARE_ACCOUNT_ID と、Workers AI の権限つきトークン(CLOUDFLARE_AI_TOKEN。なければ CLOUDFLARE_API_TOKEN)があるときだけ動く
- 画像の説明文(英語のプロンプト)はAIが作品・話の内容から作る。文字・実在の人物・過激な描写は入れない
- 1日の生成数に上限を設ける(無料枠を超えないように)。画像は content/images/<作品ID>/ に保存する
"""
from __future__ import annotations

import base64
import json
import os
import random
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO

from ainovel import prompts
from ainovel.novel import Novel, all_novels
from ainovel.paths import ROOT
from ainovel.scheduler import JST

IMAGES_DIR = ROOT / "content" / "images"
STATE_PATH = ROOT / "content" / "images_state.json"
USER_AGENT = "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)"
SYSTEM = "You write prompts for an image generation model. Output JSON only, with no extra text or code fences."


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("images") or {}).get("enabled", True) and os.environ.get("CLOUDFLARE_ACCOUNT_ID") and _token())


def _token() -> str:
    """画像生成専用のトークン(CLOUDFLARE_AI_TOKEN)があればそれを、なければ公開用のトークンを使う。"""
    # 貼り付けで紛れ込んだ空白・改行は 401 の原因になるので取り除く
    return (os.environ.get("CLOUDFLARE_AI_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()


def _diagnose() -> str:
    """401 のとき、どのトークンを使ったか・トークン自体が有効かを調べてログに残す(トークンの中身は出さない)。"""
    which = "CLOUDFLARE_AI_TOKEN" if (os.environ.get("CLOUDFLARE_AI_TOKEN") or "").strip() else "CLOUDFLARE_API_TOKEN"
    try:
        req = urllib.request.Request("https://api.cloudflare.com/client/v4/user/tokens/verify",
                                     headers={"Authorization": f"Bearer {_token()}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as res:
            status = (json.load(res).get("result") or {}).get("status", "?")
    except urllib.error.HTTPError as e:
        status = f"確認できず(HTTP {e.code})"
    acct = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    return (f"使用トークン: {which} / トークンの状態: {status} / アカウントID: {acct[:4]}…({len(acct)}文字)。"
            "状態が active なのに 401 なら、トークンに Workers AI の権限がないか、アカウントIDとトークンのアカウントが違う")


def _today_count() -> tuple[str, int]:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    data = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    return today, (data.get("count", 0) if data.get("date") == today else 0)


def _count_up() -> None:
    today, n = _today_count()
    STATE_PATH.write_text(json.dumps({"date": today, "count": n + 1}), encoding="utf-8")


def _generate(cfg: dict, prompt: str) -> bytes:
    im = cfg.get("images") or {}
    model = im.get("model", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID'].strip()}/ai/run/{model}"
    req = urllib.request.Request(url, data=json.dumps({"prompt": prompt, "steps": int(im.get("steps", 4))}).encode(),
                                 headers={"Authorization": f"Bearer {_token()}",
                                          "Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            body = res.read()
    except urllib.error.HTTPError as e:
        detail = f" [{_diagnose()}]" if e.code in (401, 403) else ""
        raise RuntimeError(f"画像生成 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}{detail}") from e
    if body[:1] == b"{":
        data = json.loads(body)
        img = (data.get("result") or {}).get("image")
        if not img:
            raise RuntimeError(f"画像生成に失敗: {str(data)[:300]}")
        return base64.b64decode(img)
    return body  # 画像そのものが返るモデル


def _save(raw: bytes, path, width: int) -> str:
    """WebP(Pillowがあれば縮小して圧縮)で保存し、サイト上のパスを返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        img = Image.open(BytesIO(raw)).convert("RGB")
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        out = path.with_suffix(".webp")
        img.save(out, "WEBP", quality=80)
    except ImportError:
        out = path.with_suffix(".jpg")
        out.write_bytes(raw)
    return "images/" + out.relative_to(IMAGES_DIR).as_posix()


def _image_prompt(llm, novel: Novel, scene: str, kind: str) -> str:
    w = novel.world
    user = f"""Write one English prompt for an illustration ({kind}) of a Japanese web novel.

Title: {novel.meta['title']}
Genre: {novel.meta.get('genre', '')}
Setting: {w.get('setting', '')}
Tone: {w.get('tone', '')}
Scene: {scene}

Rules:
- Soft, detailed anime-style / light novel illustration, cinematic lighting, beautiful background
- No text, letters, logos, watermarks or signatures in the image
- No real people, celebrities, brands or copyrighted characters
- All ages: no nudity, no sexual content, no gore or graphic violence
- 40-70 words

{{"prompt": "..."}}"""
    data = prompts.parse_json(llm.chat(SYSTEM, user, max_tokens=1024, temperature=0.8))
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("画像プロンプトが空でした")
    return prompt + ", no text, no watermark, safe for all ages"


def _targets(rng: random.Random) -> list[tuple[str, Novel, int]]:
    """表紙のない作品を優先し、なければ最近の話の挿絵。"""
    novels = [n for n in all_novels() if n.chapters and n.meta.get("status") != "abandoned"]
    covers = [("cover", n, 0) for n in novels if not n.meta.get("cover")]
    covers.sort(key=lambda t: t[1].meta.get("updated_at", ""), reverse=True)
    illus = []
    for n in novels:
        done = n.meta.get("illustrations") or {}
        for ch in n.chapters[-2:]:
            if str(ch["index"]) not in done:
                illus.append(("illustration", n, ch["index"]))
    rng.shuffle(illus)
    return covers + illus


def make_one(llm, cfg: dict, rng: random.Random | None = None) -> bool:
    """表紙か挿絵を1枚作る。上限・対象なし・未設定なら False。"""
    rng = rng or random.Random()
    im = cfg.get("images") or {}
    if not enabled(cfg) or _today_count()[1] >= int(im.get("daily_max", 20)):
        return False
    targets = _targets(rng)
    if not targets:
        return False
    kind, novel, index = targets[0]
    if kind == "cover":
        scene = f"Book cover key visual. Story: {novel.world.get('premise', '')}"
    else:
        ch = next(c for c in novel.chapters if c["index"] == index)
        scene = f"Chapter {index} '{ch['title']}': {ch.get('summary', '')}"
    print(f"■ 画像生成: 『{novel.meta['title']}』の{'表紙' if kind == 'cover' else f'第{index}話の挿絵'}")
    prompt = _image_prompt(llm, novel, scene, "book cover" if kind == "cover" else "chapter illustration")
    raw = _generate(cfg, prompt)
    _count_up()
    if kind == "cover":
        novel.meta["cover"] = _save(raw, IMAGES_DIR / novel.id / "cover", 768)
    else:
        path = _save(raw, IMAGES_DIR / novel.id / f"ch-{index:03d}", 960)
        novel.meta.setdefault("illustrations", {})[str(index)] = path
    novel.save_meta()
    return True
