"""小説を書くAIの呼び出し口。どのプロバイダでも chat(system, user, ...) -> str で使える。

外部ライブラリを増やさないよう、API呼び出しは標準ライブラリ(urllib)だけで行う。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from typing import Optional


class LLMError(RuntimeError):
    def __init__(self, message: str, daily_quota: bool = False):
        super().__init__(message)
        self.daily_quota = daily_quota  # 1日の無料枠を使い切った(今日はもう書けない)


def _is_daily_quota(body: str) -> bool:
    b = body.lower()
    return "perday" in b or "per_day" in b or "per day" in b or "requestsperday" in b


class BaseLLM:
    name = "base"

    def __init__(self, min_interval: float = 0.0, tpm_limit: int = 0):
        self.min_interval = min_interval
        self.tpm_limit = tpm_limit  # 1分あたりのトークン上限(0なら管理しない)
        self._last_call = 0.0
        self._window: list[tuple[float, int]] = []  # 直近60秒の (時刻, 使用トークン)
        self.last_model = ""
        self.tokens_used = 0

    def _throttle(self, est_tokens: int = 0) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        if self.tpm_limit:
            # 毎分のトークン上限に当たらないよう、直近60秒の使用量に余裕ができるまで待つ
            while True:
                now = time.time()
                self._window = [(t, n) for t, n in self._window if now - t < 60]
                used = sum(n for _, n in self._window)
                if not self._window or used + est_tokens <= self.tpm_limit:
                    break
                time.sleep(max(1.0, 60 - (now - self._window[0][0]) + 0.5))
        self._last_call = time.time()

    def _record(self, tokens: int) -> None:
        self.tokens_used += tokens
        self._window.append((time.time(), tokens))

    def chat(self, system: str, user: str, max_tokens: int = 2048, temperature: float = 0.9) -> str:
        raise NotImplementedError


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def _with_retry(fn, attempts: int = 4):
    """429(上限)や5xxは待ってやり直す。それ以外のエラーはすぐ諦めて次のモデルへ。"""
    delay = 15.0
    for i in range(attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            if e.code == 429 and _is_daily_quota(body):
                raise LLMError(f"HTTP 429 (1日の無料枠を使い切りました): {body[:300]}", daily_quota=True) from e
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                print(f"  [retry] HTTP {e.code}、{delay:.0f}秒待って再試行: {body}")
                time.sleep(delay)
                delay *= 2
                continue
            raise LLMError(f"HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if i < attempts - 1:
                time.sleep(delay)
                continue
            raise LLMError(str(e)) from e


def _estimate_tokens(system: str, user: str, max_tokens: int) -> int:
    """呼び出し前の使用トークン見積もり(日本語は1文字≒1トークン弱、出力は上限の7割程度と見る)。"""
    return int((len(system) + len(user)) * 0.9 + max_tokens * 0.7)


class GeminiLLM(BaseLLM):
    """Google AI Studio の Gemini API(Gemma 4 は無料枠で利用可能)。"""

    name = "gemini"
    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, models: list[str], min_interval: float, tpm_limit: int = 0):
        super().__init__(min_interval, tpm_limit)
        self.api_key = api_key
        self.models = models

    def chat(self, system, user, max_tokens=2048, temperature=0.9):
        est = _estimate_tokens(system, user, max_tokens)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        errors = []
        for model in self.models:
            self._throttle(est)
            try:
                data = _with_retry(
                    lambda: _post_json(
                        self.ENDPOINT.format(model=model), payload, {"x-goog-api-key": self.api_key}
                    )
                )
            except LLMError as e:
                if e.daily_quota and model == self.models[-1]:
                    raise
                errors.append(f"{model}: {e}")
                continue
            self._record(int((data.get("usageMetadata") or {}).get("totalTokenCount") or est))
            candidates = data.get("candidates") or []
            if not candidates:
                errors.append(f"{model}: 応答なし {data.get('promptFeedback')}")
                continue
            parts = (candidates[0].get("content") or {}).get("parts") or []
            # Gemma 4 は思考過程(thought)を返すことがあるので本文だけ拾う
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if text.strip():
                self.last_model = model
                return text.strip()
            errors.append(f"{model}: 本文が空 (finishReason={candidates[0].get('finishReason')})")
        raise LLMError(" / ".join(errors))


class OpenAICompatLLM(BaseLLM):
    """OpenAI互換API(OpenRouterの無料モデルなど)。"""

    name = "openai"

    def __init__(self, api_key: str, base_url: str, models: list[str], min_interval: float, tpm_limit: int = 0):
        super().__init__(min_interval, tpm_limit)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.models = models

    def chat(self, system, user, max_tokens=2048, temperature=0.9):
        errors = []
        est = _estimate_tokens(system, user, max_tokens)
        for model in self.models:
            self._throttle(est)
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            try:
                data = _with_retry(
                    lambda: _post_json(
                        f"{self.base_url}/chat/completions", payload, {"Authorization": f"Bearer {self.api_key}"}
                    )
                )
                text = data["choices"][0]["message"]["content"] or ""
            except LLMError as e:
                if e.daily_quota and model == self.models[-1]:
                    raise
                errors.append(f"{model}: {e}")
                continue
            except (KeyError, IndexError) as e:
                errors.append(f"{model}: {e}")
                continue
            self._record(int((data.get("usage") or {}).get("total_tokens") or est))
            if text.strip():
                self.last_model = model
                return text.strip()
            errors.append(f"{model}: 本文が空")
        raise LLMError(" / ".join(errors))


class MockLLM(BaseLLM):
    """APIキーなしで仕組みを確認するためのダミー。"""

    name = "mock"

    def __init__(self):
        super().__init__(0)
        self.last_model = "mock"
        self.rng = random.Random()

    def chat(self, system, user, max_tokens=2048, temperature=0.9):
        n = self.rng.randint(100, 999)
        if '"characters"' in user:
            return json.dumps({"characters": [
                {"name": f"登場人物{n}{i}", "role": r, "age": 20 + i, "gender": "不明",
                 "appearance": "外見", "personality": "性格", "speech_style": "口調",
                 "background": "経歴", "motivation": "目的", "relationships": [], "arc": "変化"}
                for i, r in enumerate(["主人公", "相棒", "敵役"])]}, ensure_ascii=False)
        if '"premise"' in user:
            return json.dumps({"title": f"モック作品{n}", "genre": "テスト", "tone": "静か", "pov": "三人称",
                               "tense": "過去形", "setting": "どこか", "premise": f"これはモックのあらすじ{n}です。",
                               "themes": ["テスト"], "rules": ["特になし"],
                               "plot_outline": ["起", "承", "転", "結"], "style_notes": "平易に"}, ensure_ascii=False)
        if '"summary"' in user:
            return json.dumps({"title": f"章タイトル{n}", "summary": "モックの要約。",
                               "new_facts": [f"事実{n}"], "open_threads": []}, ensure_ascii=False)
        m = re.search(r"約(\d+)文字", user)
        target = int(m.group(1)) if m else 1000
        para = "モックの本文。静かな夜だった。登場人物は遠くの灯りを見つめ、次に起こることを考えていた。"
        return "\n\n".join(para for _ in range(target // len(para) + 1))


def make_llm(cfg: dict) -> Optional[BaseLLM]:
    """設定と環境変数からAIを作る。必要なAPIキーが無ければ None(執筆をスキップ)。"""
    provider = os.environ.get("AINOVEL_PROVIDER") or cfg.get("provider", "gemini")
    interval = float(cfg.get("min_interval_sec", 6))
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        g = cfg["gemini"]
        return GeminiLLM(key, g["models"], interval, int(g.get("tpm_limit", 0))) if key else None
    if provider == "openai":
        key = os.environ.get("OPENAI_COMPAT_API_KEY")
        o = cfg["openai"]
        return OpenAICompatLLM(key, o["base_url"], o["models"], interval, int(o.get("tpm_limit", 0))) if key else None
    if provider == "mock":
        return MockLLM()
    raise ValueError(f"不明なprovider: {provider}")
