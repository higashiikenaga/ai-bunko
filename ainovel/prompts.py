"""プロンプト。StoRyan の prompts.py をベースに、人間の指示なしで書き進められるようにしたもの。"""
from __future__ import annotations

import json
from typing import Any

EDITOR_SYSTEM = "あなたは小説の企画・編集者です。指示されたJSON形式のみを厳密に出力します。前後に説明文やコードブロック記号を付けません。"


def author_block(author: dict[str, Any] | None) -> str:
    if not author:
        return ""
    return f"""# あなたの作家としての書き方(必ず守る)
- ペンネーム: {author.get('name')}
- 文体: {author.get('style', '')}
- 口調・語り: {author.get('tone', '')}
- こだわり: {author.get('quirks', '')}
"""


def world_prompt(genre: str, motifs: list[str], avoid_titles: list[str], author: dict[str, Any] | None = None) -> str:
    avoid = "、".join(avoid_titles[-15:]) or "(なし)"
    return f"""オリジナルの長編小説を1本企画してください。人間からの指示はありません。あなた自身が面白いと思う物語を自由に考えてください。
{author_block(author)}
# 条件
- ジャンル: {genre}
- 作家の書き方・作風に合った物語にする(style_notes にはその作家の文体を反映する)
- 物語のどこかに次のモチーフを自然に取り入れる: {"、".join(motifs)}
- 既存作品のタイトル({avoid})とは似ないタイトル・設定にする
- 実在の人物・団体・作品のキャラクターは使わない

# 出力JSON形式(全項目を日本語で埋める)
{{
  "title": "作品タイトル",
  "genre": "{genre}",
  "tone": "文体・雰囲気(短く)",
  "pov": "視点(例: 三人称)",
  "tense": "時制(例: 過去形)",
  "setting": "舞台(時代・場所)",
  "premise": "あらすじの核(3〜5文。読者を引き込む紹介文として書く)",
  "themes": ["テーマ1", "テーマ2"],
  "rules": ["世界のルール(矛盾防止のため具体的に)"],
  "plot_outline": ["序盤: ...", "中盤: ...", "終盤: ...", "結末: ..."],
  "style_notes": "文体上の指示"
}}"""


def characters_prompt(world: dict[str, Any]) -> str:
    return f"""次の小説の主要登場人物を3〜4人設計してください。

# 作品
タイトル: {world.get('title')} / ジャンル: {world.get('genre')} / 舞台: {world.get('setting')}
あらすじ: {world.get('premise')}
プロット: {" / ".join(world.get('plot_outline', []))}

# 出力JSON形式(日本語で埋める)
{{
  "characters": [
    {{
      "name": "フルネーム",
      "role": "主人公/相棒/敵役/脇役など",
      "age": 0,
      "gender": "性別",
      "appearance": "外見の特徴",
      "personality": "性格・一人称",
      "speech_style": "話し方のクセ",
      "background": "経歴",
      "motivation": "物語上の目的",
      "relationships": [{{"name": "相手の名前", "relation": "関係"}}],
      "arc": "物語を通じた変化"
    }}
  ]
}}"""


def system_prompt(world: dict[str, Any], author: dict[str, Any] | None = None) -> str:
    rules = "\n".join(f"- {r}" for r in world.get("rules", [])) or "(特になし)"
    who = f"日本語小説家「{author['name']}」" if author else "プロの日本語小説家"
    return f"""あなたは{who}です。以下の作品設定を厳守して、小説の本文を執筆してください。
{author_block(author)}
# 作品設定
タイトル: {world.get('title', '')}
ジャンル: {world.get('genre', '')}
トーン: {world.get('tone', '')}
視点: {world.get('pov', '三人称')} / 時制: {world.get('tense', '過去形')}
舞台: {world.get('setting', '')}
テーマ: {", ".join(world.get('themes', []))}

# 世界のルール(矛盾厳禁)
{rules}

# 文体
{world.get('style_notes') or '自然で読みやすい日本語で、地の文と会話文をバランスよく。'}

出力は小説の本文のみ。章タイトル・見出し・Markdown記法・あとがき・解説・「以上です」のようなメタ発言は書かない。"""


def _characters_block(characters: list[dict[str, Any]]) -> str:
    lines = []
    for c in characters:
        rel = "; ".join(f"{r.get('name')}({r.get('relation')})" for r in c.get("relationships", []) or [])
        lines.append(
            f"### {c.get('name')}({c.get('role', '')})\n"
            f"- {c.get('age')}歳 / {c.get('gender')} / 外見: {c.get('appearance')}\n"
            f"- 性格: {c.get('personality')} / 口調: {c.get('speech_style')}\n"
            f"- 目的: {c.get('motivation')} / 変化: {c.get('arc')}\n"
            f"- 関係: {rel or 'なし'}"
        )
    return "\n\n".join(lines)


def phase_instruction(index: int, total: int) -> str:
    if index == 1:
        return "物語の導入。主人公と舞台を印象的に描き、読者を引き込む事件や謎を提示する。"
    if index == total:
        return "最終章。これまでの伏線を回収し、物語をきちんと完結させる。余韻のある結末にする。"
    if index == total - 1:
        return "クライマックス直前。最大の危機・対立が頂点に達し、結末へ向けて緊張を高める。"
    ratio = index / total
    if ratio < 0.4:
        return "物語を展開させる。人物同士の関係を深め、新たな障害や手がかりを出す。"
    return "中盤から終盤への転換点。予想外の展開や真実の一端を明かし、物語を大きく動かす。"


def chapter_prompt(
    world: dict[str, Any],
    characters: list[dict[str, Any]],
    memory_bundle: str,
    last_tail: str,
    index: int,
    total: int,
    target_chars: int,
    feedback: str = "",
) -> str:
    outline = "\n".join(f"- {p}" for p in world.get("plot_outline", []))
    return f"""# 登場人物(正典・不変)
{_characters_block(characters)}

# プロット骨子
{outline}

# これまでの物語で確定していること(正典)
{memory_bundle or "(まだ何もない。これが第1章)"}

# 直前の章の末尾(文体と場面をつなげるため)
{last_tail or "(なし。冒頭です)"}

# 今回書くもの: 全{total}章中の第{index}章
{phase_instruction(index, total)}
{("" if not feedback else chr(10) + feedback + chr(10))}
# 分量
日本語で約{target_chars}文字。途中で切り上げず、章として一区切りつくところまで書く。

上記すべてと矛盾しないように、第{index}章の本文を書いてください。"""


def summary_prompt(chapter_text: str) -> str:
    return f"""以下は小説の1章分の本文です。次の章を書くときに参照する記録を作るため、次のJSON形式で出力してください。

{{
  "title": "この章の短い題名(10文字前後)",
  "summary": "3〜5文のあらすじ",
  "new_facts": ["この章で確定した、今後も変わらない事実"],
  "open_threads": ["この章で生まれた未回収の伏線"]
}}

# 本文
{chapter_text}"""


def parse_json(text: str) -> dict:
    """モデル出力からJSONオブジェクトを取り出す(```json などが付いていても拾う)。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
    return json.loads(text[start : end + 1])


def clean_chapter(text: str) -> str:
    """見出し・Markdown記法・コードブロックなど本文以外の混入を取り除く。"""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("#") or s.startswith("```") or s in ("---", "***", "* * *"):
            continue
        lines.append(line.replace("**", "").replace("__", ""))
    return "\n".join(lines).strip()
