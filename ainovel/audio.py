"""聞く小説(Gemini の TTS で人気作品を朗読音声にする)。
- 対象は人気作品(AI評価の上位・コンテスト受賞作・特別企画)。第1話から順に、1回の実行で1話ずつ作る
- 無料枠の上限に当たったら、その日は作らない。1日の上限も設定で決める
- Gemini の TTS は 24kHz・16bit・モノラルの PCM を返すので、ffmpeg で AAC(.m4a)に圧縮して content/audio に保存する
- 長い本文はいくつかに分けて読み上げ、つなげる
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime

from ainovel.novel import Novel, all_novels
from ainovel.paths import ROOT
from ainovel.review import load_reviews
from ainovel.scheduler import JST

AUDIO_DIR = ROOT / "content" / "audio"
STATE_PATH = ROOT / "content" / "audio_state.json"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
USER_AGENT = "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)"
VOICES = ["Kore", "Aoede", "Leda", "Charon", "Puck", "Orus", "Zephyr", "Fenrir"]
CHUNK_CHARS = 3500   # 1回の呼び出しで読ませる量(無料枠の1日の回数が少ないので、なるべくまとめる)


def enabled(cfg: dict) -> bool:
    t = cfg.get("tts") or {}
    return bool(t.get("enabled", True) and os.environ.get("GEMINI_API_KEY") and shutil.which("ffmpeg"))


def _state() -> dict:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    data = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    return data if data.get("date") == today else {"date": today, "count": 0, "requests": 0, "exhausted": False}


def _save_state(s: dict) -> None:
    STATE_PATH.write_text(json.dumps(s), encoding="utf-8")


def _tts(cfg: dict, text: str, voice: str) -> bytes:
    """テキストを読み上げた PCM(24kHz/16bit/mono)を返す。"""
    t = cfg.get("tts") or {}
    style = t.get("style", "落ち着いた声で、情景が浮かぶように、小説を朗読してください。会話文は少しだけ声色を変えてください。")
    payload = {
        "contents": [{"parts": [{"text": f"{style}\n\n{text}"}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    req = urllib.request.Request(ENDPOINT.format(model=t.get("model", "gemini-3.8-flash-lite-tts")),
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT,
                                          "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"TTS HTTP {e.code}: {body}", e.code) from e
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    audio = next((p["inlineData"]["data"] for p in parts if p.get("inlineData")), None)
    if not audio:
        raise RuntimeError(f"TTS: 音声が返りませんでした {str(data)[:200]}")
    return base64.b64decode(audio)


def _chunks(text: str) -> list[str]:
    """段落の切れ目で CHUNK_CHARS 文字前後に分ける。"""
    out, cur = [], ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) > CHUNK_CHARS:
            out.append(cur.strip())
            cur = ""
        cur += para + "\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def _encode(pcm: bytes, dest) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pcm") as f:
        f.write(pcm)
        f.flush()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", f.name,
                        "-c:a", "aac", "-b:a", "40k", str(dest)], check=True)


def _popular(cfg: dict) -> list[Novel]:
    """聞く小説にする人気作品(AI評価の上位・コンテスト受賞作・特別企画)。"""
    from ainovel.contest import awards

    t = cfg.get("tts") or {}
    novels = [n for n in all_novels() if n.chapters and n.meta.get("status") != "abandoned"]
    scores = []
    all_scores = [r["score"] for n in novels for r in load_reviews(n)]
    mean = sum(all_scores) / len(all_scores) if all_scores else 3.0
    for n in novels:
        rv = load_reviews(n)
        scores.append(((3 * mean + sum(r["score"] for r in rv)) / (3 + len(rv)), n))
    top = [n for s, n in sorted(scores, key=lambda x: -x[0]) if len(load_reviews(n)) >= 3][:int(t.get("top_works", 5))]
    winners = {a["novel"] for lst in awards().values() for a in lst}
    picked = [n for n in novels if n.meta.get("special") or n.id in winners]
    return list(dict.fromkeys(picked + top))


def make_one(cfg: dict) -> bool:
    """人気作品の、まだ音声のない最も前の話を1話ぶん朗読音声にする。作ったら True。"""
    t = cfg.get("tts") or {}
    if not enabled(cfg):
        return False
    st = _state()
    rpd = int(t.get("daily_requests", 9))  # 1日の呼び出し回数の上限(無料枠のRPDより少し少なく)
    if st["exhausted"] or st.get("requests", 0) >= rpd:
        return False
    for n in _popular(cfg):
        done = n.meta.get("audio") or {}
        ch = next((c for c in n.chapters if str(c["index"]) not in done), None)
        if not ch:
            continue
        voice = VOICES[int(hashlib.md5(n.id.encode()).hexdigest(), 16) % len(VOICES)]  # 作品ごとに同じ声
        parts = _chunks(n.chapter_text(ch["index"]))
        if st.get("requests", 0) + len(parts) > rpd:
            return False  # 今日の残り回数では1話を読み切れない(途中までの音声は作らない)
        print(f"■ 聞く小説: 『{n.meta['title']}』第{ch['index']}話を朗読({voice}・{len(parts)}回に分けて)")
        pcm = b""
        try:
            for k, part in enumerate(parts):
                if k:
                    time.sleep(float(t.get("interval_sec", 25)))  # 1分あたりの回数の上限(RPM)に当たらないように
                st["requests"] = st.get("requests", 0) + 1
                _save_state(st)
                pcm += _tts(cfg, part, voice)
        except RuntimeError as e:
            if len(e.args) > 1 and e.args[1] == 429:
                st["exhausted"] = True  # 無料枠の上限。今日はもう作らない
                _save_state(st)
                print("  TTSの無料枠の上限に達したため、今日の朗読はここまでにします")
                return False
            raise
        dest = AUDIO_DIR / n.id / f"{ch['index']:03d}.m4a"
        _encode(pcm, dest)
        n.meta.setdefault("audio", {})[str(ch["index"])] = "audio/" + dest.relative_to(AUDIO_DIR).as_posix()
        n.meta["audio_voice"] = voice
        n.save_meta()
        st["count"] += 1
        _save_state(st)
        print(f"  → {dest.stat().st_size // 1024}KB({len(pcm) / 48000 / 60:.1f}分)")
        return True
    return False
