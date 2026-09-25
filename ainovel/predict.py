"""展開予想。人間もAIも参加できる「次の話どうなる?」の3択。
- 1話書くたびに、AIが次の話の予想問題(3択)を作り、評価AIの何人かが予想を投じる
- 人間はサイトで投票する(Cloudflare Functions /api/predict)
- 作家は次の話を書く前に予想の分布を見て、期待に応えるか裏切るかを自分で決める
- 次の話が出たら、どの選択肢が当たったかをAIが判定し、人間とAIの的中率を記録する
"""
from __future__ import annotations

import json
import random
import urllib.request

from ainovel import prompts
from ainovel.novel import Novel, now_iso

USER_AGENT = "ai-bunko/1.0 (+https://github.com/higashiikenaga/ai-bunko)"
SYSTEM = "あなたは小説投稿サイトの企画担当AIです。指定されたJSONのみを出力します。前後に説明文やコードブロック記号を付けません。"
_human_cache: dict | None = None


def fetch_human_votes(site_url: str) -> dict:
    """人間の予想票 {"作品ID:話": [a, b, c]}。取れなければ空(執筆は止めない)。"""
    global _human_cache
    if _human_cache is not None:
        return _human_cache
    _human_cache = {}
    if site_url:
        try:
            req = urllib.request.Request(f"{site_url.rstrip('/')}/api/predictions", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as res:
                _human_cache = json.load(res).get("votes", {})
        except Exception as e:  # noqa: BLE001
            print(f"  (人間の予想票を取得できませんでした: {e})")
    return _human_cache


def human_counts(novel: Novel, chapter: int, site_url: str) -> list[int]:
    return list(fetch_human_votes(site_url).get(f"{novel.id}:{chapter}", [0, 0, 0]))


def _pct(counts: list[int]) -> str:
    total = sum(counts)
    return " / ".join(f"{chr(65 + i)} {round(100 * c / total) if total else 0}%" for i, c in enumerate(counts))


def author_block(novel: Novel, index: int, site_url: str) -> str:
    """これから書く話の予想が出ていれば、その分布を作家に見せる。"""
    p = novel.meta.get("prediction")
    if not p or p["chapter"] != index:
        return ""
    human = human_counts(novel, index, site_url)
    lines = ["", "# 読者の展開予想(この話について)", f"問題: {p['question']}"]
    lines += [f"- {chr(65 + i)}: {o}" for i, o in enumerate(p["options"])]
    lines.append(f"人間の読者の予想({sum(human)}票): {_pct(human)}")
    lines.append(f"評価AIの予想({sum(p['ai_votes'])}票): {_pct(p['ai_votes'])}")
    lines.append("予想に応えるか、裏切るかは作家としてあなたが決めてよい。ただし物語の設定・構成・これまでの正典を優先し、"
                 "予想に合わせるために話をねじ曲げない。")
    return "\n".join(lines)


def make_prediction(llm, novel: Novel, cfg: dict, rng: random.Random | None = None) -> None:
    """次の話の予想問題を作り、評価AIの何人かに予想させる。完結していれば何もしない。"""
    rng = rng or random.Random()
    if novel.meta.get("status") == "completed" or not novel.chapters:
        novel.meta.pop("prediction", None)
        novel.save_meta()
        return
    critics = rng.sample(cfg.get("readers") or [], min(8, len(cfg.get("readers") or [])))
    last = novel.chapters[-1]
    who = "\n".join(f"- {c['name']}(辛口度: {c.get('strictness', '普通')}。好み: {c.get('taste', '')})" for c in critics)
    user = f"""小説『{novel.meta['title']}』の読者参加企画として、「次の話でどうなるか」の3択予想問題を作ってください。

# あらすじ
{novel.world.get('premise', '')}

# これまでの話
{chr(10).join(f"- 第{i}話: {c.get('summary', '')}" for i, c in enumerate(novel.chapters, 1))}

# 最新話(第{len(novel.chapters)}話「{last['title']}」)の続きが気になるポイントを1つ選び、3つの選択肢を作る
- 選択肢はどれもありえそうで、読者が迷うものにする。1つは意外な展開にする
- ネタバレになる確定情報は書かない。各選択肢は25文字以内

# 次の評価AIたちが、それぞれの好みで予想する(選択肢の番号 0〜2)
{who}

{{"question": "問題文(35文字以内)", "options": ["選択肢A", "選択肢B", "選択肢C"], "votes": {{"評価AIの名前": 0, ...}}}}"""
    data = prompts.parse_json(llm.chat(SYSTEM, user, max_tokens=2048, temperature=0.9))
    options = [str(o).strip()[:40] for o in data.get("options") or [] if str(o).strip()][:3]
    if len(options) < 3 or not str(data.get("question", "")).strip():
        raise ValueError("予想問題を作れませんでした")
    ai_votes = [0, 0, 0]
    voters = {}
    names = {c["name"] for c in critics}
    for name, v in (data.get("votes") or {}).items():
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if name in names and 0 <= v < 3:
            ai_votes[v] += 1
            voters[name] = v
    novel.meta["prediction"] = {"chapter": len(novel.chapters) + 1, "question": str(data["question"]).strip()[:60],
                                "options": options, "ai_votes": ai_votes, "ai_voters": voters, "created_at": now_iso()}
    novel.save_meta()
    print(f"  🔮 展開予想: {novel.meta['prediction']['question']}")


def judge(llm, novel: Novel, index: int, site_url: str) -> None:
    """書き終えた話が予想のどれに当たるかを判定して記録する。"""
    p = novel.meta.get("prediction")
    if not p or p["chapter"] != index:
        return
    ch = novel.chapters[-1]
    opts = "\n".join(f"{i}: {o}" for i, o in enumerate(p["options"]))
    user = f"""小説の第{index}話について、事前の3択予想のどれが当たったかを判定してください。

# 予想問題
{p['question']}
{opts}

# 実際の第{index}話のあらすじ
{ch.get('summary', '')}

いちばん近い選択肢の番号を answer に。どれにも当てはまらなければ -1(予想外の展開)。
{{"answer": 0, "reason": "判定の理由(40文字以内)"}}"""
    try:
        data = prompts.parse_json(llm.chat(SYSTEM, user, max_tokens=1024, temperature=0.2))
        answer = int(data.get("answer", -1))
    except (ValueError, TypeError):
        answer, data = -1, {}
    answer = answer if 0 <= answer < len(p["options"]) else -1
    human = human_counts(novel, index, site_url)
    result = {**p, "answer": answer, "reason": str(data.get("reason", "")).strip()[:60], "human_votes": human,
              "judged_at": now_iso()}
    novel.meta.setdefault("prediction_results", []).append(result)
    novel.meta.pop("prediction", None)
    novel.save_meta()
    mark = f"{chr(65 + answer)}が的中" if answer >= 0 else "予想外の展開"
    print(f"  🔮 予想の結果: {mark}(人間 {_pct(human)} / AI {_pct(p['ai_votes'])})")


def scoreboard(results: list[dict]) -> dict:
    """人間とAIの的中率(票ベース)。"""
    h_hit = h_all = a_hit = a_all = 0
    for r in results:
        a = r.get("answer", -1)
        hv, av = r.get("human_votes") or [0, 0, 0], r.get("ai_votes") or [0, 0, 0]
        h_all += sum(hv)
        a_all += sum(av)
        if a >= 0:
            h_hit += hv[a]
            a_hit += av[a]
    return {"human": {"hit": h_hit, "all": h_all, "rate": round(100 * h_hit / h_all) if h_all else None},
            "ai": {"hit": a_hit, "all": a_all, "rate": round(100 * a_hit / a_all) if a_all else None},
            "questions": len(results), "surprises": sum(1 for r in results if r.get("answer", -1) < 0)}
