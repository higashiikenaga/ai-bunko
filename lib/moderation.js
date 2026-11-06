// 全年齢向けサイトのための表現チェック(ainovel/moderation.py と同じ規則)。
// 該当する語があれば投稿を拒否する。語リストは config.yaml の moderation(ビルド時に data/moderation.json へ出力)。
// 語の一致だけを見るので、言い換えや文脈は判定できない。完全な検閲ではなく、露骨な表現を弾く最低限の仕組み。

const SEP = "[\\s\\u200b-\\u200d\\u2060\\ufeff・･.,_*＊※\\-－—\\u3000]*";
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 文字数を変えずに表記ゆれをそろえる(全角→半角英数、カタカナ→ひらがな、小文字化)
export function fold(text) {
  let out = "";
  for (const ch of text) {
    let c = ch.normalize("NFKC");
    if ([...c].length !== 1) c = ch;
    const code = c.codePointAt(0);
    if (code >= 0x30a1 && code <= 0x30f6) c = String.fromCodePoint(code - 0x60);
    const low = c.toLowerCase();
    out += [...low].length === 1 ? low : c;
  }
  return out;
}

export function compileWords(words) {
  return [...new Set(words.filter(Boolean))].map((w) => [w, new RegExp([...fold(w)].map(esc).join(SEP))]);
}

export function findWords(text, pats) {
  const folded = fold(text);
  return pats.filter(([, p]) => p.test(folded)).map(([w]) => w);
}
