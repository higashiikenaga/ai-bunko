// お題箱。人間が送った「お題」(短い言葉)を、AI作家が新作のモチーフに使う。
// GET  /api/odai            … 届いたお題(新しい順、最大60件)
// POST /api/odai {"word"}   … お題を送る(1人1日1回。1〜12文字、日本語・英数字のみ)
import { db, jstDay, json, notConfigured, readBody, sameOrigin, voterHash } from "../../lib/db.js";

// 作品に使えない言葉(完全一致でなく部分一致で弾く)。AI作家側でも不適切なお題は使わない
const NG = ["死ね", "殺", "エロ", "セックス", "レイプ", "ちんこ", "まんこ", "おっぱい", "爆弾", "テロ", "自殺", "薬物", "麻薬",
  "差別", "ヘイト", "ナチ", "http", "www", ".com", "@"];
const ALLOWED = /^[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}ーa-zA-Z0-9０-９Ａ-Ｚａ-ｚ・ 　]{1,12}$/u;

export async function onRequestGet({ env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  const { results } = await DB.prepare("SELECT word, count, last_at FROM odai ORDER BY last_at DESC LIMIT 60").all();
  return json({ odai: results }, 200, { "cache-control": "public, max-age=30" });
}

export async function onRequestPost({ request, env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return json({ error: "不正なリクエストです" }, 403);
  const body = await readBody(request);
  const word = String(body?.word || "").normalize("NFKC").trim().replace(/\s+/g, " ");
  if (!ALLOWED.test(word)) return json({ error: "お題は1〜12文字の日本語・英数字で送ってください" }, 400);
  const lower = word.toLowerCase();
  if (NG.some((w) => lower.includes(w))) return json({ error: "そのお題は受け付けていません" }, 400);

  const day = jstDay();
  const voter = await voterHash(env, request, `odai:${day}`);
  const seen = await DB.prepare("INSERT OR IGNORE INTO odai_seen (voter, day) VALUES (?1, ?2)").bind(voter, day).run();
  if (seen.meta.changes === 0) return json({ error: "お題は1日1回まで送れます。また明日どうぞ" }, 429);
  const now = Date.now();
  await DB.batch([
    DB.prepare(`INSERT INTO odai (word, count, first_at, last_at) VALUES (?1, 1, ?2, ?2)
                ON CONFLICT(word) DO UPDATE SET count = count + 1, last_at = excluded.last_at`).bind(word, now),
    DB.prepare("DELETE FROM odai_seen WHERE day < ?1").bind(jstDay(2)),
  ]);
  return json({ ok: true, word });
}
