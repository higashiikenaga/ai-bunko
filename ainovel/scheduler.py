"""投稿タイミングと本数を決める。

- 1日(日本時間)の投稿数・使用トークン数・無料枠切れを content/state.json に記録する
- 実行ごとの投稿数は「今日あと何本必要か ÷ 今日の残り実行回数」を基準にランダムに決め、
  ときどき多めに書く(連投)。1日の最低本数(daily_min_posts)は必ず超えるようにし、
  上限は無料枠が尽きるまで(daily_max_posts を 0 にした場合)。
- 実行開始時にランダムな時間待つことで、投稿される時刻もばらけさせる
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

from ainovel.paths import ROOT

JST = timezone(timedelta(hours=9))
STATE_PATH = ROOT / "content" / "state.json"


def now_jst() -> datetime:
    return datetime.now(JST)


class DailyState:
    def __init__(self):
        today = now_jst().strftime("%Y-%m-%d")
        data = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        if data.get("date") != today:
            data = {"date": today, "posts": 0, "tokens": 0, "quota_exhausted": False,
                    "total_posts": data.get("total_posts", 0)}
        self.data = data

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def posts(self) -> int:
        return self.data["posts"]

    def add_post(self) -> None:
        self.data["posts"] += 1
        self.data["total_posts"] = self.data.get("total_posts", 0) + 1
        self.save()

    def add_tokens(self, n: int) -> None:
        self.data["tokens"] += int(n)
        self.save()

    def mark_quota_exhausted(self) -> None:
        self.data["quota_exhausted"] = True
        self.save()


def plan_posts(state: DailyState, sched: dict, rng: random.Random | None = None) -> int:
    """今回の実行で投稿する章数を決める。"""
    rng = rng or random.Random()
    if state.data.get("quota_exhausted"):
        return 0
    daily_min = int(sched["daily_min_posts"])
    daily_max = int(sched.get("daily_max_posts") or 0)  # 0 = 無料枠が尽きるまで
    if daily_max and state.posts >= daily_max:
        return 0

    now = now_jst()
    # 最低本数は夕方(min_deadline_hour時)までに書き終える目標にし、それ以降の実行は予備にする。
    # GitHubの定期実行はたまに飛ばされるので、最後の1回に頼らないようにするため。
    deadline = now.replace(hour=int(sched.get("min_deadline_hour", 18)), minute=0, second=0, microsecond=0)
    hours_left = (deadline - now).total_seconds() / 3600
    need = max(0, daily_min - state.posts)
    if hours_left <= 0:
        base = float(need)  # 期限を過ぎて足りなければ、この回でまとめて書く
    else:
        runs_left = max(1, math.floor(hours_left / float(sched["run_interval_hours"]) * float(sched["reliability"])))
        base = need / runs_left
    n = int(base) + (1 if rng.random() < base - int(base) else 0)
    if rng.random() < float(sched["burst_probability"]):
        n += rng.randint(1, int(sched["burst_max"]))
    elif need == 0 and rng.random() < 0.5:
        n += rng.randint(0, 2)  # 最低本数を満たした後も、ゆるやかに書き続ける

    n = min(n, int(sched["per_run_max"]))
    if daily_max:
        n = min(n, daily_max - state.posts)
    return max(0, n)


def start_delay_seconds(sched: dict, state: DailyState, rng: random.Random | None = None) -> int:
    """書き始めるまでのランダムな待ち時間。日付をまたいで今日の本数に数えられなくならないよう、
    日付が変わる30分前までに書き始められる範囲に収める。今日の最低本数が未達で残り時間が少ないときは待たない。"""
    rng = rng or random.Random()
    now = now_jst()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    room = (midnight - now).total_seconds() - 30 * 60
    need = int(sched["daily_min_posts"]) - state.posts
    if need > 0 and room < float(sched["run_interval_hours"]) * 3600:
        return 0
    return rng.randint(0, int(max(0, min(float(sched["max_start_delay_min"]) * 60, room))))
