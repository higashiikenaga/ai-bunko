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
    def __init__(self, message: str, daily_quota: bool = False, server_error: bool = False):
        super().__init__(message)
        self.daily_quota = daily_quota  # 1日の無料枠を使い切った(今日はもう書けない)
        self.server_error = server_error  # API側の一時的な障害(5xx)


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
        self._exhausted: set[str] = set()  # 1日の無料枠を使い切ったモデル(今回の実行ではもう使わない)

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
        # Python標準のUser-Agentだと、Cloudflare配下のAPI(Groqなど)に 403 (error code: 1010) で弾かれる
        headers={"Content-Type": "application/json", "User-Agent": "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def _with_retry(fn, attempts: int = 4, server_attempts: int = 2):
    """429(上限)や5xxは待ってやり直す。それ以外のエラーはすぐ諦めて次のモデルへ。
    5xx(Google側の一時的な障害)は同じモデルで粘らず、server_attempts 回で次のモデルに切り替える。"""
    delay = 15.0
    for i in range(attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            if e.code == 429 and _is_daily_quota(body):
                raise LLMError(f"HTTP 429 (1日の無料枠を使い切りました): {body[:300]}", daily_quota=True) from e
            if e.code >= 500 and i >= server_attempts - 1:
                raise LLMError(f"HTTP {e.code}: {body}", server_error=True) from e
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
        # 全モデルが5xxだったときだけ、少し待ってもう一巡する
        for model in self.models + self.models:
            if model in self._exhausted:
                continue
            if len(errors) == len(self.models):
                if not all(e.startswith("5xx ") for e in errors):
                    break
                time.sleep(30)
            self._throttle(est)
            try:
                data = _with_retry(
                    lambda: _post_json(
                        self.ENDPOINT.format(model=model), payload, {"x-goog-api-key": self.api_key}
                    )
                )
            except LLMError as e:
                if e.daily_quota:
                    # モデルごとに1日の枠は別。全モデルを使い切ったときだけ「今日はもう書けない」とする
                    self._exhausted.add(model)
                    if self._exhausted >= set(self.models):
                        raise
                    print(f"  ({model} は本日の無料枠を使い切ったため、以降は他のモデルを使います)")
                    continue
                errors.append(f"{'5xx ' if e.server_error else ''}{model}: {e}")
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
            if model in self._exhausted:
                continue
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
                if e.daily_quota:
                    # モデルごとに1日の枠は別。全モデルを使い切ったときだけ「今日はもう書けない」とする
                    self._exhausted.add(model)
                    if self._exhausted >= set(self.models):
                        raise
                    print(f"  ({model} は本日の無料枠を使い切ったため、以降は他のモデルを使います)")
                    continue
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


class ChainLLM:
    """メインのAPIが全モデル失敗したとき、別の無料API(Groqなど)に切り替える。"""

    name = "chain"

    def __init__(self, llms: list[BaseLLM]):
        self.llms = llms
        self.last_model = ""

    @property
    def tokens_used(self) -> int:
        return sum(l.tokens_used for l in self.llms)

    def prefer(self, name: str) -> "ChainLLM":
        """指定したAPIを先に試す版(中のAPIは共有するので、毎分の上限の管理も共有される)。"""
        return ChainLLM(sorted(self.llms, key=lambda l: l.name != name))

    def chat(self, system, user, max_tokens=2048, temperature=0.9):
        errors: list[LLMError] = []
        for llm in self.llms:
            try:
                text = llm.chat(system, user, max_tokens=max_tokens, temperature=temperature)
            except LLMError as e:
                errors.append(e)
                if llm is not self.llms[-1]:
                    print(f"  ({llm.name} が使えないため、別のAPIに切り替えます: {str(e)[:120]})")
                continue
            self.last_model = llm.last_model
            return text
        raise LLMError(" / ".join(f"{l.name}: {e}" for l, e in zip(self.llms, errors)),
                       daily_quota=all(e.daily_quota for e in errors))


class MockLLM(BaseLLM):
    """APIキーなしで仕組みを確認するためのダミー。"""

    name = "mock"

    def __init__(self):
        super().__init__(0)
        self.last_model = "mock"
        self.rng = random.Random()

    def chat(self, system, user, max_tokens=2048, temperature=0.9):
        n = self.rng.randint(100, 999)
        if '"headline"' in user:
            return json.dumps({"headline": f"モック見出し{n}", "items": [{"title": "辛口AIが大暴れ", "body": "モックの記事本文。"}]},
                              ensure_ascii=False)
        if '"text"' in user:
            return json.dumps({"text": f"モックのつぶやき{n}。この作品、続きが気になる。"}, ensure_ascii=False)
        if '"score"' in user:
            return json.dumps({"score": self.rng.randint(1, 5),
                               "scores": {k: self.rng.randint(1, 5) for k in ("story", "characters", "writing", "originality")},
                               "comment": f"モックの感想{n}。主人公の迷いが丁寧で、続きが気になる。",
                               "good": "空気感", "bad": "展開が遅い"}, ensure_ascii=False)
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
        # 毎回違う文章にする(重複チェックに引っかからない、普通に書き進めた章の代わり)
        chars = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん朝夜雨風灯扉紙港塔森駅鏡星海橋鍵声影光道空花街窓"
        text = "".join(self.rng.choice(chars) + ("。\n\n" if self.rng.random() < 0.02 else "") for _ in range(target))
        return "モックの本文。" + text


def _primary_llm(cfg: dict, provider: str, interval: float) -> Optional[BaseLLM]:
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


def llm_for(llm, cfg: dict, task: str):
    """作業の種類ごとに、先に使うAPIを切り替える(config の routes)。該当しなければそのまま。"""
    name = (cfg.get("routes") or {}).get(task)
    return llm.prefer(name) if name and isinstance(llm, ChainLLM) else llm


def make_llm(cfg: dict):
    """設定と環境変数からAIを作る。使えるAPIキーが1つも無ければ None(執筆をスキップ)。
    config の fallbacks(Groqなど)は、キーが設定されていればメインが全滅したときの予備として後ろにつなぐ。"""
    provider = os.environ.get("AINOVEL_PROVIDER") or cfg.get("provider", "gemini")
    interval = float(cfg.get("min_interval_sec", 6))
    primary = _primary_llm(cfg, provider, interval)
    if provider == "mock":
        return primary
    llms = [primary] if primary else []
    for fb in cfg.get("fallbacks") or []:
        key = os.environ.get(fb["api_key_env"])
        if key:
            llm = OpenAICompatLLM(key, fb["base_url"], fb["models"], float(fb.get("min_interval_sec", interval)),
                                  int(fb.get("tpm_limit", 0)))
            llm.name = fb.get("name", "fallback")
            llms.append(llm)
    if not llms:
        return None
    return llms[0] if len(llms) == 1 else ChainLLM(llms)
