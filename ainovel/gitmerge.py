"""push前の git pull --rebase でぶつかった content/ のファイルを自動で解決する。
実行が重なって別の実行が先にコミットしていても、書いた内容を捨てずに取り込むため。

  python -m ainovel.gitmerge   (rebase が止まっている状態で実行する)

- レビュー・AI広場の投稿など「記録の一覧」(JSONの配列): 両方の記録を合わせる(重複は除く)
- state.json(その日の投稿数・トークン数): 先にコミットされた値に、こちらで増えた分を足す
- それ以外(作品データ・本文など): 先にコミットされた方を採用する(話数の食い違いを避けるため)
"""
from __future__ import annotations

import json
import subprocess
import sys


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def _stage(n: int, path: str):
    out = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def _key(item) -> str:
    if isinstance(item, dict):
        if "id" in item:
            return str(item["id"])
        return f"{item.get('reader', item.get('who', ''))}|{item.get('created_at', '')}"
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def _merge(path: str, base, upstream, mine):
    if isinstance(upstream, list) and isinstance(mine, list):
        seen = {_key(x): x for x in upstream}
        # 同じ投稿に両方の実行で付いたいいね・リポストは合わせる
        for x in mine:
            y = seen.get(_key(x))
            if isinstance(x, dict) and isinstance(y, dict):
                for field in ("likes", "reposts"):
                    extra = [v for v in x.get(field) or [] if v not in (y.get(field) or [])]
                    if extra:
                        y[field] = (y.get(field) or []) + extra
        merged = upstream + [x for x in mine if _key(x) not in seen]
        if all(isinstance(x, dict) and "created_at" in x for x in merged):
            merged.sort(key=lambda x: x["created_at"])
        return merged
    if path.endswith("state.json") and isinstance(upstream, dict) and isinstance(mine, dict):
        base = base if isinstance(base, dict) else {}
        merged = dict(upstream)
        if mine.get("date") == upstream.get("date"):
            for k in ("posts", "tokens", "total_posts"):
                if isinstance(mine.get(k), int):
                    merged[k] = upstream.get(k, 0) + mine[k] - (base.get(k, 0) if base.get("date") == mine.get("date") else 0)
            merged["quota_exhausted"] = bool(upstream.get("quota_exhausted") or mine.get("quota_exhausted"))
        return merged
    return None  # 自動では合わせられない


def main() -> int:
    conflicted = [p for p in _git("diff", "--name-only", "--diff-filter=U").split() if p]
    if not conflicted:
        return 0
    for path in conflicted:
        # rebase中は stage 2 = 先にコミットされた側(upstream)、stage 3 = これから載せる自分のコミット
        if path.endswith(".jsonl"):
            # 追記だけのファイル: 両方の行を合わせる(重複は除く)
            lines = [subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True).stdout.splitlines()
                     for n in (2, 3)]
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(l + "\n" for l in dict.fromkeys(lines[0] + lines[1]) if l.strip()))
            print(f"  自動マージ: {path}")
            _git("add", "--", path)
            continue
        base, upstream, mine = _stage(1, path), _stage(2, path), _stage(3, path)
        merged = _merge(path, base, upstream, mine) if path.endswith(".json") else None
        if merged is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=1 if path.endswith("sns.json") else 2)
            print(f"  自動マージ: {path}")
        else:
            _git("checkout", "--ours", "--", path, check=False)  # 先にコミットされた方
            print(f"  先のコミットを採用: {path}")
        _git("add", "--", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
